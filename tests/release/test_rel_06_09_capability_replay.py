"""Pruebas de aceptación formal de Release: REL-06, REL-07, REL-08 y REL-09 (Capability Robustness & Anti-Replay).

Conforme al documento de cierre 'PRAXEON v1.0.0 — Plan formal de cierre de versión antes de integrar Context Caching':
- REL-06: Mismatch de session_id, action_hash o state_hash -> Rechazado determinísticamente en SecureExecutor.
- REL-07: Capability expirada por timestamp -> Rechazada determinísticamente (PolicyViolation / HTTP 403).
- REL-08: Firma HMAC forjada o con secreto erróneo -> Rechazada determinísticamente (PolicyViolation / HTTP 403).
- REL-09: Replay attack & concurrencia real (50-100 llamadas concurrentes simultáneas sobre la misma capability):
          Exactamente 1 ejecución física exitosa y N-1 bloqueadas por nonce consumido.
"""

import concurrent.futures
from datetime import datetime, timedelta
import os
from pathlib import Path
import threading
import pytest
from starlette.testclient import TestClient

from praxeon.domain.action import compute_action_hash
from praxeon.domain.decision import (
    DecisionReceipt,
    DecisionStatus,
    ExecutionMode,
    PolicyDecision,
    compute_receipt_signature,
    compute_state_hash,
    sign_receipt,
    verify_receipt_signature,
)
from praxeon.domain.models import ActionCandidate, Goal, ToolCall
from praxeon.runtime.executor import PolicyViolation, SecureExecutor
from praxeon.runtime.nonce_store import InMemoryNonceStore, SqliteNonceStore
from praxeon.runtime.state import SessionState
from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    set_active_security_profile,
    set_runtime_service,
)
from praxeon.server.schemas.action import ProposeActionRequest
from praxeon.server.schemas.decision import ExecuteDecisionRequest


LEGIT_SECRET = "praxeon_super_production_secret_key_9999"
ATTACKER_SECRET = "evil_hacker_forged_secret_key_00000"


@pytest.fixture(autouse=True)
def reset_security_context():
    """Limpia el contexto de seguridad activo antes y después de cada prueba."""
    set_active_security_profile(None)
    yield
    set_active_security_profile(None)


@pytest.fixture
def runtime_service(tmp_path, monkeypatch):
    """Instancia limpia de RuntimeApplicationService."""
    monkeypatch.setenv("PRAXEON_SECRET_KEY", LEGIT_SECRET)
    monkeypatch.setenv("PRAXEON_API_KEY", LEGIT_SECRET)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "replay_cache"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


@pytest.fixture
def test_client(runtime_service):
    """Cliente de test para llamadas HTTP autenticadas."""
    app = create_app()
    return TestClient(app)


# =============================================================================
# REL-06: Hash & Session Binding Invariants
# =============================================================================

def test_rel_06_session_id_mismatch_rejected(tmp_path):
    """REL-06: Un capability emitido para una sesión no puede ejecutarse en otra sesión (Session hijacking)."""
    nonce_store = InMemoryNonceStore()
    executor = SecureExecutor(secret_key=LEGIT_SECRET, nonce_store=nonce_store)

    state_victim = SessionState(
        session_id="sess_victim",
        goal=Goal(objective="Sesión víctima"),
        workspace_root=str(tmp_path),
    )
    action = ActionCandidate(
        id="act_1",
        description="Lectura de reporte",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "report.txt"}),
    )

    # Capability emitido para 'sess_attacker', no para 'sess_victim'
    receipt_alien = DecisionReceipt(
        decision_id="dec_alien_001",
        session_id="sess_attacker",
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=compute_state_hash(state_victim.to_snapshot()),
        nonce="nonce_alien_1",
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
    )
    receipt_alien = sign_receipt(receipt_alien, LEGIT_SECRET)

    decision = PolicyDecision(status=DecisionStatus.ALLOW)

    with pytest.raises(PolicyViolation, match="ID de sesión del capability"):
        executor.execute(
            action=action,
            state=state_victim,
            receipt=receipt_alien,
            decision=decision,
        )


