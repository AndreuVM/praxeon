"""Orquestador central (Navigator) para JEV Reasoning Navigator v0.2.

Coordina de manera desacoplada:
Proposal -> Evidence -> Risk -> JEV Provider -> Policy -> Decision -> Execution -> Observation
con soporte de checkpoints automáticos, chunking tipado y rollback determinista.
"""

from datetime import datetime, timezone
import hashlib
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from praxeon.domain.interfaces import ReasoningProvider
from praxeon.domain.models import (
    ActionCandidate,
    compute_receipt_signature,
    DecisionReceipt,
    DecisionStatus,
    Goal,
    PolicyDecision,
    ProviderAssessment,
)
from praxeon.runtime.efficiency import (
    EfficiencyCalculator,
    ModelPricing,
    MODEL_PRICING_CATALOG,
    SessionEfficiencySummary,
    StepEfficiencyRecord,
)
from praxeon.models.schema import BatchSemantics
from praxeon.policy.engine import PolicyEngine
from praxeon.reasoning.completion import CompletionAssessment, CompletionVerifier
from praxeon.reasoning.evidence import EvidenceEngine
from praxeon.reasoning.risk import RiskEngine
from praxeon.runtime.checkpoints import Checkpoint, CheckpointManager
from praxeon.runtime.executor import SecureExecutor, ToolObservation
from praxeon.runtime.state import SessionState
from praxeon.runtime.telemetry import (
    DecisionEvent,
    EfficiencyStepEvent,
    ObservationEvent,
    ToolExecutionEvent,
    global_event_bus,
    EventBus,
)


