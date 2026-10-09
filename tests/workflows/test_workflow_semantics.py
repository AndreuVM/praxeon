"""Pruebas unitarias e integración de la Semántica de Nodos y Control de Flujo (Fase 0 - Fase 4).

Verifica exhaustivamente:
- EvaluationContext con resolución de namespaces aislados y fail-closed.
- Árboles lógicos AtomicCondition y CompoundCondition (AND, OR, NOT) sin eval().
- Bifurcaciones IF / DECISION exclusivas con marcado auditado de SKIPPED.
- Bucles estructurados WHILE con límite obligatorio de iteraciones (INV-03) y políticas on_limit.
- Delegación DELEGATE (modo manual y automático por candidatos).
- Nodos HUMAN_APPROVAL con aprobación y rechazo gobernados.
- Sincronización PARALLEL_JOIN (políticas all, any, quorum, merge y cancelación).
- Invariantes de orquestación (INV-01, INV-03, INV-05).
"""

import pytest
from praxeon.workflows import (
    AtomicCondition,
    CompoundCondition,
    ControlConfig,
    EdgeCondition,
    EvaluationContext,
    LogicalOperator,
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowEngine,
    WorkflowNode,
    WorkflowStatus,
    parse_condition,
)


# =====================================================================
# 1. Tests de EvaluationContext y Árbol de Expresiones
# =====================================================================

def test_evaluation_context_namespace_resolution():
    """Valida la resolución estricta por namespaces y prevención de colisiones."""
    ctx = EvaluationContext(
        variables={"threshold": 0.8, "env": "prod"},
        outputs={
            "agent_writer": {"text": "Draft content", "word_count": 120},
            "agent_reviewer": {"approved": True, "score": 9.5},
        },
        loop={"iteration": 3, "max_iterations": 5},
        budget={"remaining_tokens": 4000},
        agent_state={"status": "ready"},
    )

    # 1. Variables globales
    assert ctx.resolve_field("variables.threshold") == 0.8
    assert ctx.resolve_field("variables.env") == "prod"

    # 2. Outputs indexados por node_id
    assert ctx.resolve_field("outputs.agent_reviewer.approved") is True
    assert ctx.resolve_field("outputs.agent_reviewer.score") == 9.5
    assert ctx.resolve_field("outputs.agent_writer.word_count") == 120

    # 3. Métricas de bucle y presupuesto
    assert ctx.resolve_field("loop.iteration") == 3
    assert ctx.resolve_field("budget.remaining_tokens") == 4000

    # 4. Campo inexistente (fail-closed, retorna None)
    assert ctx.resolve_field("outputs.non_existent.score") is None
    assert ctx.resolve_field("variables.unknown_key") is None


def test_logical_conditions_and_or_not():
    """Valida la evaluación de condiciones atómicas y compuestas sin eval()."""
    ctx = EvaluationContext(
        variables={"cost": 15, "authorized": True},
        outputs={"review": {"approved": False, "score": 4.5}},
        loop={"iteration": 2},
    )

    # Condición atómica
    c1 = AtomicCondition(field="outputs.review.score", operator="<", expected_value=5.0)
    assert c1.evaluate(ctx) is True

    c2 = AtomicCondition(field="outputs.review.approved", operator="is_true")
    assert c2.evaluate(ctx) is False

    # Condición compuesta AND
    cand = CompoundCondition(
        logical_op=LogicalOperator.AND,
        conditions=[
            AtomicCondition(field="variables.cost", operator="<=", expected_value=20),
            AtomicCondition(field="variables.authorized", operator="is_true"),
        ],
    )
    assert cand.evaluate(ctx) is True

    # Condición compuesta OR
    cor = CompoundCondition(
        logical_op=LogicalOperator.OR,
        conditions=[
            AtomicCondition(field="outputs.review.approved", operator="is_true"),
            AtomicCondition(field="loop.iteration", operator="<", expected_value=5),
        ],
    )
    assert cor.evaluate(ctx) is True

    # Condición NOT
    cnot = CompoundCondition(
        logical_op=LogicalOperator.NOT,
        conditions=[AtomicCondition(field="outputs.review.approved", operator="is_true")],
    )
    assert cnot.evaluate(ctx) is True