def test_rel_06_action_hash_mismatch_rejected(tmp_path):
    """REL-06: Si se manipula la herramienta o los argumentos entre la emisión y la ejecución física, el hash falla."""
    nonce_store = InMemoryNonceStore()
    executor = SecureExecutor(secret_key=LEGIT_SECRET, nonce_store=nonce_store)

    state = SessionState(
        session_id="sess_hash_check",
        goal=Goal(objective="Verificación de integridad de acción"),
        workspace_root=str(tmp_path),
    )

    legit_action = ActionCandidate(
        id="act_legit",
        description="Lectura de documento público",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "public_doc.txt"}),
    )
    tampered_action = ActionCandidate(
        id="act_tampered",
        description="Escritura de script modificado",
        tool_call=ToolCall(tool_name="write_file", arguments={"path": "backdoor.sh", "content": "malicious"}),
    )

    # El receipt fue generado y firmado para 'legit_action'
    receipt = DecisionReceipt(
        decision_id="dec_action_tamper",
        session_id="sess_hash_check",
        action_id=legit_action.id,
        action_hash=compute_action_hash(legit_action),
        state_hash=compute_state_hash(state.to_snapshot()),
        nonce="nonce_act_tamper_1",
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
    )
    receipt = sign_receipt(receipt, LEGIT_SECRET)

    decision = PolicyDecision(status=DecisionStatus.ALLOW)

    # Atacante intenta ejecutar 'tampered_action' con el capability de 'legit_action'
    with pytest.raises(PolicyViolation, match="El hash de la acción .* no coincide"):
        executor.execute(
            action=tampered_action,
            state=state,
            receipt=receipt,
            decision=decision,
        )


def test_rel_06_state_hash_mismatch_rejected(tmp_path):
    """REL-06: Si el estado del sistema avanza (desfase temporal/concurrente), un capability del estado anterior es nulo."""
    nonce_store = InMemoryNonceStore()
    executor = SecureExecutor(secret_key=LEGIT_SECRET, nonce_store=nonce_store)

    state = SessionState(
        session_id="sess_state_desync",
        goal=Goal(objective="Detección de desincronización de estado"),
        workspace_root=str(tmp_path),
    )
    action = ActionCandidate(
        id="act_step",
        description="Lectura de estado",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "status.json"}),
    )

    # Estado ficticio o anterior
    stale_state_hash = "000000000000000000000000000000000000000000000000000000000000dead"

    receipt_stale = DecisionReceipt(
        decision_id="dec_stale_state",
        session_id="sess_state_desync",
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=stale_state_hash,
        nonce="nonce_stale_1",
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
    )
    receipt_stale = sign_receipt(receipt_stale, LEGIT_SECRET)

    decision = PolicyDecision(status=DecisionStatus.ALLOW)

    with pytest.raises(PolicyViolation, match="hash de estado .* está desfasado"):
        executor.execute(
            action=action,
            state=state,
            receipt=receipt_stale,
            decision=decision,
        )


# =============================================================================
# REL-07: Timestamp Expiration Invariants
# =============================================================================

def test_rel_07_expired_capability_rejected_in_executor(tmp_path):
    """REL-07: Una capability cuya fecha expires_at ha vencido es rechazada determinísticamente por SecureExecutor."""
    nonce_store = InMemoryNonceStore()
    executor = SecureExecutor(secret_key=LEGIT_SECRET, nonce_store=nonce_store)

    state = SessionState(
        session_id="sess_expired",
        goal=Goal(objective="Comprobación de expiración TTL"),
        workspace_root=str(tmp_path),
    )
    action = ActionCandidate(
        id="act_exp",
        description="Lectura de prueba para expiración",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "test.txt"}),
    )

    # Expiró hace 10 segundos
    expired_at = datetime.utcnow() - timedelta(seconds=10)

    receipt_expired = DecisionReceipt(
        decision_id="dec_expired_001",
        session_id="sess_expired",
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=compute_state_hash(state.to_snapshot()),
        nonce="nonce_expired_1",
        decision_status=DecisionStatus.ALLOW,
        expires_at=expired_at,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
    )
    receipt_expired = sign_receipt(receipt_expired, LEGIT_SECRET)

    assert receipt_expired.is_expired() is True

    decision = PolicyDecision(status=DecisionStatus.ALLOW)

    with pytest.raises(PolicyViolation, match="El capability ha expirado"):
        executor.execute(
            action=action,
            state=state,
            receipt=receipt_expired,
            decision=decision,
        )


