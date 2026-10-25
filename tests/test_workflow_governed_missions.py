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


def _failing_workflow(runtime):
    from praxeon.workflows.models import NodeType, WorkflowDefinition, WorkflowEdge, WorkflowNode

    wf = WorkflowDefinition(
        workflow_id="wf_fail",
        name="Falla",
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "work": WorkflowNode(node_id="work", name="Trabajar", node_type=NodeType.AGENT, agent_id="ag_dev_01",
                                 metadata={"mission": "Haz algo", "max_steps": 2}),
            "after": WorkflowNode(node_id="after", name="Cerrar", node_type=NodeType.AGENT, agent_id="ag_rev_01",
                                  metadata={"mission": "Resume lo hecho"}),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
        },
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="work"),
            WorkflowEdge(edge_id="e2", from_node="work", to_node="after"),
            WorkflowEdge(edge_id="e3", from_node="after", to_node="end"),
        ],
        metadata={"active": True},
    )
    runtime.workflow_editor_service.register_workflow(wf)


def _never_finishes(n=2):
    return ['Thought: sigo\nAction: run_command({"command": "echo %d"})' % i for i in range(n)]


def _wait_pending_failure(runtime, exec_id):
    def pending():
        ex = runtime.workflow_service.get_execution(exec_id)
        return bool(ex and ex.variables.get("_pending_failure"))

    return _wait(pending, 60)


def test_failed_node_asks_the_operator_and_abort_ends_without_inventing_an_answer(runtime, tmp_path):
    _failing_workflow(runtime)
    llm = ScriptedLLM(_never_finishes(20))  # el agente nunca llama a finish
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="Trabaja", session_id="wf-m3", llm_provider="scripted",
                                     workspace_root=str(tmp_path), workflow_id="wf_fail", step_delay_ms=0)
        sid, exec_id = meta["session_id"], "wfx-wf-m3"
        assert _wait_pending_failure(runtime, exec_id), "la misión debía quedar esperando la decisión del operador"
        # Mientras decide, la misión sigue viva (no se dio por terminada ni se inventó respuesta)
        assert runtime._running_missions[sid].get("status") is None
        assert runtime.workflow_service.resolve_failed_node(exec_id, "after", "abort") is False  # nodo equivocado
        assert runtime.workflow_service.resolve_failed_node(exec_id, "work", "abort") is True
        assert _wait(lambda: runtime._running_missions.get(sid, {}).get("status") is not None, 30)

    execution = runtime.workflow_service.get_execution(exec_id)
    assert execution.status.value == "FAILED" and execution.node_states["work"].value == "FAILED"
    assert runtime.get_session_summary(sid)["status"] == "Intervened"
    with pytest.raises(ValueError):
        runtime.workflow_service.resolve_failed_node(exec_id, "work", "explotar")


def test_failed_node_retry_runs_it_again_and_the_flow_continues(runtime, tmp_path):
    _failing_workflow(runtime)
    llm = ScriptedLLM(_never_finishes(2) + [finish("Trabajo hecho tras el reintento, con detalle suficiente."),
                                              finish("Cierre final del flujo.")])
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="Trabaja", session_id="wf-m4", llm_provider="scripted",
                                     workspace_root=str(tmp_path), workflow_id="wf_fail", step_delay_ms=0)
        sid, exec_id = meta["session_id"], "wfx-wf-m4"
        assert _wait_pending_failure(runtime, exec_id)
        assert runtime.workflow_service.resolve_failed_node(exec_id, "work", "retry") is True
        assert _wait(lambda: runtime._running_missions.get(sid, {}).get("status") is not None, 60)

    execution = runtime.workflow_service.get_execution(exec_id)
    assert execution.status.value == "COMPLETED"
    assert "Trabajo hecho tras el reintento" in execution.node_outputs["work"]["response"]
    assert runtime.get_session_summary(sid)["status"] == "Completed"


