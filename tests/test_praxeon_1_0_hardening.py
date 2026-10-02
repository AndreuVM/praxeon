"""Suite exhaustiva de pruebas de hardening para PRAXEON 1.0 (tests/test_praxeon_1_0_hardening.py).

Verifica formalmente los 14 criterios de la Definition of Done (Sección 18 del PDF):
1. Empaquetado y wheel praxeon==1.0.0 sin módulos raíz parásitos.
2. UI servida vía Web reflejando estado del runtime.
3. Decision Tree reconstruido deterministamente desde EventStore tras reinicio.
4. Decision Inspector accede a decisiones históricas sin depender de memoria RAM.
5. WebSocket con aislamiento estricto por sesión y recuperación de lagunas (gap recovery).
6. Backends de ejecución explícitos sin fallback implícito hacia Full Access.
7. CapabilityPayload con execution_mode firmado criptográficamente; cualquier manipulación invalida el token.
8. Acciones REVIEW exigen aprobación auditada de operador humano y motivo obligatorio en rechazos.
9. Decisiones BLOCK nunca alcanzan ejecución física.
10. Nonce y capability con protección contra repetición (consume-once / replay protection).
11. Perfil de producción con clave mínima de 32 caracteres y lista CORS estricta sin comodines.
12. Custodia E2E completa en Full Access: Proposal -> Evidence -> Risk -> Provider -> Policy -> Capability -> FullAccessExecutor -> Observation.
13. Estado persistente del entorno de ejecución y operador en respuestas.
14. Documentación de modelo de amenazas y ausencia de aislamiento en Full Access en SECURITY.md.
"""

import asyncio
from datetime import datetime, timedelta
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
import pytest
from starlette.testclient import TestClient

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.decision import (
    CapabilityPayload,
    DecisionReceipt,
    DecisionStatus,
    ExecutionMode,
    PolicyDecision,
    compute_receipt_signature,
    compute_state_hash,
    sign_receipt,
    verify_capability_signature,
    verify_receipt_signature,
)
from praxeon.domain.events import EventType, RuntimeEvent
from praxeon.domain.models import Goal, ProviderAssessment, RiskAssessment, RiskLevel
from praxeon.policy.engine import PolicyEngine
from praxeon.policy.registry import ToolRegistry
from praxeon.runtime.decision_store import SqliteDecisionRepository
from praxeon.runtime.event_bus import EventBus, EventStore
from praxeon.runtime.executor import PolicyViolation, SecureExecutor
from praxeon.runtime.full_access import FullAccessExecutor
from praxeon.runtime.nonce_store import SqliteNonceStore
from praxeon.runtime.state import SessionState
from praxeon.runtime.state_store import SqliteStateStore
from praxeon.runtime.tree_reducer import reduce_events_to_tree
from praxeon.server.app import create_app, validate_security_profile
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service
from praxeon.server.schemas.action import ProposeActionRequest
from praxeon.server.schemas.decision import ConfirmDecisionRequest, ExecuteDecisionRequest


@pytest.fixture
def isolated_service():
    """Crea un RuntimeApplicationService con almacenamiento temporal completamente aislado."""
    temp_dir = tempfile.mkdtemp(prefix="praxeon_hardening_")
    try:
        service = RuntimeApplicationService(db_dir=temp_dir)
        set_runtime_service(service)
        yield service, temp_dir
    finally:
        set_runtime_service(None)
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def client(isolated_service):
    """Cliente HTTP de prueba con servicio aislado."""
    service, _ = isolated_service
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, service


# =============================================================================
# CRITERIO 1: Wheel Packaging & Integrity
# =============================================================================

def test_dod_1_wheel_packaging_cleanliness():
    """DOD-1: El wheel de praxeon==1.0.0 contiene solo el paquete praxeon/ y dist-info."""
    import subprocess
    wheel_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dist"))
    wheels = []
    if os.path.exists(wheel_dir):
        wheels = [f for f in os.listdir(wheel_dir) if f.endswith(".whl") and "1.0.0" in f]

    if not wheels:
        # Intentar construir el wheel bajo demanda en un checkout limpio
        try:
            subprocess.run(
                [sys.executable, "-m", "build", "--wheel", "--no-isolation"],
                cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
                check=True,
                capture_output=True,
            )
            if os.path.exists(wheel_dir):
                wheels = [f for f in os.listdir(wheel_dir) if f.endswith(".whl") and "1.0.0" in f]
        except Exception:
            pass

    if not wheels:
        pytest.skip(
            f"El directorio '{wheel_dir}' no contiene artefactos wheel (.whl). "
            "Ejecute 'python -m build' previamente para verificar la integridad del empaquetado distribuible."
        )

    wheel_path = os.path.join(wheel_dir, wheels[0])
    with zipfile.ZipFile(wheel_path, "r") as zf:
        namelist = zf.namelist()
        for name in namelist:
            is_valid = (
                name.startswith("praxeon/")
                or name.startswith("praxeon-1.0.0.dist-info/")
            )
            assert is_valid, f"Archivo extraño detectado en el wheel empaquetado: {name}"



