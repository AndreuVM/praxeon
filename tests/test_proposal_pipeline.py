"""Pipeline formal de propuestas como secuencia de pasos: cada paso por separado y su orden (auditoría A-01)."""

import threading
from types import SimpleNamespace as NS

import pytest

from praxeon.core.pipeline import Outcome
from praxeon.domain.decision import DecisionStatus
from praxeon.domain.events import EventType
from praxeon.domain.models import RiskLevel
from praxeon.server.dependencies import RuntimeApplicationService
from praxeon.server.runtime_service import proposal_pipeline as P
from praxeon.server.schemas.action import ProposeActionRequest


class Bus:
    def __init__(self):
        self.events = []

    def emit(self, **kw):
        self.events.append(kw)


def _step(tool, path=None, command=None, observation=None):
    args = {k: v for k, v in (("path", path), ("command", command)) if v is not None}
    return NS(action=NS(tool_call=NS(tool_name=tool, arguments=args)), observation=observation)


def _ctx(tool="read_file", arguments=None, steps=(), goal="analiza el código", session_metadata=None, base_dir=".", **kw):
    runtime = NS(event_bus=Bus(), _lock=threading.Lock(), _sessions_meta={}, _may_auto_confirm=lambda tool: True)
    proposal = ProposeActionRequest(action_id="a1", tool=tool, arguments=arguments or {})
    state = NS(steps=list(steps), goal=NS(objective=goal), metadata={})
    ctx = P.ProposalContext(runtime=runtime, session_id="s1", proposal=proposal, state=state, action_id="a1",
                            decision_id="d_000001", session_metadata=session_metadata or {}, base_dir=base_dir, **kw)
    return ctx


@pytest.fixture
def svc(tmp_path):
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("print('hi')\n", encoding="utf-8")
    service.create_session(goal="analiza archivos", session_id="s1", execution_mode="local_restricted", workspace_root=str(ws))
    return service


# ------------------------------------------------------------------------------------------------ orden y extremo a extremo


def test_proposal_steps_run_in_an_explicit_order():
    assert [step.name for step in P.PROPOSAL_STEPS] == [
        "load_session", "build_action", "emit_proposed", "classify_operation", "missing_resource", "premature_finish",
        "assess_evidence", "assess_risk", "evaluate_supervisor", "decide_policy", "resolve_status", "issue_capability",
        "record_decision", "publish_decision",
    ]


def test_the_events_follow_the_order_of_the_steps(svc):
    resp = svc.propose_action("s1", ProposeActionRequest(action_id="a1", tool="read_file", arguments={"path": "app.py"}))
    assert resp.status == "ALLOW" and resp.capability
    types = [e.type for e in svc.event_bus.get_all_events("s1") if e.decision_id == resp.decision_id]
    assert types == [
        EventType.ACTION_PROPOSED, EventType.OPERATION_CLASSIFIED, EventType.EVIDENCE_EVALUATED, EventType.RISK_ASSESSED,
        EventType.PROVIDER_EVALUATED, EventType.POLICY_DECIDED, EventType.CAPABILITY_ISSUED,
    ]


def test_a_blocked_proposal_is_pruned_and_recorded_in_the_session(svc):
    resp = svc.propose_action("s1", ProposeActionRequest(action_id="a1", tool="run_command", arguments={"command": "sudo ls"}))
    assert resp.status == "BLOCK" and resp.capability is None
    assert svc.event_bus.get_all_events("s1")[-1].type == EventType.DECISION_PRUNED
    assert [s.action.id for s in svc.state_store.load_state("s1").steps] == ["a1"]


def test_a_proposal_for_an_unknown_session_creates_it(tmp_path):
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    resp = service.propose_action("nueva", ProposeActionRequest(tool="list_dir", arguments={"path": "."}, context={"goal": "mira"}))
    assert service.state_store.load_state("nueva").goal.objective == "mira" and resp.session_id == "nueva"


# ------------------------------------------------------------------------------------------------ pasos


