"""Hash de estado incremental y contadores de sesión por SQL (auditoría P-03/P-04, AUD-20).

Los dos cambios son optimizaciones: el resultado tiene que ser idéntico al cálculo anterior. Aquí se compara contra él.
"""

import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.decision import DecisionStatus, PolicyDecision, compute_state_hash
from praxeon.domain.events import EventType, RuntimeEvent
from praxeon.domain.goal import Goal
from praxeon.domain.models import Evidence
from praxeon.runtime.event_bus import EventBus, EventStore
from praxeon.runtime.state import SessionState
from praxeon.server.dependencies import session_counters_from_events


# --- hash de estado ----------------------------------------------------------------------------------------------
def _state(steps=3, evidence=1):
    st = SessionState(session_id="s", goal=Goal(objective="o", success_criteria=["c"]), forbidden_tools={"b", "a"},
                      checkpoint_ids=["c1", "c2"], metadata={"k": 1})
    for i in range(steps):
        st.add_step(ActionCandidate(id=f"a{i}", description="d", tool_call=ToolCall(tool_name="t", arguments={"x": i})),
                    PolicyDecision(status=DecisionStatus.ALLOW, reason="r", action_id=f"a{i}"), observation="salida " * i)
    for i in range(evidence):
        st.add_evidence(Evidence(id=f"ev{i}", claim="c", source="read_file", content=f"contenido {i}"))
    return st


def test_the_live_state_and_its_snapshot_hash_the_same():
    st = _state()
    assert compute_state_hash(st) == compute_state_hash(st.to_snapshot()) == st.state_digest()
    snap = st.to_snapshot()
    snap["checkpoint_ids"] = ["c1"]
    assert st.state_digest(checkpoint_ids=["c1"]) == compute_state_hash(snap)
    restored = SessionState.from_snapshot(st.to_snapshot())
    assert compute_state_hash(restored) == compute_state_hash(st)


@pytest.mark.parametrize("change", [
    lambda st: st.add_step(ActionCandidate(id="z", description="d"), PolicyDecision(status=DecisionStatus.ALLOW, reason="r", action_id="z")),
    lambda st: st.add_evidence(Evidence(id="evn", claim="c", source="x", content="nueva", content_hash="h-nueva")),
    lambda st: st.forbid_tool("rm"),
    lambda st: st.checkpoint_ids.append("c3"),
    lambda st: st.metadata.update(k=2),
    lambda st: st.steps.pop(),
])
def test_any_change_of_state_changes_the_hash(change):
    st = _state()
    before = compute_state_hash(st)
    change(st)
    assert compute_state_hash(st) != before
    assert compute_state_hash(st) == compute_state_hash(st.to_snapshot())


def test_hashing_a_state_reuses_the_hash_of_steps_already_seen(monkeypatch):
    import praxeon.runtime.state as state_module

    st = _state(steps=50)
    compute_state_hash(st)
    calls = []
    real = state_module.canonical_digest
    monkeypatch.setattr(state_module, "canonical_digest", lambda v: calls.append(1) or real(v))
    st.add_step(ActionCandidate(id="nuevo", description="d"), PolicyDecision(status=DecisionStatus.ALLOW, reason="r", action_id="nuevo"))
    compute_state_hash(st)
    assert len(calls) == 1                            # solo el paso nuevo; los 50 anteriores, desde la caché


def test_the_executor_accepts_receipts_for_the_state_before_the_last_checkpoint():
    from praxeon.runtime.executor import SecureExecutor
    from praxeon.policy.engine import PolicyEngine

    engine = PolicyEngine(secret_key="k" * 32)
    st = _state()
    action = ActionCandidate(id="r", description="leer", tool_call=ToolCall(tool_name="read_file", arguments={"path": "README.md"}))
    decision, receipt = engine.evaluate_action(action, st, session_id="s")
    st.checkpoint_ids.append("auto")                  # el runtime hace un checkpoint automático antes de ejecutar
    executor = SecureExecutor(secret_key=engine.secret_key)
    executor.register_handler("read_file", lambda args: "contenido")      # no toca disco: solo interesa la verificación
    obs = executor.execute(action, st, receipt=receipt, decision=decision)   # PolicyViolation si el hash no casara
    assert obs.success, obs.output