def test_failed_node_skip_continues_with_the_next_node(runtime, tmp_path):
    _failing_workflow(runtime)
    llm = ScriptedLLM(_never_finishes(2) + [finish("Cierre sin el nodo omitido, con detalle suficiente.")])
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="Trabaja", session_id="wf-m5", llm_provider="scripted",
                                     workspace_root=str(tmp_path), workflow_id="wf_fail", step_delay_ms=0)
        sid, exec_id = meta["session_id"], "wfx-wf-m5"
        assert _wait_pending_failure(runtime, exec_id)
        assert runtime.workflow_service.resolve_failed_node(exec_id, "work", "skip") is True
        assert _wait(lambda: runtime._running_missions.get(sid, {}).get("status") is not None, 60)

    execution = runtime.workflow_service.get_execution(exec_id)
    assert execution.status.value == "COMPLETED"
    assert execution.node_outputs["work"]["skipped_by_operator"] is True
    assert "Cierre sin el nodo omitido" in execution.node_outputs["after"]["response"]
    # el nodo siguiente sabe que el anterior fue omitido (lo recibe como información, no como silencio)
    assert "omitido por el operador" in llm.calls[-1]["system"].lower()


def test_node_mission_resolution_order():
    from praxeon.workflows.models import NodeType, WorkflowNode

    n = WorkflowNode(node_id="n", name="Nombre", node_type=NodeType.AGENT, metadata={"mission": "M"}, inputs={"task": "T"})
    assert node_mission(n) == "M"
    assert node_mission(WorkflowNode(node_id="n", name="Nombre", node_type=NodeType.AGENT, inputs={"task": "T"})) == "T"
    assert node_mission(WorkflowNode(node_id="n", name="Nombre", node_type=NodeType.AGENT)) == "Nombre"


def test_friendly_node_form_payload_is_stored_and_drives_the_node(runtime):
    """Lo que envía el formulario (misión, resultado esperado, pasos) llega al nodo y lo lee el driver."""
    wf = runtime.workflow_editor_service.instantiate_template("code_review_loop")
    runtime.workflow_editor_service.update_node(
        wf.workflow_id, "dev",
        agent_id="ag_dev_01", tool_name="",
        inputs={"lang": "py"},
        metadata={"mission": "Implementa la función suma", "expected_output": "código completo", "max_steps": 6},
    )
    node = runtime.workflow_editor_service.get_workflow(wf.workflow_id).nodes["dev"]
    assert node_mission(node) == "Implementa la función suma"
    assert node.metadata["max_steps"] == 6 and node.inputs == {"lang": "py"} and node.tool_name is None


def test_actions_carry_their_agent_and_each_node_hangs_from_the_previous_one(runtime, tmp_path):
    """El árbol de decisiones debe poder mostrar quién tiene el control y el traspaso entre agentes."""
    wf = runtime.workflow_editor_service.instantiate_template("code_review_loop")
    runtime.workflow_editor_service.set_workflow_active(wf.workflow_id, True)
    llm = ScriptedLLM([
        finish("CODIGO_V1"),
        finish('{"approved": true, "feedback": "ok"}'),
    ])
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="Haz algo", session_id="wf-m3", llm_provider="scripted",
                                     workspace_root=str(tmp_path), workflow_id=wf.workflow_id, step_delay_ms=0)
        sid = meta["session_id"]
        _approve_gate(runtime, f"wfx-{sid}")
        assert _wait(lambda: runtime._running_missions.get(sid, {}).get("status") is not None, 90)

    proposed = [e for e in runtime.event_bus.get_events(sid, limit=2000) if e.type.value == "action.proposed"]
    by_node = {e.node_id: e for e in proposed}
    dev, rev = by_node["dev_i1_act_1"], by_node["reviewer_i1_act_1"]

    assert dev.payload["workflow_node"]["node_id"] == "dev" and dev.payload["workflow_node"]["agent_name"]
    assert rev.payload["workflow_node"]["node_id"] == "reviewer"
    assert dev.payload["workflow_node"]["agent_name"] != rev.payload["workflow_node"]["agent_name"]
    assert rev.payload["workflow_node"]["handoff_from"] == dev.payload["workflow_node"]["agent_name"]
    # el primer paso del revisor cuelga de la última acción del desarrollador (el traspaso se ve en el árbol)
    assert rev.parent_id == "dev_i1_act_1"
    assert dev.parent_id.startswith("root_")


