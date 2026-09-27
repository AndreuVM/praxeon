"""Tests para el Modelo de Eventos, EventBus, DecisionTree Reducer y Hardening Fase 1.

Verifica los requisitos y criterios de aceptación de PRAXEON 1.0 (DOD-01 a DOD-08):
- 15 tipos canónicos de evento.
- Persistencia SQLite WAL con secuencia monótona por sesión.
- Pub/Sub reactivo para WebSocket / SSE.
- Reducción determinista del Decision Tree y soporte de Replay idéntico.
- Operación atómica consume-once para prevención de replay attacks.
- CapabilityPayload formal y fail-closed si la decisión no es ALLOW.
- SandboxTier con detección explícita de fallback a proceso local.
- Integración de emisión de eventos en JEVProxyMiddleware.
"""

import asyncio
from datetime import datetime, timedelta
import os
import pytest
import sqlite3
import threading

from praxeon.domain.decision import (
    CapabilityPayload,
    DecisionReceipt,
    DecisionStatus,
    compute_receipt_signature,
)
from praxeon.domain.events import EventType, RuntimeEvent, make_event
from praxeon.domain.tree import (
    DecisionTree,
    NodeActor,
    NodeKind,
    NodeStatus,
    TreeNode,
)
from praxeon.interceptor.proxy_middleware import JEVProxyMiddleware
from praxeon.runtime.event_bus import EventBus, EventStore
from praxeon.runtime.nonce_store import InMemoryNonceStore, SqliteNonceStore
from praxeon.runtime.sandbox import (
    ContainerSandboxAdapter,
    ContainerSandboxConfig,
    DryRunSandbox,
    LocalProcessSandbox,
    SandboxTier,
)
from praxeon.runtime.tree_reducer import TreeReducer, reduce_events_to_tree


def test_fifteen_canonical_event_types():
    """DOD-02: Verificar que los 15 tipos de eventos de la especificación existen."""
    expected_types = {
        "session.started",
        "goal.created",
        "action.proposed",
        "evidence.evaluated",
        "risk.assessed",
        "provider.evaluated",
        "policy.decided",
        "capability.issued",
        "execution.started",
        "execution.completed",
        "observation.recorded",
        "approval.requested",
        "approval.completed",
        "decision.pruned",
        "session.completed",
    }
    actual_types = {e.value for e in EventType}
    assert expected_types.issubset(actual_types)
    assert EventType.INTERVENTION_APPLIED.value == "intervention.applied"


def test_runtime_event_schema_and_serialization():
    """Contrato de evento: verificar campos requeridos y serialización JSON."""
    ev = make_event(
        session_id="s-test123",
        sequence=1,
        event_type=EventType.POLICY_DECIDED,
        node_id="n-005-policy",
        parent_id="n-005",
        decision_id="d-005",
        payload={"status": "REVIEW", "reason_code": "HIGH_RISK_HUMAN_CONFIRMATION"},
    )
    d = ev.to_dict()
    assert d["session_id"] == "s-test123"
    assert d["sequence"] == 1
    assert d["type"] == "policy.decided"
    assert d["node_id"] == "n-005-policy"
    assert d["decision_id"] == "d-005"
    assert d["payload"]["status"] == "REVIEW"


def test_event_store_monotonic_sequence(tmp_path):
    """DOD-02: Garantía de secuencia estrictamente monótona por sesión en SQLite WAL."""
    db_file = str(tmp_path / "test_events.db")
    store = EventStore(db_path=db_file)

    s1 = "session_alpha"
    s2 = "session_beta"

    # Secuencia para sesión 1
    ev1 = make_event(s1, sequence=store.next_sequence(s1), event_type=EventType.SESSION_STARTED)
    store.append(ev1)
    ev2 = make_event(s1, sequence=store.next_sequence(s1), event_type=EventType.GOAL_CREATED)
    store.append(ev2)

    # Secuencia independiente para sesión 2
    ev_s2 = make_event(s2, sequence=store.next_sequence(s2), event_type=EventType.SESSION_STARTED)
    store.append(ev_s2)

    events_s1 = store.get_all_events(s1)
    assert len(events_s1) == 2
    assert events_s1[0].sequence == 1
    assert events_s1[1].sequence == 2

    events_s2 = store.get_all_events(s2)
    assert len(events_s2) == 1
    assert events_s2[0].sequence == 1