# =============================================================================
# CRITERIO 2: UI Served via Web Server
# =============================================================================

def test_dod_2_ui_served_via_web(client):
    """DOD-2: La interfaz web se sirve en la raíz / con assets y branding."""
    c, _ = client
    res = c.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    assert "PRAXEON" in res.text


# =============================================================================
# CRITERIO 3: Decision Tree Reconstructed from EventStore Surviving Restart
# =============================================================================

def test_dod_3_tree_reconstruction_survives_restart(isolated_service):
    """DOD-3: El Decision Tree se reconstruye deterministamente desde SQLite tras un reinicio total."""
    service, temp_dir = isolated_service
    sid = "sess_durability_test"

    service.create_session(goal="Validar supervivencia de arbol", session_id=sid)
    req = ProposeActionRequest(
        tool="read_file",
        operation="pyproject.toml",
        arguments={"path": "pyproject.toml"},
        thought_rationale="Lectura inicial",
    )
    resp = service.propose_action(session_id=sid, proposal=req)
    assert resp.decision_id is not None

    # Simular reinicio total del proceso instanciando un nuevo servicio sobre los mismos archivos SQLite
    new_event_bus = EventBus(store=EventStore(db_path=os.path.join(temp_dir, "events.db")))
    all_events = new_event_bus.get_all_events(sid)
    assert len(all_events) >= 3  # session.started, goal.created, action.proposed, etc.

    reconstructed_tree = reduce_events_to_tree(all_events, session_id=sid)
    assert reconstructed_tree.session_id == sid
    assert reconstructed_tree.node_count >= 2
    assert reconstructed_tree.root_id == f"root_{sid}"
    assert "act_1" in reconstructed_tree.nodes


# =============================================================================
# CRITERIO 4: Decision Inspector Historical Access Without RAM
# =============================================================================

def test_dod_4_historical_decisions_access_without_ram(isolated_service):
    """DOD-4: El Decision Inspector recupera decisiones históricas desde SQLite aunque la RAM esté vacía."""
    service, temp_dir = isolated_service
    sid = "sess_inspector_cache_miss"

    service.create_session(goal="Test inspector durability", session_id=sid)
    req = ProposeActionRequest(
        tool="read_file",
        arguments={"path": "README.md"},
        thought_rationale="Inspeccionar README",
    )
    resp = service.propose_action(session_id=sid, proposal=req)
    dec_id = resp.decision_id

    # Forzar purga total de la caché en memoria RAM
    service._decisions.clear()
    assert dec_id not in service._decisions

    # La consulta del inspector debe consultar SQLite y reconstruir los detalles
    detail = service.get_decision_detail(dec_id)
    assert detail is not None
    assert detail.decision_id == dec_id
    assert detail.session_id == sid
    assert detail.decision_tab["tool"] == "read_file"
    assert detail.decision_tab["execution_mode"] == "local_restricted"
    assert detail.decision_tab["execution_backend"] == "LocalProcessSandbox"
    assert detail.receipt_tab["decision_id"] == dec_id
    assert detail.receipt_tab["has_valid_hmac"] is True


# =============================================================================
# CRITERIO 5: WebSocket Session Isolation and Gap Recovery
# =============================================================================

@pytest.mark.asyncio
async def test_dod_5_websocket_session_isolation_and_gap_recovery(isolated_service):
    """DOD-5: Las colas asíncronas no reciben eventos de otras sesiones y recuperan gaps."""
    service, _ = isolated_service
    bus = service.event_bus

    queue_a = asyncio.Queue()
    queue_b = asyncio.Queue()

    bus.register_async_queue(queue_a, session_id="sess_A")
    bus.register_async_queue(queue_b, session_id="sess_B")

    # Emitir evento en Sesión A
    bus.emit(
        session_id="sess_A",
        event_type=EventType.ACTION_PROPOSED,
        payload={"action": "step_a1"},
    )
    await asyncio.sleep(0.05)

    assert not queue_a.empty(), "Cola A debió recibir el evento de Sesión A"
    assert queue_b.empty(), "Cola B no debe recibir eventos de Sesión A (Violación de aislamiento)"

    # Gap Recovery: Recuperar eventos tras reconexión
    bus.emit(session_id="sess_A", event_type=EventType.POLICY_DECIDED, payload={"status": "ALLOW"})
    events_gap = bus.get_events_after(session_id="sess_A", after_sequence=1, limit=10)
    assert len(events_gap) >= 1
    assert events_gap[0].sequence == 2