def test_node_finish_is_a_milestone_and_only_the_end_of_the_workflow_completes_the_mission(runtime, tmp_path):
    """El finish de un agente cierra su nodo, no la misión: la respuesta final es la del final del grafo."""
    wf = runtime.workflow_editor_service.instantiate_template("code_review_loop")
    runtime.workflow_editor_service.set_workflow_active(wf.workflow_id, True)
    llm = ScriptedLLM([
        finish("RESULTADO_INTERMEDIO_DEV"),
        finish('{"approved": true, "feedback": "RESULTADO_FINAL_REVISOR"}'),
    ])
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="Haz algo", session_id="wf-m4", llm_provider="scripted",
                                     workspace_root=str(tmp_path), workflow_id=wf.workflow_id, step_delay_ms=0)
        sid = meta["session_id"]
        _approve_gate(runtime, f"wfx-{sid}")
        assert _wait(lambda: runtime._running_missions.get(sid, {}).get("status") is not None, 90)

    assert _wait(lambda: any(e.type.value == "session.completed" for e in runtime.event_bus.get_events(sid, limit=2000)), 30)
    events = runtime.event_bus.get_events(sid, limit=2000)
    completed = [e for e in events if e.type.value == "session.completed"]
    milestones = [e for e in events if e.type.value == "workflow.node_completed"]

    assert [m.payload["node_id"] for m in milestones] == ["dev", "reviewer"]
    assert milestones[0].payload["summary"] == "RESULTADO_INTERMEDIO_DEV"
    assert milestones[0].payload["agent_name"] and milestones[0].payload["iteration"] == 1
    # una sola finalización de la misión, y es la última: el resultado de un nodo intermedio no la provoca
    assert len(completed) == 1
    assert "RESULTADO_INTERMEDIO_DEV" not in completed[0].payload["summary"]
    assert events.index(completed[0]) > events.index(milestones[-1])


def test_node_start_is_announced_before_its_first_action(runtime, tmp_path):
    """El traspaso se ve al instante: el nodo anuncia su inicio antes de que el modelo responda."""
    wf = runtime.workflow_editor_service.instantiate_template("code_review_loop")
    runtime.workflow_editor_service.set_workflow_active(wf.workflow_id, True)
    llm = ScriptedLLM([finish("CODIGO"), finish('{"approved": true}')])
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="Haz algo", session_id="wf-m5", llm_provider="scripted",
                                     workspace_root=str(tmp_path), workflow_id=wf.workflow_id, step_delay_ms=0)
        sid = meta["session_id"]
        _approve_gate(runtime, f"wfx-{sid}")
        assert _wait(lambda: any(e.type.value == "session.completed" for e in runtime.event_bus.get_events(sid, limit=2000)), 90)

    events = runtime.event_bus.get_events(sid, limit=2000)
    order = [(e.type.value, e.node_id) for e in events if e.type.value.startswith("workflow.") or (e.type.value == "action.proposed")]
    assert order.index(("workflow.node_started", "dev")) < order.index(("action.proposed", "dev_i1_act_1"))
    assert order.index(("workflow.node_started", "reviewer")) < order.index(("action.proposed", "reviewer_i1_act_1"))
    started = {e.node_id: e.payload for e in events if e.type.value == "workflow.node_started"}
    assert started["reviewer"]["handoff_from"] == started["dev"]["agent_name"]
    assert started["reviewer"]["parent_id"] == "dev_i1_act_1"


def _single_agent_workflow(runtime, agent_id="ag_dev_01", mission="Implementa el módulo pedido"):
    from praxeon.workflows.models import NodeType, WorkflowDefinition, WorkflowEdge, WorkflowNode

    wf = WorkflowDefinition(
        workflow_id="wf_dev_only",
        name="Solo dev",
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "dev": WorkflowNode(node_id="dev", name="Dev", node_type=NodeType.AGENT, agent_id=agent_id,
                                metadata={"mission": mission, "max_steps": 8}),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
        },
        edges=[WorkflowEdge(edge_id="e1", from_node="start", to_node="dev"),
               WorkflowEdge(edge_id="e2", from_node="dev", to_node="end")],
        metadata={"active": True},
    )
    runtime.workflow_editor_service.register_workflow(wf)
    return wf


def _run_full_access(runtime, tmp_path, llm, sid):
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="Crea el módulo", session_id=sid, llm_provider="scripted",
                                     workspace_root=str(tmp_path), workflow_id="wf_dev_only", step_delay_ms=0,
                                     execution_mode="full_access", metadata={"full_access_authorized_by_operator": True}, autonomous=True,
                                     allow_unattended_execution=True)
        assert _wait(lambda: any(e.type.value == "session.completed" for e in runtime.event_bus.get_events(meta["session_id"], limit=2000)), 240)
    return meta["session_id"]