# --- contadores de sesión ------------------------------------------------------------------------------------------
TYPES = [t.value for t in EventType]
NODE_IDS = [None, "", "n1", "n2", "n3", "root_s"]
STATUSES = ["ALLOW", "allow", "BLOCK", "Review", "REVIEW", "WAIT", None, 5, "_falta"]
ACTION_IDS = ["x", "y", 3, 3.5, True, None, "", "_falta"]


def _random_events(rng, session_id, n):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    out = []
    for seq in range(1, n + 1):
        etype = rng.choice(TYPES)
        payload = {}
        status, action_id = rng.choice(STATUSES), rng.choice(ACTION_IDS)
        if status != "_falta":
            payload["status"] = status
        if action_id != "_falta":
            payload["action_id"] = action_id
        out.append(RuntimeEvent(event_id=f"e-{session_id}-{seq}", session_id=session_id, sequence=seq, type=EventType(etype),
                                timestamp=t0 + timedelta(seconds=seq), node_id=rng.choice(NODE_IDS),
                                parent_id=rng.choice(NODE_IDS), payload=payload))
    return out


def _bulk_insert(db_path, events):
    """Como EventStore.append (misma serialización), pero en una transacción: miles de eventos en un instante."""
    conn = sqlite3.connect(db_path)
    with conn:
        conn.executemany(
            "INSERT INTO runtime_events (event_id, session_id, sequence, type, timestamp, node_id, parent_id, decision_id,"
            " payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(e.event_id, e.session_id, e.sequence, e.type.value, e.timestamp.isoformat(), e.node_id, e.parent_id,
              e.decision_id, json.dumps(e.payload, default=str)) for e in events])
    conn.close()


def test_sql_counters_match_counting_the_events(tmp_path):
    rng = random.Random(20)
    db_path = str(tmp_path / "events.db")
    bus = EventBus(store=EventStore(db_path=db_path))
    sessions = [f"s{k}" for k in range(400)]
    _bulk_insert(db_path, [ev for sid in sessions for ev in _random_events(rng, sid, rng.randint(0, 40))])
    for sid in sessions:
        expected = session_counters_from_events(bus.get_all_events(sid), sid)
        assert bus.session_counters(sid) == expected, sid


def test_sql_counters_survive_a_malformed_payload(tmp_path):
    store = EventStore(db_path=str(tmp_path / "events.db"))
    store.append(RuntimeEvent(event_id="e1", session_id="s", sequence=1, type=EventType.SESSION_STARTED, payload={}))
    conn = sqlite3.connect(str(tmp_path / "events.db"))
    conn.execute("INSERT INTO runtime_events VALUES ('e2','s',2,'policy.decided',?,NULL,NULL,NULL,'{no es json')",
                 (datetime.now(timezone.utc).isoformat(),))
    conn.commit()
    conn.close()
    counters = store.session_counters("s")
    assert counters["event_count"] == 2 and counters["total_decisions"] == 1 and counters["node_count"] == 1
    assert counters["allowed_count"] == 0


def test_the_session_summary_uses_the_sql_counters(tmp_path, monkeypatch):
    from praxeon.server.dependencies import RuntimeApplicationService

    service = RuntimeApplicationService(db_dir=str(tmp_path / "cache"))
    sid = service.create_session(goal="x", execution_mode="local_restricted")["session_id"]
    service.event_bus.emit(session_id=sid, event_type=EventType.POLICY_DECIDED, payload={"status": "ALLOW"})
    monkeypatch.setattr(service.event_bus, "get_all_events", lambda *_: pytest.fail("no debe cargar todos los eventos"))
    summary = service.get_session_summary(sid)
    assert summary["allowed_count"] == 1 and summary["total_decisions"] == 1 and summary["node_count"] == 1
    assert json.loads(json.dumps(summary, default=str))