class Navigator:
    """Orquestador central del ciclo de vida de supervisión y ejecución formal del agente."""

    def __init__(
        self,
        provider: ReasoningProvider,
        policy_engine: Optional[PolicyEngine] = None,
        executor: Optional[SecureExecutor] = None,
        evidence_engine: Optional[EvidenceEngine] = None,
        risk_engine: Optional[RiskEngine] = None,
        checkpoint_manager: Optional[CheckpointManager] = None,
        completion_verifier: Optional[CompletionVerifier] = None,
        event_bus: Optional[EventBus] = None,
        shadow_mode: bool = False,
        model_name: Optional[str] = None,
    ):
        self.provider = provider
        self.policy_engine = policy_engine or PolicyEngine()
        self.executor = executor or SecureExecutor()
        if self.executor.secret_key is None:
            self.executor.secret_key = self.policy_engine.secret_key
        self.secret_key = self.policy_engine.secret_key

        self.evidence_engine = evidence_engine or EvidenceEngine()
        self.risk_engine = risk_engine or RiskEngine()
        self.checkpoint_manager = checkpoint_manager or CheckpointManager()
        self.completion_verifier = completion_verifier or CompletionVerifier()
        self.event_bus = event_bus or global_event_bus
        self.shadow_mode = shadow_mode
        self.model_name = model_name or getattr(self.provider, "model", "deepseek-r1-7b")

        self.state: Optional[SessionState] = None
        self.audit_receipts: List[DecisionReceipt] = []
        self.efficiency_records: List[StepEfficiencyRecord] = []
        self._last_timing: Dict[str, float] = {"llm_latency_ms": 0.0, "praxeon_overhead_ms": 0.0}

    @property
    def router_telemetry(self) -> Optional[Any]:
        """Devuelve la telemetría acumulada de enrutamiento si el proveedor es un ConfidenceAwareRouter."""
        if hasattr(self.provider, "get_telemetry"):
            return self.provider.get_telemetry()
        return None

    def start_session(self, goal: Goal, session_id: Optional[str] = None) -> SessionState:
        """Inicializa una nueva sesión de supervisión formal con checkpoint génesis."""
        sid = session_id or f"sess_{len(self.audit_receipts)}"
        self.state = SessionState(session_id=sid, goal=goal)
        self.efficiency_records = []
        self._last_timing = {"llm_latency_ms": 0.0, "praxeon_overhead_ms": 0.0}
        if hasattr(self.executor, "sandbox") and hasattr(self.executor.sandbox, "workspace_root"):
            self.state.metadata["workspace_root"] = self.executor.sandbox.workspace_root
        self.checkpoint_manager.create_checkpoint(self.state, reason="Genesis checkpoint")
        return self.state

    def _ensure_session(self) -> SessionState:
        if self.state is None:
            raise RuntimeError("No hay una sesión activa en Navigator. Llame a start_session(goal) primero.")
        return self.state

    def is_batchable(self, action: ActionCandidate) -> bool:
        """Determina si una acción es segura para ser agrupada en un chunk (Batchable).
        
        Según el principio de chunking de v0.2:
        - Acciones batchables: lecturas independientes, observaciones sin side effects.
        - Acciones NO batchables: mutaciones, destructivas, dependientes de observaciones aún no producidas.
        """
        tool_name = action.tool_call.tool_name if action.tool_call else None
        if not tool_name:
            return True

        spec = self.policy_engine.registry.get_tool(tool_name)
        if spec:
            return spec.read_only and not spec.external_side_effect

        return False

    def confirm_action(
        self,
        action_id: str,
        action_hash: Optional[str] = None,
        approver_id: str = "operator",
        ttl_seconds: Optional[float] = 300.0,
        reason: Optional[str] = None,
    ) -> Any:
        """Marca una acción sensible como confirmada explícitamente emitiendo un ticket auditable."""
        return self.policy_engine.permission_manager.confirm_action(
            action_id=action_id,
            action_hash=action_hash,
            approver_id=approver_id,
            ttl_seconds=ttl_seconds,
            reason=reason,
        )

    def is_action_confirmed(self, action_id: str, action_hash: Optional[str] = None) -> bool:
        """Verifica si una acción cuenta con confirmación del operador humano."""
        return self.policy_engine.permission_manager.is_action_confirmed(action_id, action_hash=action_hash)

    def propose(self, actions: List[ActionCandidate]) -> List[ActionCandidate]:
        """Filtra y valida candidatos eliminando herramientas prohibidas en el estado actual."""
        state = self._ensure_session()
        viable: List[ActionCandidate] = []
        for action in actions:
            tool_name = action.tool_call.tool_name if action.tool_call else None
            if tool_name and tool_name in state.forbidden_tools:
                continue
            viable.append(action)
        return viable

    def evaluate(
        self,
        actions: List[ActionCandidate],
        batch_semantics: Optional[BatchSemantics] = None,
    ) -> List[Tuple[ActionCandidate, ProviderAssessment, PolicyDecision, DecisionReceipt]]:
        """Evalúa un lote de acciones candidatas produciendo juicios semánticos, decisiones y recibos."""
        state = self._ensure_session()
        results: List[Tuple[ActionCandidate, ProviderAssessment, PolicyDecision, DecisionReceipt]] = []

        is_independent = batch_semantics.independent if batch_semantics else False
        has_diverged = False

        # Para cada acción aplicamos el pipeline:
        for action in actions:
            # 1. Comprobar si es un intento de finalización
            completion_assessment: Optional[CompletionAssessment] = None
            if self.completion_verifier.is_finish_action(action):
                completion_assessment = self.completion_verifier.verify(state.goal, state, action)

            # 2. Evaluación semántica pura (JEV / Provider)
            assessments = self.provider.evaluate(state, [action])
            assessment = assessments[0] if assessments else ProviderAssessment(
                provider="unknown",
                available=False,
                confidence=0.0,
                failure_reason="No assessment returned",
            )

            # Si una acción anterior divergió y el lote NO es independiente, penalizar en cascada
            if has_diverged and not is_independent:
                assessment = ProviderAssessment(
                    provider=assessment.provider,
                    available=assessment.available,
                    confidence=0.1,
                    loop_probability=0.9,
                    grounded_probability=0.05,
                    progress_probability=0.01,
                    failure_reason="Penalización en cascada por divergencia en acción previa dependiente del lote",
                    reason_codes=["BATCH_CASCADE_REJECTION"],
                )

            # 3. Evaluación contextual de riesgo operacional
            risk_assessment = self.risk_engine.assess_action_risk(action)

            # 4. Decisión operacional de política
            decision, receipt = self.policy_engine.evaluate_action(
                action=action,
                state=state.to_snapshot(),
                provider_assessment=assessment,
                available_evidence=state.evidence,
                forbidden_tools=state.forbidden_tools,
                completion_assessment=completion_assessment,
                risk_assessment=risk_assessment,
                session_id=state.session_id,
            )

            if decision.status != DecisionStatus.ALLOW:
                has_diverged = True

            self.audit_receipts.append(receipt)
            results.append((action, assessment, decision, receipt))

        return results

    def decide(
        self,
        action: ActionCandidate,
        assessment: Optional[ProviderAssessment] = None,
    ) -> Tuple[PolicyDecision, DecisionReceipt]:
        """Toma la decisión operacional formal para una única acción."""
        state = self._ensure_session()
        t_decide_start = time.perf_counter()

        completion_assessment: Optional[CompletionAssessment] = None
        if self.completion_verifier.is_finish_action(action):
            completion_assessment = self.completion_verifier.verify(state.goal, state, action)

        llm_latency_ms = 0.0
        if assessment is None:
            t_llm_start = time.perf_counter()
            assessments = self.provider.evaluate(state, [action])
            t_llm_end = time.perf_counter()
            llm_latency_ms = (t_llm_end - t_llm_start) * 1000.0
            assessment = assessments[0] if assessments else ProviderAssessment(
                provider="unknown",
                available=False,
                confidence=0.0,
                failure_reason="No assessment returned",
            )
        elif hasattr(assessment, "metadata") and isinstance(assessment.metadata, dict) and "llm_latency_ms" in assessment.metadata:
            llm_latency_ms = float(assessment.metadata["llm_latency_ms"])

        risk_assessment = self.risk_engine.assess_action_risk(action)

        decision, receipt = self.policy_engine.evaluate_action(
            action=action,
            state=state.to_snapshot(),
            provider_assessment=assessment,
            available_evidence=state.evidence,
            forbidden_tools=state.forbidden_tools,
            completion_assessment=completion_assessment,
            risk_assessment=risk_assessment,
            session_id=state.session_id,
        )
        self.audit_receipts.append(receipt)

        t_decide_end = time.perf_counter()
        total_decide_ms = (t_decide_end - t_decide_start) * 1000.0
        praxeon_overhead_ms = max(0.0, total_decide_ms - llm_latency_ms)
        self._last_timing = {
            "llm_latency_ms": round(llm_latency_ms, 2),
            "praxeon_overhead_ms": round(praxeon_overhead_ms, 2),
        }

        # Emitir evento estructurado de decisión (Secciones 21 y 30)
        self.event_bus.publish(
            DecisionEvent(
                session_id=state.session_id,
                decision_id=receipt.decision_id,
                action_id=action.id,
                state_hash=receipt.state_hash,
                action_hash=receipt.action_hash,
                provider=assessment.provider if assessment else "unknown",
                model=assessment.model if assessment else None,
                decision=decision.status.value,
                confidence=decision.confidence,
                risk=risk_assessment.level.value if risk_assessment else "low",
                latency_ms=receipt.latency_ms,
                reason_codes=decision.reason_codes,
                shadow_mode=self.shadow_mode,
            )
        )
        return decision, receipt

    def step(
        self,
        action: ActionCandidate,
        auto_checkpoint: bool = True,
    ) -> Tuple[PolicyDecision, Optional[ToolObservation]]:
        """Ejecuta un paso normativo completo de supervisión y ejecución:
        
        Proposal -> Evidence -> Risk -> JEV -> Policy -> Decision -> Execution -> Observation.
        """
        state = self._ensure_session()
        t_step_start = time.perf_counter()

        # 1. Evaluar decisión con la política
        decision, receipt = self.decide(action)
        tool_name = action.tool_call.tool_name if action.tool_call else None
        tool_args = action.tool_call.arguments if action.tool_call else {}

        # Estimación / extracción de tokens in / out
        tokens_in = 0
        tokens_out = 0
        if action.metadata and isinstance(action.metadata, dict):
            tokens_in = int(action.metadata.get("tokens_in", 0))
            tokens_out = int(action.metadata.get("tokens_out", 0))
        if tokens_in <= 0:
            prompt_context_size = len(str(getattr(state.goal, "objective", state.goal))) + sum(len(str(st)) for st in state.steps) + 120
            tokens_in = max(15, prompt_context_size // 4)
        if tokens_out <= 0:
            gen_size = len(action.rationale or "") + len(str(action.tool_call or "")) + 40
            tokens_out = max(10, gen_size // 4)
        tokens_total = tokens_in + tokens_out

        # 2. Si la política DENEGÓ la ejecución (BLOCK, REPLAN, ABSTAIN)
        # En modo shadow, se observa y registra pero se permite continuar
        if decision.status != DecisionStatus.ALLOW and not self.shadow_mode:
            state.add_step(action=action, decision=decision, observation=None)
            t_step_end = time.perf_counter()
            total_step_lat_ms = (t_step_end - t_step_start) * 1000.0

            cost_usd = EfficiencyCalculator.calculate_step_cost(tokens_in, tokens_out, self.model_name)
            record = StepEfficiencyRecord(
                step_index=len(self.efficiency_records),
                action_id=action.id,
                tool_name=tool_name or "none",
                decision_status=decision.status.value,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                tokens_total=tokens_total,
                llm_calls=1,
                llm_latency_ms=self._last_timing.get("llm_latency_ms", 0.0),
                praxeon_overhead_ms=self._last_timing.get("praxeon_overhead_ms", 0.0),
                execution_time_ms=0.0,
                total_step_latency_ms=round(total_step_lat_ms, 2),
                physical_execution_attempted=True,
                physical_execution_allowed=False,
                physical_execution_success=None,
                is_error=False,
                cost_usd=round(cost_usd, 6),
            )
            self.efficiency_records.append(record)
            self.event_bus.publish(
                EfficiencyStepEvent(
                    session_id=state.session_id,
                    step_index=record.step_index,
                    action_id=record.action_id,
                    tool_name=record.tool_name,
                    decision=record.decision_status,
                    tokens_in=record.tokens_in,
                    tokens_out=record.tokens_out,
                    tokens_total=record.tokens_total,
                    llm_calls=record.llm_calls,
                    llm_latency_ms=record.llm_latency_ms,
                    praxeon_overhead_ms=record.praxeon_overhead_ms,
                    execution_time_ms=record.execution_time_ms,
                    total_step_latency_ms=record.total_step_latency_ms,
                    physical_execution_attempted=record.physical_execution_attempted,
                    physical_execution_allowed=record.physical_execution_allowed,
                    physical_execution_success=record.physical_execution_success,
                    is_error=record.is_error,
                    cost_usd=record.cost_usd,
                )
            )
            return decision, None

        # 3. La acción está AUTORIZADA (ALLOW o Shadow Mode)
        # Checkpoint preventivo automático ante mutaciones relevantes
        if auto_checkpoint and tool_name:
            spec = self.policy_engine.registry.get_tool(tool_name)
            if spec and not spec.read_only:
                self.checkpoint_manager.create_checkpoint(
                    state,
                    reason=f"Auto-checkpoint previo a mutación por '{tool_name}'",
                )

        # 4. Ejecución física con el Executor garantizado mediante capability ligado
        exec_receipt = receipt
        if self.shadow_mode and receipt.decision_status != DecisionStatus.ALLOW:
            exec_receipt_data = receipt.model_dump()
            exec_receipt_data["decision_status"] = DecisionStatus.ALLOW
            exec_receipt_data["reason_codes"] = ["SHADOW_MODE_OVERRIDE"]
            if self.secret_key:
                exec_receipt_data["signature"] = compute_receipt_signature(
                    secret_key=self.secret_key,
                    decision_id=exec_receipt_data["decision_id"],
                    session_id=exec_receipt_data["session_id"],
                    action_hash=exec_receipt_data["action_hash"],
                    state_hash=exec_receipt_data["state_hash"],
                    nonce=exec_receipt_data["nonce"],
                    decision_status=DecisionStatus.ALLOW,
                    expires_at=exec_receipt_data.get("expires_at"),
                )
            exec_receipt = DecisionReceipt(**exec_receipt_data)

        exec_decision = PolicyDecision(status=DecisionStatus.ALLOW) if self.shadow_mode else decision
        observation = self.executor.execute(action, state, receipt=exec_receipt, decision=exec_decision)

        # Emitir eventos de ejecución y observación
        self.event_bus.publish(
            ToolExecutionEvent(
                session_id=state.session_id,
                action_id=action.id,
                tool_name=tool_name or "unknown",
                arguments_hash=receipt.action_hash,
                success=observation.success,
                execution_time_ms=observation.execution_time_ms,
                is_error=observation.is_error,
            )
        )
        self.event_bus.publish(
            ObservationEvent(
                session_id=state.session_id,
                observation_id=f"obs_{len(state.steps)}",
                tool_name=tool_name,
                content_hash=hashlib.sha256(observation.output.encode("utf-8", errors="replace")).hexdigest(),
                size_bytes=len(observation.output.encode("utf-8", errors="replace")),
            )
        )

        # Actualizar el recibo de decisión con la dimensión de ejecución completada (Cuadro 1)
        receipt_dict = receipt.model_dump()
        receipt_dict["is_executed"] = True
        receipt_dict["observation_id"] = f"obs_{len(state.steps)}"
        receipt_dict["execution_timestamp"] = datetime.now(timezone.utc)
        updated_receipt = DecisionReceipt(**receipt_dict)

        if self.audit_receipts:
            self.audit_receipts[-1] = updated_receipt

        # 5. Ingestión de evidencia y resolución de mutaciones
        if observation.success:
            if tool_name:
                new_evidences = self.evidence_engine.ingest_from_observation(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    observation=observation.output,
                    step_id=f"step_{len(state.steps)}",
                )
                for ev in new_evidences:
                    state.add_evidence(ev)

        # 6. Registrar paso completado en el estado canónico
        state.add_step(action=action, decision=decision, observation=observation.output)

        t_step_end = time.perf_counter()
        total_step_lat_ms = (t_step_end - t_step_start) * 1000.0

        cost_usd = EfficiencyCalculator.calculate_step_cost(tokens_in, tokens_out, self.model_name)
        record = StepEfficiencyRecord(
            step_index=len(self.efficiency_records),
            action_id=action.id,
            tool_name=tool_name or "unknown",
            decision_status=decision.status.value,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_total=tokens_total,
            llm_calls=1,
            llm_latency_ms=self._last_timing.get("llm_latency_ms", 0.0),
            praxeon_overhead_ms=self._last_timing.get("praxeon_overhead_ms", 0.0),
            execution_time_ms=round(observation.execution_time_ms, 2),
            total_step_latency_ms=round(total_step_lat_ms, 2),
            physical_execution_attempted=True,
            physical_execution_allowed=True,
            physical_execution_success=observation.success,
            is_error=observation.is_error,
            cost_usd=round(cost_usd, 6),
        )
        self.efficiency_records.append(record)
        self.event_bus.publish(
            EfficiencyStepEvent(
                session_id=state.session_id,
                step_index=record.step_index,
                action_id=record.action_id,
                tool_name=record.tool_name,
                decision=record.decision_status,
                tokens_in=record.tokens_in,
                tokens_out=record.tokens_out,
                tokens_total=record.tokens_total,
                llm_calls=record.llm_calls,
                llm_latency_ms=record.llm_latency_ms,
                praxeon_overhead_ms=record.praxeon_overhead_ms,
                execution_time_ms=record.execution_time_ms,
                total_step_latency_ms=record.total_step_latency_ms,
                physical_execution_attempted=record.physical_execution_attempted,
                physical_execution_allowed=record.physical_execution_allowed,
                physical_execution_success=record.physical_execution_success,
                is_error=record.is_error,
                cost_usd=record.cost_usd,
            )
        )

        return decision, observation

    def get_efficiency_summary(
        self,
        scale: str = "medium",
        successful_completion: Optional[bool] = None,
    ) -> SessionEfficiencySummary:
        """Calcula y devuelve el resumen formal de telemetría de eficiencia de la sesión actual."""
        state = self._ensure_session()
        if successful_completion is None:
            successful_completion = (
                len(self.efficiency_records) > 0
                and not self.efficiency_records[-1].is_error
                and self.efficiency_records[-1].physical_execution_success is not False
            )
        return EfficiencyCalculator.aggregate_session(
            session_id=state.session_id,
            scale=scale,
            model_name=self.model_name,
            successful_completion=successful_completion,
            steps=list(self.efficiency_records),
        )

    def rollback(
        self,
        checkpoint_id: Optional[str] = None,
        culprit_tool: Optional[str] = None,
        reason: str = "Rollback formal por degradación de trayectoria",
    ) -> SessionState:
        """Restaura el estado al checkpoint indicado o al más reciente, prohibiendo transiciones fallidas."""
        state = self._ensure_session()
        target_id = checkpoint_id
        if not target_id:
            latest = self.checkpoint_manager.get_latest_checkpoint()
            if not latest:
                raise RuntimeError("No existen checkpoints disponibles para rollback.")
            target_id = latest.id

        restored_state = self.checkpoint_manager.rollback(
            checkpoint_id=target_id,
            current_state=state,
            culprit_tool=culprit_tool,
            reason=reason,
        )
        self.state = restored_state
        return self.state

    def get_receipts(self) -> List[DecisionReceipt]:
        """Devuelve todos los recibos de auditoría emitidos."""
        return list(self.audit_receipts)
