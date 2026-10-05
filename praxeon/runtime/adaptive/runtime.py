"""Adaptive Agent Runtime Holístico (Fase 8 - F8-01).

Ensambla e integra de extremo a extremo todos los subsistemas de PRAXEON:
1. TaskClassifier: Análisis multidimensional y especificación formal TaskRequirement.
2. AgentRouter: Selección óptima multicriterio (Reglas, Coste, Semántica, Adaptativa).
3. DynamicEscalationEngine: Escalado supervisado de modelos y roles especialistas.
4. ContextManager: Contexto mínimo suficiente, caché multinivel y poda de ramas.
5. StepLevelAdaptiveDispatcher: Autorización dinámica por paso ("Semántica != Autoridad").
6. TrajectoryController: Supervisión adaptativa en vivo (CONTINUE, PRUNE, RECOVER, ESCALATE).
7. AgentMessageBus: Despacho y comunicación coordinada en buzones formales.
8. EventStore / Telemetría: Registro inmutable de auditoría y métricas de ejecución.
"""

from datetime import datetime, timezone
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

from praxeon.agents.bus import AgentMessageBus
from praxeon.agents.definition import AgentDefinition, AgentStatus
from praxeon.agents.registry import AgentRegistry
from praxeon.agents.templates import AgentTemplateCatalog
from praxeon.context.entities import ContextItem
from praxeon.context.fragments import FragmentType
from praxeon.context.manager import ContextManager
from praxeon.domain.assessment import RiskLevel
from praxeon.routing.classifier import TaskClassifier
from praxeon.routing.escalation import DynamicEscalationEngine, EscalationContext, EscalationTriggerType
from praxeon.routing.models import RoutingDecision, RoutingStrategyType, TaskRequirement
from praxeon.routing.router import AgentRouter
from praxeon.runtime.adaptive.models import (
    AdaptiveExecutionSummary,
    AdaptiveSessionState,
    StepDispatchSpec,
    TrajectoryDirective,
    TrajectoryStepRecord,
)
from praxeon.runtime.adaptive.step_dispatcher import StepLevelAdaptiveDispatcher
from praxeon.runtime.adaptive.trajectory_controller import TrajectoryController
from praxeon.runtime.event_bus import EventStore
from praxeon.workflows.models import WorkflowDefinition


