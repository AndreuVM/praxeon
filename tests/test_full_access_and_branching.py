"""Pruebas unitarias para Modo Full Access Automático y Bifurcaciones del Árbol de Decisiones."""

import pytest
from praxeon.domain.models import ActionCandidate, RiskLevel, ToolCall
from praxeon.reasoning.risk import RiskEngine
from praxeon.domain.events import EventType, RuntimeEvent
from praxeon.runtime.event_bus import EventBus
from praxeon.runtime.state_store import InMemoryStateStore
from praxeon.runtime.tree_reducer import TreeReducer, reduce_events_to_tree
from praxeon.server.dependencies import RuntimeApplicationService, generate_goal_tailored_steps
from praxeon.server.schemas.action import ProposeActionRequest


def test_full_access_auto_execution_policy():
    """Verifica que en modo Full Access, las acciones operacionales se autoricen en automático."""
    service = RuntimeApplicationService(
        state_store=InMemoryStateStore(),
        event_bus=EventBus(),
    )
    sid = "sess_full_access_interactive"
    service.create_session(
        goal="Tarea en modo full access interactivo",
        session_id=sid,
        execution_mode="full_access",
        metadata={"autonomous": False, "allow_unattended_execution": False},
    )

    # 1. Proponer un comando ordinario de baja criticidad
    req1 = ProposeActionRequest(
        tool="run_command",
        arguments={"command": "python --version"},
        thought_rationale="Comprobación de entorno",
    )
    resp1 = service.propose_action(session_id=sid, proposal=req1)
    assert resp1.status == "ALLOW"
    assert not resp1.policy.requires_confirmation
    assert resp1.capability is not None

    # 2. Proponer comando con side-effects (ej. git push) en modo FULL_ACCESS interactivo
    # Debe retener obligatoriamente confirmación humana (REVIEW)
    req2 = ProposeActionRequest(
        tool="run_command",
        arguments={"command": "git push origin main"},
        thought_rationale="Publicación de cambios",
    )
    resp2 = service.propose_action(session_id=sid, proposal=req2)
    assert resp2.status == "REVIEW"
    assert resp2.policy.requires_confirmation is True

    # 3. En modo FULL_ACCESS + AUTONOMOUS (con consentimiento previo explícito), autoriza en automático
    sid_auto = "sess_full_access_autonomous"
    service.create_session(
        goal="Tarea en modo full access autónomo explícito",
        session_id=sid_auto,
        execution_mode="full_access",
        metadata={
            "autonomous": True,
            "allow_unattended_execution": True,
            "full_access_authorized_by_operator": True,
        },
    )
    resp_auto = service.propose_action(session_id=sid_auto, proposal=req2)
    assert resp_auto.status == "ALLOW"
    assert not resp_auto.policy.requires_confirmation
    assert resp_auto.capability is not None

    # 4. Comprobar que comandos destructivos CRÍTICOS siguen siendo bloqueados incondicionalmente
    req_critical = ProposeActionRequest(
        tool="run_command",
        arguments={"command": "rm -rf /"},
        thought_rationale="Intento destructivo",
    )
    resp_critical = service.propose_action(session_id=sid_auto, proposal=req_critical)
    assert resp_critical.status == "BLOCK"
    assert resp_critical.risk.level == "CRITICAL"


def test_tree_branching_and_backtracking_reduction():
    """Verifica que el TreeReducer detecte bifurcaciones (edge_type='branch') y retrocesos."""
    sid = "sess_branch_test"
    reducer = TreeReducer(session_id=sid)

    # 1. Inicio de sesión
    reducer.apply_event(RuntimeEvent(
        event_id="e1", session_id=sid, sequence=1,
        type=EventType.SESSION_STARTED, node_id=f"root_{sid}",
        payload={"label": "Start"}
    ))

    # 2. Paso 1 (act_1)
    reducer.apply_event(RuntimeEvent(
        event_id="e2", session_id=sid, sequence=2,
        type=EventType.ACTION_PROPOSED, node_id="act_1", parent_id=f"root_{sid}",
        payload={"tool": "read_file", "operation": "Inspección", "action_id": "act_1"}
    ))
    reducer.apply_event(RuntimeEvent(
        event_id="e3", session_id=sid, sequence=3,
        type=EventType.EXECUTION_COMPLETED, node_id="act_1",
        payload={"success": True, "execution_time_ms": 10}
    ))

    # 3. Hipótesis 1 (act_2) - Falla o se poda
    reducer.apply_event(RuntimeEvent(
        event_id="e4", session_id=sid, sequence=4,
        type=EventType.ACTION_PROPOSED, node_id="act_2", parent_id="act_1",
        payload={"tool": "run_command", "operation": "Hipótesis 1", "action_id": "act_2"}
    ))
    reducer.apply_event(RuntimeEvent(
        event_id="e5", session_id=sid, sequence=5,
        type=EventType.EXECUTION_COMPLETED, node_id="act_2",
        payload={"success": False, "execution_time_ms": 15}
    ))

    # 4. Hipótesis 2 (act_3) - BIFURCACIÓN desde act_1
    reducer.apply_event(RuntimeEvent(
        event_id="e6", session_id=sid, sequence=6,
        type=EventType.ACTION_PROPOSED, node_id="act_3", parent_id="act_1",
        payload={"tool": "run_command", "operation": "Hipótesis 2 (Bifurcación)", "action_id": "act_3"}
    ))

    tree = reducer.tree
    # act_1 debe tener dos hijos: act_2 y act_3
    children = [edge.target_id for edge in tree.edges if edge.source_id == "act_1"]
    assert "act_2" in children
    assert "act_3" in children
    assert len(children) == 2

    # La arista hacia act_3 debe ser de tipo 'branch' (bifurcación)
    branch_edges = [edge for edge in tree.edges if edge.source_id == "act_1" and edge.target_id == "act_3"]
    assert len(branch_edges) == 1
    assert branch_edges[0].edge_type == "branch"