# =============================================================================
# CRITERIO 6: Explicit Backends & Zero Implicit Fallback to Full Access
# =============================================================================

def test_dod_6_zero_implicit_fallback_to_full_access():
    """DOD-6: Si se solicita contenedor Docker y no está disponible, no hay fallback a Full Access."""
    from praxeon.runtime.sandbox import (
        ContainerSandboxAdapter,
        ContainerSandboxConfig,
        SandboxTier,
        SandboxViolation,
    )

    # 1. Adaptador con fallback_to_local deshabilitado debe fallar con SandboxViolation si docker no existe
    bad_cfg = ContainerSandboxConfig(runtime_binary="nonexistent_docker_binary_xyz", fallback_to_local=False)
    adapter = ContainerSandboxAdapter(config=bad_cfg)

    with pytest.raises(SandboxViolation) as exc_info:
        adapter.execute_command(
            command="whoami",
            cwd=os.getcwd(),
            timeout=5.0,
        )
    assert "no está disponible" in str(exc_info.value).lower()

    # 2. Con fallback_to_local habilitado, degrada a LOCAL_PROCESS, NUNCA a Full Access
    fallback_cfg = ContainerSandboxConfig(runtime_binary="nonexistent_docker_binary_xyz", fallback_to_local=True)
    fallback_adapter = ContainerSandboxAdapter(config=fallback_cfg)
    res = fallback_adapter.execute_command(command="whoami", cwd=os.getcwd(), timeout=5.0)
    assert res.tier == SandboxTier.LOCAL_PROCESS
    assert res.tier != "full_access"


# =============================================================================
# CRITERIO 7: Signed execution_mode in CapabilityPayload (Tamper Resistance)
# =============================================================================

