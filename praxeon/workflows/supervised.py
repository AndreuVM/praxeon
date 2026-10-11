"""Ejecución supervisada de nodos TASK de workflows (W-02).

Cada herramienta de un workflow recorre la misma cadena que el resto de PRAXEON:

    ActionCandidate -> PolicyEngine -> DecisionReceipt (HMAC) -> SecureExecutor -> Sandbox

Sin esa cadena no se ejecuta nada: una herramienta desconocida, de riesgo alto o que requiera
confirmación humana se detiene (WorkflowApprovalRequired) y solo continúa tras una aprobación
explícita y auditable; un BLOCK/REPLAN falla el nodo.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import threading

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.decision import DecisionStatus
from praxeon.domain.goal import Goal
from praxeon.policy.engine import PolicyEngine
from praxeon.runtime.executor import SecureExecutor
from praxeon.runtime.state import SessionState


class WorkflowApprovalRequired(Exception):
    """La política exige confirmación humana antes de ejecutar la herramienta del nodo."""

    def __init__(self, action_id: str, tool_name: str, reason_codes: List[str]):
        self.action_id = action_id
        self.tool_name = tool_name
        self.reason_codes = list(reason_codes)
        super().__init__(
            f"La herramienta '{tool_name}' requiere confirmación humana ({', '.join(self.reason_codes)})."
        )


class WorkflowToolBlocked(Exception):
    """La política bloqueó o exigió replanificar la herramienta del nodo."""


class SupervisedToolRunner:
    """Ejecuta herramientas de nodos TASK bajo PolicyEngine, capability HMAC y SecureExecutor."""

    def __init__(
        self,
        policy_engine: Optional[PolicyEngine] = None,
        executor: Optional[SecureExecutor] = None,
        execution_mode: str = "local_restricted",
        workspace_root: Optional[str] = None,
    ):
        self.policy_engine = policy_engine or PolicyEngine()
        self.executor = executor or SecureExecutor(secret_key=self.policy_engine.secret_key)
        if self.executor.secret_key is None:
            self.executor.secret_key = self.policy_engine.secret_key
        self.execution_mode = execution_mode
        self.workspace_root = workspace_root
        self._states: Dict[str, SessionState] = {}
        self._lock = threading.Lock()

    @staticmethod
    def action_id_for(execution_id: str, node_id: str) -> str:
        return f"wf:{execution_id}:{node_id}"

    def _state_for(self, execution_id: str, workflow_id: str) -> SessionState:
        with self._lock:
            state = self._states.get(execution_id)
            if state is None:
                metadata: Dict[str, Any] = {"execution_mode": self.execution_mode}
                if self.workspace_root:
                    metadata["workspace_root"] = self.workspace_root
                state = SessionState(
                    session_id=execution_id,
                    goal=Goal(objective=f"Ejecución supervisada del workflow '{workflow_id}'"),
                    metadata=metadata,
                )
                self._states[execution_id] = state
            return state

    def _build_action(self, execution_id: str, node_id: str, tool_name: str, arguments: Dict[str, Any]) -> ActionCandidate:
        return ActionCandidate(
            id=self.action_id_for(execution_id, node_id),
            description=f"Nodo de workflow '{node_id}' invoca '{tool_name}'",
            tool_call=ToolCall(tool_name=tool_name, arguments=dict(arguments)),
            rationale=f"Workflow node {node_id}",
        )

    def confirm(self, execution_id: str, node_id: str, tool_name: str, arguments: Dict[str, Any],
                approver_id: str = "operator", reason: Optional[str] = None) -> None:
        """Registra la confirmación humana (ticket auditable) de esa acción concreta."""
        from praxeon.domain.action import compute_action_hash
        action = self._build_action(execution_id, node_id, tool_name, arguments)
        self.policy_engine.permission_manager.confirm_action(
            action_id=action.id,
            action_hash=compute_action_hash(action),
            approver_id=approver_id,
            reason=reason,
        )

    def run(self, execution_id: str, workflow_id: str, node_id: str, tool_name: str,
            arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Evalúa, autoriza y ejecuta la herramienta; lanza si la política no la permite."""
        state = self._state_for(execution_id, workflow_id)
        action = self._build_action(execution_id, node_id, tool_name, arguments)
        decision, receipt = self.policy_engine.evaluate_action(
            action,
            state.to_snapshot(),
            session_id=state.session_id,
            forbidden_tools=set(state.forbidden_tools),
            execution_mode=self.execution_mode,
        )
        if decision.status == DecisionStatus.ABSTAIN:
            raise WorkflowApprovalRequired(action.id, tool_name, decision.reason_codes)
        if decision.status != DecisionStatus.ALLOW:
            raise WorkflowToolBlocked(
                f"Herramienta '{tool_name}' {decision.status.value.upper()} por política: {', '.join(decision.reason_codes)}"
            )
        observation = self.executor.execute(action, state, receipt=receipt, decision=decision)
        if not observation.success:
            raise RuntimeError(f"La herramienta '{tool_name}' falló: {observation.output}")
        return {
            "tool": tool_name,
            "output": observation.output,
            "success": True,
            "decision_id": receipt.decision_id,
            "supervised": True,
        }
