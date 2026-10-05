"""Puente Bidireccional entre el Motor de Workflows, Decision Tree y EventStore (F6-03).

Garantiza la trazabilidad unificada y la gobernanza física paso a paso de los workflows:
- WorkflowDecisionBridge:
    * Intercepción y evaluación previa de cada nodo mediante PolicyDecision y RiskAssessment.
    * Bloqueo físico de nodos no autorizados o que requieren aprobación humana (WAITING_APPROVAL).
    * Registro de eventos inmutables en tiempo real en el EventStore de PRAXEON.
    * Mapeo del avance del workflow al árbol de razonamiento (BranchPath y BranchStep).
    * Reconstrucción determinista del estado del flujo desde el log histórico de eventos (Event Replay).
"""

from datetime import datetime, timezone
import json
import time
from typing import Any, Callable, Dict, List, Optional
import uuid

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.assessment import RiskAssessment, RiskLevel
from praxeon.domain.branch import (
    BranchPath,
    BranchScore,
    BranchStatus,
    BranchStep,
    RollbackCheckpoint,
)
from praxeon.domain.decision import DecisionStatus, PolicyDecision
from praxeon.domain.events import EventType, RuntimeEvent, make_event
from praxeon.domain.observation import Observation
from praxeon.runtime.event_bus import EventBus, EventStore
from praxeon.workflows.engine import (
    WorkflowEngine,
    WorkflowExecutionContext,
    WorkflowStatus,
)
from praxeon.workflows.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowNode,
)