@pytest.mark.parametrize("tool, arguments, operation, expected", [
    ("run_command", {"command": "ls -la"}, "inofensivo", "ls -la"),          # el comando real prevalece sobre la etiqueta
    ("mcp__x__y", {"cmd": "go"}, "", "mcp__x__y go"),
    ("read_file", {"path": "a"}, "lee a", "read_file lee a"),
    ("read_file", {"path": "a"}, "read_file a", "read_file a"),
    ("list_dir", {}, "", "list_dir"),
])
def test_the_operation_text_comes_from_the_real_command(tool, arguments, operation, expected):
    proposal = ProposeActionRequest(tool=tool, arguments=arguments, operation=operation)
    assert P.ClassifyOperationStep.operation_text(proposal) == expected


def test_a_missing_file_is_only_flagged_when_the_agent_insists(tmp_path):
    first = _ctx(arguments={"path": "ghost.py"}, base_dir=str(tmp_path))
    P.MissingResourceStep().run(first)
    assert not first.is_missing_resource

    again = _ctx(arguments={"path": "ghost.py"}, base_dir=str(tmp_path), steps=[_step("read_file", path="ghost.py")])
    P.MissingResourceStep().run(again)
    assert again.is_missing_resource and again.missing_resource_name == "ghost.py"


def test_a_missing_script_is_detected_from_the_command(tmp_path):
    (tmp_path / "real.py").write_text("x")
    assert P.MissingResourceStep._missing_script("python ghost.py --flag", str(tmp_path)) == "ghost.py"
    assert P.MissingResourceStep._missing_script("python real.py", str(tmp_path)) == ""
    assert P.MissingResourceStep._missing_script("python -c 'print(1)'", str(tmp_path)) == ""


@pytest.mark.parametrize("summary, steps, metadata, premature", [
    ("", (), {}, True),
    ("pendiente de lectura de los módulos", (), {}, True),
    ("8123", (), {}, True),                                                   # respuesta corta sin evidencia
    ("8123", (_step("read_file", observation="x" * 40),), {}, False),         # corta pero con observaciones
    ("informe breve del proyecto", (_step("read_file", observation="x" * 40),), {}, False),
    ('{"approved": true}', (), {"workflow_id": "wf"}, False),                 # veredicto legítimo de un nodo
])
def test_premature_finish(summary, steps, metadata, premature):
    ctx = _ctx("finish", {"summary": summary}, steps=steps, session_metadata=metadata)
    P.PrematureFinishStep().run(ctx)
    assert ctx.is_premature_finish is premature


def test_premature_finish_ignores_other_tools():
    ctx = _ctx("read_file", {"path": "a"})
    P.PrematureFinishStep().run(ctx)
    assert not ctx.is_premature_finish


def test_ungrounded_signals_lower_the_grounding_and_add_their_reason():
    ctx = _ctx(is_missing_resource=True, missing_resource_name="ghost.py")
    assessment = NS(reason_codes=["X"], grounded_probability=0.9,
                    model_copy=lambda update: NS(**{"reason_codes": ["X"], "grounded_probability": 0.9, **update}))
    updated = P.EvaluateSupervisorStep._with_ungrounded_signals(ctx, assessment)
    assert updated.grounded_probability == P.UNGROUNDED_SCORE
    assert updated.reason_codes == ["X", "UNGROUNDED_FILE_NOT_FOUND (ghost.py)"]


@pytest.mark.parametrize("name, configured, expected", [
    ("MockProvider", "laya", "mock"), ("LayaAdapter", "typesafe", "LAYA"), ("typesafe", "laya", "TypeSafe"),
    ("kev", "laya", "LAYA"), ("kev", "typesafe", "TypeSafe"),
])
def test_supervisor_display_name(name, configured, expected):
    runtime = NS(config=NS(provider=NS(name=configured)))
    assert P.EvaluateSupervisorStep.display_name(NS(name=name), runtime) == expected