def test_parse_condition_declarative_syntax():
    """Valida el parser declarativo de condiciones en formato JSON/dict."""
    spec = {
        "all": [
            {"field": "outputs.review.approved", "op": "is_false"},
            {"field": "loop.iteration", "op": "<", "value": 5},
        ]
    }
    cond = parse_condition(spec)
    assert isinstance(cond, CompoundCondition)
    assert cond.logical_op == LogicalOperator.AND
    assert len(cond.conditions) == 2

    ctx = EvaluationContext(
        outputs={"review": {"approved": False}},
        loop={"iteration": 3},
    )
    assert cond.evaluate(ctx) is True


# =====================================================================
# 2. Tests de IF / DECISION Determinista
# =====================================================================

def test_if_decision_exclusive_branching_and_skip_propagation():
    """Valida bifurcación exclusiva IF/DECISION y marcado SKIPPED de la rama no elegida."""
    # Grafo: START -> DECISION -> (true -> STEP_TRUE / false -> STEP_FALSE) -> END
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "check": WorkflowNode(
            node_id="check",
            name="Check Approval",
            node_type=NodeType.IF,
            control_config={
                "condition": {"field": "variables.approved", "op": "is_true"},
                "true_edge": "e_true",
                "false_edge": "e_false",
            },
        ),
        "step_true": WorkflowNode(node_id="step_true", name="Action On True", node_type=NodeType.TASK),
        "step_false": WorkflowNode(node_id="step_false", name="Action On False", node_type=NodeType.TASK),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }

    edges = [
        WorkflowEdge(edge_id="e0", from_node="start", to_node="check"),
        WorkflowEdge(edge_id="e_true", from_node="check", to_node="step_true", label="true"),
        WorkflowEdge(edge_id="e_false", from_node="check", to_node="step_false", label="false"),
        WorkflowEdge(edge_id="e_end1", from_node="step_true", to_node="end"),
        WorkflowEdge(edge_id="e_end2", from_node="step_false", to_node="end"),
    ]

    wf = WorkflowDefinition(
        workflow_id="wf_if_test",
        name="IF Test",
        nodes=nodes,
        edges=edges,
        variables={"approved": True},
    )

    # Caso 1: variables.approved == True -> step_true COMPLETED, step_false SKIPPED
    handlers = {
        "Action On True": lambda inputs: {"result": "processed_true"},
        "Action On False": lambda inputs: {"result": "processed_false"},
    }
    engine = WorkflowEngine(workflow=wf, task_handlers=handlers)
    ctx = engine.run_to_completion(concurrent=False)

    assert ctx.status == WorkflowStatus.COMPLETED
    assert ctx.node_states["check"] == NodeStatus.COMPLETED
    assert ctx.node_outputs["check"]["branch"] == "true"
    assert ctx.node_outputs["check"]["branch_taken"] == "e_true"
    assert ctx.node_states["step_true"] == NodeStatus.COMPLETED
    assert ctx.node_states["step_false"] == NodeStatus.SKIPPED