def test_event_store_unique_sequence_constraint(tmp_path):
    """Verifica que SQLite impida insertar secuencias duplicadas en la misma sesión."""
    db_file = str(tmp_path / "test_conflict.db")
    store = EventStore(db_path=db_file)

    ev1 = make_event("s_dup", sequence=1, event_type=EventType.SESSION_STARTED)
    store.append(ev1)

    ev_dup = make_event("s_dup", sequence=1, event_type=EventType.GOAL_CREATED)
    with pytest.raises(sqlite3.IntegrityError):
        store.append(ev_dup)


def test_event_bus_sync_and_async_dispatch(tmp_path):
    """DOD-04: Pub/Sub en memoria despacha a observers síncronos y colas asíncronas."""
    db_file = str(tmp_path / "test_bus.db")
    bus = EventBus(store=EventStore(db_path=db_file))

    received_sync = []
    unsub = bus.subscribe(lambda ev: received_sync.append(ev))

    queue: asyncio.Queue = asyncio.Queue()
    unreg = bus.register_async_queue(queue)

    ev = bus.emit(
        session_id="s_dispatch",
        event_type=EventType.ACTION_PROPOSED,
        payload={"tool": "read_file", "path": "README.md"},
    )

    assert len(received_sync) == 1
    assert received_sync[0].event_id == ev.event_id
    assert not queue.empty()
    assert queue.get_nowait().event_id == ev.event_id

    # Test desuscripción
    unsub()
    unreg()
    bus.emit(session_id="s_dispatch", event_type=EventType.SESSION_COMPLETED)
    assert len(received_sync) == 1
    assert queue.empty()


def test_deterministic_tree_reducer_and_replay():
    """DOD-05 y DOD-10: El DecisionTree se deriva de eventos y soporta Replay idéntico."""
    session_id = "s-replay-demo"
    events = [
        make_event(session_id, 1, EventType.SESSION_STARTED, node_id="root", payload={"label": "Start"}),
        make_event(session_id, 2, EventType.GOAL_CREATED, node_id="goal_1", parent_id="root", payload={"goal": "Fix authentication bug"}),
        make_event(session_id, 3, EventType.ACTION_PROPOSED, node_id="n-1", parent_id="root", payload={"tool": "read_file", "operation": "auth.py", "label": "1. Read file"}),
        make_event(session_id, 4, EventType.PROVIDER_EVALUATED, node_id="n-1", payload={"provider_name": "LAYA", "score": 0.91, "verdict": "ALLOW"}),
        make_event(session_id, 5, EventType.POLICY_DECIDED, node_id="n-1", payload={"status": "ALLOW", "reason_code": "APPROVED"}),
        make_event(session_id, 6, EventType.CAPABILITY_ISSUED, node_id="n-1", payload={"capability_id": "cap-001"}),
        make_event(session_id, 7, EventType.EXECUTION_STARTED, node_id="n-1"),
        make_event(session_id, 8, EventType.EXECUTION_COMPLETED, node_id="n-1", payload={"success": True}),
        make_event(session_id, 9, EventType.OBSERVATION_RECORDED, node_id="n-1", payload={"output": "def login(): pass"}),
        make_event(session_id, 10, EventType.ACTION_PROPOSED, node_id="n-2", parent_id="n-1", payload={"tool": "git", "operation": "push origin main", "label": "2. Propose changes"}),
        make_event(session_id, 11, EventType.POLICY_DECIDED, node_id="n-2-policy", parent_id="n-2", payload={"status": "REVIEW", "reason_code": "HIGH_RISK_HUMAN_CONFIRMATION"}),
    ]

    # Reducción inicial en vivo
    tree1 = reduce_events_to_tree(events, session_id=session_id)
    assert tree1.session_id == session_id
    assert "root" in tree1.nodes
    assert "n-1" in tree1.nodes
    assert "n-2" in tree1.nodes
    assert "n-2-policy" in tree1.nodes

    # Verificar estados
    assert tree1.nodes["n-1"].status == NodeStatus.SUCCESS
    assert tree1.nodes["n-2-policy"].status == NodeStatus.REVIEW
    assert tree1.nodes["n-2-policy"].kind == NodeKind.POLICY

    # Replay: reducir exactamente la misma lista de eventos en una segunda pasada
    tree_replay = reduce_events_to_tree(events, session_id=session_id)
    assert tree_replay.to_dict() == tree1.to_dict()


