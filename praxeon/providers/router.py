"""Enrutador de razonamiento consciente de la confianza (Confidence-Aware Cascade Router).

Implementa la arquitectura de dos niveles descrita en el paper:
'JEV-as-a-Judge: Accept When Confident, Escalate When Unsure' (arXiv:2609.26550, Sept 2026).

Permite orquestar una cascada entre:
- Proveedor Primario / Local (p. ej. LAYA System-1, ultrarrápido y de bajo coste)
- Proveedor Secundario / Supervisor (p. ej. TypeSafe AI o LLM hosted System-2)

La confianza actúa como señal de enrutamiento ('routing signal'), nunca como permiso directo de ejecución.
"""

from enum import Enum
import logging
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from praxeon.domain.action import ActionCandidate
from praxeon.domain.assessment import ProviderAssessment, RiskAssessment, RiskLevel
from praxeon.providers.base import BaseReasoningProvider

logger = logging.getLogger("praxeon.providers.router")


class RoutingStrategy(str, Enum):
    """Estrategias de enrutamiento soportadas."""
    CASCADE_CONFIDENCE = "cascade_confidence"  # Primario si conf >= umbral, sino Secundario
    ALWAYS_PRIMARY = "always_primary"          # Solo evalúa el proveedor primario
    ALWAYS_SECONDARY = "always_secondary"      # Solo evalúa el proveedor secundario
    SHADOW_EVALUATE = "shadow_evaluate"        # Ejecuta ambos para comparar desacuerdo sin alterar el despacho


class RoutingTelemetry(BaseModel):
    """Métricas y contadores acumulados de decisiones de enrutamiento."""
    model_config = ConfigDict(frozen=True)

    total_queries: int = 0
    local_accepted_count: int = 0
    escalated_count: int = 0
    dual_uncertain_count: int = 0
    primary_failures_count: int = 0
    secondary_failures_count: int = 0
    disagreements_count: int = 0
    shadow_evaluations_count: int = 0

    @property
    def local_decision_rate(self) -> float:
        """Proporción de consultas resueltas por el proveedor local primario sin escalar."""
        if self.total_queries == 0:
            return 0.0
        return self.local_accepted_count / self.total_queries

    @property
    def escalation_rate(self) -> float:
        """Tasa de escalado al proveedor secundario debido a baja confianza o fallo del primario."""
        if self.total_queries == 0:
            return 0.0
        return self.escalated_count / self.total_queries

    @property
    def dual_uncertainty_rate(self) -> float:
        """Tasa en que ambos proveedores resultaron inciertos o de baja confianza."""
        if self.total_queries == 0:
            return 0.0
        return self.dual_uncertain_count / self.total_queries

    @property
    def disagreement_rate(self) -> float:
        """Tasa de desacuerdo cuando ambos proveedores evalúan la misma acción."""
        evals = self.escalated_count + self.shadow_evaluations_count
        if evals == 0:
            return 0.0
        return self.disagreements_count / evals


