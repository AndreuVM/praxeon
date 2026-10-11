"""Notificaciones de aprobaciones pendientes de la aplicación de escritorio."""

import time

from fastapi.testclient import TestClient

from praxeon.desktop.notifications import (
    ApprovalNotifier,
    NullNotifier,
    RecordingNotifier,
    SystemNotifier,
    describe_event,
)
from praxeon.domain.events import EventType, make_event
from praxeon.runtime.event_bus import EventBus
from praxeon.server.app import create_app
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service


def _event(event_type, session="s1", node="n1", payload=None):
    return make_event(event_type=event_type, session_id=session, sequence=1, node_id=node, payload=payload or {})


def test_notifies_once_per_pending_approval_and_counts():
    rec, counts = RecordingNotifier(), []
    approvals = ApprovalNotifier(rec, on_pending_changed=counts.append)
    approvals.handle_event(_event(EventType.APPROVAL_REQUESTED, payload={"reasons": ["Comando de red"], "risk_level": "HIGH"}))
    approvals.handle_event(_event(EventType.APPROVAL_REQUESTED, payload={"reasons": ["Comando de red"]}))  # duplicado
    assert len(rec.messages) == 1
    assert "Comando de red" in rec.messages[0][1] and "HIGH" in rec.messages[0][1]
    assert approvals.pending_count == 1
    approvals.handle_event(_event(EventType.APPROVAL_COMPLETED))
    assert approvals.pending_count == 0
    assert counts == [1, 1, 0]


def test_distinct_approvals_notify_separately():
    rec = RecordingNotifier()
    approvals = ApprovalNotifier(rec)
    approvals.handle_event(_event(EventType.APPROVAL_REQUESTED, node="a"))
    approvals.handle_event(_event(EventType.APPROVAL_REQUESTED, node="b"))
    assert len(rec.messages) == 2 and approvals.pending_count == 2


def test_workflow_event_message():
    title, body = describe_event(_event(
        EventType.APPROVAL_REQUESTED,
        payload={"source": "workflow", "node_name": "Deploy", "prompt": "Aprobar despliegue"},
    ))
    assert "Workflow" in title and "Deploy" in body and "Aprobar despliegue" in body


def test_notifier_failure_never_propagates():
    class Boom:
        def notify(self, title, body):
            raise RuntimeError("fallo")

    ApprovalNotifier(Boom()).handle_event(_event(EventType.APPROVAL_REQUESTED))  # no debe lanzar


def test_unrelated_events_are_ignored():
    rec = RecordingNotifier()
    ApprovalNotifier(rec).handle_event(_event(EventType.ACTION_PROPOSED))
    assert rec.messages == []


def test_subscribes_to_event_bus():
    bus, rec = EventBus(), RecordingNotifier()
    approvals = ApprovalNotifier(rec)
    approvals.attach(bus)
    bus.emit(session_id="x", event_type=EventType.APPROVAL_REQUESTED, node_id="n")
    assert len(rec.messages) == 1
    approvals.detach()
    bus.emit(session_id="x", event_type=EventType.APPROVAL_REQUESTED, node_id="n2")
    assert len(rec.messages) == 1


def test_system_notifier_commands_escape_text():
    win = SystemNotifier("win32")._command("T'itulo", "cuerpo <&> 'x'")
    assert win[0] == "powershell" and "&lt;&amp;&gt;" in win[-1] and "''x''" in win[-1]
    mac = SystemNotifier("darwin")._command('a"b', 'c"d')
    assert mac[0] == "osascript" and '\\"' in mac[-1]
    assert isinstance(NullNotifier().notify("a", "b"), type(None))


def test_scheduled_workflow_waiting_for_approval_triggers_notification(tmp_path, monkeypatch):
    for key in ("PRAXEON_PROFILE", "PRAXEON_ENV", "PRAXEON_API_KEY", "PRAXEON_REQUIRE_AUTH"):
        monkeypatch.delenv(key, raising=False)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    rec = RecordingNotifier()
    approvals = ApprovalNotifier(rec)
    approvals.attach(service.event_bus)
    client = TestClient(create_app(profile="dev"))

    wid = client.post("/v1/workflows", json={"name": "aprobacion"}).json()["data"]["workflow_id"]
    nid = client.post(f"/v1/workflows/{wid}/nodes", json={"name": "Gate", "node_type": "HUMAN_APPROVAL"}).json()["data"]["node_id"]
    client.delete(f"/v1/workflows/{wid}/edges/e_start_end")
    client.post(f"/v1/workflows/{wid}/edges", json={"from_node": "start", "to_node": nid})
    client.post(f"/v1/workflows/{wid}/edges", json={"from_node": nid, "to_node": "end"})
    eid = client.post(f"/v1/workflows/{wid}/executions").json()["data"]["execution_id"]

    deadline = time.time() + 8
    while not rec.messages and time.time() < deadline:
        time.sleep(0.1)
    assert rec.messages, "no se recibió ninguna notificación"
    assert "Gate" in rec.messages[0][1]
    assert approvals.pending_count == 1

    client.post(f"/v1/workflows/executions/{eid}/nodes/{nid}/approve", json={"approved": True})
    deadline = time.time() + 8
    while approvals.pending_count and time.time() < deadline:
        time.sleep(0.1)
    assert approvals.pending_count == 0
    approvals.detach()