def test_developer_node_can_list_write_and_verify_files_before_finishing(runtime, tmp_path):
    _single_agent_workflow(runtime)
    code = "def suma(a, b):\n    return a + b\n"
    llm = ScriptedLLM([
        'Thought: veo qué hay\nAction: list_directory({"path": "."})',
        'Thought: creo el módulo\nAction: write_file(' + json.dumps({"path": "app/calc.py", "text": code}) + ')',
        'Thought: compruebo\nAction: list_dir({"path": "app"})',
        finish("Creado app/calc.py con la función suma(a, b)."),
    ])
    sid = _run_full_access(runtime, tmp_path, llm, "wf-m6")

    assert (tmp_path / "app" / "calc.py").read_text(encoding="utf-8") == code     # se escribió de verdad, con su contenido
    obs = [e.payload["output"] for e in runtime.event_bus.get_events(sid, limit=2000) if e.type.value == "observation.recorded"]
    assert any("app/" in o and "NO EXISTE" not in o for o in obs)                 # list_dir (alias) funcionó
    assert any("calc.py" in o for o in obs)                                       # y vio el archivo creado
    # el prompt del nodo describe las herramientas reales y no el de analista
    assert "write_file(path, content)" in llm.calls[0]["system"] and "list_dir(path)" in llm.calls[0]["system"]
    assert "ANTI-SESGO" not in llm.calls[0]["system"]


def test_finish_that_only_announces_intentions_does_not_close_the_node(runtime, tmp_path):
    _single_agent_workflow(runtime)
    llm = ScriptedLLM([
        "Thought: x\nAction: finish(\"Ahora inspeccionaré la estructura de directorios para entender el código existente.\")",
        'Thought: trabajo\nAction: write_file({"path": "hecho.txt", "content": "ok"})',
        finish("Creado hecho.txt con el resultado."),
    ])
    _run_full_access(runtime, tmp_path, llm, "wf-m7")

    assert "FINISH PREMATURO" in llm.calls[1]["messages"]                          # el agente recibe el motivo
    assert (tmp_path / "hecho.txt").exists()
    execution = runtime.workflow_service.get_execution("wfx-wf-m7")
    assert execution.node_outputs["dev"]["response"] == "Creado hecho.txt con el resultado."


def test_reply_without_action_is_not_turned_into_the_nodes_result(runtime, tmp_path):
    _single_agent_workflow(runtime)
    llm = ScriptedLLM([
        "Voy a empezar revisando el directorio y después escribiré el código.",   # prosa, sin Thought/Action
        finish("Resultado real tras reintentar con el formato correcto."),
    ])
    _run_full_access(runtime, tmp_path, llm, "wf-m8")

    assert "FORMATO" in llm.calls[1]["messages"]
    execution = runtime.workflow_service.get_execution("wfx-wf-m8")
    assert execution.node_outputs["dev"]["response"] == "Resultado real tras reintentar con el formato correcto."


def test_node_interrupted_by_the_supervisor_is_a_failure_not_a_success(runtime, tmp_path):
    _single_agent_workflow(runtime)
    bad = 'Thought: x\nAction: read_file({"path": "no_existe_%d.json"})'
    llm = ScriptedLLM([bad % i for i in range(8)])
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="Crea el módulo", session_id="wf-m9", llm_provider="scripted",
                                     workspace_root=str(tmp_path), workflow_id="wf_dev_only", step_delay_ms=0,
                                     execution_mode="full_access", metadata={"full_access_authorized_by_operator": True})
        sid = meta["session_id"]

        def decide():
            ex = runtime.workflow_service.get_execution(f"wfx-{sid}")
            return ex is not None and (ex.status.value in ("FAILED", "COMPLETED") or ex.node_states.get("dev") and ex.node_states["dev"].value in ("FAILED", "WAITING_APPROVAL"))

        assert _wait(decide, 90)
    events = runtime.event_bus.get_events(sid, limit=3000)
    completed_nodes = [e for e in events if e.type.value == "workflow.node_completed"]
    assert not completed_nodes, "un nodo interrumpido por el supervisor no debe figurar como completado"


def _autonomous(runtime, tmp_path, llm, sid):
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="Trabaja", session_id=sid, llm_provider="scripted",
                                     workspace_root=str(tmp_path), workflow_id="wf_fail", step_delay_ms=0,
                                     execution_mode="full_access", metadata={"full_access_authorized_by_operator": True},
                                     autonomous=True, allow_unattended_execution=True)
    return meta["session_id"], f"wfx-{meta['session_id']}"


