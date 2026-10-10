"""Suite de pruebas de verificación formal para la semántica de bucles WHILE

y la sincronización de schedulers (secuencial y concurrente).

Cubre:
- Reproducción A: Condición falsa en la entrada -> cuerpo ejecuta 0 veces y pasa a SKIPPED.
- Reproducción B: Scheduler concurrente no finaliza prematuramente en END mientras WHILE esté activo o en READY.
- Decisiones canónicas CONTINUE / EXIT.
- Limpieza y estado terminal determinista (_finalize_terminal_state).
- Manejo de max_iterations con políticas ABORT y ESCALATE.
"""

import pytest
from praxeon.workflows.models import (
    WorkflowDefinition,
    WorkflowNode,
    WorkflowEdge,
    NodeType,
    NodeStatus,
    WorkflowStatus,
)
from praxeon.workflows.engine import WorkflowEngine


def test_reproduction_a_while_false_on_entry_zero_iterations():
    """Reproducción A: Si la condición inicial es falsa, el cuerpo del bucle

    NO debe ejecutarse ninguna vez (0 iteraciones) y debe marcarse como SKIPPED.
    """
    wf = WorkflowDefinition(
        workflow_id="wf_while_false_entry",
        name="While False on Entry",
        variables={"counter": 5},
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "loop": WorkflowNode(
                node_id="loop",
                name="While Loop",
                node_type=NodeType.WHILE,
                control_config={
                    "condition": "counter < 3",
                    "max_iterations": 5,
                    "body_entry": "inc",
                    "exit_target": "end",
                },
            ),
            "inc": WorkflowNode(node_id="inc", name="Increment", node_type=NodeType.TASK, tool_name="increment_counter"),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
        },
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="loop"),
            WorkflowEdge(edge_id="e_body", from_node="loop", to_node="inc", label="body"),
            WorkflowEdge(edge_id="e_repeat", from_node="inc", to_node="loop"),
            WorkflowEdge(edge_id="e_exit", from_node="loop", to_node="end", label="exit"),
        ],
    )

    inc_calls = 0

    def inc_handler(inputs):
        nonlocal inc_calls
        inc_calls += 1
        current = engine.context.variables.get("counter", 0)
        engine.context.variables["counter"] = current + 1
        return {"counter": current + 1}

    # Inicializamos con counter = 5 (la condición counter < 3 es FALSA de entrada)
    engine = WorkflowEngine(wf, task_handlers={"increment_counter": inc_handler})

    ctx = engine.run_to_completion(max_steps=20, concurrent=False)

    # Verificaciones críticas
    assert ctx.status == WorkflowStatus.COMPLETED
    assert inc_calls == 0, f"El cuerpo del bucle se ejecutó {inc_calls} veces; debía ser 0."
    assert ctx.variables["counter"] == 5

    # Estados de nodos
    assert ctx.node_states["start"] == NodeStatus.COMPLETED
    assert ctx.node_states["loop"] == NodeStatus.COMPLETED
    assert ctx.node_states["inc"] == NodeStatus.SKIPPED, "El cuerpo del bucle debe transicionar a SKIPPED."
    assert ctx.node_states["end"] == NodeStatus.COMPLETED

    # Salida canónica del nodo loop
    loop_output = ctx.node_outputs["loop"]
    assert loop_output.get("decision") == "EXIT"
    assert loop_output.get("action") == "exit_loop"
    assert loop_output.get("condition_matched") is False
    assert loop_output.get("iteration") == 0


