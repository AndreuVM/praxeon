"""Servicio Backend para Gestión, Edición y Simulación de Workflows Visuales (F6-04).

Provee la lógica de negocio y persistencia para el editor drag & drop de flujos de trabajo:
- WorkflowEditorService:
    * CRUD de WorkflowDefinitions con almacenamiento en memoria o disco.
    * Manipulación interactiva de nodos (creación, edición, eliminación, actualización de posición).
    * Gestión de cableado y conexiones dirigidas (aristas) con condiciones lógicas.
    * Coordinación con WorkflowEngine y WorkflowDecisionBridge para ejecución interactiva y backtrack.
"""

from datetime import datetime, timezone
import json
import time
from typing import Any, Dict, List, Optional
import uuid

from praxeon.agents.bus import AgentMessageBus
from praxeon.runtime.event_bus import EventBus
from praxeon.workflows.decision_bridge import WorkflowDecisionBridge
from praxeon.workflows.engine import WorkflowEngine, WorkflowExecutionContext, WorkflowStatus
from praxeon.workflows.models import (
    EdgeCondition,
    NodeStatus,
    NodeType,
    RetryPolicy,
    UIPosition,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)


class WorkflowEditorService:
    """Servicio central para el editor visual e interactivo de workflows."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        agent_bus: Optional[AgentMessageBus] = None,
    ):
        self.event_bus = event_bus or EventBus()
        self.agent_bus = agent_bus
        self._workflows: Dict[str, WorkflowDefinition] = {}
        self._active_engines: Dict[str, WorkflowEngine] = {}
        self._active_bridges: Dict[str, WorkflowDecisionBridge] = {}

        # Registrar un workflow de demostración por defecto
        self._init_demo_workflow()

    def _init_demo_workflow(self) -> None:
        """Inicializa un flujo canónico de muestra para el editor."""
        nodes = {
            "start": WorkflowNode(
                node_id="start",
                name="Start Ingestion",
                node_type=NodeType.START,
                position=UIPosition(x=100.0, y=250.0),
            ),
            "research": WorkflowNode(
                node_id="research",
                name="Research Analysis",
                node_type=NodeType.AGENT,
                agent_id="ag_res_01",
                inputs={"query": "security_audit_spec"},
                position=UIPosition(x=350.0, y=150.0),
            ),
            "dev": WorkflowNode(
                node_id="dev",
                name="Code Implementation",
                node_type=NodeType.AGENT,
                agent_id="ag_dev_01",
                inputs={"task": "implement_patch"},
                position=UIPosition(x=350.0, y=350.0),
            ),
            "decision": WorkflowNode(
                node_id="decision",
                name="Quality Gate Decision",
                node_type=NodeType.DECISION,
                position=UIPosition(x=600.0, y=250.0),
            ),
            "deploy": WorkflowNode(
                node_id="deploy",
                name="Deploy Production",
                node_type=NodeType.TASK,
                tool_name="deploy_service",
                metadata={"require_confirmation": True},
                position=UIPosition(x=850.0, y=250.0),
            ),
            "end": WorkflowNode(
                node_id="end",
                name="Pipeline Success",
                node_type=NodeType.END,
                position=UIPosition(x=1100.0, y=250.0),
            ),
        }
        edges = [
            WorkflowEdge(edge_id="e1", from_node="start", to_node="research", label="Start to Research"),
            WorkflowEdge(edge_id="e2", from_node="start", to_node="dev", label="Start to Dev"),
            WorkflowEdge(edge_id="e3", from_node="research", to_node="decision", label="Research to Decision"),
            WorkflowEdge(edge_id="e4", from_node="dev", to_node="decision", label="Dev to Decision"),
            WorkflowEdge(edge_id="e5", from_node="decision", to_node="deploy", label="Decision to Deploy"),
            WorkflowEdge(edge_id="e6", from_node="deploy", to_node="end", label="Deploy to End"),
        ]
        demo_wf = WorkflowDefinition(
            workflow_id="wf_demo_autonomous",
            name="Autonomous Agent DevOps Pipeline",
            description="Flujo visual canónico que orquesta Researcher, Developer y despliegue supervisado.",
            nodes=nodes,
            edges=edges,
            variables={"env": "staging", "target_branch": "main"},
        )
        self.register_workflow(demo_wf)

    def register_workflow(self, workflow: WorkflowDefinition) -> None:
        """Registra o actualiza una definición de workflow."""
        self._workflows[workflow.workflow_id] = workflow

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        """Obtiene la definición de un flujo de trabajo."""
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> List[Dict[str, Any]]:
        """Lista resúmenes de todos los flujos registrados."""
        return [
            {
                "workflow_id": wf.workflow_id,
                "name": wf.name,
                "description": wf.description,
                "version": wf.version,
                "node_count": len(wf.nodes),
                "edge_count": len(wf.edges),
                "updated_at": wf.updated_at.isoformat(),
            }
            for wf in self._workflows.values()
        ]

    def create_workflow(
        self,
        name: str,
        description: str = "",
        workflow_id: Optional[str] = None,
    ) -> WorkflowDefinition:
        """Crea un nuevo workflow vacío con nodos START y END elementales."""
        wid = workflow_id or f"wf_{int(time.time()*1000)%1000000}"
        start_node = WorkflowNode(node_id="start", name="Start", node_type=NodeType.START, position=UIPosition(x=100.0, y=250.0))
        end_node = WorkflowNode(node_id="end", name="End", node_type=NodeType.END, position=UIPosition(x=600.0, y=250.0))
        edge = WorkflowEdge(edge_id="e_start_end", from_node="start", to_node="end")

        wf = WorkflowDefinition(
            workflow_id=wid,
            name=name,
            description=description,
            nodes={"start": start_node, "end": end_node},
            edges=[edge],
        )
        self.register_workflow(wf)
        return wf

    def add_node(
        self,
        workflow_id: str,
        name: str,
        node_type: NodeType,
        position: Optional[UIPosition] = None,
        agent_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> WorkflowNode:
        """Añade un nodo al flujo de trabajo."""
        wf = self.get_workflow(workflow_id)
        if not wf:
            raise ValueError(f"Workflow '{workflow_id}' no encontrado.")

        node_id = f"node_{node_type.value.lower()}_{int(time.time()*1000)%10000}"
        node = WorkflowNode(
            node_id=node_id,
            name=name,
            node_type=node_type,
            agent_id=agent_id,
            tool_name=tool_name,
            inputs=inputs or {},
            position=position or UIPosition(x=300.0, y=250.0),
        )

        new_nodes = dict(wf.nodes)
        new_nodes[node_id] = node

        updated_wf = WorkflowDefinition(
            workflow_id=wf.workflow_id,
            name=wf.name,
            description=wf.description,
            version=wf.version + 1,
            nodes=new_nodes,
            edges=list(wf.edges),
            variables=dict(wf.variables),
            updated_at=datetime.now(timezone.utc),
        )
        self.register_workflow(updated_wf)
        return node

    def update_node_position(
        self,
        workflow_id: str,
        node_id: str,
        x: float,
        y: float,
    ) -> WorkflowNode:
        """Actualiza las coordenadas de un nodo en el lienzo tras arrastrar."""
        wf = self.get_workflow(workflow_id)
        if not wf or node_id not in wf.nodes:
            raise ValueError(f"Nodo '{node_id}' o workflow '{workflow_id}' no encontrado.")

        old_node = wf.nodes[node_id]
        dump = old_node.model_dump()
        dump["position"] = {"x": x, "y": y}
        updated_node = WorkflowNode(**dump)

        new_nodes = dict(wf.nodes)
        new_nodes[node_id] = updated_node

        updated_wf = WorkflowDefinition(
            workflow_id=wf.workflow_id,
            name=wf.name,
            description=wf.description,
            version=wf.version,
            nodes=new_nodes,
            edges=list(wf.edges),
            variables=dict(wf.variables),
            updated_at=datetime.now(timezone.utc),
        )
        self.register_workflow(updated_wf)
        return updated_node

    def remove_node(self, workflow_id: str, node_id: str) -> bool:
        """Elimina un nodo y limpia todas las aristas conectadas."""
        wf = self.get_workflow(workflow_id)
        if not wf or node_id not in wf.nodes:
            return False

        if wf.nodes[node_id].node_type in (NodeType.START, NodeType.END):
            raise ValueError("No se puede eliminar el nodo START o END raíz.")

        new_nodes = {k: v for k, v in wf.nodes.items() if k != node_id}
        new_edges = [e for e in wf.edges if e.from_node != node_id and e.to_node != node_id]

        updated_wf = WorkflowDefinition(
            workflow_id=wf.workflow_id,
            name=wf.name,
            description=wf.description,
            version=wf.version + 1,
            nodes=new_nodes,
            edges=new_edges,
            variables=dict(wf.variables),
            updated_at=datetime.now(timezone.utc),
        )
        self.register_workflow(updated_wf)
        return True

    def connect_nodes(
        self,
        workflow_id: str,
        from_node: str,
        to_node: str,
        condition: Optional[EdgeCondition] = None,
        label: str = "",
    ) -> WorkflowEdge:
        """Conecta dos nodos mediante una nueva arista dirigida."""
        wf = self.get_workflow(workflow_id)
        if not wf:
            raise ValueError(f"Workflow '{workflow_id}' no encontrado.")
        if from_node not in wf.nodes or to_node not in wf.nodes:
            raise ValueError(f"Los nodos '{from_node}' y '{to_node}' deben existir en el workflow.")

        edge_id = f"e_{from_node}_{to_node}_{int(time.time()*1000)%10000}"
        edge = WorkflowEdge(
            edge_id=edge_id,
            from_node=from_node,
            to_node=to_node,
            condition=condition,
            label=label or f"{from_node} -> {to_node}",
        )

        new_edges = list(wf.edges)
        new_edges.append(edge)

        updated_wf = WorkflowDefinition(
            workflow_id=wf.workflow_id,
            name=wf.name,
            description=wf.description,
            version=wf.version + 1,
            nodes=dict(wf.nodes),
            edges=new_edges,
            variables=dict(wf.variables),
            updated_at=datetime.now(timezone.utc),
        )
        self.register_workflow(updated_wf)
        return edge

    def disconnect_nodes(self, workflow_id: str, edge_id: str) -> bool:
        """Elimina una conexión existente."""
        wf = self.get_workflow(workflow_id)
        if not wf:
            return False

        new_edges = [e for e in wf.edges if e.edge_id != edge_id]
        if len(new_edges) == len(wf.edges):
            return False

        updated_wf = WorkflowDefinition(
            workflow_id=wf.workflow_id,
            name=wf.name,
            description=wf.description,
            version=wf.version + 1,
            nodes=dict(wf.nodes),
            edges=new_edges,
            variables=dict(wf.variables),
            updated_at=datetime.now(timezone.utc),
        )
        self.register_workflow(updated_wf)
        return True

    def get_or_create_engine(self, workflow_id: str) -> WorkflowEngine:
        """Obtiene o crea un motor de ejecución para el workflow."""
        if workflow_id not in self._active_engines:
            wf = self.get_workflow(workflow_id)
            if not wf:
                raise ValueError(f"Workflow '{workflow_id}' no encontrado.")
            engine = WorkflowEngine(workflow=wf, agent_bus=self.agent_bus)
            self._active_engines[workflow_id] = engine
            bridge = WorkflowDecisionBridge(engine=engine, event_bus=self.event_bus)
            self._active_bridges[workflow_id] = bridge
        return self._active_engines[workflow_id]

    def execute_workflow(self, workflow_id: str, max_steps: int = 100) -> Dict[str, Any]:
        """Ejecuta el flujo de trabajo supervisado por gobernanza."""
        engine = self.get_or_create_engine(workflow_id)
        bridge = self._active_bridges[workflow_id]
        ctx = bridge.run_to_completion(max_steps=max_steps)
        return ctx.to_dict()

    def step_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Avanza un paso individual en el workflow."""
        engine = self.get_or_create_engine(workflow_id)
        bridge = self._active_bridges[workflow_id]
        step_nid = bridge.step()
        return {
            "executed_node_id": step_nid,
            "status": engine.context.status.value,
            "context": engine.context.to_dict(),
        }

    def backtrack_workflow(self, workflow_id: str, target_node_id: str) -> Dict[str, Any]:
        """Aplica backtrack determinista hacia un nodo objetivo."""
        engine = self.get_or_create_engine(workflow_id)
        success = engine.backtrack_to_node(target_node_id)
        return {
            "success": success,
            "target_node_id": target_node_id,
            "status": engine.context.status.value,
            "context": engine.context.to_dict(),
        }

    def reset_execution(self, workflow_id: str) -> None:
        """Reinicia el motor de ejecución del workflow a estado IDLE."""
        self._active_engines.pop(workflow_id, None)
        self._active_bridges.pop(workflow_id, None)