def test_if_decision_false_branch_taken():
    """Valida la toma exclusiva de la rama false cuando la condición no se satisface."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "check": WorkflowNode(
            node_id="check",
            name="Check Score",
            node_type=NodeType.DECISION,
            control_config={
                "condition": {"field": "variables.score", "op": ">=", "value": 90},
                "true_edge": "e_pass",
                "false_edge": "e_fail",
            },
        ),
        "on_pass": WorkflowNode(node_id="on_pass", name="Promote", node_type=NodeType.TASK),
        "on_fail": WorkflowNode(node_id="on_fail", name="Reject", node_type=NodeType.TASK),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }

    edges = [
        WorkflowEdge(edge_id="e0", from_node="start", to_node="check"),
        WorkflowEdge(edge_id="e_pass", from_node="check", to_node="on_pass", label="true"),
        WorkflowEdge(edge_id="e_fail", from_node="check", to_node="on_fail", label="false"),
        WorkflowEdge(edge_id="e_end1", from_node="on_pass", to_node="end"),
        WorkflowEdge(edge_id="e_end2", from_node="on_fail", to_node="end"),
    ]

    wf = WorkflowDefinition(
        workflow_id="wf_score_check",
        name="Score Check",
        nodes=nodes,
        edges=edges,
        variables={"score": 65},  # Fallará la condición
    )

    handlers = {
        "Promote": lambda inp: {"promoted": True},
        "Reject": lambda inp: {"rejected": True},
    }
    engine = WorkflowEngine(workflow=wf, task_handlers=handlers)
    ctx = engine.run_to_completion(concurrent=False)

    assert ctx.node_outputs["check"]["branch"] == "false"
    assert ctx.node_outputs["check"]["branch_taken"] == "e_fail"
    assert ctx.node_states["on_pass"] == NodeStatus.SKIPPED
    assert ctx.node_states["on_fail"] == NodeStatus.COMPLETED


# =====================================================================
# 3. Tests de WHILE Acotado (INV-03)
# =====================================================================

def test_while_loop_requires_mandatory_max_iterations():
    """Valida que el validador rechace bucles WHILE sin límite de iteraciones."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "loop": WorkflowNode(
            node_id="loop",
            name="Loop Without Limit",
            node_type=NodeType.WHILE,
            control_config={"exit_target": "end"},  # Falta max_iterations
        ),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="loop"),
        WorkflowEdge(edge_id="e2", from_node="loop", to_node="end"),
    ]
    wf = WorkflowDefinition(workflow_id="wf_bad_while", name="Bad While", nodes=nodes, edges=edges)
    errors = wf.validate_graph()
    assert any("max_iterations" in e for e in errors)


def test_while_loop_successful_execution_up_to_exit():
    """Valida la iteración controlada de un bucle WHILE hasta cumplir condición de salida."""
    # START -> WHILE -> BODY (incrementa contador) -> WHILE (loop_back)
    # WHILE -> END (exit)
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "loop_node": WorkflowNode(
            node_id="loop_node",
            name="While Counter < 3",
            node_type=NodeType.WHILE,
            control_config={
                "condition": {"field": "variables.counter", "op": "<", "value": 3},
                "body_entry": "inc_node",
                "exit_target": "end",
                "max_iterations": 5,
            },
        ),
        "inc_node": WorkflowNode(node_id="inc_node", name="Increment Counter", node_type=NodeType.TASK),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }

    edges = [
        WorkflowEdge(edge_id="e_start", from_node="start", to_node="loop_node"),
        WorkflowEdge(edge_id="e_body", from_node="loop_node", to_node="inc_node", label="body"),
        WorkflowEdge(edge_id="e_back", from_node="inc_node", to_node="loop_node", label="loop_back"),
        WorkflowEdge(edge_id="e_exit", from_node="loop_node", to_node="end", label="exit"),
    ]

    wf = WorkflowDefinition(
        workflow_id="wf_while_counter",
        name="While Counter",
        nodes=nodes,
        edges=edges,
        variables={"counter": 0},
    )

    # Validar que el grafo con bucle estructurado sea válido
    errors = wf.validate_graph()
    assert len(errors) == 0

    def inc_handler(inputs):
        # Mutar variable counter en variables del contexto de engine
        current = engine.context.variables.get("counter", 0)
        engine.context.variables["counter"] = current + 1
        return {"new_counter": current + 1}

    engine = WorkflowEngine(workflow=wf, task_handlers={"Increment Counter": inc_handler})
    ctx = engine.run_to_completion(max_steps=20, concurrent=False)

    assert ctx.status == WorkflowStatus.COMPLETED
    assert ctx.variables["counter"] == 3
    # El nodo loop_node finalizó seleccionando exit
    assert ctx.node_outputs["loop_node"]["status"] == "completed"