class AdaptiveAgentRuntime:
    """Runtime adaptativo unificado para orquestación, gobernanza y supervisión de agentes."""

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        router: Optional[AgentRouter] = None,
        context_manager: Optional[ContextManager] = None,
        bus: Optional[AgentMessageBus] = None,
        event_store: Optional[EventStore] = None,
        step_dispatcher: Optional[StepLevelAdaptiveDispatcher] = None,
        trajectory_controller: Optional[TrajectoryController] = None,
        escalation_engine: Optional[DynamicEscalationEngine] = None,
    ):
        # 1. Registro canónico de agentes
        self.registry = registry or AgentRegistry()
        if not self.registry.list_all():
            # Registrar catálogo por defecto si el registro viene vacío
            for tpl in ["developer", "security_auditor", "researcher", "writer", "code_reviewer"]:
                try:
                    self.registry.register(AgentTemplateCatalog.instantiate(tpl, f"ag_{tpl}"))
                except Exception:
                    pass

        # 2. Componentes centrales de enrutamiento y supervisión
        self.router = router or AgentRouter(registry=self.registry)
        self.classifier = TaskClassifier()
        self.escalation_engine = escalation_engine or DynamicEscalationEngine(router=self.router)
        self.context_manager = context_manager or ContextManager()
        self.bus = bus or AgentMessageBus()
        self.event_store = event_store or EventStore()

        # 3. Componentes adaptativos de paso y trayectoria
        self.step_dispatcher = step_dispatcher or StepLevelAdaptiveDispatcher(
            context_manager=self.context_manager
        )
        self.trajectory_controller = trajectory_controller or TrajectoryController(
            escalation_engine=self.escalation_engine
        )

        # 4. Estado de sesiones activas en memoria
        self._sessions: Dict[str, AdaptiveSessionState] = {}
        self._step_history: Dict[str, List[TrajectoryStepRecord]] = {}

    def initialize_session(
        self,
        session_id: str,
        goal: str,
        primary_agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AdaptiveSessionState:
        """Inicializa una sesión adaptativa y siembra la meta en el ContextManager."""
        agent_id = primary_agent_id or "ag_developer"
        now = datetime.now(timezone.utc)

        # Sembrar meta en el gestor de contexto
        goal_item = ContextItem.create(
            item_id=f"goal_{session_id[:8]}",
            item_type=FragmentType.GOAL,
            source_id=session_id,
            content=f"OBJETIVO: {goal}",
        )
        self.context_manager.add_item(goal_item)

        state = AdaptiveSessionState(
            session_id=session_id,
            goal=goal,
            status="INITIALIZED",
            primary_agent_id=agent_id,
            escalation_level=0,
            step_count=0,
            total_cost=0.0,
            total_tokens=0,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        self._sessions[session_id] = state
        self._step_history[session_id] = []
        return state

    def execute_task(
        self,
        prompt: str,
        session_id: Optional[str] = None,
        max_steps: int = 8,
        target_files: Optional[List[str]] = None,
        routing_strategy: Optional[RoutingStrategyType] = None,
        custom_step_executor: Optional[Callable[[StepDispatchSpec, int], Tuple[Dict[str, Any], str]]] = None,
    ) -> AdaptiveExecutionSummary:
        """Ejecuta una tarea de extremo a extremo mediante el ciclo adaptativo unificado."""
        start_time = time.time()
        sid = session_id or f"sess_{uuid.uuid4().hex[:8]}"

        # 1. Clasificación multidimensional de la tarea
        task_req = self.classifier.classify(
            prompt=prompt,
            target_files=target_files,
        )

        # 2. Enrutamiento inteligente al agente óptimo inicial
        initial_decision = self.router.route(
            task=task_req,
            strategy=routing_strategy or RoutingStrategyType.ADAPTIVE,
        )
        active_agent = self.registry.get(initial_decision.selected_agent_id)
        if not active_agent:
            active_agent = self.registry.list_all()[0]

        # 3. Inicializar o recuperar sesión
        if sid not in self._sessions:
            self.initialize_session(session_id=sid, goal=prompt, primary_agent_id=active_agent.agent_id)

        history = self._step_history[sid]
        agents_involved = {active_agent.agent_id}
        directives_applied = []
        escalation_count = 0
        final_result = None
        status = "COMPLETED"

        # 4. Ciclo Adaptativo Paso a Paso (Step-level Dispatch & Trajectory Control)
        step_idx = 0
        while step_idx < max_steps:
            # A. Despacho adaptativo de paso (autorización y contexto mínimo)
            dispatch_spec = self.step_dispatcher.create_dispatch_spec(
                task=task_req,
                agent=active_agent,
                step_index=step_idx,
                session_id=sid,
            )

            # B. Ejecución de la acción del paso (custom_step_executor o simulación determinista)
            if custom_step_executor:
                action_proposed, observation = custom_step_executor(dispatch_spec, step_idx)
            else:
                # Simulación determinista por defecto
                if step_idx == 0:
                    action_proposed = {"tool_name": "view_file", "arguments": {"path": "main.py"}}
                    observation = "File read successfully: content inspected."
                elif step_idx == max_steps - 1:
                    action_proposed = {"tool_name": "replace_file_content", "arguments": {"path": "main.py"}}
                    observation = "File updated successfully. task_completed."
                else:
                    action_proposed = {"tool_name": "run_command", "arguments": {"command": "pytest"}}
                    observation = "Tests passed cleanly."

            # C. Validar autorización de la acción propuesta bajo 'Semántica != Autoridad'
            tool_name = action_proposed.get("tool_name", "unknown")
            is_auth, auth_err = self.step_dispatcher.validate_action_authorization(
                spec=dispatch_spec,
                tool_name=tool_name,
                arguments=action_proposed.get("arguments"),
            )
            policy_decision = "ALLOW" if is_auth else "DENY"
            if not is_auth:
                observation = f"Policy Denial: {auth_err}"

            # D. Telemetría de tokens y coste del paso
            tokens_used = 150 + len(observation.split()) * 2
            step_cost = round(tokens_used * 0.000002, 6)

            step_record = TrajectoryStepRecord(
                step_id=f"step_{sid}_{step_idx}",
                step_index=step_idx,
                dispatch_spec=dispatch_spec,
                action_proposed=action_proposed,
                policy_decision=policy_decision,
                observation=observation,
                tokens_used=tokens_used,
                step_cost=step_cost,
                metadata={"is_complete": "task_completed" in observation.lower()},
            )

            # E. Supervisión Adaptativa de Trayectoria (Trajectory Controller)
            directive, directive_rationale = self.trajectory_controller.evaluate_step(
                step_record=step_record,
                history=history,
                task=task_req,
            )
            directives_applied.append(directive.value)
            history.append(step_record)

            # F. Aplicación de la Directiva Adaptativa
            if directive == TrajectoryDirective.TERMINATE:
                final_result = observation
                status = "COMPLETED"
                break

            elif directive == TrajectoryDirective.SUSPEND_HITL:
                final_result = f"Suspended for Human Approval: {directive_rationale}"
                status = "SUSPENDED_HITL"
                break

            elif directive == TrajectoryDirective.PRUNE:
                # Podar fragmentos de contexto redundantes y continuar con acción correctiva
                self.context_manager.prune_branch(session_id=sid, branch_id=f"step_{step_idx}")
                step_idx += 1

            elif directive == TrajectoryDirective.RECOVER:
                # Retroceso a checkpoint previo (reintentar el paso)
                step_idx += 1

            elif directive == TrajectoryDirective.ESCALATE:
                # Invocar DynamicEscalationEngine para subir modelo o cambiar agente
                escalation_count += 1
                esc_context = EscalationContext(
                    task=task_req,
                    current_decision=initial_decision,
                    trigger_type=EscalationTriggerType.TEST_FAILURE,
                    failure_reason=directive_rationale,
                    attempt_count=escalation_count,
                )
                esc_res = self.escalation_engine.evaluate_escalation(esc_context)
                if esc_res.requires_human or not esc_res.new_decision:
                    status = "SUSPENDED_HITL"
                    final_result = f"Escalation reached limit. Requiring Human: {esc_res.rationale}"
                    break
                else:
                    new_agent = self.registry.get(esc_res.new_decision.selected_agent_id)
                    if new_agent:
                        active_agent = new_agent
                        agents_involved.add(active_agent.agent_id)
                    step_idx += 1

            else:  # CONTINUE
                step_idx += 1

        # 5. Consolidación de estado de la sesión
        total_tokens = sum(s.tokens_used for s in history)
        total_cost = sum(s.step_cost for s in history)

        now = datetime.now(timezone.utc)
        self._sessions[sid] = AdaptiveSessionState(
            session_id=sid,
            goal=prompt,
            status=status,
            primary_agent_id=active_agent.agent_id,
            escalation_level=escalation_count,
            step_count=len(history),
            total_cost=total_cost,
            total_tokens=total_tokens,
            created_at=self._sessions[sid].created_at,
            updated_at=now,
        )

        duration = round(time.time() - start_time, 3)

        return AdaptiveExecutionSummary(
            session_id=sid,
            task_id=task_req.task_id,
            status=status,
            goal=prompt,
            total_steps=len(history),
            total_cost=round(total_cost, 6),
            total_tokens=total_tokens,
            agents_involved=sorted(list(agents_involved)),
            escalations_count=escalation_count,
            directives_applied=directives_applied,
            final_artifact_or_result=final_result or observation,
            duration_seconds=duration,
        )

    def get_session_state(self, session_id: str) -> Optional[AdaptiveSessionState]:
        """Obtiene el estado actual de una sesión."""
        return self._sessions.get(session_id)

    def get_step_history(self, session_id: str) -> List[TrajectoryStepRecord]:
        """Recupera el historial de pasos ejecutados para una sesión."""
        return list(self._step_history.get(session_id, []))
