"""Grafo dirigido de estados cognitivos (Trajectory DAG) implementado con NetworkX."""

from typing import Any, Dict, List, Optional
import networkx as nx

from praxeon.config import PraxeonConfig, default_config
from praxeon.models.schema import Step, StepType, Trajectory


class StateGraph:
    """Grafo de estados de razonamiento para modelar la trayectoria del LLM."""

    def __init__(self, config: Optional[PraxeonConfig] = None):
        self.config = config or default_config
        self.graph: nx.DiGraph = nx.DiGraph()
        self.goal: str = ""
        self.session_id: str = ""
        self._chronological_nodes: List[str] = []

    def load_trajectory(self, trajectory: Trajectory) -> None:
        """Carga una trayectoria completa en el grafo de estados."""
        self.session_id = trajectory.session_id
        self.goal = trajectory.goal

        for step in trajectory.steps:
            self.add_step(step)

    def add_step(self, step: Step, **kwargs: Any) -> str:
        """Agrega un nuevo paso cognitivo al grafo en tiempo real."""
        step_id = step.id

        # Agregar nodo con atributos estructurados
        self.graph.add_node(
            step_id,
            step=step,
            step_type=step.step_type.value,
            content=step.content,
            tool_name=step.tool_name,
            tool_args=step.tool_args,
            semantic_hash=step.semantic_hash,
            timestamp=step.timestamp,
            jev_score=None,
        )

        # Arista cronológica con el paso anterior
        if self._chronological_nodes:
            prev_id = self._chronological_nodes[-1]
            self.graph.add_edge(prev_id, step_id, edge_type="temporal", weight=1.0)

        # Arista de árbol si tiene un parent_id específico
        if step.parent_id and step.parent_id in self.graph and step.parent_id != (self._chronological_nodes[-1] if self._chronological_nodes else None):
            self.graph.add_edge(step.parent_id, step_id, edge_type="branch_parent", weight=1.0)

        self._chronological_nodes.append(step_id)
        return step_id

    def remove_step(self, step_id: str) -> None:
        """Elimina un paso del grafo y de la lista cronológica."""
        if self.graph.has_node(step_id):
            self.graph.remove_node(step_id)
        if step_id in self._chronological_nodes:
            self._chronological_nodes.remove(step_id)

    def get_step(self, step_id: str) -> Optional[Step]:
        """Obtiene el objeto Step de un nodo."""
        if step_id in self.graph:
            return self.graph.nodes[step_id].get("step")
        return None

    def get_all_steps(self) -> List[Step]:
        """Devuelve todos los pasos en orden cronológico."""
        return [self.graph.nodes[nid]["step"] for nid in self._chronological_nodes if nid in self.graph]

    def get_recent_steps(self, k: int = 5) -> List[Step]:
        """Devuelve los últimos k pasos ejecutados."""
        recent_ids = self._chronological_nodes[-k:]
        return [self.graph.nodes[nid]["step"] for nid in recent_ids if nid in self.graph]

    def get_chronological_nodes(self) -> List[str]:
        """Lista de IDs de nodos en orden cronológico."""
        return list(self._chronological_nodes)

    def get_ancestors(self, step_id: str) -> List[str]:
        """Devuelve todos los ancestros de un paso a través de aristas temporales o de rama."""
        if step_id not in self.graph:
            return []
        return list(nx.ancestors(self.graph, step_id))

    def set_step_jev(self, step_id: str, jev_score: float) -> None:
        """Asigna el score JEV calculado a un nodo."""
        if step_id in self.graph:
            self.graph.nodes[step_id]["jev_score"] = jev_score
            self.graph.nodes[step_id]["step"].metadata["jev_score"] = jev_score

    def get_highest_jev_step(self) -> Optional[Step]:
        """Encuentra el paso previo con mayor puntuación JEV (para backtracking)."""
        best_step = None
        best_score = -float("inf")
        for nid in self._chronological_nodes:
            score = self.graph.nodes[nid].get("jev_score")
            if score is not None and score > best_score:
                best_score = score
                best_step = self.graph.nodes[nid]["step"]
        return best_step

    @property
    def nodes_count(self) -> int:
        return self.graph.number_of_nodes()

    @property
    def edges_count(self) -> int:
        return self.graph.number_of_edges()
