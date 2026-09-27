"""Reducer determinista del Decision Tree para PRAXEON 1.0 (praxeon/runtime/tree_reducer.py).

Especificación PRAXEON 1.0 (Sección 4.3 y Sección 5).
Deriva deterministamente el estado completo y estructurado del DecisionTree
a partir de un stream ordenado de RuntimeEvent.
Soporta tanto actualización continua en tiempo real (Live) como reproducción
histórica exacta (Replay).
"""

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from praxeon.domain.events import EventType, RuntimeEvent
from praxeon.domain.tree import (
    DecisionTree,
    NodeActor,
    NodeKind,
    NodeStatus,
    TreeEdge,
    TreeNode,
)


def _map_policy_status(status_str: Optional[str]) -> NodeStatus:
    if not status_str:
        return NodeStatus.PENDING
    s = status_str.upper()
    if "ALLOW" in s:
        return NodeStatus.ALLOW
    if "BLOCK" in s:
        return NodeStatus.BLOCKED
    if "REVIEW" in s:
        return NodeStatus.REVIEW
    if "WAIT" in s:
        return NodeStatus.WAITING
    if "PRUNE" in s:
        return NodeStatus.PRUNED
    return NodeStatus.PENDING


class TreeReducer:
    """Reducer reactivo y reproducible para construir el DecisionTree."""

    def __init__(self, session_id: str, initial_tree: Optional[DecisionTree] = None):
        self.session_id = session_id
        self.tree = initial_tree or DecisionTree(session_id=session_id)
        self._last_step_node_id: Optional[str] = None
        self._root_created: bool = False

    def get_tree(self) -> DecisionTree:
        return self.tree

    def apply_event(self, event: RuntimeEvent) -> DecisionTree:
        """Aplica un evento individual al árbol de forma determinista."""
        if isinstance(event.type, EventType):
            ev_type = event.type
        else:
            try:
                ev_type = EventType(event.type)
            except Exception:
                ev_type = str(event.type)

        if ev_type == EventType.SESSION_STARTED:
            self._handle_session_started(event)
        elif ev_type == EventType.GOAL_CREATED:
            self._handle_goal_created(event)
        elif ev_type == EventType.ACTION_PROPOSED:
            self._handle_action_proposed(event)
        elif ev_type == EventType.EVIDENCE_EVALUATED:
            self._handle_evidence_evaluated(event)
        elif ev_type == EventType.RISK_ASSESSED:
            self._handle_risk_assessed(event)
        elif ev_type == EventType.PROVIDER_EVALUATED:
            self._handle_provider_evaluated(event)
        elif ev_type == EventType.POLICY_DECIDED:
            self._handle_policy_decided(event)
        elif ev_type == EventType.CAPABILITY_ISSUED:
            self._handle_capability_issued(event)
        elif ev_type == EventType.EXECUTION_STARTED:
            self._handle_execution_started(event)
        elif ev_type == EventType.EXECUTION_COMPLETED:
            self._handle_execution_completed(event)
        elif ev_type == EventType.OBSERVATION_RECORDED:
            self._handle_observation_recorded(event)
        elif ev_type == EventType.APPROVAL_REQUESTED:
            self._handle_approval_requested(event)
        elif ev_type == EventType.APPROVAL_COMPLETED:
            self._handle_approval_completed(event)
        elif ev_type == EventType.DECISION_PRUNED:
            self._handle_decision_pruned(event)
        elif ev_type == EventType.INTERVENTION_APPLIED or str(ev_type) == "intervention.applied":
            self._handle_intervention_applied(event)
        elif ev_type == EventType.SESSION_COMPLETED:
            self._handle_session_completed(event)

        return self.tree

    # --- Handlers específicos por tipo de evento ---

    def _handle_session_started(self, event: RuntimeEvent) -> None:
        node_id = event.node_id or f"root_{event.session_id}"
        if node_id not in self.tree.nodes:
            root_node = TreeNode(
                node_id=node_id,
                session_id=event.session_id,
                parent_id=None,
                kind=NodeKind.GOAL,
                status=NodeStatus.SUCCESS,
                label=event.payload.get("label", "Start"),
                actor="system",
                started_at=event.timestamp,
                ended_at=event.timestamp,
                metadata=event.payload,
            )
            self.tree.add_node(root_node)
            self._root_created = True
            self._last_step_node_id = node_id

    def _handle_goal_created(self, event: RuntimeEvent) -> None:
        goal = event.payload.get("goal", "")
        # Si el root era "Start", enriquecemos su metadata o actualizamos el objetivo
        if self.tree.root_id and self.tree.root_id in self.tree.nodes:
            self.tree.update_node(
                self.tree.root_id,
                metadata={"goal": goal},
            )

    def _handle_action_proposed(self, event: RuntimeEvent) -> None:
        node_id = event.node_id or f"node_{event.payload.get('action_id', event.sequence)}"
        parent_id = event.parent_id or event.payload.get("parent_id") or self._last_step_node_id or self.tree.root_id

        # Determinar etiqueta amigable adaptada a la demo
        tool = event.payload.get("tool") or ""
        op = event.payload.get("operation") or event.payload.get("description") or ""
        label = event.payload.get("label") or (f"{tool} {op}".strip() if tool or op else "Proposed action")

        node = TreeNode(
            node_id=node_id,
            session_id=event.session_id,
            parent_id=parent_id,
            kind=NodeKind.ACTION,
            status=NodeStatus.PENDING,
            label=label,
            actor=event.payload.get("source", "LLM"),
            provider=event.payload.get("provider"),
            decision_id=event.decision_id,
            started_at=event.timestamp,
            metadata=event.payload,
        )
        self.tree.add_node(node)
        if parent_id and parent_id != node_id:
            # Detectar si el nodo padre ya tiene otros hijos (bifurcación en el árbol)
            existing_children = [
                edge.target_id for edge in self.tree.edges
                if edge.source_id == parent_id
            ]
            edge_type = "branch" if len(existing_children) > 0 else "sequence"
            self.tree.add_edge(parent_id, node_id, edge_type=edge_type)

        self._last_step_node_id = node_id

    def _handle_evidence_evaluated(self, event: RuntimeEvent) -> None:
        target_id = event.node_id or self._last_step_node_id
        if target_id and target_id in self.tree.nodes:
            self.tree.update_node(target_id, metadata={"evidence": event.payload})

    def _handle_risk_assessed(self, event: RuntimeEvent) -> None:
        target_id = event.node_id or self._last_step_node_id
        if target_id and target_id in self.tree.nodes:
            self.tree.update_node(target_id, metadata={"risk": event.payload})

    def _handle_provider_evaluated(self, event: RuntimeEvent) -> None:
        target_id = event.node_id or self._last_step_node_id
        if target_id and target_id in self.tree.nodes:
            existing = self.tree.nodes[target_id].metadata.get("providers", [])
            new_list = list(existing) if isinstance(existing, list) else []
            new_list.append(event.payload)
            self.tree.update_node(
                target_id,
                metadata={"providers": new_list},
                provider=event.payload.get("provider_name") or self.tree.nodes[target_id].provider,
            )

    def _handle_policy_decided(self, event: RuntimeEvent) -> None:
        status_raw = event.payload.get("status")
        mapped_status = _map_policy_status(status_raw)

        # Si el evento especifica un node_id nuevo con un parent_id diferente, representa una rama de policy
        if event.node_id and event.parent_id and event.node_id != event.parent_id:
            # Nodo de rama
            label = event.payload.get("reason_code") or f"Policy: {status_raw}"
            branch_node = TreeNode(
                node_id=event.node_id,
                session_id=event.session_id,
                parent_id=event.parent_id,
                kind=NodeKind.POLICY,
                status=mapped_status,
                label=label,
                actor="policy",
                decision_id=event.decision_id,
                started_at=event.timestamp,
                metadata=event.payload,
            )
            self.tree.add_node(branch_node)
            self.tree.add_edge(event.parent_id, event.node_id, edge_type="policy")
        else:
            # Actualización del nodo en curso
            target_id = event.node_id or self._last_step_node_id
            if target_id and target_id in self.tree.nodes:
                self.tree.update_node(
                    target_id,
                    status=mapped_status,
                    metadata={"policy": event.payload},
                )

    def _handle_capability_issued(self, event: RuntimeEvent) -> None:
        target_id = event.node_id or self._last_step_node_id
        if target_id and target_id in self.tree.nodes:
            self.tree.update_node(
                target_id,
                metadata={"capability": event.payload},
            )

    def _handle_execution_started(self, event: RuntimeEvent) -> None:
        target_id = event.node_id or self._last_step_node_id
        if target_id and target_id in self.tree.nodes:
            self.tree.update_node(
                target_id,
                status=NodeStatus.RUNNING,
                started_at=event.timestamp,
            )

    def _handle_execution_completed(self, event: RuntimeEvent) -> None:
        target_id = event.node_id or self._last_step_node_id
        success = event.payload.get("success", True)
        if target_id and target_id in self.tree.nodes:
            self.tree.update_node(
                target_id,
                status=NodeStatus.SUCCESS if success else NodeStatus.FAILED,
                ended_at=event.timestamp,
                metadata={"execution": event.payload},
            )
            # Si la ejecución falló, retroceder al padre para bifurcar en el siguiente paso
            if not success:
                pid = self.tree.nodes[target_id].parent_id
                if pid:
                    self._last_step_node_id = pid

    def _handle_observation_recorded(self, event: RuntimeEvent) -> None:
        target_id = event.node_id or self._last_step_node_id
        if target_id and target_id in self.tree.nodes:
            self.tree.update_node(
                target_id,
                metadata={"observation": event.payload},
            )

    def _handle_approval_requested(self, event: RuntimeEvent) -> None:
        target_id = event.node_id or self._last_step_node_id
        if target_id and target_id in self.tree.nodes:
            self.tree.update_node(
                target_id,
                status=NodeStatus.WAITING,
                metadata={"approval_request": event.payload},
            )

    def _handle_approval_completed(self, event: RuntimeEvent) -> None:
        target_id = event.node_id or self._last_step_node_id
        approved = event.payload.get("approved", True)
        if target_id and target_id in self.tree.nodes:
            self.tree.update_node(
                target_id,
                status=NodeStatus.ALLOW if approved else NodeStatus.BLOCKED,
                metadata={"approval_completed": event.payload},
            )
            if not approved:
                pid = self.tree.nodes[target_id].parent_id
                if pid:
                    self._last_step_node_id = pid

    def _handle_decision_pruned(self, event: RuntimeEvent) -> None:
        target_id = event.node_id or self._last_step_node_id
        if target_id and target_id in self.tree.nodes:
            self.tree.update_node(
                target_id,
                status=NodeStatus.PRUNED,
                metadata={"prune_reason": event.payload.get("reason")},
            )
            # En caso de poda, retroceder al padre del nodo podado para explorar rama alternativa
            pid = self.tree.nodes[target_id].parent_id
            if pid:
                self._last_step_node_id = pid

    def _handle_intervention_applied(self, event: RuntimeEvent) -> None:
        target_id = event.node_id or self._last_step_node_id
        backtrack_to = event.payload.get("backtrack_to")
        if target_id and target_id in self.tree.nodes:
            self.tree.update_node(
                target_id,
                metadata={"intervention": event.payload},
            )
        if backtrack_to and backtrack_to in self.tree.nodes:
            self._last_step_node_id = backtrack_to

    def _handle_session_completed(self, event: RuntimeEvent) -> None:
        # Registrar finalización en metadata del árbol
        if self.tree.root_id and self.tree.root_id in self.tree.nodes:
            self.tree.update_node(
                self.tree.root_id,
                metadata={"session_completed": event.payload},
            )


def reduce_events_to_tree(
    events: Iterable[RuntimeEvent],
    session_id: Optional[str] = None,
    initial_tree: Optional[DecisionTree] = None,
) -> DecisionTree:
    """Función pura para reducir una colección iterable de eventos a un DecisionTree."""
    ev_list = list(events)
    sid = session_id or (ev_list[0].session_id if ev_list else "unknown_session")
    reducer = TreeReducer(session_id=sid, initial_tree=initial_tree)
    for ev in ev_list:
        reducer.apply_event(ev)
    return reducer.get_tree()
