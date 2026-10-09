"""Plantillas canónicas de flujos de trabajo multiagente (Fase 4).

Provee patrones arquitectónicos preconfigurados y gobernados:
- Code Review Loop: Iteración cíclica Developer ↔ Reviewer hasta aprobación o límite (INV-03).
- Research → Writer → Reviewer: Pipeline tripartito de síntesis con compuerta de aprobación.
- Triage Router: Enrutamiento adaptativo a agente ligero o profundo según complejidad.
"""

from praxeon.workflows.models import (
    NodeType,
    UIPosition,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)


def create_code_review_loop_template() -> WorkflowDefinition:
    """Crea la plantilla canónica de ciclo de desarrollo y revisión de código (Developer ↔ Reviewer)."""
    nodes = {
        "start": WorkflowNode(
            node_id="start",
            name="Inicio Tarea",
            node_type=NodeType.START,
            position=UIPosition(x=50.0, y=200.0),
        ),
        "dev": WorkflowNode(
            node_id="dev",
            name="Developer Agent",
            node_type=NodeType.AGENT,
            agent_id="ag_dev_01",
            inputs={"task": "implement_feature"},
            position=UIPosition(x=250.0, y=200.0),
        ),
        "reviewer": WorkflowNode(
            node_id="reviewer",
            name="Reviewer Agent",
            node_type=NodeType.AGENT,
            agent_id="ag_rev_01",
            inputs={"criteria": "security_and_tests"},
            position=UIPosition(x=480.0, y=200.0),
        ),
        "review_loop": WorkflowNode(
            node_id="review_loop",
            name="Control Bucle Calidad",
            node_type=NodeType.WHILE,
            control_config={
                "condition": {"field": "outputs.reviewer.approved", "op": "is_false"},
                "body_entry": "dev",
                "exit_target": "human_gate",
                "max_iterations": 3,
                "on_limit": "ESCALATE",
            },
            position=UIPosition(x=700.0, y=200.0),
        ),
        "human_gate": WorkflowNode(
            node_id="human_gate",
            name="Aprobación Despliegue",
            node_type=NodeType.HUMAN_APPROVAL,
            control_config={
                "prompt": "¿Autorizar despliegue tras verificación de tests y revisión?",
                "approver_role": "lead_engineer",
            },
            position=UIPosition(x=920.0, y=200.0),
        ),
        "end": WorkflowNode(
            node_id="end",
            name="Finalizado",
            node_type=NodeType.END,
            position=UIPosition(x=1140.0, y=200.0),
        ),
    }

    edges = [
        WorkflowEdge(edge_id="e_start_dev", from_node="start", to_node="dev"),
        WorkflowEdge(edge_id="e_dev_rev", from_node="dev", to_node="reviewer"),
        WorkflowEdge(edge_id="e_rev_loop", from_node="reviewer", to_node="review_loop"),
        WorkflowEdge(edge_id="e_loop_back", from_node="review_loop", to_node="dev", label="body"),
        WorkflowEdge(edge_id="e_loop_exit", from_node="review_loop", to_node="human_gate", label="exit"),
        WorkflowEdge(edge_id="e_gate_end", from_node="human_gate", to_node="end"),
    ]

    return WorkflowDefinition(
        workflow_id="tpl_code_review_loop",
        name="Code Review Loop (Developer ↔ Reviewer)",
        description="Ciclo interactivo de refinamiento de código gobernado con límite de 3 iteraciones.",
        version=1,
        nodes=nodes,
        edges=edges,
        variables={"repo": "praxeon_core"},
    )