def test_rel_07_expired_capability_rejected_via_api(runtime_service, test_client, tmp_path):
    """REL-07: Llamada HTTP a /v1/decisions/{id}/execute con capability expirada retorna 403 Forbidden."""
    test_file = (tmp_path / "test.txt").resolve()
    test_file.write_text("lectura valida", encoding="utf-8")
    session_id = "sess_api_expired"
    runtime_service.create_session(
        goal="Sesión para prueba de expiración en API",
        session_id=session_id,
        workspace_root=str(tmp_path),
    )

    # Proponer acción legítima
    prop = ProposeActionRequest(
        tool="read_file",
        arguments={"path": str(test_file)},
        thought_rationale="Lectura inocua",
    )
    resp = runtime_service.propose_action(session_id=session_id, proposal=prop)
    assert resp.status == "ALLOW"
    assert resp.capability is not None

    # Simular paso del tiempo: alterar el registro para que expires_at esté vencido
    record = runtime_service.decision_repository.get(resp.decision_id)
    expired_receipt = record["receipt"].model_copy(
        update={"expires_at": datetime.utcnow() - timedelta(seconds=30)}
    )
    # Volver a firmar con timestamp vencido
    expired_receipt = sign_receipt(expired_receipt, LEGIT_SECRET)
    record["receipt"] = expired_receipt
    runtime_service.decision_repository.save(record)
    with runtime_service._lock:
        runtime_service._decisions[resp.decision_id] = record

    # Intentar ejecutar vía endpoint HTTP
    cap_payload = expired_receipt.to_capability_payload()
    http_resp = test_client.post(
        f"/v1/decisions/{resp.decision_id}/execute",
        headers={"X-API-Key": LEGIT_SECRET},
        json={"capability_token": cap_payload.model_dump(mode="json") if cap_payload else {}},
    )

    assert http_resp.status_code == 403
    assert "ha expirado" in http_resp.json()["detail"]


# =============================================================================
# REL-08: HMAC Authenticity & Tamper Resistance
# =============================================================================

def test_rel_08_missing_signature_rejected(tmp_path):
    """REL-08: Capability sin firma HMAC presentado ante un runtime con secret_key configurada es denegado."""
    nonce_store = InMemoryNonceStore()
    executor = SecureExecutor(secret_key=LEGIT_SECRET, nonce_store=nonce_store)

    state = SessionState(
        session_id="sess_unsigned",
        goal=Goal(objective="Comprobación de firma ausente"),
        workspace_root=str(tmp_path),
    )
    action = ActionCandidate(
        id="act_unsigned",
        description="Lectura sin firma",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "test.txt"}),
    )

    receipt_unsigned = DecisionReceipt(
        decision_id="dec_unsigned_001",
        session_id="sess_unsigned",
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=compute_state_hash(state.to_snapshot()),
        nonce="nonce_unsigned_1",
        decision_status=DecisionStatus.ALLOW,
        signature=None,  # Sin firma
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
    )

    decision = PolicyDecision(status=DecisionStatus.ALLOW)

    with pytest.raises(PolicyViolation, match="carece de firma HMAC auténtica"):
        executor.execute(
            action=action,
            state=state,
            receipt=receipt_unsigned,
            decision=decision,
        )


def test_rel_08_forged_or_wrong_secret_signature_rejected(tmp_path):
    """REL-08: Capability firmado por atacante con clave secreta arbitraria o adulterada es rechazado."""
    nonce_store = InMemoryNonceStore()
    executor = SecureExecutor(secret_key=LEGIT_SECRET, nonce_store=nonce_store)

    state = SessionState(
        session_id="sess_forged",
        goal=Goal(objective="Comprobación de clave forjada"),
        workspace_root=str(tmp_path),
    )
    action = ActionCandidate(
        id="act_forged",
        description="Lectura con clave atacante",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "secret.txt"}),
    )

    receipt_forged = DecisionReceipt(
        decision_id="dec_forged_001",
        session_id="sess_forged",
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=compute_state_hash(state.to_snapshot()),
        nonce="nonce_forged_1",
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
    )
    # Firmado con la clave del atacante
    receipt_forged = sign_receipt(receipt_forged, ATTACKER_SECRET)

    decision = PolicyDecision(status=DecisionStatus.ALLOW)

    with pytest.raises(PolicyViolation, match="firma HMAC del capability es inválida o ha sido manipulada"):
        executor.execute(
            action=action,
            state=state,
            receipt=receipt_forged,
            decision=decision,
        )