def test_goal_tailored_steps_exhibit_branching():
    """Comprueba que el planificador genere pasos con parent_id explícito y bifurcación."""
    steps_auth = generate_goal_tailored_steps("Fix authentication bug in the API", max_steps=6)
    assert len(steps_auth) >= 3

    # Paso 2 debe ser exploratorio y fallar/podarse
    assert steps_auth[1].get("simulate_failure") is True
    assert steps_auth[1].get("parent_id") == "act_1"

    # Paso 3 debe ser una bifurcación que conecta de nuevo a act_1
    assert steps_auth[2].get("parent_id") == "act_1"
    assert "Bifurcación" in steps_auth[2]["operation"] or "Bifurcación" in steps_auth[2]["thought"]


def test_intervention_applied_event_and_reducer_backtracking():
    """Verifica que INTERVENTION_APPLIED se emita y reduzca correctamente sin excepciones."""
    sid = "sess_intervention_test"
    bus = EventBus()
    reducer = TreeReducer(session_id=sid)

    # 1. Start session
    ev1 = bus.emit(
        session_id=sid,
        event_type=EventType.SESSION_STARTED,
        node_id=f"root_{sid}",
        payload={"label": "Start"},
    )
    reducer.apply_event(ev1)

    # 2. Step 1 (act_1)
    ev2 = bus.emit(
        session_id=sid,
        event_type=EventType.ACTION_PROPOSED,
        node_id="act_1",
        parent_id=f"root_{sid}",
        payload={"tool": "read_file", "label": "Paso 1"},
    )
    reducer.apply_event(ev2)

    # 3. Step 2 (act_2) - Falla
    ev3 = bus.emit(
        session_id=sid,
        event_type=EventType.ACTION_PROPOSED,
        node_id="act_2",
        parent_id="act_1",
        payload={"tool": "run_command", "label": "Paso 2 tentativo"},
    )
    reducer.apply_event(ev3)

    # 4. Intervención: retroceso al nodo act_1
    ev_intervene = bus.emit(
        session_id=sid,
        event_type=EventType.INTERVENTION_APPLIED,
        node_id="act_2",
        parent_id="act_1",
        payload={
            "intervention": "BACKTRACK_AND_BRANCH",
            "message": "Fallo en act_2. Retrocediendo a act_1.",
            "backtrack_to": "act_1",
            "failed_node": "act_2",
        },
    )
    assert ev_intervene.type == EventType.INTERVENTION_APPLIED
    reducer.apply_event(ev_intervene)

    # El cursor _last_step_node_id debe haber retrocedido a act_1
    assert reducer._last_step_node_id == "act_1"

    # 5. Nuevo paso sin parent_id explícito se conecta automáticamente al backtracked node (act_1)
    ev4 = bus.emit(
        session_id=sid,
        event_type=EventType.ACTION_PROPOSED,
        node_id="act_3",
        payload={"tool": "run_command", "label": "Paso 3 alternativo"},
    )
    reducer.apply_event(ev4)

    assert reducer.tree.nodes["act_3"].parent_id == "act_1"
    # Arista entre act_1 y act_3 es bifurcación
    branch_edges = [edge for edge in reducer.tree.edges if edge.source_id == "act_1" and edge.target_id == "act_3"]
    assert len(branch_edges) == 1
    assert branch_edges[0].edge_type == "branch"


def test_full_access_finish_execution_and_summary_recording():
    """Garantiza que 'finish' en modo Full Access se reconozca como respuesta y no se intente ejecutar en la shell del OS."""
    from praxeon.runtime.full_access import FullAccessExecutor

    executor = FullAccessExecutor()
    res = executor.execute_tool(
        tool_name="finish",
        arguments={"summary": "La última actualización fue el 27 de septiembre de 2026."},
    )
    assert res.success is True
    assert res.is_error is False
    assert res.exit_code == 0
    assert "La última actualización fue el 27 de septiembre de 2026." in res.output

    # Test end-to-end con RuntimeApplicationService en modo full_access
    service = RuntimeApplicationService(
        state_store=InMemoryStateStore(),
        event_bus=EventBus(),
    )
    sid = "sess_finish_test"
    service.create_session(
        goal="Dime la última actualización del proyecto",
        session_id=sid,
        execution_mode="full_access",
    )

    req = ProposeActionRequest(
        tool="finish",
        arguments={"summary": "fix(ollama): auto-start daemon, fallback loopback ipv4/ipv6"},
        thought_rationale="Conclusión de la misión",
    )
    resp = service.propose_action(session_id=sid, proposal=req)
    assert resp.status == "ALLOW"

    exec_res = service.execute_decision(resp.decision_id)
    assert exec_res.success is True
    assert "fix(ollama)" in exec_res.output
    assert service._sessions_meta[sid]["status"] == "Completed"
    assert "fix(ollama)" in service._sessions_meta[sid]["final_answer"]