def test_autonomous_mission_retries_a_failed_node_by_itself_before_asking_a_human(runtime, tmp_path):
    _failing_workflow(runtime)
    llm = ScriptedLLM(_never_finishes(4) + [finish("Trabajo terminado en el segundo reintento automático."),
                                            finish("Cierre final con detalle suficiente.")])
    sid, exec_id = _autonomous(runtime, tmp_path, llm, "wf-m10")
    assert _wait(lambda: any(e.type.value == "session.completed" for e in runtime.event_bus.get_events(sid, limit=3000)), 90)

    events = runtime.event_bus.get_events(sid, limit=3000)
    assert not [e for e in events if e.type.value == "approval.requested"], "no debía molestar al operador"
    execution = runtime.workflow_service.get_execution(exec_id)
    assert execution.status.value == "COMPLETED"
    assert "segundo reintento" in execution.node_outputs["work"]["response"]
    # el reintento sabe por qué se interrumpió el intento anterior
    assert "INTENTO ANTERIOR FUE INTERRUMPIDO" in llm.calls[4]["system"]
    started = [e.payload for e in events if e.type.value == "workflow.node_started" and e.node_id == "work"]
    assert [s["iteration"] for s in started] == [1, 2, 3] and started[1]["retry_reason"]


def test_autonomous_mission_asks_a_human_only_after_exhausting_its_automatic_retries(runtime, tmp_path):
    _failing_workflow(runtime)
    llm = ScriptedLLM(_never_finishes(30))
    sid, exec_id = _autonomous(runtime, tmp_path, llm, "wf-m11")
    assert _wait_pending_failure(runtime, exec_id), "agotados los reintentos debía pedir decisión"

    started = [e for e in runtime.event_bus.get_events(sid, limit=3000) if e.type.value == "workflow.node_started" and e.node_id == "work"]
    requests = [e for e in runtime.event_bus.get_events(sid, limit=3000) if e.type.value == "approval.requested"]
    assert len(started) == 3          # intento inicial + 2 reintentos automáticos
    assert len(requests) == 1         # y una sola petición al operador
    runtime.workflow_service.resolve_failed_node(exec_id, "work", "abort")
    assert _wait(lambda: runtime._running_missions.get(sid, {}).get("status") is not None, 30)


def test_writing_several_different_files_is_progress_not_a_loop():
    from praxeon.domain.action import ActionCandidate, ToolCall
    from praxeon.domain.decision import DecisionStatus, PolicyDecision
    from praxeon.domain.goal import Goal
    from praxeon.runtime.state import SessionState
    from praxeon.providers.laya import LayaProvider

    provider = LayaProvider(backend="simulated")
    state = SessionState(session_id="s-loop", goal=Goal(objective="Crear la aplicación", success_criteria=[]))
    for i in range(3):
        state.add_step(
            action=ActionCandidate(id=f"w{i}", description="Escribir archivo",
                                   tool_call=ToolCall(tool_name="edit_file", arguments={"path": f"src/f{i}.js", "content": f"v{i}"})),
            decision=PolicyDecision(status=DecisionStatus.ALLOW),
            observation="Archivo modificado satisfactoriamente",
        )

    def loop_prob(path, content):
        cand = ActionCandidate(id="n", description="Escribir archivo",
                               tool_call=ToolCall(tool_name="edit_file", arguments={"path": path, "content": content}))
        return provider.evaluate_action(state, cand).loop_probability

    assert loop_prob("src/f4.js", "nuevo") < 0.65             # archivo nuevo tras tres escrituras seguidas
    assert loop_prob("src/f1.js", "versión mejorada") < 0.65  # reescribir un archivo con contenido nuevo
    assert loop_prob("src/f1.js", "v1") < 0.65                # una repetición aislada es un reintento razonable
    state.add_step(
        action=ActionCandidate(id="w3", description="Escribir archivo",
                               tool_call=ToolCall(tool_name="edit_file", arguments={"path": "src/f1.js", "content": "v1"})),
        decision=PolicyDecision(status=DecisionStatus.ALLOW), observation="Archivo modificado satisfactoriamente",
    )
    assert loop_prob("src/f1.js", "v1") >= 0.65               # mismo archivo y mismo contenido por segunda vez: bucle


def _interventions(runtime, sid, name):
    return [e.payload for e in runtime.event_bus.get_events(sid, limit=3000)
            if e.type.value == "intervention.applied" and e.payload.get("intervention") == name]