def test_atomic_consume_once_in_sqlite_nonce_store(tmp_path):
    """Hardening 8.1: Prevenir replay attacks y race conditions con consume atómico."""
    db_file = str(tmp_path / "nonces_atomic.db")
    store = SqliteNonceStore(db_path=db_file)

    decision_id = "d-atomic-1"
    nonce = "nonce-abc-123"

    # Primer consumo debe tener éxito
    assert store.consume(decision_id, nonce) is True

    # Segundo consumo inmediato debe ser denegado atómicamente
    assert store.consume(decision_id, nonce) is False

    # Verificación concurrente con múltiples hilos intentando consumir el mismo nonce
    results = []
    def try_consume():
        res = store.consume("d-concurrent", "nonce-multi")
        results.append(res)

    threads = [threading.Thread(target=try_consume) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactamente UN hilo debe haber obtenido True, los otros 9 deben ser False
    assert results.count(True) == 1
    assert results.count(False) == 9


def test_capability_payload_formal_contract():
    """Hardening 8.1: CapabilityPayload formal emitido solo en decisiones ALLOW."""
    receipt_allow = DecisionReceipt(
        decision_id="dec-100",
        session_id="sess-100",
        action_id="act-100",
        state_hash="hash_state",
        action_hash="hash_action",
        nonce="nonce-999",
        decision_status=DecisionStatus.ALLOW,
        signature="sig-hmac-123",
        expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    cap = receipt_allow.to_capability_payload(allowed_tools=["read_file"])
    assert isinstance(cap, CapabilityPayload)
    assert cap.decision_id == "dec-100"
    assert cap.allowed_tools == ["read_file"]
    assert not cap.is_expired()

    # Si la decisión es BLOCK o REVIEW, NO se emite capability (Fail closed)
    receipt_block = DecisionReceipt(
        decision_id="dec-200",
        session_id="sess-200",
        action_id="act-200",
        state_hash="hash_state",
        action_hash="hash_action",
        nonce="nonce-888",
        decision_status=DecisionStatus.BLOCK,
    )
    assert receipt_block.to_capability_payload() is None


def test_sandbox_tier_and_explicit_fallback():
    """Hardening 8.1: Sandbox informa explícitamente el nivel (tier) y si hubo fallback."""
    dry_run = DryRunSandbox()
    res_dry = dry_run.execute_command("echo test")
    assert res_dry.tier == SandboxTier.DRY_RUN
    assert res_dry.fallback_occurred is False

    local_sb = LocalProcessSandbox()
    res_local = local_sb.read_file("pyproject.toml")
    assert res_local.tier == SandboxTier.LOCAL_PROCESS
    assert res_local.fallback_occurred is False

    # Container adapter sin docker instalado debe reportar fallback explícito
    container_cfg = ContainerSandboxConfig(runtime_binary="non_existent_binary_xyz", fallback_to_local=True)
    container_sb = ContainerSandboxAdapter(config=container_cfg)
    res_fallback = container_sb.execute_command("echo hello from fallback")
    assert res_fallback.tier == SandboxTier.LOCAL_PROCESS
    assert res_fallback.fallback_occurred is True


def test_proxy_middleware_event_bus_integration(tmp_path):
    """Verifica que JEVProxyMiddleware emita eventos de sesión, propuesta y ejecución."""
    db_file = str(tmp_path / "test_middleware_events.db")
    bus = EventBus(store=EventStore(db_path=db_file))
    session_id = "s-middleware-test"

    middleware = JEVProxyMiddleware(
        goal="Verificar integración de eventos",
        event_bus=bus,
        session_id=session_id,
    )

    # Debe haber emitido session.started y goal.created
    initial_events = bus.get_all_events(session_id)
    assert len(initial_events) >= 2
    types = [e.type for e in initial_events]
    assert EventType.SESSION_STARTED in types
    assert EventType.GOAL_CREATED in types

    # Proponer un bloque de pasos válidos
    chunk_res = middleware.intercept_step_chunk([
        {"thought_rationale": "Leer el archivo de configuración", "tool_name": "read_file", "tool_args": {"path": "pyproject.toml"}},
    ])
    assert chunk_res.all_safe is True

    # Debe haberse emitido action.proposed, provider.evaluated, policy.decided, capability.issued
    events_after_chunk = bus.get_all_events(session_id)
    types_after = [e.type for e in events_after_chunk]
    assert EventType.ACTION_PROPOSED in types_after
    assert EventType.PROVIDER_EVALUATED in types_after
    assert EventType.POLICY_DECIDED in types_after
    assert EventType.CAPABILITY_ISSUED in types_after

    # Ejecutar la herramienta física
    obs = middleware.execute_tool("read_file", {"path": "pyproject.toml"}, thought_rationale="Leyendo archivo")
    assert obs.success is True

    # Debe haberse emitido execution.started, execution.completed y observation.recorded
    events_after_exec = bus.get_all_events(session_id)
    types_final = [e.type for e in events_after_exec]
    assert EventType.EXECUTION_STARTED in types_final
    assert EventType.EXECUTION_COMPLETED in types_final
    assert EventType.OBSERVATION_RECORDED in types_final

    # Reducir todos los eventos y comprobar que el árbol reconstruido es coherente
    tree = reduce_events_to_tree(events_after_exec, session_id=session_id)
    assert tree.node_count >= 3
    assert len(tree.edges) >= 2


def test_event_store_high_concurrency_stress(tmp_path):
    """Fase 1: Prueba de estrés de 300 a 400 eventos concurrentes con secuencias monótonas garantizadas."""
    import concurrent.futures

    db_file = str(tmp_path / "stress_events.db")
    store = EventStore(db_path=db_file)
    session_id = "s-stress-concurrent"
    session_parallel = "s-stress-parallel"

    total_events_main = 300
    total_events_parallel = 100

    def insert_event(sid: str, idx: int):
        return store.append_new(
            session_id=sid,
            type=EventType.ACTION_PROPOSED,
            node_id=f"act_{idx}",
            payload={"index": idx},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = []
        for i in range(total_events_main):
            futures.append(executor.submit(insert_event, session_id, i))
        for j in range(total_events_parallel):
            futures.append(executor.submit(insert_event, session_parallel, j))

        for f in concurrent.futures.as_completed(futures):
            ev = f.result()
            assert ev is not None
            assert ev.sequence > 0

    # Verificar sesión principal: exactamente 300 eventos con secuencias 1..300 sin colisiones ni huecos
    events_main = store.get_all_events(session_id)
    assert len(events_main) == total_events_main
    sequences_main = [e.sequence for e in events_main]
    assert sequences_main == list(range(1, total_events_main + 1))
    assert len(set(sequences_main)) == total_events_main

    # Verificar sesión paralela: exactamente 100 eventos con secuencias 1..100 sin colisiones ni huecos
    events_parallel = store.get_all_events(session_parallel)
    assert len(events_parallel) == total_events_parallel
    sequences_parallel = [e.sequence for e in events_parallel]
    assert sequences_parallel == list(range(1, total_events_parallel + 1))
    assert len(set(sequences_parallel)) == total_events_parallel