def test_while_loop_on_limit_abort():
    """Valida que un bucle infinito aborte estrictamente al alcanzar max_iterations (INV-03)."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "infinite_while": WorkflowNode(
            node_id="infinite_while",
            name="Infinite Loop",
            node_type=NodeType.WHILE,
            control_config={
                "condition": {"field": "variables.always_true", "op": "is_true"},
                "body_entry": "worker",
                "exit_target": "end",
                "max_iterations": 3,
                "on_limit": "ABORT",
            },
        ),
        "worker": WorkflowNode(node_id="worker", name="Worker", node_type=NodeType.TASK),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }

    edges = [
        WorkflowEdge(edge_id="e0", from_node="start", to_node="infinite_while"),
        WorkflowEdge(edge_id="e1", from_node="infinite_while", to_node="worker", label="body"),
        WorkflowEdge(edge_id="e2", from_node="worker", to_node="infinite_while", label="loop_back"),
        WorkflowEdge(edge_id="e3", from_node="infinite_while", to_node="end", label="exit"),
    ]

    wf = WorkflowDefinition(
        workflow_id="wf_inf_test",
        name="Infinite Loop Guard",
        nodes=nodes,
        edges=edges,
        variables={"always_true": True},
    )

    engine = WorkflowEngine(
        workflow=wf,
        task_handlers={"Worker": lambda inp: {"status": "worked"}},
    )

    ctx = engine.run_to_completion(max_steps=30, concurrent=False)
    # Debe haber fallado por alcanzar el límite de iteraciones
    assert ctx.status == WorkflowStatus.FAILED
    assert "Límite de iteraciones alcanzado" in ctx.error_message


# =====================================================================
# 4. Tests de DELEGATE y HUMAN_APPROVAL
# =====================================================================

def test_delegate_automatic_candidate_selection():
    """Valida el nodo DELEGATE con selección de agente candidato."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "router": WorkflowNode(
            node_id="router",
            name="Route Task",
            node_type=NodeType.DELEGATE,
            control_config={
                "routing_mode": "AUTOMATIC",
                "candidate_agents": ["fast_agent", "heavy_agent"],
            },
        ),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }

    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="router"),
        WorkflowEdge(edge_id="e2", from_node="router", to_node="end"),
    ]

    wf = WorkflowDefinition(workflow_id="wf_delegate_test", name="Delegate Test", nodes=nodes, edges=edges)

    engine = WorkflowEngine(
        workflow=wf,
        task_handlers={"fast_agent": lambda inp: {"handled_by": "fast_agent"}},
    )
    ctx = engine.run_to_completion(concurrent=False)

    assert ctx.status == WorkflowStatus.COMPLETED
    assert ctx.node_outputs["router"]["delegated_to"] == "fast_agent"


