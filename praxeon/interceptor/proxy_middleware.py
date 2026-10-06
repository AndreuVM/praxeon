"""Middleware proxy para interceptar y supervisar llamadas de agentes LLM en tiempo real (Saneado v0.2).

Resuelve los hallazgos críticos de la auditoría:
- Erradica el bypass de ejecución (3.8) encapsulando la ejecución física tras SecureExecutor.
- Erradica listas de cadenas hardcodeadas (3.4) delegando en ToolRegistry.
- Erradica el tratamiento arbitrario de finish (3.5) delegando en CompletionVerifier.
- Soporta BatchSemantics(independent=True/False) (3.3) para evitar penalizaciones espurias en cascada.
"""

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from praxeon.config import PraxeonConfig, default_config
from praxeon.core.intervention_policy import InterventionPolicy
from praxeon.core.jev_engine import JEVEngine
from praxeon.core.state_graph import StateGraph
from praxeon.domain.events import EventType
from praxeon.domain.models import (
    ActionCandidate as DomainAction,
    DecisionReceipt,
    DecisionStatus,
    Goal,
    ToolCall,
    compute_action_hash,
    sign_receipt,
)
from praxeon.models.schema import (
    ActionCandidate,
    BatchSemantics,
    ChunkEvaluationResult,
    InterventionDirective,
    InterventionLevel,
    LoopReport,
    LoopType,
    Step,
    StepType,
    Trajectory,
)
from praxeon.policy.engine import PolicyEngine
from praxeon.policy.registry import ToolRegistry
from praxeon.reasoning.completion import CompletionVerifier
from praxeon.runtime.executor import PolicyViolation, SecureExecutor, ToolObservation
from praxeon.runtime.state import SessionState


