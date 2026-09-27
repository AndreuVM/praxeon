"""Modelo de datos del Decision Tree (praxeon/domain/tree.py).

Especificación PRAXEON 1.0 (Sección 5).
Define los nodos del árbol de decisiones (TreeNode), las aristas (TreeEdge)
y la estructura consolidada del árbol (DecisionTree) derivada deterministamente
del stream de eventos del runtime.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, computed_field


class NodeKind(str, Enum):
    """Categorías funcionales de nodo en el árbol de decisión."""
    GOAL = "GOAL"
    REASONING = "REASONING"
    ACTION = "ACTION"
    TOOL = "TOOL"
    POLICY = "POLICY"
    APPROVAL = "APPROVAL"
    EXECUTION = "EXECUTION"
    OBSERVATION = "OBSERVATION"


class NodeStatus(str, Enum):
    """Estados canónicos observables de un nodo."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    ALLOW = "ALLOW"
    BLOCKED = "BLOCKED"
    REVIEW = "REVIEW"
    WAITING = "WAITING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PRUNED = "PRUNED"


class NodeActor(str, Enum):
    """Agente o subsistema responsable del nodo."""
    LLM = "LLM"
    PROVIDER = "provider"
    POLICY = "policy"
    TOOL = "tool"
    HUMAN = "human"


class TreeNode(BaseModel):
    """Nodo individual dentro del Decision Tree (Sección 5)."""
    model_config = ConfigDict(frozen=True)

    node_id: str
    session_id: str
    parent_id: Optional[str] = None
    kind: NodeKind
    status: NodeStatus = NodeStatus.PENDING
    label: str
    actor: str = "LLM"
    provider: Optional[str] = None
    decision_id: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialización estructurada para API y frontend."""
        return {
            "node_id": self.node_id,
            "session_id": self.session_id,
            "parent_id": self.parent_id,
            "kind": self.kind.value if isinstance(self.kind, NodeKind) else str(self.kind),
            "status": self.status.value if isinstance(self.status, NodeStatus) else str(self.status),
            "label": self.label,
            "actor": self.actor,
            "provider": self.provider,
            "decision_id": self.decision_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "metadata": self.metadata,
        }


class TreeEdge(BaseModel):
    """Conexión dirigida entre dos nodos del Decision Tree."""
    model_config = ConfigDict(frozen=True)

    source_id: str
    target_id: str
    label: Optional[str] = None
    edge_type: str = "sequence"  # "sequence", "branch", "policy", "execution"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "label": self.label,
            "edge_type": self.edge_type,
        }


class DecisionTree(BaseModel):
    """Estructura completa de árbol para una sesión de supervisión."""
    session_id: str
    root_id: Optional[str] = None
    nodes: Dict[str, TreeNode] = Field(default_factory=dict)
    edges: List[TreeEdge] = Field(default_factory=list)

    @computed_field
    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @computed_field
    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def add_node(self, node: TreeNode) -> None:
        """Añade o actualiza un nodo en el árbol."""
        self.nodes[node.node_id] = node
        if self.root_id is None and node.parent_id is None:
            self.root_id = node.node_id

    def update_node(self, node_id: str, **updates: Any) -> Optional[TreeNode]:
        """Actualiza campos específicos de un nodo existente conservando inmutabilidad interna."""
        if node_id not in self.nodes:
            return None
        existing = self.nodes[node_id]
        new_data = existing.model_dump()
        for k, v in updates.items():
            if k == "metadata" and "metadata" in new_data:
                # Merge profundo de metadata
                new_data["metadata"] = {**new_data["metadata"], **v}
            else:
                new_data[k] = v
        updated = TreeNode(**new_data)
        self.nodes[node_id] = updated
        return updated

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        label: Optional[str] = None,
        edge_type: str = "sequence",
    ) -> None:
        """Añade una arista si no existe ya entre esos nodos."""
        for edge in self.edges:
            if edge.source_id == source_id and edge.target_id == target_id:
                return
        self.edges.append(TreeEdge(source_id=source_id, target_id=target_id, label=label, edge_type=edge_type))

    def get_node(self, node_id: str) -> Optional[TreeNode]:
        return self.nodes.get(node_id)

    def get_children(self, node_id: str) -> List[TreeNode]:
        return [node for node in self.nodes.values() if node.parent_id == node_id]

    def to_dict(self) -> Dict[str, Any]:
        """Estructura completa formateada para el canvas de React Flow o Cytoscape."""
        return {
            "session_id": self.session_id,
            "root_id": self.root_id,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
        }