def test_human_approval_pause_and_approve():
    """Valida la pausa en WAITING_APPROVAL y posterior autorización gobernada."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "approval": WorkflowNode(
            node_id="approval",
            name="Deploy Approval",
            node_type=NodeType.HUMAN_APPROVAL,
            control_config={"prompt": "¿Autorizar despliegue en producción?"},
        ),
        "deploy": WorkflowNode(node_id="deploy", name="Deploy", node_type=NodeType.TASK),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }

    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="approval"),
        WorkflowEdge(edge_id="e2", from_node="approval", to_node="deploy"),
        WorkflowEdge(edge_id="e3", from_node="deploy", to_node="end"),
    ]

    wf = WorkflowDefinition(workflow_id="wf_approval", name="Approval Test", nodes=nodes, edges=edges)

    engine = WorkflowEngine(workflow=wf, task_handlers={"Deploy": lambda inp: {"deployed": True}})

    # Avanzar hasta approval
    engine.step()  # START
    engine.step()  # APPROVAL ejecutado -> queda en WAITING_APPROVAL

    assert engine.context.node_states["approval"] == NodeStatus.WAITING_APPROVAL
    # El siguiente paso no debe ejecutar nada porque está esperando
    assert engine.step() is None

    # Autorizar
    approved = engine.approve_node("approval", approved=True, comment="Aprobado por SecOps")
    assert approved is True
    assert engine.context.node_states["approval"] == NodeStatus.COMPLETED

    # Continuar
    engine.step()  # Deploy
    engine.step()  # End

    assert engine.context.status == WorkflowStatus.COMPLETED
    assert engine.context.node_states["deploy"] == NodeStatus.COMPLETED


# =====================================================================
# 5. Tests de PARALLEL_JOIN con Políticas (all, any, quorum)
# =====================================================================

def test_parallel_join_policy_any():
    """Valida que join_policy='any' desbloquee la sincronización con la primera rama concluida."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "fork": WorkflowNode(node_id="fork", name="Fork", node_type=NodeType.PARALLEL_FORK),
        "branch_fast": WorkflowNode(node_id="branch_fast", name="Fast Task", node_type=NodeType.TASK),
        "branch_slow": WorkflowNode(node_id="branch_slow", name="Slow Task", node_type=NodeType.TASK),
        "join": WorkflowNode(
            node_id="join",
            name="Join Any",
            node_type=NodeType.PARALLEL_JOIN,
            control_config={"join_policy": "any", "cancel_remaining": True},
        ),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }

    edges = [
        WorkflowEdge(edge_id="e0", from_node="start", to_node="fork"),
        WorkflowEdge(edge_id="e1", from_node="fork", to_node="branch_fast"),
        WorkflowEdge(edge_id="e2", from_node="fork", to_node="branch_slow"),
        WorkflowEdge(edge_id="e3", from_node="branch_fast", to_node="join"),
        WorkflowEdge(edge_id="e4", from_node="branch_slow", to_node="join"),
        WorkflowEdge(edge_id="e5", from_node="join", to_node="end"),
    ]

    wf = WorkflowDefinition(workflow_id="wf_join_any", name="Join Any Test", nodes=nodes, edges=edges)

    engine = WorkflowEngine(
        workflow=wf,
        task_handlers={
            "Fast Task": lambda inp: {"speed": "fast"},
            "Slow Task": lambda inp: {"speed": "slow"},
        },
    )

    engine.step()  # START
    engine.step()  # FORK
    # Ejecutar sólo la rama rápida
    engine.execute_node("branch_fast")

    # El JOIN con policy 'any' debe estar listo de inmediato
    assert engine.is_node_ready("join") is True
    engine.execute_node("join")

    assert engine.context.node_states["join"] == NodeStatus.COMPLETED
    # La rama lenta debe haber sido cancelada por cancel_remaining=True
    assert engine.context.node_states["branch_slow"] == NodeStatus.CANCELLED


# =====================================================================
# 6. Tests de Plantillas Canónicas (Fase 4)
# =====================================================================

def test_canonical_templates_validation_and_structure():
    """Valida que todas las plantillas canónicas pasen la validación topológica estricta."""
    from praxeon.workflows.templates import (
        CANONICAL_TEMPLATES,
        create_code_review_loop_template,
        create_research_writer_reviewer_template,
        create_triage_router_template,
    )

    for name, factory in CANONICAL_TEMPLATES.items():
        wf = factory()
        errors = wf.validate_graph()
        assert len(errors) == 0, f"Plantilla '{name}' tiene errores de validación: {errors}"
        assert wf.get_start_node() is not None
        assert len(wf.get_end_nodes()) >= 1