def test_circuit_breaker_only_talks_about_missing_files_when_files_are_missing(runtime, tmp_path):
    _single_agent_workflow(runtime)
    (tmp_path / "real.txt").write_text("x")
    # Fallos que NO son de rutas: la herramienta no existe para el agente / comandos que fallan
    llm = ScriptedLLM(['Thought: x\nAction: run_command({"command": "exit 3"})'] * 8)
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="g", session_id="wf-m12", llm_provider="scripted",
                                     workspace_root=str(tmp_path), workflow_id="wf_dev_only", step_delay_ms=0,
                                     execution_mode="full_access", metadata={"full_access_authorized_by_operator": True})
        sid = meta["session_id"]
        assert _wait(lambda: bool(_interventions(runtime, sid, "CIRCUIT_BREAKER_GROUNDING_INJECTION")), 60)
    msg = _interventions(runtime, sid, "CIRCUIT_BREAKER_GROUNDING_INJECTION")[0]["message"]
    assert "no existen" not in msg and "alucinación" not in msg
    assert "fallos consecutivos" in msg


def test_circuit_breaker_lists_the_workspace_not_the_server_folder_when_paths_are_missing(runtime, tmp_path):
    _single_agent_workflow(runtime)
    (tmp_path / "solo_aqui.txt").write_text("x")
    llm = ScriptedLLM(['Thought: x\nAction: read_file({"path": "fantasma_%d.json"})' % i for i in range(8)])
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="g", session_id="wf-m13", llm_provider="scripted",
                                     workspace_root=str(tmp_path), workflow_id="wf_dev_only", step_delay_ms=0,
                                     execution_mode="full_access", metadata={"full_access_authorized_by_operator": True})
        sid = meta["session_id"]
        assert _wait(lambda: bool(_interventions(runtime, sid, "CIRCUIT_BREAKER_GROUNDING_INJECTION")), 60)
    payload = _interventions(runtime, sid, "CIRCUIT_BREAKER_GROUNDING_INJECTION")[0]
    assert "no existen" in payload["message"]
    assert "solo_aqui.txt" in payload["real_files"] and "praxeon" not in payload["real_files"]  # el workspace, no la carpeta del servidor


def test_zero_max_steps_means_unlimited_for_nodes_and_missions(runtime, tmp_path):
    """Un nodo con max_steps=0 no se corta por número de pasos: solo termina al hacer finish."""
    _single_agent_workflow(runtime)
    node = runtime.workflow_editor_service.get_workflow("wf_dev_only").nodes["dev"]
    runtime.workflow_editor_service.update_node("wf_dev_only", "dev", metadata={**node.metadata, "max_steps": 0})
    n = 40  # más pasos que el tope anterior (12) y que el valor por defecto
    llm = ScriptedLLM(
        ['Thought: sigo\nAction: write_file(%s)' % json.dumps({"path": f"f{i}.txt", "content": str(i)}) for i in range(n)]
        + [finish("Terminado tras muchos pasos.")]
    )
    _run_full_access(runtime, tmp_path, llm, "wf-m14")
    execution = runtime.workflow_service.get_execution("wfx-wf-m14")
    assert execution.status.value == "COMPLETED"
    assert execution.node_outputs["dev"]["response"] == "Terminado tras muchos pasos."
    assert execution.node_outputs["dev"]["steps"] == n + 1
    assert (tmp_path / f"f{n - 1}.txt").exists()


def test_run_api_accepts_zero_max_steps(runtime):
    from fastapi.testclient import TestClient

    from praxeon.server.app import create_app

    client = TestClient(create_app(profile="dev"))
    res = client.post("/v1/sessions/run", json={"goal": "g", "llm_provider": "simulator", "max_steps": 0, "step_delay_ms": 200, "session_id": "api-unl"})
    assert res.status_code == 201, res.text
    assert runtime._running_missions["api-unl"]["max_steps"] == 0
    assert client.post("/v1/sessions/run", json={"goal": "g", "max_steps": -1}).status_code == 422