def test_rel_08_tampered_payload_invalidates_hmac(tmp_path):
    """REL-08: Si un atacante modifica un solo campo (p. ej. execution_mode a full_access), el HMAC no cuadra."""
    nonce_store = InMemoryNonceStore()
    executor = SecureExecutor(secret_key=LEGIT_SECRET, nonce_store=nonce_store, allow_full_access=True)

    state = SessionState(
        session_id="sess_tamper_mode",
        goal=Goal(objective="Comprobación de elevación no autorizada"),
        workspace_root=str(tmp_path),
        metadata={"execution_mode": "full_access"},
    )
    action = ActionCandidate(
        id="act_tamper",
        description="Lectura para prueba de manipulación",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "test.txt"}),
    )

    # Emitido originalmente para local_restricted
    receipt = DecisionReceipt(
        decision_id="dec_tamper_001",
        session_id="sess_tamper_mode",
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=compute_state_hash(state.to_snapshot()),
        nonce="nonce_tamper_1",
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
    )
    receipt = sign_receipt(receipt, LEGIT_SECRET)

    # Atacante adultera el objeto para cambiarlo a 'full_access' sin cambiar la firma
    receipt_elevated = receipt.model_copy(update={"execution_mode": ExecutionMode.FULL_ACCESS.value})

    assert not verify_receipt_signature(LEGIT_SECRET, receipt_elevated)

    decision = PolicyDecision(status=DecisionStatus.ALLOW)

    with pytest.raises(PolicyViolation, match="firma HMAC del capability es inválida"):
        executor.execute(
            action=action,
            state=state,
            receipt=receipt_elevated,
            decision=decision,
        )


# =============================================================================
# REL-09: Replay Prevention & Massive Concurrency Stress Test
# =============================================================================

def test_rel_09_sequential_replay_rejected(tmp_path):
    """REL-09: La segunda ejecución del mismo capability es bloqueada inmediatamente."""
    nonce_store = InMemoryNonceStore()
    executor = SecureExecutor(secret_key=LEGIT_SECRET, nonce_store=nonce_store)

    test_file = tmp_path / "seq_test.txt"
    test_file.write_text("contenido inicial", encoding="utf-8")

    state = SessionState(
        session_id="sess_seq_replay",
        goal=Goal(objective="Replay secuencial"),
        metadata={"workspace_root": str(tmp_path)},
    )
    action = ActionCandidate(
        id="act_read",
        description="Lectura secuencial",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "seq_test.txt"}),
    )
    receipt = DecisionReceipt(
        decision_id="dec_seq_001",
        session_id="sess_seq_replay",
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=compute_state_hash(state.to_snapshot()),
        nonce="nonce_seq_1",
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
    )
    receipt = sign_receipt(receipt, LEGIT_SECRET)
    decision = PolicyDecision(status=DecisionStatus.ALLOW)

    # 1. Primera ejecución -> Éxito
    obs1 = executor.execute(action=action, state=state, receipt=receipt, decision=decision)
    assert not obs1.is_error

    # 2. Segunda ejecución del mismo receipt -> Bloqueo estricto
    with pytest.raises(PolicyViolation, match="ya ha sido consumido previamente \\(Replay attack prevention\\)"):
        executor.execute(action=action, state=state, receipt=receipt, decision=decision)


def test_rel_09_sqlite_nonce_store_durable_replay_prevention(tmp_path):
    """REL-09: La prevención de replay persiste en disco y sobrevive al reinicio de SecureExecutor (SqliteNonceStore)."""
    db_file = str(tmp_path / "durable_nonces.db")
    store_1 = SqliteNonceStore(db_path=db_file)
    executor_1 = SecureExecutor(secret_key=LEGIT_SECRET, nonce_store=store_1)

    test_file = tmp_path / "any.txt"
    test_file.write_text("contenido durable", encoding="utf-8")

    state = SessionState(
        session_id="sess_sqlite",
        goal=Goal(objective="Replay durable"),
        metadata={"workspace_root": str(tmp_path)},
    )
    action = ActionCandidate(
        id="act_durable",
        description="Lectura durable",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "any.txt"}),
    )
    receipt = DecisionReceipt(
        decision_id="dec_durable_001",
        session_id="sess_sqlite",
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=compute_state_hash(state.to_snapshot()),
        nonce="nonce_durable_42",
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
    )
    receipt = sign_receipt(receipt, LEGIT_SECRET)
    decision = PolicyDecision(status=DecisionStatus.ALLOW)

    # 1. Consumir con executor 1
    obs = executor_1.execute(action=action, state=state, receipt=receipt, decision=decision)
    assert not obs.is_error

    # 2. Instanciar un executor 2 completamente nuevo sobre el mismo store SQLite
    store_2 = SqliteNonceStore(db_path=db_file)
    executor_2 = SecureExecutor(secret_key=LEGIT_SECRET, nonce_store=store_2)

    # 3. Debe fallar inmediatamente
    with pytest.raises(PolicyViolation, match="ya ha sido consumido previamente"):
        executor_2.execute(action=action, state=state, receipt=receipt, decision=decision)


