"""Misiones gobernadas por workflow: el grafo manda, los agentes trabajan dentro de cada nodo."""

import json
import threading
import time
from unittest.mock import patch

import pytest

from praxeon.agent_llm import BaseAgentLLM
from praxeon.agents.definition import AgentDefinition
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service
from praxeon.workflows.mission_driver import node_mission, parse_structured_output


def finish(text: str) -> str:
    return f"Thought: concluyo mi nodo\nAction: finish({json.dumps({'summary': text})})"


class ScriptedLLM(BaseAgentLLM):
    """Emite turnos fijos y registra el prompt completo recibido en cada llamada."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    @property
    def provider_name(self):
        return "scripted"

    @property
    def model_name(self):
        return "scripted-1"

    def generate(self, messages, system_prompt=None):
        self.calls.append({"system": system_prompt or "", "messages": json.dumps(messages, ensure_ascii=False)})
        return self.turns.pop(0) if self.turns else finish("sin más turnos programados en el test")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("PRAXEON_SECRET_KEY", raising=False)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    for agent_id, name, role, prompt in (
        ("ag_dev_01", "Dev", "Developer", "Eres un desarrollador meticuloso."),
        ("ag_rev_01", "Rev", "Reviewer", "Eres un revisor estricto."),
    ):
        service.agent_registry.register(AgentDefinition(agent_id=agent_id, name=name, role=role, system_prompt=prompt))
    yield service
    set_runtime_service(None)


def _wait(predicate, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _approve_gate(runtime, exec_id, node_id="human_gate"):
    def worker():
        def waiting():
            ex = runtime.workflow_service.get_execution(exec_id)
            return ex is not None and ex.node_states.get(node_id) is not None and ex.node_states[node_id].value == "WAITING_APPROVAL"

        if _wait(waiting, 45):
            runtime.workflow_service.approve_execution_node(exec_id, node_id, approved=True, approver_id="test")

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t


def test_helpers():
    assert parse_structured_output('{"approved": false, "feedback": "x"}') == {"approved": False, "feedback": "x"}
    assert parse_structured_output('Veredicto:\n```json\n{"approved": true}\n```') == {"approved": True}
    assert parse_structured_output("texto libre") == {}


def test_inactive_or_unknown_workflow_is_rejected(runtime):
    runtime.workflow_editor_service.instantiate_template("code_review_loop")
    wid = runtime.workflow_editor_service.list_workflows()[-1]["workflow_id"]
    with pytest.raises(ValueError, match="no está activo"):
        runtime.start_mission(goal="x", workflow_id=wid)
    with pytest.raises(ValueError, match="no encontrado"):
        runtime.start_mission(goal="x", workflow_id="wf_inexistente")


def test_activation_flag_is_listed_and_persisted(runtime):
    wf = runtime.workflow_editor_service.instantiate_template("code_review_loop")
    listed = {w["workflow_id"]: w for w in runtime.workflow_editor_service.list_workflows()}
    assert listed[wf.workflow_id]["active"] is False
    runtime.workflow_editor_service.set_workflow_active(wf.workflow_id, True)
    assert {w["workflow_id"]: w for w in runtime.workflow_editor_service.list_workflows()}[wf.workflow_id]["active"] is True
    runtime.workflow_editor_service.set_workflow_active(wf.workflow_id, False)
    assert runtime.workflow_editor_service.get_workflow(wf.workflow_id).metadata["active"] is False


def test_dev_reviewer_loop_hands_over_information_and_respects_the_loop(runtime, tmp_path):
    wf = runtime.workflow_editor_service.instantiate_template("code_review_loop")
    runtime.workflow_editor_service.set_workflow_active(wf.workflow_id, True)

    llm = ScriptedLLM([
        finish("CODIGO_V1: def suma(a, b): return a + b  # sin tests"),                           # dev, iteración 1
        finish('{"approved": false, "feedback": "FALTAN_TESTS: añade pruebas unitarias"}'),       # reviewer, iteración 1
        finish("CODIGO_V2: def suma(a, b): return a + b  + tests unitarios incluidos"),          # dev, iteración 2
        finish('{"approved": true, "feedback": "todo correcto"}'),                                # reviewer, iteración 2
    ])
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(
            goal="Implementa una función suma con tests",
            session_id="wf-m1",
            llm_provider="scripted",
            workspace_root=str(tmp_path),
            workflow_id=wf.workflow_id,
            step_delay_ms=0,
        )
        sid = meta["session_id"]
        exec_id = f"wfx-{sid}"
        _approve_gate(runtime, exec_id)
        assert _wait(lambda: runtime._running_missions.get(sid, {}).get("status") is not None, 90), "la misión no terminó"

    # 1. El workflow terminó y todos los nodos reflejan el recorrido (la UI sigue viéndolo por nodos)
    execution = runtime.workflow_service.get_execution(exec_id)
    assert execution is not None and execution.status.value == "COMPLETED", execution and execution.error_message
    assert [n for n in execution.execution_history if n in ("dev", "reviewer")] == ["dev", "reviewer", "dev", "reviewer"]
    assert execution.node_states["human_gate"].value == "COMPLETED"

    # 2. Información completa y ordenada entre agentes, indicando su misión
    dev1, rev1, dev2, rev2 = llm.calls
    assert "Iteración de este nodo: 1" in dev1["system"] and "OBJETIVO GLOBAL" in dev1["system"]
    assert "Implementa una función suma con tests" in dev1["system"]
    assert "Eres un desarrollador meticuloso." in dev1["system"]            # identidad del agente
    assert "CODIGO_V1" in rev1["system"] and "TU MISIÓN EN ESTE NODO" in rev1["system"]  # el revisor recibe el código completo
    assert "Eres un revisor estricto." in rev1["system"]
    assert "CODIGO_V1" in dev2["system"] and "FALTAN_TESTS" in dev2["system"]  # el dev recibe su intento previo y el feedback
    assert "Iteración de este nodo: 2" in dev2["system"]
    assert "CODIGO_V2" in rev2["system"]

    # 3. La salida estructurada del revisor gobierna el bucle (approved lo leen las condiciones del grafo)
    assert execution.node_outputs["reviewer"]["approved"] is True
    assert "CODIGO_V2" in execution.node_outputs["dev"]["response"]

    # 4. La misión termina con la respuesta del último agente y queda enlazada a su ejecución
    summary = runtime.get_session_summary(sid)
    assert summary["status"] == "Completed"
    assert runtime._sessions_meta[sid]["metadata"]["workflow_execution_id"] == exec_id
    assert "approved" in runtime._sessions_meta[sid]["final_answer"]


def test_agent_tool_restrictions_are_enforced_inside_the_node(runtime, tmp_path):
    from praxeon.workflows.models import NodeType, WorkflowDefinition, WorkflowEdge, WorkflowNode

    runtime.agent_registry.register(
        AgentDefinition(agent_id="ag_ro", name="SoloLectura", role="Auditor", system_prompt="Solo lees.",
                        allowed_tools=["read_file"])
    )
    wf = WorkflowDefinition(
        workflow_id="wf_ro",
        name="Solo lectura",
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "audit": WorkflowNode(node_id="audit", name="Auditar", node_type=NodeType.AGENT, agent_id="ag_ro",
                                  metadata={"mission": "Audita el proyecto sin modificar nada", "max_steps": 4}),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
        },
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="audit"),
            WorkflowEdge(edge_id="e2", from_node="audit", to_node="end"),
        ],
        metadata={"active": True},
    )
    runtime.workflow_editor_service.register_workflow(wf)
    llm = ScriptedLLM([
        'Thought: borro\nAction: run_command({"command": "echo prohibido"})',
        finish("Auditoría completada sin tocar nada: todo en orden."),
    ])
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="Audita", session_id="wf-m2", llm_provider="scripted",
                                     workspace_root=str(tmp_path), workflow_id="wf_ro", step_delay_ms=0)
        sid = meta["session_id"]
        assert _wait(lambda: runtime._running_missions.get(sid, {}).get("status") is not None, 60)

    # La segunda llamada al LLM recibe el rechazo de la herramienta no permitida como observación
    assert "no está permitida para el agente" in llm.calls[1]["messages"]
    execution = runtime.workflow_service.get_execution(f"wfx-{sid}")
    assert execution.status.value == "COMPLETED"


def test_node_failure_stops_the_mission_without_inventing_an_answer(runtime, tmp_path):
    from praxeon.workflows.models import NodeType, WorkflowDefinition, WorkflowEdge, WorkflowNode

    wf = WorkflowDefinition(
        workflow_id="wf_fail",
        name="Falla",
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "work": WorkflowNode(node_id="work", name="Trabajar", node_type=NodeType.AGENT, agent_id="ag_dev_01",
                                 metadata={"mission": "Haz algo", "max_steps": 2}),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
        },
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="work"),
            WorkflowEdge(edge_id="e2", from_node="work", to_node="end"),
        ],
        metadata={"active": True},
    )
    runtime.workflow_editor_service.register_workflow(wf)
    # El agente nunca llama a finish: agota sus pasos
    llm = ScriptedLLM(['Thought: sigo\nAction: run_command({"command": "echo %d"})' % i for i in range(20)])
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="Trabaja", session_id="wf-m3", llm_provider="scripted",
                                     workspace_root=str(tmp_path), workflow_id="wf_fail", step_delay_ms=0)
        sid = meta["session_id"]
        assert _wait(lambda: runtime._running_missions.get(sid, {}).get("status") is not None, 60)

    execution = runtime.workflow_service.get_execution(f"wfx-{sid}")
    assert execution.status.value == "FAILED"
    assert execution.node_states["work"].value == "FAILED"
    assert runtime.get_session_summary(sid)["status"] == "Intervened"


def test_node_mission_resolution_order():
    from praxeon.workflows.models import NodeType, WorkflowNode

    n = WorkflowNode(node_id="n", name="Nombre", node_type=NodeType.AGENT, metadata={"mission": "M"}, inputs={"task": "T"})
    assert node_mission(n) == "M"
    assert node_mission(WorkflowNode(node_id="n", name="Nombre", node_type=NodeType.AGENT, inputs={"task": "T"})) == "T"
    assert node_mission(WorkflowNode(node_id="n", name="Nombre", node_type=NodeType.AGENT)) == "Nombre"