def test_reproduction_b_concurrent_scheduler_while_synchronization():
    """Reproducción B: En ejecución concurrente (step_concurrent), el nodo END

    NO debe completarse prematuramente mientras el bucle WHILE esté activo o en READY.
    """
    wf = WorkflowDefinition(
        workflow_id="wf_concurrent_while",
        name="Concurrent While Synchronization",
        variables={"counter": 0},
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "loop": WorkflowNode(
                node_id="loop",
                name="While Loop",
                node_type=NodeType.WHILE,
                control_config={
                    "condition": "counter < 3",
                    "max_iterations": 10,
                    "body_entry": "inc",
                    "exit_target": "end",
                },
            ),
            "inc": WorkflowNode(node_id="inc", name="Increment", node_type=NodeType.TASK, tool_name="increment_counter"),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
        },
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="loop"),
            WorkflowEdge(edge_id="e_body", from_node="loop", to_node="inc", label="body"),
            WorkflowEdge(edge_id="e_repeat", from_node="inc", to_node="loop"),
            WorkflowEdge(edge_id="e_exit", from_node="loop", to_node="end", label="exit"),
        ],
    )

    inc_calls = 0

    def inc_handler(inputs):
        nonlocal inc_calls
        inc_calls += 1
        current = engine.context.variables.get("counter", 0)
        engine.context.variables["counter"] = current + 1
        return {"counter": current + 1}

    engine = WorkflowEngine(wf, task_handlers={"increment_counter": inc_handler})

    ctx = engine.run_to_completion(max_steps=30, concurrent=True, max_workers=4)

    assert ctx.status == WorkflowStatus.COMPLETED
    assert inc_calls == 3, f"El contador debía incrementarse exactamente 3 veces, pero fue {inc_calls}."
    assert ctx.variables["counter"] == 3

    assert ctx.node_states["start"] == NodeStatus.COMPLETED
    assert ctx.node_states["loop"] == NodeStatus.COMPLETED
    assert ctx.node_states["inc"] == NodeStatus.COMPLETED
    assert ctx.node_states["end"] == NodeStatus.COMPLETED

    # Verificar que no queden nodos en READY ni PENDING
    residual_nodes = [
        nid for nid, st in ctx.node_states.items()
        if st in (NodeStatus.READY, NodeStatus.PENDING, NodeStatus.RUNNING)
    ]
    assert len(residual_nodes) == 0, f"Nodos residuales no terminales encontrados: {residual_nodes}"


def test_while_loop_sequential_exact_iterations():
    """Valida la ejecución secuencial completa para N iteraciones exactas."""
    wf = WorkflowDefinition(
        workflow_id="wf_seq_while",
        name="Sequential While",
        variables={"counter": 0},
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "loop": WorkflowNode(
                node_id="loop",
                name="While Loop",
                node_type=NodeType.WHILE,
                control_config={
                    "condition": "counter < 4",
                    "max_iterations": 10,
                    "body_entry": "work",
                    "exit_target": "end",
                },
            ),
            "work": WorkflowNode(node_id="work", name="Do Work", node_type=NodeType.TASK, tool_name="do_work"),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
        },
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="loop"),
            WorkflowEdge(edge_id="e_body", from_node="loop", to_node="work", label="body"),
            WorkflowEdge(edge_id="e_repeat", from_node="work", to_node="loop"),
            WorkflowEdge(edge_id="e_exit", from_node="loop", to_node="end", label="exit"),
        ],
    )

    steps_done = []

    def work_handler(inputs):
        c = engine.context.variables.get("counter", 0)
        steps_done.append(c)
        engine.context.variables["counter"] = c + 1
        return {"step": c}

    engine = WorkflowEngine(wf, task_handlers={"do_work": work_handler})

    ctx = engine.run_to_completion(max_steps=50, concurrent=False)

    assert ctx.status == WorkflowStatus.COMPLETED
    assert steps_done == [0, 1, 2, 3]
    assert ctx.variables["counter"] == 4
    assert ctx.node_outputs["loop"].get("decision") == "EXIT"