def test_verdict_is_found_in_any_format_a_model_writes_it():
    bt = "`"
    verdict_first = "### INFORME\n**Veredicto:** " + bt + '{"approved": false}' + bt + "\n\nHallazgos: ver código:\n" + bt * 3 + 'json\n{"nombre": "ejemplo", "valor": 3}\n' + bt * 3
    assert parse_structured_output(verdict_first, prefer_keys={"approved"}) == {"approved": False}
    assert parse_structured_output('Resultado: {"approved": true, "feedback": "ok"} fin', prefer_keys={"approved"})["approved"] is True
    assert parse_structured_output("Veredicto final -> approved: false (faltan tests)", prefer_keys={"approved"}) == {"approved": False}
    assert parse_structured_output('{"a": 1}') == {"a": 1}                       # sin claves preferidas se comporta como antes
    assert parse_structured_output("solo texto, sin datos", prefer_keys={"approved"}) == {}


def test_required_fields_come_from_the_graphs_conditions(runtime):
    wf = runtime.workflow_editor_service.instantiate_template("code_review_loop")
    from praxeon.workflows.mission_driver import WorkflowMissionDriver

    driver = WorkflowMissionDriver(workflow=wf, mission={"session_id": "s"}, goal="g", agent_registry=runtime.agent_registry,
                                   workflow_service=runtime.workflow_service, run_agent_loop=lambda *a, **k: {}, llm_for_agent=lambda a: None)
    assert driver.required_output_keys("reviewer") == ["approved"]
    assert driver.required_output_keys("dev") == []
    briefing = driver.build_briefing(wf.nodes["reviewer"], {}, 1)
    assert "FORMATO OBLIGATORIO" in briefing and "approved" in briefing


def test_reviewer_verdict_in_prose_drives_the_loop_and_incomplete_results_are_rejected(runtime, tmp_path):
    wf = runtime.workflow_editor_service.instantiate_template("code_review_loop")
    runtime.workflow_editor_service.set_workflow_active(wf.workflow_id, True)
    bt = "`"
    llm = ScriptedLLM([
        finish("CODIGO_V1 con errores"),                                                          # dev, iteración 1
        finish("Informe sin veredicto: encontré varios bugs críticos en el código."),             # reviewer: SIN el campo → rechazado
        finish("### INFORME\n**Veredicto:** " + bt + '{"approved": false}' + bt + "\nBUG_CRITICO_1: falta validar"),  # reviewer: reenvía
        finish("CODIGO_V2 corregido"),                                                            # dev, iteración 2 (el bucle sigue)
        finish('{"approved": true, "feedback": "ahora sí"}'),                                     # reviewer, iteración 2
    ])
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="Implementa algo", session_id="wf-m15", llm_provider="scripted",
                                     workspace_root=str(tmp_path), workflow_id=wf.workflow_id, step_delay_ms=0)
        sid = meta["session_id"]
        _approve_gate(runtime, f"wfx-{sid}")
        assert _wait(lambda: any(e.type.value == "session.completed" for e in runtime.event_bus.get_events(sid, limit=3000)), 90)

    execution = runtime.workflow_service.get_execution(f"wfx-{sid}")
    assert execution.status.value == "COMPLETED"
    # el veredicto «false» del primer informe devolvió el control al desarrollador: no se aprobó como final
    assert [n for n in execution.execution_history if n in ("dev", "reviewer")] == ["dev", "reviewer", "dev", "reviewer"]
    # el revisor recibió el rechazo del resultado incompleto y reenvió el veredicto
    assert "RESULTADO INCOMPLETO" in llm.calls[2]["messages"]
    # el desarrollador de la 2.ª pasada recibe el informe del revisor
    assert "BUG_CRITICO_1" in llm.calls[3]["system"]
    assert execution.node_outputs["reviewer"]["approved"] is True


def test_node_that_runs_out_of_steps_while_writing_delivers_what_it_did_instead_of_failing(runtime, tmp_path):
    """Un Dev que agota el presupuesto trabajando (no atascado) entrega lo hecho y el flujo sigue; antes fallaba y repetía todo."""
    _single_agent_workflow(runtime)      # max_steps = 8
    llm = ScriptedLLM(
        ['Thought: sigo\nAction: write_file(%s)' % json.dumps({"path": f"g{i}.txt", "content": str(i)}) for i in range(12)]
    )
    _run_full_access(runtime, tmp_path, llm, "wf-budget")
    execution = runtime.workflow_service.get_execution("wfx-wf-budget")
    assert execution.status.value == "COMPLETED"
    response = execution.node_outputs["dev"]["response"]
    assert "límite de 8 pasos" in response and "g0.txt" in response and "NO está terminado" in response
    assert any("te quedan" in c["messages"] for c in llm.calls)            # se le avisó antes de agotarlo