def test_rel_09_massive_concurrent_execution_race_condition_proof(tmp_path):
    """REL-09: 100 hilos concurrentes intentando ejecutar simultáneamente la misma capability.

    Aserciones matemáticas estrictas:
    - Éxitos: Exactamente 1.
    - Rechazos por replay: Exactamente 99.
    - Condición de carrera: 0.
    """
    nonce_store = InMemoryNonceStore()
    executor = SecureExecutor(secret_key=LEGIT_SECRET, nonce_store=nonce_store)

    test_file = tmp_path / "concurrent_target.txt"
    test_file.write_text("datos", encoding="utf-8")

    state = SessionState(
        session_id="sess_concurrent_stress",
        goal=Goal(objective="Estrés de concurrencia extrema"),
        workspace_root=str(tmp_path),
    )
    action = ActionCandidate(
        id="act_concurrent",
        description="Lectura de estrés concurrente",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": str(test_file)}),
    )
    receipt = DecisionReceipt(
        decision_id="dec_race_001",
        session_id="sess_concurrent_stress",
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=compute_state_hash(state.to_snapshot()),
        nonce="nonce_atomic_unique_999",
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
    )
    receipt = sign_receipt(receipt, LEGIT_SECRET)
    decision = PolicyDecision(status=DecisionStatus.ALLOW)

    NUM_CONCURRENT_THREADS = 100
    results = {"success": 0, "rejected": 0, "errors": []}
    lock = threading.Lock()

    def attempt_execution(worker_id: int):
        try:
            executor.execute(action=action, state=state, receipt=receipt, decision=decision)
            with lock:
                results["success"] += 1
        except PolicyViolation as pv:
            with lock:
                if "ya ha sido consumido previamente" in str(pv):
                    results["rejected"] += 1
                else:
                    results["errors"].append(f"Unexpected policy violation: {str(pv)}")
        except Exception as e:
            with lock:
                results["errors"].append(f"Unexpected exception: {str(e)}")

    # Disparar 100 hilos simultáneos
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
        futures = [pool.submit(attempt_execution, i) for i in range(NUM_CONCURRENT_THREADS)]
        concurrent.futures.wait(futures)

    # Verificación matemática estricta
    assert len(results["errors"]) == 0, f"Errores imprevistos: {results['errors']}"
    assert results["success"] == 1, f"Debió haber exactamente 1 éxito, pero hubo: {results['success']}"
    assert results["rejected"] == NUM_CONCURRENT_THREADS - 1, (
        f"Debió haber exactamente {NUM_CONCURRENT_THREADS - 1} rechazos por replay, pero hubo: {results['rejected']}"
    )


def test_rel_09_concurrent_api_replay_stress(runtime_service, test_client, tmp_path):
    """REL-09: 50 llamadas HTTP concurrentes al endpoint /v1/decisions/{id}/execute con el mismo capability.

    Aserciones matemáticas estrictas:
    - HTTP 200 (éxito): Exactamente 1.
    - HTTP 403 (rechazado por replay / política): Exactamente 49.
    """
    target_file = (tmp_path / "any.txt").resolve()
    target_file.write_text("datos", encoding="utf-8")
    session_id = "sess_api_concurrent"
    runtime_service.create_session(
        goal="Sesión de estrés concurrente vía API",
        session_id=session_id,
        workspace_root=str(tmp_path),
    )

    prop = ProposeActionRequest(
        tool="read_file",
        arguments={"path": str(target_file)},
        thought_rationale="Lectura para probar replay en API",
    )
    resp = runtime_service.propose_action(session_id=session_id, proposal=prop)
    assert resp.status == "ALLOW"
    assert resp.capability is not None

    decision_id = resp.decision_id
    cap_token = resp.capability

    NUM_HTTP_REQUESTS = 50
    http_statuses = []
    lock = threading.Lock()

    def send_http_execute(req_id: int):
        r = test_client.post(
            f"/v1/decisions/{decision_id}/execute",
            headers={"X-API-Key": LEGIT_SECRET},
            json={"capability_token": cap_token},
        )
        with lock:
            http_statuses.append(r.status_code)

    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as pool:
        futures = [pool.submit(send_http_execute, i) for i in range(NUM_HTTP_REQUESTS)]
        concurrent.futures.wait(futures)

    assert len(http_statuses) == NUM_HTTP_REQUESTS
    count_200 = http_statuses.count(200)
    count_403 = http_statuses.count(403)

    assert count_200 == 1, f"Esperado exactamente un HTTP 200, obtenido: {count_200}"
    assert count_403 == NUM_HTTP_REQUESTS - 1, (
        f"Esperados {NUM_HTTP_REQUESTS - 1} rechazos HTTP 403, obtenidos: {count_403} (estados: {http_statuses})"
    )