def test_while_loop_max_iterations_abort():
    """Valida que exceder max_iterations con on_limit='ABORT' falle el nodo/workflow deterministamente."""
    wf = WorkflowDefinition(
        workflow_id="wf_while_abort",
        name="While Abort on Limit",
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "loop": WorkflowNode(
                node_id="loop",
                name="While Loop",
                node_type=NodeType.WHILE,
                control_config={
                    "condition": "true",
                    "max_iterations": 2,
                    "on_limit": "ABORT",
                    "body_entry": "noop",
                    "exit_target": "end",
                },
            ),
            "noop": WorkflowNode(node_id="noop", name="Noop", node_type=NodeType.TASK, tool_name="noop"),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
        },
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="loop"),
            WorkflowEdge(edge_id="e_body", from_node="loop", to_node="noop", label="body"),
            WorkflowEdge(edge_id="e_repeat", from_node="noop", to_node="loop"),
            WorkflowEdge(edge_id="e_exit", from_node="loop", to_node="end", label="exit"),
        ],
    )

    engine = WorkflowEngine(wf, task_handlers={"noop": lambda inp: {}})

    ctx = engine.run_to_completion(max_steps=20, concurrent=False)

    assert ctx.status == WorkflowStatus.FAILED
    assert ctx.node_states["loop"] == NodeStatus.FAILED
    assert "Límite de iteraciones alcanzado" in (ctx.error_message or "")


def test_while_loop_max_iterations_escalate():
    """Valida que exceder max_iterations con on_limit='ESCALATE' pase a WAITING_APPROVAL."""
    wf = WorkflowDefinition(
        workflow_id="wf_while_escalate",
        name="While Escalate on Limit",
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "loop": WorkflowNode(
                node_id="loop",
                name="While Loop",
                node_type=NodeType.WHILE,
                control_config={
                    "condition": "true",
                    "max_iterations": 2,
                    "on_limit": "ESCALATE",
                    "body_entry": "noop",
                    "exit_target": "end",
                },
            ),
            "noop": WorkflowNode(node_id="noop", name="Noop", node_type=NodeType.TASK, tool_name="noop"),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
        },
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="loop"),
            WorkflowEdge(edge_id="e_body", from_node="loop", to_node="noop", label="body"),
            WorkflowEdge(edge_id="e_repeat", from_node="noop", to_node="loop"),
            WorkflowEdge(edge_id="e_exit", from_node="loop", to_node="end", label="exit"),
        ],
    )

    engine = WorkflowEngine(wf, task_handlers={"noop": lambda inp: {}})

    ctx = engine.run_to_completion(max_steps=20, concurrent=False)

    assert ctx.node_states["loop"] == NodeStatus.WAITING_APPROVAL
    assert ctx.node_outputs["loop"].get("status") == "escalated_on_limit"


def test_finalize_terminal_state_leaves_no_ready_or_pending_nodes():
    """Valida que _finalize_terminal_state transicione limpiamente cualquier nodo residual a SKIPPED."""
    wf = WorkflowDefinition(
        workflow_id="wf_residual_cleanup",
        name="Residual Cleanup Test",
        variables={"val": 1},
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "decide": WorkflowNode(
                node_id="decide",
                name="Decide",
                node_type=NodeType.DECISION,
                control_config={"condition": "val == 1", "true_edge": "left", "false_edge": "right"},
            ),
            "left": WorkflowNode(node_id="left", name="Left Branch", node_type=NodeType.TASK, tool_name="noop"),
            "right": WorkflowNode(node_id="right", name="Right Branch", node_type=NodeType.TASK, tool_name="noop"),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
        },
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="decide"),
            WorkflowEdge(edge_id="left", from_node="decide", to_node="left", label="true"),
            WorkflowEdge(edge_id="right", from_node="decide", to_node="right", label="false"),
            WorkflowEdge(edge_id="e_end", from_node="left", to_node="end"),
        ],
    )

    engine = WorkflowEngine(wf, task_handlers={"noop": lambda inp: {}})

    ctx = engine.run_to_completion(max_steps=10, concurrent=True)

    assert ctx.status == WorkflowStatus.COMPLETED
    assert ctx.node_states["right"] == NodeStatus.SKIPPED
    assert ctx.node_states["left"] == NodeStatus.COMPLETED
    assert ctx.node_states["end"] == NodeStatus.COMPLETED

    for nid, st in ctx.node_states.items():
        assert st not in (NodeStatus.READY, NodeStatus.PENDING, NodeStatus.RUNNING), f"Nodo {nid} quedó en estado {st}"