def create_research_writer_reviewer_template() -> WorkflowDefinition:
    """Crea la plantilla de análisis y redacción técnica en 3 etapas."""
    nodes = {
        "start": WorkflowNode(
            node_id="start",
            name="Inicio Investigación",
            node_type=NodeType.START,
            position=UIPosition(x=50.0, y=180.0),
        ),
        "research": WorkflowNode(
            node_id="research",
            name="Research Agent",
            node_type=NodeType.AGENT,
            agent_id="ag_res_01",
            inputs={"scope": "literature_and_data"},
            position=UIPosition(x=280.0, y=180.0),
        ),
        "writer": WorkflowNode(
            node_id="writer",
            name="Technical Writer",
            node_type=NodeType.AGENT,
            agent_id="ag_writer_01",
            position=UIPosition(x=520.0, y=180.0),
        ),
        "reviewer": WorkflowNode(
            node_id="reviewer",
            name="Editorial Reviewer",
            node_type=NodeType.AGENT,
            agent_id="ag_rev_01",
            position=UIPosition(x=760.0, y=180.0),
        ),
        "end": WorkflowNode(
            node_id="end",
            name="Entrega Informe",
            node_type=NodeType.END,
            position=UIPosition(x=1000.0, y=180.0),
        ),
    }

    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="research"),
        WorkflowEdge(edge_id="e2", from_node="research", to_node="writer"),
        WorkflowEdge(edge_id="e3", from_node="writer", to_node="reviewer"),
        WorkflowEdge(edge_id="e4", from_node="reviewer", to_node="end"),
    ]

    return WorkflowDefinition(
        workflow_id="tpl_research_writer_reviewer",
        name="Research → Writer → Reviewer",
        description="Pipeline colaborativo para análisis en profundidad, síntesis y control de calidad.",
        version=1,
        nodes=nodes,
        edges=edges,
    )


def create_triage_router_template() -> WorkflowDefinition:
    """Crea la plantilla de clasificación y enrutamiento inteligente según dificultad."""
    nodes = {
        "start": WorkflowNode(
            node_id="start",
            name="Recepción Ticket",
            node_type=NodeType.START,
            position=UIPosition(x=50.0, y=250.0),
        ),
        "triage": WorkflowNode(
            node_id="triage",
            name="Triage Router",
            node_type=NodeType.IF,
            control_config={
                "condition": {"field": "variables.complexity", "op": "==", "value": "high"},
                "true_edge": "e_heavy",
                "false_edge": "e_light",
            },
            position=UIPosition(x=280.0, y=250.0),
        ),
        "agent_heavy": WorkflowNode(
            node_id="agent_heavy",
            name="Specialized Deep Agent",
            node_type=NodeType.AGENT,
            agent_id="ag_heavy_01",
            position=UIPosition(x=550.0, y=150.0),
        ),
        "agent_light": WorkflowNode(
            node_id="agent_light",
            name="Fast Lightweight Agent",
            node_type=NodeType.AGENT,
            agent_id="ag_light_01",
            position=UIPosition(x=550.0, y=350.0),
        ),
        "join_node": WorkflowNode(
            node_id="join_node",
            name="Consolidación Respuesta",
            node_type=NodeType.PARALLEL_JOIN,
            control_config={"join_policy": "any"},
            position=UIPosition(x=820.0, y=250.0),
        ),
        "end": WorkflowNode(
            node_id="end",
            name="Ticket Resuelto",
            node_type=NodeType.END,
            position=UIPosition(x=1050.0, y=250.0),
        ),
    }

    edges = [
        WorkflowEdge(edge_id="e0", from_node="start", to_node="triage"),
        WorkflowEdge(edge_id="e_heavy", from_node="triage", to_node="agent_heavy", label="true"),
        WorkflowEdge(edge_id="e_light", from_node="triage", to_node="agent_light", label="false"),
        WorkflowEdge(edge_id="e_j1", from_node="agent_heavy", to_node="join_node"),
        WorkflowEdge(edge_id="e_j2", from_node="agent_light", to_node="join_node"),
        WorkflowEdge(edge_id="e_end", from_node="join_node", to_node="end"),
    ]

    return WorkflowDefinition(
        workflow_id="tpl_triage_router",
        name="Triage Router (Light vs Deep Agent)",
        description="Enrutamiento condicional adaptativo para optimización de latencia y coste de inferencia.",
        version=1,
        nodes=nodes,
        edges=edges,
        variables={"complexity": "low"},
    )


CANONICAL_TEMPLATES = {
    "code_review_loop": create_code_review_loop_template,
    "research_writer_reviewer": create_research_writer_reviewer_template,
    "triage_router": create_triage_router_template,
}