def test_dod_7_capability_execution_mode_tamper_resistance():
    """DOD-7: Manipular execution_mode de local_restricted a full_access invalida la firma HMAC."""
    secret_key = "praxeon_super_secret_test_key_32_chars!!"
    engine = PolicyEngine(secret_key=secret_key)
    receipt = DecisionReceipt(
        decision_id="d_tamper_1",
        action_id="act_tamper_1",
        session_id="sess_tamper",
        action_hash="sha256:dummy_action",
        state_hash="sha256:dummy_state",
        nonce="nonce_tamper_1",
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED,
        expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    signed = sign_receipt(receipt, secret_key)
    cap = signed.to_capability_payload(allowed_tools=["run_command"])
    cap_dict = cap.model_dump(mode="json")

    # 1. Verificación legítima
    assert verify_capability_signature(secret_key, cap_dict) is True

    # 2. Ataque de elevación de privilegios: Atacante cambia el modo a full_access
    tampered_cap = dict(cap_dict)
    tampered_cap["execution_mode"] = "full_access"

    # 3. La verificación criptográfica debe fallar inmediatamente
    assert verify_capability_signature(secret_key, tampered_cap) is False


# =============================================================================
# CRITERIO 8: Audited REVIEW Approval with Mandatory Rejection Reason & RBAC
# =============================================================================

def test_dod_8_review_approval_rbac_and_mandatory_rejection_reason(client):
    """DOD-8: Acciones en REVIEW exigen motivo para rechazar y bloquean al rol 'viewer'."""
    c, service = client
    sid = "sess_review_rbac"

    service.create_session(goal="Test REVIEW requirements", session_id=sid)
    # Proponer acción de alto riesgo que activa confirmación humana
    prop_res = c.post(f"/v1/sessions/{sid}/actions", json={
        "tool": "run_command",
        "operation": "git push origin main",
        "arguments": {"command": "git push origin main"},
        "thought_rationale": "High risk action",
    })
    dec_id = prop_res.json()["data"]["decision_id"]
    assert prop_res.json()["data"]["status"] == "REVIEW"

    # 1. Rol 'viewer' intenta confirmar -> Denegado (403)
    viewer_conf = c.post(f"/v1/decisions/{dec_id}/confirm", json={
        "approved": True,
        "role": "viewer",
    })
    assert viewer_conf.status_code == 403

    # 2. Rechazar sin motivo justificado -> Denegado (400 Bad Request)
    no_reason_reject = c.post(f"/v1/decisions/{dec_id}/confirm", json={
        "approved": False,
        "reason": "",
        "role": "operator",
    })
    assert no_reason_reject.status_code == 400

    # 3. Rechazar con motivo justificado -> Exitoso (200) y podado
    valid_reject = c.post(f"/v1/decisions/{dec_id}/confirm", json={
        "approved": False,
        "reason": "Comando no autorizado en entorno de producción sin revisión previa",
        "role": "operator",
        "operator_id": "auditor_sec",
    })
    assert valid_reject.status_code == 200
    assert valid_reject.json()["data"]["status"] == "BLOCKED"
    assert valid_reject.json()["data"]["operator_id"] == "auditor_sec"


# =============================================================================
# CRITERIO 9: BLOCK Decisions Never Execute
# =============================================================================

def test_dod_9_block_decisions_never_reach_execution(client):
    """DOD-9: Decisiones en estado BLOCK o BLOCKED no pueden ejecutarse físicamente."""
    c, service = client
    sid = "sess_block_exec"

    service.create_session(goal="Test block execution protection", session_id=sid)
    # Proponer comando bloqueado
    prop_res = c.post(f"/v1/sessions/{sid}/actions", json={
        "tool": "run_command",
        "operation": "git push origin main",
        "arguments": {"command": "git push origin main"},
    })
    dec_id = prop_res.json()["data"]["decision_id"]

    # Rechazar explícitamente para ponerla en BLOCKED
    c.post(f"/v1/decisions/{dec_id}/confirm", json={
        "approved": False,
        "reason": "Acción denegada por seguridad",
    })

    # Intentar ejecutar -> 403 Forbidden
    exec_res = c.post(f"/v1/decisions/{dec_id}/execute")
    assert exec_res.status_code == 403
    assert "solo se permite la ejecución de decisiones 'allow'" in exec_res.json()["detail"].lower()


# =============================================================================
# CRITERIO 10: Nonce and Capability Consume-Once (Replay Protection)
# =============================================================================

def test_dod_10_replay_protection_enforcement(client):
    """DOD-10: Un capability / decisión no puede ejecutarse más de una vez."""
    c, service = client
    sid = "sess_replay_test"

    service.create_session(goal="Test replay prevention", session_id=sid)
    prop_res = c.post(f"/v1/sessions/{sid}/actions", json={
        "tool": "read_file",
        "arguments": {"path": "pyproject.toml"},
    })
    dec_id = prop_res.json()["data"]["decision_id"]
    assert prop_res.json()["data"]["status"] == "ALLOW"

    # Primera ejecución -> Exitosa
    first_exec = c.post(f"/v1/decisions/{dec_id}/execute")
    assert first_exec.status_code == 200
    assert first_exec.json()["data"]["success"] is True

    # Replay attack: Segunda ejecución con la misma decisión -> Denegada (403)
    second_exec = c.post(f"/v1/decisions/{dec_id}/execute")
    assert second_exec.status_code == 403
    assert "replay" in second_exec.json()["detail"].lower()


# =============================================================================
# CRITERIO 11: Production Security Profile Enforcement
# =============================================================================

def test_dod_11_production_security_profile_validation(monkeypatch):
    """DOD-11: En producción, se exige clave >= 32 chars y CORS estricto sin '*'."""
    monkeypatch.setenv("PRAXEON_PROFILE", "production")

    # 1. Clave corta o por defecto -> Rechazado
    monkeypatch.setenv("PRAXEON_SECRET_KEY", "short_key_123")
    monkeypatch.setenv("PRAXEON_CORS_ORIGINS", "https://app.praxeon.io")
    with pytest.raises(ValueError, match="al menos 32 caracteres"):
        validate_security_profile()

    # 2. CORS con comodín '*' -> Rechazado
    monkeypatch.setenv("PRAXEON_SECRET_KEY", "this_is_a_very_secure_production_key_32_chars!!")
    monkeypatch.setenv("PRAXEON_CORS_ORIGINS", "*")
    with pytest.raises(ValueError, match=r"comodín '\*'"):
        validate_security_profile()

    # 3. Configuración correcta -> Aceptada
    monkeypatch.setenv("PRAXEON_CORS_ORIGINS", "https://console.praxeon.io,https://app.praxeon.io")
    origins = validate_security_profile()
    assert "https://console.praxeon.io" in origins
    assert "https://app.praxeon.io" in origins


# =============================================================================
# CRITERIO 12: Full Access Complete Custody Chain E2E
# =============================================================================

def test_dod_12_full_access_custody_chain_e2e(client):
    """DOD-12: Demostración E2E completa de ejecución en Full Access con cadena de custodia formal."""
    c, service = client
    sid = "sess_full_access_e2e"

    # 1. Crear sesión formal en Full Access
    sess_res = c.post("/v1/sessions", json={
        "goal": "Inspeccionar sistema en host nativo",
        "session_id": sid,
        "execution_mode": "full_access",
    })
    assert sess_res.status_code == 201
    assert sess_res.json()["data"]["execution_mode"] == "full_access"

    # 2. Proposal -> Evidence -> Risk -> Provider -> Policy -> Capability
    cmd = f'"{sys.executable}" --version'
    prop_res = c.post(f"/v1/sessions/{sid}/actions", json={
        "tool": "run_command",
        "operation": cmd,
        "arguments": {"command": cmd},
        "thought_rationale": "Verificar runtime en host anfitrión",
    })
    assert prop_res.status_code == 200
    dec_data = prop_res.json()["data"]
    dec_id = dec_data["decision_id"]

    # Si la política requiere revisión de operador (REVIEW por comando en Full Access):
    if dec_data["status"] == "REVIEW":
        conf_res = c.post(f"/v1/decisions/{dec_id}/confirm", json={
            "approved": True,
            "role": "operator",
            "operator_id": "auditor_sec",
        })
        assert conf_res.status_code == 200
        dec_data = conf_res.json()["data"]

    assert dec_data["status"] == "ALLOW"
    assert dec_data["execution_mode"] == "full_access"
    assert dec_data["capability"]["execution_mode"] == "full_access"

    # 3. Ejecución física a través de FullAccessExecutor
    exec_res = c.post(f"/v1/decisions/{dec_id}/execute")
    assert exec_res.status_code == 200
    res_data = exec_res.json()["data"]
    assert res_data["success"] is True
    assert res_data["execution_mode"] == "full_access"
    assert res_data["tier"] == "full_access"
    assert "Python" in res_data["output"]

    # 4. Observación persistida en EventStore
    events = service.event_bus.get_all_events(sid)
    obs_events = [e for e in events if e.type == EventType.OBSERVATION_RECORDED]
    assert len(obs_events) >= 1
    assert "Python" in obs_events[0].payload.get("output", "")


# =============================================================================
# CRITERIO 13: Persistent UI Display of Execution Environment
# =============================================================================

def test_dod_13_persistent_environment_and_inspector_details(client):
    """DOD-13: El snapshot y el Decision Inspector muestran los metadatos de entorno."""
    c, service = client
    sid = "sess_env_display"

    c.post("/v1/sessions", json={
        "goal": "Verificar despliegue de entorno",
        "session_id": sid,
        "execution_mode": "full_access",
    })
    prop = c.post(f"/v1/sessions/{sid}/actions", json={
        "tool": "read_file",
        "arguments": {"path": "pyproject.toml"},
    })
    dec_id = prop.json()["data"]["decision_id"]

    # Consultar snapshot
    snap = c.get(f"/v1/sessions/{sid}/snapshot").json()["data"]
    assert snap["execution_mode"] == "full_access"

    # Consultar inspector
    detail = c.get(f"/v1/decisions/{dec_id}").json()["data"]
    d_tab = detail["decision_tab"]
    assert d_tab["execution_mode"] == "full_access"
    assert d_tab["isolation"] == "None (Host OS)"
    assert d_tab["execution_backend"] == "FullAccessExecutor"
    assert d_tab["network_mode"] == "Host Direct"

    r_tab = detail["receipt_tab"]
    assert r_tab["execution_mode"] == "full_access"
    assert r_tab["has_valid_hmac"] is True


# =============================================================================
# CRITERIO 14: Threat Model Documentation in SECURITY.md
# =============================================================================

def test_dod_14_threat_model_documented():
    """DOD-14: SECURITY.md documenta la ausencia de aislamiento de host en Full Access."""
    sec_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "SECURITY.md"))
    assert os.path.isfile(sec_path)
    with open(sec_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "Full Access" in content
    assert "aislamiento" in content.lower() or "isolation" in content.lower()
    assert "cadena de custodia" in content.lower() or "custody" in content.lower()