def _decided(ctx, status, requires_confirmation=False, risk=RiskLevel.LOW, mode="local_restricted", metadata=None):
    ctx.decision = NS(status=status, requires_confirmation=requires_confirmation, reason_codes=["R1"], risk=NS(level=risk))
    ctx.risk_assessment = NS(level=risk)
    ctx.session_mode = mode
    ctx.action = NS(tool_call=NS(tool_name=ctx.tool))
    ctx.state.metadata = metadata or {}
    P.ResolveStatusStep().run(ctx)
    return ctx


def test_block_and_critical_risk_never_execute():
    assert _decided(_ctx(), DecisionStatus.BLOCK).status == "BLOCK"
    ctx = _decided(_ctx(), DecisionStatus.ALLOW, risk=RiskLevel.CRITICAL, mode="full_access",
                   metadata={"full_access_authorized_by_operator": True})
    assert ctx.status == "BLOCK" and not ctx.requires_confirmation


def test_replan_does_not_ask_for_confirmation():
    ctx = _decided(_ctx(), DecisionStatus.REPLAN, requires_confirmation=True)
    assert ctx.status == "REPLAN" and not ctx.requires_confirmation


def test_confirmation_is_asked_unless_full_access_was_authorized():
    ctx = _decided(_ctx(), DecisionStatus.ABSTAIN, requires_confirmation=True)
    assert (ctx.status, ctx.policy_decision, ctx.requires_confirmation) == ("REVIEW", "REQUIRE_HUMAN_CONFIRMATION", True)

    ctx = _decided(_ctx(), DecisionStatus.ABSTAIN, requires_confirmation=True, mode="full_access",
                   metadata={"full_access_authorized_by_operator": True})
    assert ctx.status == "ALLOW" and ctx.policy_dto.reason_codes == ["R1", "FULL_ACCESS_OPERATOR_PREAUTHORIZED"]

    ctx = _decided(_ctx(), DecisionStatus.ABSTAIN, requires_confirmation=True, mode="full_access",
                   metadata={"full_access_authorized_by_operator": True, "require_step_confirmation": True})
    assert ctx.status == "REVIEW"


def test_resolve_status_emits_the_policy_node():
    ctx = _decided(_ctx(), DecisionStatus.ALLOW)
    event = ctx.runtime.event_bus.events[-1]
    assert event["event_type"] == EventType.POLICY_DECIDED and event["node_id"] == "a1_policy" and event["parent_id"] == "a1"


def test_every_step_lets_the_pipeline_continue(svc):
    ctx = P.ProposalContext(runtime=svc, session_id="s1", proposal=ProposeActionRequest(tool="list_dir", arguments={"path": "."}))
    assert [step.run(ctx) for step in P.PROPOSAL_STEPS] == [Outcome.CONTINUE] * len(P.PROPOSAL_STEPS)
    assert ctx.response().status == ctx.status


@pytest.mark.parametrize("tool, arguments, event_type", [
    ("write_file", {"path": "app.py", "content": "x"}, EventType.APPROVAL_REQUESTED),
    ("read_file", {"path": "app.py"}, EventType.CAPABILITY_ISSUED),
    ("run_command", {"command": "sudo ls"}, EventType.DECISION_PRUNED),
])
def test_a_decision_is_recorded_before_it_is_announced(svc, tool, arguments, event_type):
    """R-080: quien reacciona al evento (operador, cliente de la API) ya encuentra la decisión guardada."""
    seen = []

    def on_event(event):
        if event.type == event_type:
            seen.append((svc.decision_repository.get(event.decision_id) or {}).get("status"))
            if event_type == EventType.APPROVAL_REQUESTED:
                seen.append(svc.confirm_decision(decision_id=event.decision_id, approved=False, reason="no",
                                                 operator_id="op", role="operator").status)

    unsubscribe = svc.event_bus.subscribe(on_event, session_id="s1")
    try:
        resp = svc.propose_action("s1", ProposeActionRequest(action_id="a1", tool=tool, arguments=arguments))
    finally:
        unsubscribe()
    assert seen and seen[0] == resp.status, seen
    if event_type == EventType.APPROVAL_REQUESTED:
        assert resp.status == "REVIEW" and seen[1] == "BLOCKED"
