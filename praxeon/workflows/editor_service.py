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
from praxeon.workflows.templates import CANONICAL_TEMPLATES



class WorkflowEditorService:
    """Servicio central para el editor visual e interactivo de workflows."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        agent_bus: Optional[AgentMessageBus] = None,
        persistence_store: Optional[Any] = None,
    ):
        self.event_bus = event_bus or EventBus()
        self.agent_bus = agent_bus
        self.persistence_store = persistence_store
        self._workflows: Dict[str, WorkflowDefinition] = {}
        self._active_engines: Dict[str, WorkflowEngine] = {}
        self._active_bridges: Dict[str, WorkflowDecisionBridge] = {}

        # Cargar workflows desde persistencia SQLite si está disponible
        loaded = False
        if self.persistence_store and hasattr(self.persistence_store, "list_workflows"):
            try:
                stored = self.persistence_store.list_workflows()
                for wf in stored:
                    self._workflows[wf.workflow_id] = wf
                if self._workflows:
                    loaded = True
            except Exception:
                pass

        # Registrar un workflow de demostración por defecto si el almacén está vacío
        if not loaded:
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
        """Registra o actualiza una definición de workflow en memoria y persistencia."""
        self._workflows[workflow.workflow_id] = workflow
        if self.persistence_store and hasattr(self.persistence_store, "save_workflow"):
            try:
                self.persistence_store.save_workflow(workflow)
            except Exception:
                pass

    def delete_workflow(self, workflow_id: str) -> bool:
        """Elimina un workflow registrado de memoria y persistencia."""
        self._active_engines.pop(workflow_id, None)
        self._active_bridges.pop(workflow_id, None)
        removed = self._workflows.pop(workflow_id, None) is not None
        if self.persistence_store and hasattr(self.persistence_store, "delete_workflow"):
            try:
                self.persistence_store.delete_workflow(workflow_id)
            except Exception:
                pass
        return removed

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        """Obtiene la definición de un flujo de trabajo."""
        wf = self._workflows.get(workflow_id)
        if not wf and self.persistence_store and hasattr(self.persistence_store, "get_workflow"):
            try:
                wf = self.persistence_store.get_workflow(workflow_id)
                if wf:
                    self._workflows[workflow_id] = wf
            except Exception:
                pass
        return wf

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
        wid = workflow_id or f"wf_{uuid.uuid4().hex[:12]}"
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
        control_config: Optional[Dict[str, Any]] = None,
    ) -> WorkflowNode:
        """Añade un nodo al flujo de trabajo."""
        wf = self.get_workflow(workflow_id)
        if not wf:
            raise ValueError(f"Workflow '{workflow_id}' no encontrado.")

        node_id = f"node_{node_type.value.lower()}_{uuid.uuid4().hex[:8]}"
        node = WorkflowNode(
            node_id=node_id,
            name=name,
            node_type=node_type,
            agent_id=agent_id,
            tool_name=tool_name,
            inputs=inputs or {},
            control_config=control_config,
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

    def update_node(
        self,
        workflow_id: str,
        node_id: str,
        name: Optional[str] = None,
        node_type: Optional[NodeType] = None,
        agent_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        inputs: Optional[Dict[str, Any]] = None,
        control_config: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> WorkflowNode:
        """Actualiza las propiedades operativas y de configuración de un nodo."""
        wf = self.get_workflow(workflow_id)
        if not wf or node_id not in wf.nodes:
            raise ValueError(f"Nodo '{node_id}' o workflow '{workflow_id}' no encontrado.")

        old_node = wf.nodes[node_id]
        dump = old_node.model_dump()
        if name is not None:
            dump["name"] = name.strip() or old_node.name
        if node_type is not None:
            dump["node_type"] = node_type
        if agent_id is not None:
            dump["agent_id"] = agent_id.strip() if agent_id.strip() else None
        if tool_name is not None:
            dump["tool_name"] = tool_name.strip() if tool_name.strip() else None
        if inputs is not None:
            dump["inputs"] = inputs
        if control_config is not None:
            dump["control_config"] = control_config
        if metadata is not None:
            dump["metadata"] = metadata

        updated_node = WorkflowNode(**dump)
        new_nodes = dict(wf.nodes)
        new_nodes[node_id] = updated_node

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
        return updated_node

    def remove_node(self, workflow_id: str, node_id: str) -> bool:
        """Elimina un nodo y limpia todas las aristas conectadas."""
        wf = self.get_workflow(workflow_id)
        if not wf or node_id not in wf.nodes:
            return False

        # Protección: solo impedir si es el único nodo de su tipo
        node_to_del = wf.nodes[node_id]
        if node_to_del.node_type == NodeType.START:
            starts = [n for n in wf.nodes.values() if n.node_type == NodeType.START]
            if len(starts) <= 1:
                raise ValueError("No se puede eliminar el nodo START raíz si es el único del flujo.")
        elif node_to_del.node_type == NodeType.END:
            ends = [n for n in wf.nodes.values() if n.node_type == NodeType.END]
            if len(ends) <= 1:
                raise ValueError("No se puede eliminar el nodo END raíz si es el único del flujo.")

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

        edge_id = f"e_{from_node}_{to_node}_{uuid.uuid4().hex[:8]}"
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

    def update_edge(
        self,
        workflow_id: str,
        edge_id: str,
        label: Optional[str] = None,
        condition: Optional[EdgeCondition] = None,
    ) -> WorkflowEdge:
        """Actualiza la etiqueta o condición lógica de una arista dirigida existente."""
        wf = self.get_workflow(workflow_id)
        if not wf:
            raise ValueError(f"Workflow '{workflow_id}' no encontrado.")

        edge_idx = -1
        target_edge = None
        for i, e in enumerate(wf.edges):
            if e.edge_id == edge_id:
                edge_idx = i
                target_edge = e
                break

        if target_edge is None:
            raise ValueError(f"Arista '{edge_id}' no encontrada en workflow '{workflow_id}'.")

        dump = target_edge.model_dump()
        if label is not None:
            dump["label"] = label
        if condition is not None:
            dump["condition"] = condition

        updated_edge = WorkflowEdge(**dump)
        new_edges = list(wf.edges)
        new_edges[edge_idx] = updated_edge

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
        return updated_edge

    def get_or_create_engine(self, workflow_id: str) -> WorkflowEngine:
        """Obtiene o crea un motor de ejecución para el workflow."""
        if workflow_id not in self._active_engines:
            wf = self.get_workflow(workflow_id)
            if not wf:
                raise ValueError(f"Workflow '{workflow_id}' no encontrado.")
            engine = WorkflowEngine(
                workflow=wf,
                agent_bus=self.agent_bus,
                allow_synthetic_fallback=True,
            )
            self._active_engines[workflow_id] = engine
            bridge = WorkflowDecisionBridge(engine=engine, event_bus=self.event_bus)
            self._active_bridges[workflow_id] = bridge
        return self._active_engines[workflow_id]

    def execute_workflow(self, workflow_id: str, max_steps: int = 100) -> Dict[str, Any]:
        """Ejecuta el flujo de trabajo supervisado por gobernanza."""
        engine = self.get_or_create_engine(workflow_id)
        bridge = self._active_bridges[workflow_id]
        ctx = bridge.run_to_completion(max_steps=max_steps)
        if self.persistence_store and hasattr(self.persistence_store, "save_workflow_execution"):
            try:
                self.persistence_store.save_workflow_execution(engine.get_execution())
            except Exception:
                pass
        return ctx.to_dict()

    def step_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Avanza un paso individual en el workflow."""
        engine = self.get_or_create_engine(workflow_id)
        bridge = self._active_bridges[workflow_id]
        step_nid = bridge.step()
        if self.persistence_store and hasattr(self.persistence_store, "save_workflow_execution"):
            try:
                self.persistence_store.save_workflow_execution(engine.get_execution())
            except Exception:
                pass
        return {
            "executed_node_id": step_nid,
            "status": engine.context.status.value,
            "context": engine.context.to_dict(),
        }

    def backtrack_workflow(self, workflow_id: str, target_node_id: str) -> Dict[str, Any]:
        """Aplica backtrack determinista hacia un nodo objetivo."""
        engine = self.get_or_create_engine(workflow_id)
        success = engine.backtrack_to_node(target_node_id)
        if self.persistence_store and hasattr(self.persistence_store, "save_workflow_execution"):
            try:
                self.persistence_store.save_workflow_execution(engine.get_execution())
            except Exception:
                pass
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

    def approve_node(
        self,
        workflow_id: str,
        node_id: str,
        approved: bool = True,
        comment: str = "",
    ) -> Dict[str, Any]:
        """Autoriza o rechaza la ejecución de un nodo en estado WAITING_APPROVAL."""
        engine = self.get_or_create_engine(workflow_id)
        success = engine.approve_node(node_id, approved=approved, comment=comment)
        if self.persistence_store and hasattr(self.persistence_store, "save_workflow_execution"):
            try:
                self.persistence_store.save_workflow_execution(engine.get_execution())
            except Exception:
                pass
        return {
            "success": success,
            "node_id": node_id,
            "approved": approved,
            "status": engine.context.status.value,
            "context": engine.context.to_dict(),
        }

    def list_templates(self) -> List[Dict[str, Any]]:
        """Lista las plantillas canónicas de flujos de trabajo disponibles."""
        result = []
        for key, factory in CANONICAL_TEMPLATES.items():
            wf = factory()
            result.append({
                "template_id": key,
                "name": wf.name,
                "description": wf.description,
                "node_count": len(wf.nodes),
                "edge_count": len(wf.edges),
            })
        return result

    def instantiate_template(self, template_key: str, name: Optional[str] = None) -> WorkflowDefinition:
        """Instancia una plantilla canónica y la registra como un nuevo workflow."""
        if template_key not in CANONICAL_TEMPLATES:
            raise ValueError(f"Plantilla '{template_key}' no reconocida. Disponibles: {list(CANONICAL_TEMPLATES.keys())}")
        wf = CANONICAL_TEMPLATES[template_key]()
        new_id = f"wf_{template_key}_{uuid.uuid4().hex[:8]}"
        wf_dump = wf.model_dump()
        wf_dump["workflow_id"] = new_id
        if name:
            wf_dump["name"] = name
        new_wf = WorkflowDefinition(**wf_dump)
        self.register_workflow(new_wf)
        return new_wf