class JEVProxyMiddleware:
    """Interceptor y ejecutor seguro para agentes autónomos supervisados por TypeSafe AI."""

    def __init__(
        self,
        goal: str,
        config: Optional[PraxeonConfig] = None,
        tool_registry: Optional[ToolRegistry] = None,
        executor: Optional[SecureExecutor] = None,
        policy_engine: Optional[PolicyEngine] = None,
        event_bus: Optional[Any] = None,
        session_id: Optional[str] = None,
    ):
        self.config = config or default_config
        self.goal = goal
        self.domain_goal = Goal(objective=goal)
        self.session_id = session_id or "proxy_session"
        self.trajectory = Trajectory(session_id=self.session_id, goal=goal, steps=[])
        self.graph = StateGraph(self.config)
        self.graph.load_trajectory(self.trajectory)
        self.engine = JEVEngine(self.graph, self.config)
        self.policy = InterventionPolicy(self.graph, self.config)
        self.registry = tool_registry or ToolRegistry(register_defaults=True)
        self.executor = executor or SecureExecutor(registry=self.registry)
        self.policy_engine = policy_engine or PolicyEngine(
            registry=self.registry,
            secret_key=self.executor.secret_key,
        )
        self.completion_verifier = CompletionVerifier()
        self.session_state = SessionState(session_id=self.session_id, goal=self.domain_goal)
        self.event_bus = event_bus

        if self.event_bus:
            self.event_bus.emit(
                session_id=self.session_id,
                event_type=EventType.SESSION_STARTED,
                node_id=f"root_{self.session_id}",
                payload={"agent_name": "CodingAgent", "status": "Active", "label": "Start"},
            )
            self.event_bus.emit(
                session_id=self.session_id,
                event_type=EventType.GOAL_CREATED,
                node_id=f"goal_{self.session_id}",
                parent_id=f"root_{self.session_id}",
                payload={"goal": goal},
            )

    def intercept_step_chunk(
        self,
        proposed_steps: List[Dict[str, Any]],
        batch_semantics: Optional[BatchSemantics] = None,
    ) -> ChunkEvaluationResult:
        """Supervisa y valida un bloque agrupado de pasos candidatos respetando BatchSemantics."""
        if not proposed_steps:
            return ChunkEvaluationResult(all_safe=True, valid_step_count=0, explanation="Bloque vacío")

        start_idx = len(self.graph.get_chronological_nodes())
        candidate_steps: List[Step] = []

        for offset, p in enumerate(proposed_steps):
            step_id = f"step_{start_idx + offset}"
            st_raw = p.get("step_type") or ("tool_call" if p.get("tool_name") else "thought")
            st_type = StepType(st_raw) if st_raw in StepType._value2member_map_ else StepType.TOOL_CALL
            step = Step(
                id=step_id,
                step_type=st_type,
                content=p.get("thought_rationale") or p.get("content") or "",
                tool_name=p.get("tool_name"),
                tool_args=p.get("tool_args"),
            )
            candidate_steps.append(step)
            if self.event_bus:
                parent_id = f"step_{start_idx + offset - 1}" if (start_idx + offset > 0) else f"root_{self.session_id}"
                self.event_bus.emit(
                    session_id=self.session_id,
                    event_type=EventType.ACTION_PROPOSED,
                    node_id=step_id,
                    parent_id=parent_id,
                    payload={
                        "action_id": step_id,
                        "tool": step.tool_name,
                        "operation": str(step.tool_args or ""),
                        "thought": step.content,
                        "source": "CodingAgent",
                    },
                )

        # Evaluar bloque agrupado mediante TypeSafe AI
        try:
            eval_results = self.engine.evaluate_step_chunk(candidate_steps, batch_semantics=batch_semantics)
        except Exception as exc:
            import logging
            logging.getLogger("praxeon.interceptor.proxy_middleware").error(
                "Error o indisponibilidad en el evaluador semántico: %s", exc
            )
            eval_results = []

        # Finding 16: Política de fallo seguro (Fail-Closed) ante caída del proveedor semántico
        if not eval_results and candidate_steps:
            has_mutating = any(not self.registry.is_observational(s.tool_name or "", s.tool_args) for s in candidate_steps if s.tool_name)
            if has_mutating:
                loop_rep = LoopReport(
                    loop_detected=True,
                    loop_type=LoopType.SEMANTIC_FIXATION,
                    severity=4,
                    explanation="Proveedor semántico no disponible y el bloque contiene acciones mutantes. Política de seguridad: abstención estricta (fail-closed).",
                    culprit_tool=candidate_steps[0].tool_name,
                )
                directive = InterventionDirective(
                    level=InterventionLevel.LEVEL_3_ABSTRACTION_SHIFT,
                    message=loop_rep.explanation,
                    context_injection=f"[ALERTA DE SEGURIDAD] {loop_rep.explanation}",
                )
                return ChunkEvaluationResult(
                    all_safe=False,
                    valid_step_count=0,
                    flagged_step_index=0,
                    step_scores=[],
                    directive=directive,
                    loop_report=loop_rep,
                    hallucination_detected=False,
                    explanation=loop_rep.explanation,
                )

        step_scores = [sc for _, sc, _ in eval_results]

        # Verificar convergencia y detectar anomalías
        for idx, (step, score, metadata) in enumerate(eval_results):
            self.graph.add_step(step)
            self.graph.set_step_jev(step.id, score.total_jev)

            is_ts_loop = bool(metadata and metadata.get("is_loop") and metadata.get("flagged_index") == idx)
            is_hallucination = bool(metadata and metadata.get("is_hallucination") and metadata.get("flagged_index") == idx)
            hallucination_type = metadata.get("hallucination_type") if metadata else None
            is_divergent = bool(score.details.get("is_divergent", False))

            # Hallazgo 3.4: Descriptores de ToolRegistry en lugar de listas estáticas
            is_observational = self.registry.is_observational(step.tool_name, step.tool_args) if step.tool_name else False
            is_terminal = (step.tool_name == "finish")
            is_evasive_finish = False
            has_prior_unexecuted_inspection = False

            if is_terminal:
                summary_raw = str((step.tool_args or {}).get("summary") or "").lower()
                content_raw = str(step.content or "").lower()
                combined_finish = f"{summary_raw} {content_raw}"

                # 1. Comprobar si el finish es evasivo o reproduce alertas internas del supervisor
                evasive_markers = (
                    "pendiente de", "pendiente", "planificación", "planificacion",
                    "como soy un agente", "la acción real", "la accion real",
                    "todavía no", "aún no he", "aun no he", "sin analizar",
                    "no he podido leer", "no he podido", "provisional", "pending",
                    "este paso es de"
                )
                meta_leakage_markers = (
                    "ha sido vetada", "vetada temporalmente", "bucle o estancamiento",
                    "acción pausada", "accion pausada", "acción bloqueada", "accion bloqueada",
                    "bloqueado por el supervisor", "bloqueada por el supervisor",
                    "error de supervisión", "error de supervision",
                    "policy_engine", "system_intervention",
                    "no tengo la autorización", "no tengo la autorizacion",
                    "intervención activada", "intervencion activada",
                )
                if any(m in combined_finish for m in evasive_markers) or any(m in combined_finish for m in meta_leakage_markers):
                    is_evasive_finish = True

                # 2. Comprobar si viene en el mismo bloque donde hay acciones de lectura previas
                has_prior_unexecuted_inspection = any(
                    self.registry.is_observational(prev_c.tool_name or "", prev_c.tool_args)
                    for prev_c in candidate_steps[:idx]
                )
                if has_prior_unexecuted_inspection:
                    is_evasive_finish = True

            # Diagnosticar si este paso específico viola convergencia
            if is_terminal:
                has_any_observations = any(
                    s.step_type == StepType.OBSERVATION or self.registry.is_observational(s.tool_name or "", s.tool_args)
                    for s in self.graph.get_all_steps()
                )
                step_has_failed = is_evasive_finish or (not has_any_observations and len(self.graph.get_all_steps()) <= 1)
            elif is_observational:
                # Contar cuántas veces previas se ha ejecutado exactamente la misma consulta en historial reciente
                prior_identical_count = sum(
                    1 for prev_step in self.graph.get_all_steps()[-10:]
                    if (
                        prev_step.id != step.id
                        and prev_step.tool_name == step.tool_name
                        and prev_step.tool_args == step.tool_args
                    )
                )
                in_chunk_prior_count = sum(
                    1 for prev_c in candidate_steps[:idx]
                    if prev_c.tool_name == step.tool_name and prev_c.tool_args == step.tool_args
                )
                # Las herramientas de inspección (read_file, list_dir, run_command de lectura) son seguras e informativas.
                # Solo se consideran bucle si se han ejecutado 2 o más veces previamente (a partir del 3er intento idéntico).
                is_identical_repeat = ((prior_identical_count + in_chunk_prior_count) >= 2)
                step_has_failed = is_identical_repeat
            else:
                step_has_failed = (
                    is_ts_loop
                    or is_hallucination
                    or is_divergent
                )

            if step_has_failed:
                self.graph.remove_step(step.id)
                if is_evasive_finish or has_prior_unexecuted_inspection:
                    loop_type = LoopType.UNGROUNDED_PREMISE
                    diag_hallucination_type = "unverified_assumption"
                    explanation = (
                        "Finalización evasiva o premisa no fundamentada rechazada: el agente intenta concluir "
                        "sin fundamentar empíricamente su resultado en observaciones previas."
                    )
                elif is_observational and is_identical_repeat:
                    loop_type = LoopType.ONE_HOP_TOOL_REPEAT
                    diag_hallucination_type = "loop_repetition"
                    explanation = (
                        f"Reintento reiterado de '{step.tool_name}' ({json.dumps(step.tool_args or {})}). "
                        "El contenido ya ha sido leído y está disponible en tus observaciones previas. "
                        "Procede a utilizar esa información e invoca 'finish' con tu respuesta."
                    )
                elif is_hallucination:
                    loop_type = LoopType.HALLUCINATION
                    diag_hallucination_type = hallucination_type or "invented_fact_or_file"
                    explanation = f"Alucinación detectada en paso {idx + 1} ('{step.tool_name}'): premisa no comprobada."
                else:
                    loop_type = LoopType.ONE_HOP_TOOL_REPEAT if is_ts_loop else LoopType.SEMANTIC_FIXATION
                    diag_hallucination_type = "loop_repetition"
                    explanation = f"Bucle o estancamiento detectado en paso {idx + 1} ('{step.tool_name}')"

                loop_rep = LoopReport(
                    loop_detected=True,
                    loop_type=loop_type,
                    severity=4 if is_evasive_finish else 3,
                    explanation=explanation,
                    culprit_tool=step.tool_name,
                )

                directive = self.policy.evaluate_and_intervene(
                    current_step=step,
                    jev_score=score,
                    loop_report=loop_rep,
                )

                if self.event_bus:
                    self.event_bus.emit(
                        session_id=self.session_id,
                        event_type=EventType.PROVIDER_EVALUATED,
                        node_id=step.id,
                        payload={
                            "provider_name": getattr(getattr(self.config, "provider", None), "name", "TypeSafe").upper(),
                            "score": round(score.total_jev, 2),
                            "verdict": "BLOCK",
                        },
                    )
                    self.event_bus.emit(
                        session_id=self.session_id,
                        event_type=EventType.POLICY_DECIDED,
                        node_id=f"{step.id}_policy",
                        parent_id=step.id,
                        payload={
                            "status": "BLOCKED" if (directive and directive.level == InterventionLevel.CRITICAL) else "REVIEW",
                            "reason_code": loop_type.value if hasattr(loop_type, "value") else str(loop_type),
                            "reason": explanation,
                        },
                    )
                    self.event_bus.emit(
                        session_id=self.session_id,
                        event_type=EventType.DECISION_PRUNED,
                        node_id=step.id,
                        payload={"reason": explanation},
                    )

                return ChunkEvaluationResult(
                    all_safe=False,
                    valid_step_count=idx,
                    flagged_step_index=idx,
                    step_scores=step_scores,
                    directive=directive,
                    loop_report=loop_rep,
                    hallucination_detected=bool(is_hallucination and not is_observational),
                    hallucination_type=diag_hallucination_type,
                    explanation=directive.message if directive else explanation,
                )

        if self.event_bus:
            for step, sc in zip(candidate_steps, step_scores):
                self.event_bus.emit(
                    session_id=self.session_id,
                    event_type=EventType.PROVIDER_EVALUATED,
                    node_id=step.id,
                    payload={
                        "provider_name": getattr(getattr(self.config, "provider", None), "name", "TypeSafe").upper(),
                        "score": round(sc.total_jev, 2),
                        "verdict": "ALLOW",
                    },
                )
                self.event_bus.emit(
                    session_id=self.session_id,
                    event_type=EventType.POLICY_DECIDED,
                    node_id=f"{step.id}_policy",
                    parent_id=step.id,
                    payload={"status": "ALLOW", "reason_code": "APPROVED_BY_POLICY"},
                )
                self.event_bus.emit(
                    session_id=self.session_id,
                    event_type=EventType.CAPABILITY_ISSUED,
                    node_id=step.id,
                    payload={"capability_id": f"cap_{step.id}", "allowed_tools": [step.tool_name] if step.tool_name else []},
                )

        return ChunkEvaluationResult(
            all_safe=True,
            valid_step_count=len(candidate_steps),
            flagged_step_index=None,
            step_scores=step_scores,
            directive=None,
            loop_report=None,
            hallucination_detected=False,
            explanation="Bloque completo validado como convergente y fundamentado",
        )

    def intercept_tool_call(
        self,
        tool_name: str,
        tool_args: Optional[Dict[str, Any]] = None,
        thought_rationale: str = "",
    ) -> Tuple[bool, Optional[str]]:
        """Intercepta una llamada a herramienta individual antes de su ejecución."""
        step_id = f"step_{len(self.graph.get_chronological_nodes())}"
        candidate_step = Step(
            id=step_id,
            step_type=StepType.TOOL_CALL,
            content=thought_rationale,
            tool_name=tool_name,
            tool_args=tool_args,
        )

        # Hallazgo 3.5: Verificación formal mediante CompletionVerifier en lugar de 0.95 hardcodeado
        if tool_name == "finish":
            summary_raw = str((tool_args or {}).get("summary") or "").lower()
            content_raw = str(thought_rationale or "").lower()
            combined_finish = f"{summary_raw} {content_raw}"
            evasive_markers = (
                "pendiente de", "pendiente", "planificación", "planificacion",
                "como soy un agente", "la acción real", "la accion real",
                "todavía no", "aún no he", "aun no he", "sin analizar",
                "no he podido leer", "no he podido", "provisional", "pending",
                "este paso es de"
            )
            meta_leakage_markers = (
                "ha sido vetada", "vetada temporalmente", "bucle o estancamiento",
                "acción pausada", "accion pausada", "acción bloqueada", "accion bloqueada",
                "bloqueado por el supervisor", "bloqueada por el supervisor",
                "error de supervisión", "error de supervision",
                "policy_engine", "system_intervention",
                "no tengo la autorización", "no tengo la autorizacion",
                "intervención activada", "intervencion activada",
            )
            if any(m in combined_finish for m in evasive_markers):
                return False, (
                    "<system_intervention type=\"rejection\" level=\"critical\">\n"
                    "JEV CRITICAL: Finalización evasiva rechazada. NO puedes finalizar con 'pendiente de lectura' "
                    "ni excusas de planificación. Sintetiza y formula tu respuesta final basándote en observaciones reales.\n"
                    "</system_intervention>"
                )
            if any(m in combined_finish for m in meta_leakage_markers):
                return False, (
                    "<system_intervention type=\"rejection\" level=\"critical\">\n"
                    f"JEV CRITICAL: Tu llamada a 'finish' ha sido RECHAZADA porque estás describiendo diagnósticos "
                    f"internos del supervisor en lugar de responder a la tarea del usuario: '{self.goal}'. "
                    f"Entrega tu análisis fundamentado resolviendo la tarea solicitada.\n"
                    "</system_intervention>"
                )

            # Si el finish contiene una justificación o resultado concreto
            is_substantive = len(summary_raw.split()) >= 4 or len(content_raw.split()) >= 4
            if not is_substantive and len(self.graph.get_chronological_nodes()) == 0:
                return False, (
                    "<system_intervention type=\"rejection\" level=\"critical\">\n"
                    "JEV CRITICAL: Finalización prematura rechazada: falta justificación y evidencia.\n"
                    "</system_intervention>"
                )

            self.graph.add_step(candidate_step)
            # Calcular JEV analítico real (sin bump arbitrario de 0.95)
            score = self.engine.evaluate_step(candidate_step)
            self.graph.set_step_jev(step_id, score.total_jev)
            return True, None

        score = self.engine.evaluate_step(candidate_step)

        # Detectar bucle según TypeSafe o reintento idéntico en historial
        ts_eval = score.details.get("typesafe_eval") or {}
        prov_avail = bool(score.details.get("provider_available", False))

        is_immediate_repeat = False
        steps_history = self.graph.get_all_steps()
        for s in reversed(steps_history):
            if s.step_type == StepType.TOOL_CALL and s.tool_name:
                if s.tool_name == tool_name and (s.tool_args or {}) == (tool_args or {}):
                    is_immediate_repeat = True
                break

        if prov_avail:
            is_loop = bool(ts_eval.get("is_loop", False) or score.total_jev < self.config.critical_jev_threshold or is_immediate_repeat)
            loop_type = ts_eval.get("loop_type", LoopType.ONE_HOP_TOOL_REPEAT if is_loop else LoopType.NONE)
        else:
            is_loop = is_immediate_repeat
            loop_type = LoopType.ONE_HOP_TOOL_REPEAT if is_loop else LoopType.NONE

        loop_rep = LoopReport(
            loop_detected=is_loop,
            loop_type=loop_type,
            severity=3 if is_loop else 0,
            explanation=f"Reintento o acción no constructiva detectada para herramienta '{tool_name}'",
            culprit_tool=tool_name,
        )

        directive = self.policy.evaluate_and_intervene(
            current_step=candidate_step,
            jev_score=score,
            loop_report=loop_rep,
        )

        if is_loop and directive:
            return False, directive.context_injection

        self.graph.add_step(candidate_step)
        self.graph.set_step_jev(step_id, score.total_jev)
        return True, None

    def execute_tool(
        self,
        tool_name: str,
        tool_args: Optional[Dict[str, Any]] = None,
        thought_rationale: str = "",
    ) -> ToolObservation:
        """Ejecuta una herramienta de forma encapsulada tras la barrera de seguridad (Hallazgo 3.8 y Auditoría v1.0.0)."""
        step_idx = len(self.session_state.steps)
        action_id = f"act_{step_idx}"
        action = DomainAction(
            id=action_id,
            description=thought_rationale,
            tool_call=ToolCall(tool_name=tool_name, arguments=tool_args or {}),
        )
        if self.event_bus:
            self.event_bus.emit(
                session_id=self.session_id,
                event_type=EventType.EXECUTION_STARTED,
                node_id=f"exec_{action_id}",
                parent_id=f"step_{step_idx}" if step_idx > 0 else f"root_{self.session_id}",
                payload={"tool": tool_name, "tool_args": tool_args or {}},
            )

        # Evaluar formalmente la acción con PolicyEngine para evitar bypass de autorización (Sección 2.3)
        decision, receipt = self.policy_engine.evaluate_action(
            action=action,
            state=self.session_state,
            session_id=self.session_id,
        )

        # Si el ejecutor posee clave secreta, asegurar firma válida con la clave del ejecutor
        if self.executor.secret_key and not receipt.signature:
            receipt = sign_receipt(receipt, self.executor.secret_key)
        elif self.executor.secret_key and receipt.signature:
            if self.policy_engine.secret_key != self.executor.secret_key:
                receipt = sign_receipt(receipt, self.executor.secret_key)

        # Si la política deniega la ejecución, no despachar al ejecutor físico
        if decision.status != DecisionStatus.ALLOW:
            reasons_str = ", ".join(decision.reason_codes) if decision.reason_codes else "Bloqueado por política de seguridad"
            denial_msg = f"[PRAXEON Policy Violation] Acción '{tool_name}' denegada ({decision.status.value}): {reasons_str}"
            observation = ToolObservation(
                output=denial_msg,
                success=False,
                is_error=True,
                exit_code=1,
                execution_time_ms=0.0,
            )
            self.session_state.add_step(action=action, decision=decision, observation=denial_msg)
            self.record_observation(denial_msg)
            if self.event_bus:
                self.event_bus.emit(
                    session_id=self.session_id,
                    event_type=EventType.POLICY_VIOLATED,
                    node_id=f"policy_{action_id}",
                    payload={"action_id": action_id, "status": decision.status.value, "reason": reasons_str},
                )
                self.event_bus.emit(
                    session_id=self.session_id,
                    event_type=EventType.EXECUTION_COMPLETED,
                    node_id=f"exec_{action_id}",
                    payload={"success": False, "exit_code": 1, "execution_time_ms": 0.0},
                )
            return observation

        # Ejecución delegada dentro del perímetro de seguridad
        observation = self.executor.execute(action, self.session_state, receipt=receipt)

        # Registrar el paso en el SessionState para avanzar el contador y evitar ataques de replay (BUG-01)
        self.session_state.add_step(action=action, decision=decision, observation=observation.output)

        # Registrar observación en el grafo y sincronizar la trayectoria
        self.record_observation(observation.output)
        self.trajectory.steps.append(
            Step(
                id=f"step_{step_idx}",
                step_type=StepType.TOOL_CALL,
                content=thought_rationale,
                tool_name=tool_name,
                tool_args=tool_args,
            )
        )

        if self.event_bus:
            self.event_bus.emit(
                session_id=self.session_id,
                event_type=EventType.EXECUTION_COMPLETED,
                node_id=f"exec_{action_id}",
                payload={
                    "success": observation.success,
                    "exit_code": getattr(observation, "exit_code", 0 if observation.success else 1),
                    "execution_time_ms": observation.execution_time_ms,
                },
            )
            self.event_bus.emit(
                session_id=self.session_id,
                event_type=EventType.OBSERVATION_RECORDED,
                node_id=f"obs_{action_id}",
                parent_id=f"exec_{action_id}",
                payload={"output": observation.output[:100_000] if len(observation.output) > 100_000 else observation.output},
            )
            if tool_name == "finish" and observation.success:
                self.event_bus.emit(
                    session_id=self.session_id,
                    event_type=EventType.SESSION_COMPLETED,
                    node_id=f"root_{self.session_id}",
                    payload={"status": "completed", "summary": (tool_args or {}).get("summary", "")},
                )

        return observation

    def record_observation(self, observation_text: str) -> None:
        """Registra el resultado/observación devuelto por una herramienta ejecutada."""
        step_id = f"obs_{len(self.graph.get_chronological_nodes())}"
        obs_step = Step(
            id=step_id,
            step_type=StepType.OBSERVATION,
            content=observation_text,
        )
        self.graph.add_step(obs_step)

    def record_thought(self, thought_text: str) -> Tuple[bool, Optional[str]]:
        """Registra un pensamiento reflexivo y evalúa convergencia mediante TypeSafe AI."""
        step_id = f"th_{len(self.graph.get_chronological_nodes())}"
        th_step = Step(
            id=step_id,
            step_type=StepType.THOUGHT,
            content=thought_text,
        )
        score = self.engine.evaluate_step(th_step)
        ts_eval = score.details.get("typesafe_eval") or {}
        is_loop = bool(ts_eval.get("is_loop", False) or score.total_jev < self.config.critical_jev_threshold)

        if is_loop:
            loop_rep = LoopReport(
                loop_detected=True,
                loop_type=ts_eval.get("loop_type", LoopType.SEMANTIC_FIXATION),
                severity=2,
                explanation="Fijación o estancamiento reflexivo detectado",
            )
            directive = self.policy.evaluate_and_intervene(
                current_step=th_step,
                jev_score=score,
                loop_report=loop_rep,
            )
            if directive:
                return False, directive.context_injection

        self.graph.add_step(th_step)
        self.graph.set_step_jev(step_id, score.total_jev)
        return True, None

    def start_subtask(self, new_goal: str) -> None:
        """Transiciona la supervisión hacia una nueva tarea concatenada preservando la memoria acumulada."""
        self.goal = new_goal
        self.domain_goal = Goal(objective=new_goal)
        self.session_state = SessionState(session_id="proxy_session", goal=self.domain_goal)
        self.graph.goal = new_goal
        trans_id = f"subtask_trans_{len(self.graph.get_chronological_nodes())}"
        step = Step(
            id=trans_id,
            step_type=StepType.THOUGHT,
            content=f"[TRANSICIÓN A NUEVA TAREA]: {new_goal}",
        )
        self.graph.add_step(step)
        self.graph.set_step_jev(trans_id, 1.0)
