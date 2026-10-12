"""Salidas reales de modelos: transcripciones inventadas, finish con código y nombres de agente en workflows."""

from unittest.mock import patch

from praxeon.agents.definition import AgentDefinition, RiskProfile
from praxeon.domain.assessment import RiskLevel
from praxeon.live_agent.llm_adapter import parse_llm_steps

TQ = '"""'


def test_invented_next_turn_after_a_closed_call_is_ignored():
    out = (
        'Thought: listo el comando\nAction: run_command({"command": "Get-ChildItem"})\n\n'
        "[USER]: Observación de run_command:\nDirectorio: C:\\algo\n\n[ASSISTANT]:\nThought: otro\nAction: finish(\"x\")"
    )
    steps = parse_llm_steps(out)
    assert [(s["tool_name"], s["tool_args"]) for s in steps] == [("run_command", {"command": "Get-ChildItem"})]


def test_closed_call_does_not_swallow_following_plain_lines():
    out = 'Thought: a\nAction: run_command(command="dir")\ntexto suelto del modelo\nThought: b\nAction: finish("ok")'
    steps = parse_llm_steps(out)
    assert [s["tool_name"] for s in steps] == ["run_command", "finish"]
    assert steps[0]["tool_args"] == {"command": "dir"}


def test_finish_with_code_containing_docstrings_keeps_the_whole_deliverable():
    code = f'class Evento(BaseModel):\n    {TQ}Un evento local.{TQ}\n    titulo: str = Field(..., description="Título")\n    precio: float = Field(0.0)\n'
    out = f"Thought: entrego el código\nAction: finish(summary={TQ}{code}{TQ})"
    (step,) = parse_llm_steps(out)
    summary = step["tool_args"]["summary"]
    assert step["tool_name"] == "finish"
    assert "class Evento(BaseModel)" in summary and "precio: float" in summary and "Un evento local." in summary


def test_finish_with_unbalanced_quotes_in_the_text_is_recovered():
    out = 'Thought: x\nAction: finish(summary="Dijo "hola" y siguió")'
    (step,) = parse_llm_steps(out)
    assert step["tool_args"]["summary"] == 'Dijo "hola" y siguió'


def test_risk_level_is_case_insensitive():
    assert RiskProfile(max_risk_level="HIGH").max_risk_level is RiskLevel.HIGH
    assert RiskProfile.model_validate({"max_risk_level": "Critical"}).max_risk_level is RiskLevel.CRITICAL


def test_agent_edit_through_the_api_accepts_uppercase_risk(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from praxeon.server.app import create_app
    from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service

    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    try:
        service.agent_registry.register(AgentDefinition(agent_id="ag_x", name="X", role="Dev", system_prompt="p"))
        client = TestClient(create_app(profile="dev"))
        res = client.patch("/v1/agents/ag_x", json={"risk_profile": {"max_risk_level": "HIGH", "require_human_confirmation": False}})
        assert res.status_code == 200, res.text
        assert res.json()["data"]["risk_profile"]["max_risk_level"] == "high"
    finally:
        set_runtime_service(None)


def test_workflow_node_saved_with_the_visible_agent_label_still_finds_its_agent(tmp_path):
    from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service
    from praxeon.workflows.mission_driver import WorkflowMissionDriver
    from praxeon.workflows.models import NodeType, WorkflowDefinition, WorkflowEdge, WorkflowNode

    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    try:
        service.agent_registry.register(
            AgentDefinition(agent_id="ag_dev_custom", name="Dev Personalizado", role="Developer", system_prompt="Programas.")
        )
        wf = WorkflowDefinition(
            workflow_id="wf_label",
            name="Etiqueta",
            nodes={
                "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
                "dev": WorkflowNode(node_id="dev", name="Dev", node_type=NodeType.AGENT, agent_id="Dev Personalizado \u2014 Developer"),
                "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
            },
            edges=[WorkflowEdge(edge_id="e1", from_node="start", to_node="dev"), WorkflowEdge(edge_id="e2", from_node="dev", to_node="end")],
        )
        driver = WorkflowMissionDriver(
            workflow=wf, mission={"session_id": "s"}, goal="g", agent_registry=service.agent_registry,
            workflow_service=service.workflow_service, run_agent_loop=lambda *a, **k: {}, llm_for_agent=lambda a: None,
        )
        assert driver._agent_for(wf.nodes["dev"], {}).agent_id == "ag_dev_custom"
        ghost = WorkflowNode(node_id="ghost", name="Fantasma", node_type=NodeType.AGENT, agent_id="no_existe")
        assert driver._agent_for(ghost, {}) is None
    finally:
        set_runtime_service(None)