class ConfidenceAwareRouter(BaseReasoningProvider):
    """Enrutador de inferencia semántica en cascada consciente de la confianza.
    
    Flujo normativo:
    1. Consulta al proveedor primario (System-1 / LAYA).
    2. Comprueba su nivel de confianza contra el umbral correspondiente (ponderado por riesgo si aplica).
    3. Si confianza >= umbral: Acepta juicio localmente ('fast-path').
    4. Si confianza < umbral o primario falla: Escala al supervisor secundario (System-2 / TypeSafe AI).
    5. Si ambos proveedores son inciertos: Señaliza incertidumbre dual para abstención (`ABSTAIN`) en PolicyEngine.
    """

    def __init__(
        self,
        primary_provider: BaseReasoningProvider,
        secondary_provider: BaseReasoningProvider,
        default_confidence_threshold: float = 0.70,
        secondary_confidence_threshold: float = 0.50,
        risk_thresholds: Optional[Dict[RiskLevel, float]] = None,
        strategy: RoutingStrategy = RoutingStrategy.CASCADE_CONFIDENCE,
        escalate_on_unavailable: bool = True,
        track_disagreements: bool = True,
        risk_assessor: Optional[Callable[[ActionCandidate], RiskAssessment]] = None,
    ):
        self.primary = primary_provider
        self.secondary = secondary_provider
        self.name = "cascade"
        self.backend = "cascade"
        self.model_name = f"{getattr(primary_provider, 'name', 'primary')}+{getattr(secondary_provider, 'name', 'secondary')}"
        self.default_confidence_threshold = default_confidence_threshold
        self.secondary_confidence_threshold = secondary_confidence_threshold
        self.risk_thresholds = risk_thresholds or {
            RiskLevel.LOW: 0.60,
            RiskLevel.MEDIUM: 0.70,
            RiskLevel.HIGH: 0.85,
            RiskLevel.CRITICAL: 0.95,
        }
        self.strategy = strategy
        self.escalate_on_unavailable = escalate_on_unavailable
        self.track_disagreements = track_disagreements
        self.risk_assessor = risk_assessor

        # Contadores internos mutables para telemetría
        self._total_queries = 0
        self._local_accepted_count = 0
        self._escalated_count = 0
        self._dual_uncertain_count = 0
        self._primary_failures_count = 0
        self._secondary_failures_count = 0
        self._disagreements_count = 0
        self._shadow_evaluations_count = 0

    def is_available(self) -> bool:
        """La cascada responde mientras al menos uno de sus dos niveles esté operativo."""
        return any(
            bool(p.is_available()) if callable(getattr(p, "is_available", None)) else True
            for p in (self.primary, self.secondary)
        )

    def get_threshold_for_action(self, action: ActionCandidate) -> float:
        """Determina el umbral de confianza exigido según el riesgo contextual de la acción."""
        if self.risk_assessor is not None:
            try:
                risk = self.risk_assessor(action)
                if risk.level in self.risk_thresholds:
                    return self.risk_thresholds[risk.level]
            except Exception as e:
                logger.warning("Fallo al evaluar riesgo en router: %s", e)
        return self.default_confidence_threshold

    def _assessments_disagree(self, a1: ProviderAssessment, a2: ProviderAssessment) -> bool:
        """Detecta si dos evaluaciones semánticas presentan discrepancia sustancial."""
        # Si uno detecta bucle y el otro no
        loop1 = (a1.loop_probability or 0.0) >= 0.5
        loop2 = (a2.loop_probability or 0.0) >= 0.5
        if loop1 != loop2:
            return True

        # Si uno considera la acción fundamentada y el otro alucinada
        ground1 = (a1.grounded_probability or 0.0) >= 0.5
        ground2 = (a2.grounded_probability or 0.0) >= 0.5
        if ground1 != ground2:
            return True

        # Si difieren drásticamente en score JEV (diferencia > 0.4)
        jev1 = a1.analytical_jev or a1.progress_probability or 0.0
        jev2 = a2.analytical_jev or a2.progress_probability or 0.0
        if abs(jev1 - jev2) > 0.40:
            return True

        return False

    def evaluate(
        self,
        state: Any,
        actions: List[ActionCandidate],
    ) -> List[ProviderAssessment]:
        """Evalúa las acciones mediante la estrategia de enrutamiento configurada."""
        if not actions:
            return []

        results: List[ProviderAssessment] = []
        for action in actions:
            assessment = self._evaluate_single_action(state, action)
            results.append(assessment)
        return results

    def _evaluate_single_action(self, state: Any, action: ActionCandidate) -> ProviderAssessment:
        self._total_queries += 1
        threshold = self.get_threshold_for_action(action)

        if self.strategy == RoutingStrategy.ALWAYS_PRIMARY:
            return self._call_primary(state, action)[0]

        if self.strategy == RoutingStrategy.ALWAYS_SECONDARY:
            return self._call_secondary(state, action)[0]

        # 1. Llamar al proveedor primario
        primary_assessments = self._call_primary(state, action)
        primary = primary_assessments[0] if primary_assessments else ProviderAssessment(
            provider="primary",
            available=False,
            confidence=0.0,
            failure_reason="No evaluation returned from primary",
        )

        # Manejo de Shadow Evaluation
        if self.strategy == RoutingStrategy.SHADOW_EVALUATE:
            self._shadow_evaluations_count += 1
            secondary_assessments = self._call_secondary(state, action)
            secondary = secondary_assessments[0] if secondary_assessments else ProviderAssessment(
                provider="secondary",
                available=False,
                confidence=0.0,
            )
            disagree = self._assessments_disagree(primary, secondary)
            if disagree:
                self._disagreements_count += 1

            # Retornar primario con metadata de shadow
            meta = dict(primary.metadata)
            meta["shadow_secondary_confidence"] = secondary.confidence
            meta["shadow_disagreement"] = disagree
            return primary.model_copy(update={"metadata": meta})

        # 2. Comprobar si el primario falló
        if not primary.available:
            self._primary_failures_count += 1
            if not self.escalate_on_unavailable:
                return primary

            logger.info("Primario no disponible. Escalando a secundario para acción '%s'.", action.id)
            self._escalated_count += 1
            secondary_assessments = self._call_secondary(state, action)
            secondary = secondary_assessments[0] if secondary_assessments else ProviderAssessment(
                provider="secondary",
                available=False,
                confidence=0.0,
                failure_reason="Secondary failed after primary unavailable",
            )
            meta = dict(secondary.metadata)
            meta.update({
                "routed_to": "secondary",
                "escalated": True,
                "escalation_reason": "PRIMARY_UNAVAILABLE",
                "primary_failure": primary.failure_reason,
            })
            return secondary.model_copy(update={"metadata": meta})

        # 3. Comprobar confianza del primario
        if primary.confidence >= threshold:
            # VÍA RÁPIDA: Confianza suficiente en System-1
            self._local_accepted_count += 1
            meta = dict(primary.metadata)
            meta.update({
                "routed_to": "primary",
                "escalated": False,
                "confidence_threshold": threshold,
                "routing_verdict": "CONFIDENT_LOCAL_ACCEPTED",
            })
            return primary.model_copy(update={"metadata": meta})

        # 4. Confianza insuficiente: ESCALAR a System-2 (Supervisor)
        self._escalated_count += 1
        logger.info(
            "Baja confianza en primario (%.2f < %.2f). Escalando acción '%s' a secundario.",
            primary.confidence,
            threshold,
            action.id,
        )

        secondary_assessments = self._call_secondary(state, action)
        secondary = secondary_assessments[0] if secondary_assessments else ProviderAssessment(
            provider="secondary",
            available=False,
            confidence=0.0,
            failure_reason="No evaluation returned from secondary",
        )

        if not secondary.available:
            self._secondary_failures_count += 1

        # Detección de desacuerdo
        disagree = False
        if primary.available and secondary.available:
            disagree = self._assessments_disagree(primary, secondary)
            if disagree:
                self._disagreements_count += 1

        # 5. Comprobar si el secundario también es incierto (Dual Uncertainty)
        is_dual_uncertain = (
            not secondary.available
            or secondary.confidence < self.secondary_confidence_threshold
        )

        reason_codes = list(secondary.reason_codes)
        reason_codes.append(
            f"ESCALATED_FROM_PRIMARY (conf: {primary.confidence:.2f} < {threshold:.2f})"
        )

        if is_dual_uncertain:
            self._dual_uncertain_count += 1
            reason_codes.append("DUAL_PROVIDER_UNCERTAINTY_ESCALATE")

        meta = dict(secondary.metadata)
        meta.update({
            "routed_to": "secondary",
            "escalated": True,
            "escalation_reason": "LOW_PRIMARY_CONFIDENCE",
            "primary_confidence": primary.confidence,
            "confidence_threshold": threshold,
            "dual_uncertainty": is_dual_uncertain,
            "disagreement_with_primary": disagree,
        })

        return secondary.model_copy(update={
            "metadata": meta,
            "reason_codes": reason_codes,
        })

    def _call_primary(self, state: Any, action: ActionCandidate) -> List[ProviderAssessment]:
        try:
            return self.primary.evaluate(state, [action])
        except Exception as e:
            logger.error("Error llamando al proveedor primario: %s", e)
            return [
                ProviderAssessment(
                    provider="primary",
                    available=False,
                    confidence=0.0,
                    failure_reason=f"Exception in primary provider: {str(e)}",
                )
            ]

    def _call_secondary(self, state: Any, action: ActionCandidate) -> List[ProviderAssessment]:
        try:
            return self.secondary.evaluate(state, [action])
        except Exception as e:
            logger.error("Error llamando al proveedor secundario: %s", e)
            return [
                ProviderAssessment(
                    provider="secondary",
                    available=False,
                    confidence=0.0,
                    failure_reason=f"Exception in secondary provider: {str(e)}",
                )
            ]

    def get_telemetry(self) -> RoutingTelemetry:
        """Devuelve una instantánea inmutable de las métricas de enrutamiento acumuladas."""
        return RoutingTelemetry(
            total_queries=self._total_queries,
            local_accepted_count=self._local_accepted_count,
            escalated_count=self._escalated_count,
            dual_uncertain_count=self._dual_uncertain_count,
            primary_failures_count=self._primary_failures_count,
            secondary_failures_count=self._secondary_failures_count,
            disagreements_count=self._disagreements_count,
            shadow_evaluations_count=self._shadow_evaluations_count,
        )

    def reset_telemetry(self) -> None:
        """Reinicia todos los contadores de telemetría."""
        self._total_queries = 0
        self._local_accepted_count = 0
        self._escalated_count = 0
        self._dual_uncertain_count = 0
        self._primary_failures_count = 0
        self._secondary_failures_count = 0
        self._disagreements_count = 0
        self._shadow_evaluations_count = 0