class WorkflowDecisionBridge:
    """Intermediario de gobernanza física y persistencia para la ejecución de flujos de trabajo."""

    def __init__(
        self,
        engine: WorkflowEngine,
        event_bus: Optional[EventBus] = None,
        policy_evaluator: Optional[Callable[[ActionCandidate, WorkflowNode], PolicyDecision]] = None,
        session_id: Optional[str] = None,
    ):
        self.engine = engine
        self.event_bus = event_bus or EventBus()
        self.session_id = session_id or f"wf_sess_{engine.workflow.workflow_id}_{int(time.time()*1000)%100000}"
        self.policy_evaluator = policy_evaluator

        # Árbol de razonamiento activo vinculado al workflow
        self.active_branch = BranchPath(
            branch_id=f"branch_{self.engine.workflow.workflow_id}",
            root_session_id=self.session_id,
            status=BranchStatus.ACTIVE,
            score=BranchScore(confidence=1.0, goal_alignment=1.0),
        )
        self.decision_history: List[PolicyDecision] = []
        self._sequence_counter: int = 0
        self._approved_nodes: Set[str] = set()

    def _next_sequence(self) -> int:
        self._sequence_counter += 1
        return self._sequence_counter

    def _publish_event(self, event_type: EventType, payload: Dict[str, Any]) -> None:
        """Publica un evento estructurado en el EventBus durable."""
        evt = make_event(
            event_type=event_type,
            session_id=self.session_id,
            sequence=self._next_sequence(),
            payload=payload,
        )
        self.event_bus.publish(evt)

    def evaluate_node_policy(self, node_id: str) -> PolicyDecision:
        """Evalúa las políticas de gobernanza sobre la acción que representa el nodo."""
        node = self.engine.workflow.nodes[node_id]

        tool_name = node.tool_name or (f"agent_delegate_{node.agent_id}" if node.agent_id else f"workflow_{node.node_type.value}")
        tool_call = ToolCall(tool_name=tool_name, arguments=node.inputs)
        action = ActionCandidate(
            id=f"act_{node_id}_{self._sequence_counter}",
            description=f"Ejecución de nodo de workflow '{node.name}' ({node.node_type.value})",
            tool_call=tool_call,
            rationale=f"Workflow step for node {node_id}",
        )

        # 1. Emitir ACTION_PROPOSED
        self._publish_event(
            EventType.ACTION_PROPOSED,
            {
                "workflow_id": self.engine.workflow.workflow_id,
                "node_id": node_id,
                "action_id": action.id,
                "tool_name": tool_name,
                "arguments": node.inputs,
            },
        )

        # 2. Si el nodo fue explícitamente aprobado por un humano, autorizarlo
        if node_id in self._approved_nodes:
            decision = PolicyDecision(
                status=DecisionStatus.ALLOW,
                reason_codes=["HUMAN_APPROVAL_GRANTED"],
                risk=RiskAssessment(level=RiskLevel.MEDIUM),
            )
        elif self.policy_evaluator:
            decision = self.policy_evaluator(action, node)
        else:
            # Evaluación por defecto basada en tipo y metadata
            if node.metadata.get("require_confirmation", False):
                decision = PolicyDecision(
                    status=DecisionStatus.BLOCK,
                    requires_confirmation=True,
                    reason_codes=["HUMAN_CONFIRMATION_REQUIRED"],
                    risk=RiskAssessment(level=RiskLevel.HIGH, requires_confirmation=True),
                )
            elif node.metadata.get("forbidden", False):
                decision = PolicyDecision(
                    status=DecisionStatus.BLOCK,
                    reason_codes=["WORKFLOW_NODE_FORBIDDEN_POLICY"],
                    risk=RiskAssessment(level=RiskLevel.CRITICAL),
                )
            else:
                decision = PolicyDecision(
                    status=DecisionStatus.ALLOW,
                    reason_codes=["POLICY_PASS_WORKFLOW_ALLOWED"],
                    risk=RiskAssessment(level=RiskLevel.LOW),
                )

        # 3. Emitir RISK_ASSESSED y POLICY_DECIDED
        risk_lvl = decision.risk.level.value if decision.risk else RiskLevel.LOW.value
        self._publish_event(
            EventType.RISK_ASSESSED,
            {"node_id": node_id, "risk_level": risk_lvl, "requires_confirmation": decision.requires_confirmation},
        )
        self._publish_event(
            EventType.POLICY_DECIDED,
            {"node_id": node_id, "status": decision.status.value, "reason_codes": decision.reason_codes},
        )

        self.decision_history.append(decision)
        return decision

    def step(self) -> Optional[str]:
        """Avanza la ejecución del workflow supervisado por gobernanza física."""
        if self.engine.context.status == WorkflowStatus.IDLE:
            self.engine.start()
            self._publish_event(
                EventType.SESSION_STARTED,
                {"workflow_id": self.engine.workflow.workflow_id, "status": "started"},
            )

        next_nid = self.engine.find_next_ready_node()
        if not next_nid:
            end_nodes = self.engine.workflow.get_end_nodes()
            if any(self.engine.context.node_states.get(e.node_id) == NodeStatus.COMPLETED for e in end_nodes):
                self.engine.context.status = WorkflowStatus.COMPLETED
                self._publish_event(
                    EventType.SESSION_COMPLETED,
                    {"workflow_id": self.engine.workflow.workflow_id, "status": "completed"},
                )
            return None

        # Evaluación de políticas
        decision = self.evaluate_node_policy(next_nid)

        if decision.status == DecisionStatus.BLOCK:
            node = self.engine.workflow.nodes[next_nid]
            if decision.requires_confirmation:
                self.engine.context.node_states[next_nid] = NodeStatus.WAITING_APPROVAL
                self.engine.context.status = WorkflowStatus.PAUSED
                self._publish_event(
                    EventType.APPROVAL_REQUESTED,
                    {"node_id": next_nid, "reason": "Requiere aprobación humana"},
                )
                return next_nid
            else:
                self.engine.context.node_states[next_nid] = NodeStatus.FAILED
                self.engine.context.status = WorkflowStatus.FAILED
                self.engine.context.error_message = f"Acción bloqueada por política: {decision.reason_codes}"
                self._publish_event(
                    EventType.DECISION_PRUNED,
                    {"node_id": next_nid, "reason": "Acción bloqueada por política"},
                )
                return next_nid

        # Ejecución física en el motor
        node = self.engine.workflow.nodes[next_nid]
        self._publish_event(
            EventType.EXECUTION_STARTED,
            {"node_id": next_nid, "node_name": node.name, "node_type": node.node_type.value},
        )

        success = self.engine.execute_node(next_nid)
        node_output = self.engine.context.node_outputs.get(next_nid, {})

        # Registrar en el Decision Tree (BranchStep)
        step_rec = BranchStep(
            step_id=f"step_{next_nid}_{len(self.active_branch.steps) + 1}",
            action=ActionCandidate(
                id=f"act_{next_nid}",
                description=f"Paso de workflow para nodo '{node.name}'",
                tool_call=ToolCall(tool_name=node.tool_name or f"node_{node.node_type.value}", arguments=node.inputs),
            ),
            decision=decision,
            observation=json.dumps(node_output),
            is_speculative=False,
        )
        self.active_branch.steps.append(step_rec)

        if success:
            self._publish_event(
                EventType.EXECUTION_COMPLETED,
                {"node_id": next_nid, "output": node_output},
            )
            self._publish_event(
                EventType.OBSERVATION_RECORDED,
                {"node_id": next_nid, "success": True, "output": node_output},
            )
            if node.node_type == NodeType.END:
                self.active_branch.status = BranchStatus.SUCCEEDED
                self._publish_event(
                    EventType.SESSION_COMPLETED,
                    {"workflow_id": self.engine.workflow.workflow_id, "status": "completed"},
                )
        else:
            self._publish_event(
                EventType.DECISION_PRUNED,
                {"node_id": next_nid, "error": self.engine.context.error_message},
            )

        return next_nid

    def run_to_completion(self, max_steps: int = 100) -> WorkflowExecutionContext:
        """Ejecuta iterativamente con gobernanza hasta finalizar o pausar."""
        steps = 0
        while self.engine.context.status in (WorkflowStatus.IDLE, WorkflowStatus.RUNNING) and steps < max_steps:
            executed = self.step()
            steps += 1
            if not executed or self.engine.context.status in (WorkflowStatus.PAUSED, WorkflowStatus.FAILED):
                break
        return self.engine.context

    def approve_node(self, node_id: str) -> bool:
        """Aprueba un nodo que estaba en WAITING_APPROVAL para que prosiga su ejecución."""
        if self.engine.context.node_states.get(node_id) == NodeStatus.WAITING_APPROVAL:
            self._approved_nodes.add(node_id)
            self.engine.context.node_states[node_id] = NodeStatus.READY
            self.engine.context.status = WorkflowStatus.RUNNING
            self._publish_event(
                EventType.APPROVAL_COMPLETED,
                {"node_id": node_id, "approved": True},
            )
            return True
        return False

    @classmethod
    def replay_from_event_store(
        cls,
        session_id: str,
        workflow: WorkflowDefinition,
        event_store: EventStore,
    ) -> WorkflowExecutionContext:
        """Reconstruye el estado exacto de ejecución de un workflow a partir del log inmutable de eventos."""
        events = event_store.get_events(session_id=session_id)
        context = WorkflowExecutionContext(workflow=workflow, execution_id=session_id)

        for evt in events:
            p = evt.payload
            if evt.type == EventType.SESSION_STARTED.value:
                context.status = WorkflowStatus.RUNNING
                context.started_at = evt.timestamp
            elif evt.type == EventType.EXECUTION_COMPLETED.value:
                nid = p.get("node_id")
                if nid and nid in context.node_states:
                    context.node_states[nid] = NodeStatus.COMPLETED
                    context.node_outputs[nid] = p.get("output", {})
                    if nid not in context.execution_history:
                        context.execution_history.append(nid)
            elif evt.type == EventType.APPROVAL_REQUESTED.value:
                nid = p.get("node_id")
                if nid:
                    context.node_states[nid] = NodeStatus.WAITING_APPROVAL
                    context.status = WorkflowStatus.PAUSED
            elif evt.type == EventType.DECISION_PRUNED.value:
                nid = p.get("node_id")
                if nid:
                    context.node_states[nid] = NodeStatus.FAILED
                    context.status = WorkflowStatus.FAILED
            elif evt.type == EventType.SESSION_COMPLETED.value:
                context.status = WorkflowStatus.COMPLETED
                context.finished_at = evt.timestamp

        return context
