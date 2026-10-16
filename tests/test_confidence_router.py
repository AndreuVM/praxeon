"""Pruebas unitarias e integradas para el enrutador consciente de la confianza (Fase 3 / v0.4).

Verifica la arquitectura JEV-as-a-Judge de dos niveles:
- Vía rápida local (Fast-path System-1 / LAYA) ante alta confianza
- Escalado dinámico al supervisor (System-2 / TypeSafe) ante incertidumbre
- Detección de incertidumbre dual y abstención formal
- Umbrales dinámicos adaptados al nivel de riesgo operacional
- Manejo de fallos del proveedor primario
- Telemetría de enrutamiento y detección de desacuerdos
- Integración end-to-end con Navigator y PolicyEngine
"""

from typing import Any, List
import pytest

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.assessment import ProviderAssessment, RiskAssessment, RiskLevel
from praxeon.domain.decision import DecisionStatus
from praxeon.domain.goal import Goal
from praxeon.providers.base import BaseReasoningProvider
from praxeon.providers.router import (
    ConfidenceAwareRouter,
    RoutingStrategy,
    RoutingTelemetry,
)
from praxeon.runtime.navigator import Navigator


class MockReasoningProvider(BaseReasoningProvider):
    """Proveedor mock configurable para pruebas deterministas de enrutamiento."""

    def __init__(
        self,
        name: str = "mock",
        confidence: float = 0.8,
        loop_prob: float = 0.05,
        grounded_prob: float = 0.95,
        progress_prob: float = 0.9,
        available: bool = True,
        should_raise: bool = False,
    ):
        self.name = name
        self.confidence = confidence
        self.loop_prob = loop_prob
        self.grounded_prob = grounded_prob
        self.progress_prob = progress_prob
        self.available = available
        self.should_raise = should_raise
        self.call_count = 0

    def evaluate(self, state: Any, actions: List[ActionCandidate]) -> List[ProviderAssessment]:
        self.call_count += len(actions)
        if self.should_raise:
            raise RuntimeError(f"Simulated crash in {self.name}")

        return [
            ProviderAssessment(
                provider=self.name,
                model=f"{self.name}-model-v1",
                available=self.available,
                confidence=self.confidence,
                loop_probability=self.loop_prob,
                grounded_probability=self.grounded_prob,
                progress_probability=self.progress_prob,
                failure_reason=None if self.available else "Provider unavailable",
            )
            for _ in actions
        ]


@pytest.fixture
def sample_action():
    return ActionCandidate(
        id="act_read",
        description="Read documentation file",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "docs.md"}),
    )


def test_fast_path_when_primary_confidence_is_high(sample_action):
    """Si el proveedor primario tiene confianza >= umbral, se acepta sin consultar el secundario."""
    primary = MockReasoningProvider(name="laya_fast", confidence=0.85)
    secondary = MockReasoningProvider(name="typesafe_supervisor", confidence=0.95)

    router = ConfidenceAwareRouter(
        primary_provider=primary,
        secondary_provider=secondary,
        default_confidence_threshold=0.70,
    )

    assessments = router.evaluate(state={}, actions=[sample_action])
    assert len(assessments) == 1
    assessment = assessments[0]

    # Verificaciones de vía rápida
    assert assessment.provider == "laya_fast"
    assert assessment.metadata["routed_to"] == "primary"
    assert assessment.metadata["escalated"] is False
    assert assessment.metadata["routing_verdict"] == "CONFIDENT_LOCAL_ACCEPTED"

    # El proveedor secundario nunca debió ser invocado
    assert primary.call_count == 1
    assert secondary.call_count == 0

    telemetry = router.get_telemetry()
    assert telemetry.total_queries == 1
    assert telemetry.local_accepted_count == 1
    assert telemetry.escalated_count == 0
    assert telemetry.local_decision_rate == 1.0


def test_escalation_when_primary_confidence_is_low(sample_action):
    """Si el proveedor primario tiene baja confianza (< umbral), escala al secundario."""
    primary = MockReasoningProvider(name="laya_fast", confidence=0.45)
    secondary = MockReasoningProvider(name="typesafe_supervisor", confidence=0.92)

    router = ConfidenceAwareRouter(
        primary_provider=primary,
        secondary_provider=secondary,
        default_confidence_threshold=0.70,
    )

    assessments = router.evaluate(state={}, actions=[sample_action])
    assert len(assessments) == 1
    assessment = assessments[0]

    # Verificaciones de escalado
    assert assessment.provider == "typesafe_supervisor"
    assert assessment.metadata["routed_to"] == "secondary"
    assert assessment.metadata["escalated"] is True
    assert assessment.metadata["escalation_reason"] == "LOW_PRIMARY_CONFIDENCE"
    assert assessment.metadata["primary_confidence"] == 0.45
    assert "ESCALATED_FROM_PRIMARY" in assessment.reason_codes[0]

    # Ambos proveedores fueron consultados
    assert primary.call_count == 1
    assert secondary.call_count == 1

    telemetry = router.get_telemetry()
    assert telemetry.total_queries == 1
    assert telemetry.local_accepted_count == 0
    assert telemetry.escalated_count == 1
    assert telemetry.escalation_rate == 1.0


def test_dual_uncertainty_handling(sample_action):
    """Si tanto primario como secundario son inciertos, se marca incertidumbre dual."""
    primary = MockReasoningProvider(name="laya_fast", confidence=0.40)
    secondary = MockReasoningProvider(name="typesafe_supervisor", confidence=0.35)

    router = ConfidenceAwareRouter(
        primary_provider=primary,
        secondary_provider=secondary,
        default_confidence_threshold=0.70,
        secondary_confidence_threshold=0.50,
    )

    assessments = router.evaluate(state={}, actions=[sample_action])
    assert len(assessments) == 1
    assessment = assessments[0]

    assert assessment.metadata["routed_to"] == "secondary"
    assert assessment.metadata["dual_uncertainty"] is True
    assert "DUAL_PROVIDER_UNCERTAINTY_ESCALATE" in assessment.reason_codes

    telemetry = router.get_telemetry()
    assert telemetry.dual_uncertain_count == 1
    assert telemetry.dual_uncertainty_rate == 1.0


def test_risk_weighted_confidence_thresholds():
    """El umbral de confianza debe ser más estricto para acciones de mayor riesgo."""
    primary = MockReasoningProvider(name="laya_fast", confidence=0.75)
    secondary = MockReasoningProvider(name="typesafe_supervisor", confidence=0.90)

    # Assessor que clasifica rm/delete como CRITICAL y read como LOW
    def mock_risk_assessor(action: ActionCandidate) -> RiskAssessment:
        tname = action.tool_call.tool_name if action.tool_call else ""
        if "delete" in tname or "rm" in tname:
            return RiskAssessment(level=RiskLevel.CRITICAL, destructive_potential=True)
        return RiskAssessment(level=RiskLevel.LOW, destructive_potential=False)

    router = ConfidenceAwareRouter(
        primary_provider=primary,
        secondary_provider=secondary,
        default_confidence_threshold=0.70,
        risk_thresholds={
            RiskLevel.LOW: 0.60,
            RiskLevel.CRITICAL: 0.90,
        },
        risk_assessor=mock_risk_assessor,
    )

    # 1. Acción de bajo riesgo con confianza 0.75 (umbral 0.60): VÍA RÁPIDA
    low_action = ActionCandidate(
        id="act_low",
        description="Read file",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "a.txt"}),
    )
    res_low = router.evaluate(state={}, actions=[low_action])[0]
    assert res_low.metadata["routed_to"] == "primary"
    assert res_low.metadata["escalated"] is False

    # 2. Acción crítica con la MISMA confianza 0.75 (umbral 0.90): ESCALADO
    critical_action = ActionCandidate(
        id="act_crit",
        description="Delete file",
        tool_call=ToolCall(tool_name="delete_file", arguments={"path": "prod.db"}),
    )
    res_crit = router.evaluate(state={}, actions=[critical_action])[0]
    assert res_crit.metadata["routed_to"] == "secondary"
    assert res_crit.metadata["escalated"] is True
    assert res_crit.metadata["confidence_threshold"] == 0.90


def test_primary_failure_fallback_to_secondary(sample_action):
    """Si el proveedor primario falla o no está disponible, escala limpiamente al secundario."""
    primary = MockReasoningProvider(name="laya_fast", available=False)
    secondary = MockReasoningProvider(name="typesafe_supervisor", confidence=0.88)

    router = ConfidenceAwareRouter(
        primary_provider=primary,
        secondary_provider=secondary,
        escalate_on_unavailable=True,
    )

    assessments = router.evaluate(state={}, actions=[sample_action])
    assessment = assessments[0]

    assert assessment.metadata["routed_to"] == "secondary"
    assert assessment.metadata["escalation_reason"] == "PRIMARY_UNAVAILABLE"
    assert assessment.confidence == 0.88

    telemetry = router.get_telemetry()
    assert telemetry.primary_failures_count == 1
    assert telemetry.escalated_count == 1


def test_primary_exception_handled_cleanly(sample_action):
    """Si el proveedor primario lanza una excepción inesperada, escala sin romper el pipeline."""
    primary = MockReasoningProvider(name="laya_crash", should_raise=True)
    secondary = MockReasoningProvider(name="typesafe_supervisor", confidence=0.85)

    router = ConfidenceAwareRouter(
        primary_provider=primary,
        secondary_provider=secondary,
    )

    assessments = router.evaluate(state={}, actions=[sample_action])
    assert len(assessments) == 1
    assert assessments[0].provider == "typesafe_supervisor"
    assert assessments[0].metadata["escalated"] is True


def test_shadow_evaluation_strategy_detects_disagreement(sample_action):
    """La estrategia SHADOW_EVALUATE ejecuta ambos proveedores y detecta desacuerdos."""
    # Primario no ve bucle, secundario ve bucle
    primary = MockReasoningProvider(name="laya_fast", loop_prob=0.1, confidence=0.8)
    secondary = MockReasoningProvider(name="typesafe_supervisor", loop_prob=0.8, confidence=0.9)

    router = ConfidenceAwareRouter(
        primary_provider=primary,
        secondary_provider=secondary,
        strategy=RoutingStrategy.SHADOW_EVALUATE,
    )

    assessments = router.evaluate(state={}, actions=[sample_action])
    assessment = assessments[0]

    # Retorna la decisión del primario pero enriquecida con telemetría de shadow
    assert assessment.provider == "laya_fast"
    assert assessment.metadata["shadow_disagreement"] is True
    assert assessment.metadata["shadow_secondary_confidence"] == 0.9

    telemetry = router.get_telemetry()
    assert telemetry.shadow_evaluations_count == 1
    assert telemetry.disagreements_count == 1
    assert telemetry.disagreement_rate == 1.0


def test_router_integration_with_navigator_and_policy_engine(sample_action):
    """Verifica que el router se integre transparentemente como provider en Navigator."""
    primary = MockReasoningProvider(name="laya_fast", confidence=0.35)  # Provocará escalado
    secondary = MockReasoningProvider(name="typesafe_supervisor", confidence=0.88)

    router = ConfidenceAwareRouter(
        primary_provider=primary,
        secondary_provider=secondary,
        default_confidence_threshold=0.70,
    )

    nav = Navigator(provider=router)
    state = nav.start_session(Goal(objective="Test router integration"))

    results = nav.evaluate([sample_action])
    assert len(results) == 1

    action, assessment, decision, receipt = results[0]
    assert assessment.provider == "typesafe_supervisor"
    assert assessment.metadata["escalated"] is True
    assert decision.status == DecisionStatus.ALLOW
    assert receipt.decision_status == DecisionStatus.ALLOW

    # Comprobar propiedad expuesta router_telemetry
    assert nav.router_telemetry is not None
    assert nav.router_telemetry.escalated_count == 1
    assert nav.router_telemetry.local_accepted_count == 0


def test_navigator_abstains_on_dual_uncertainty(sample_action):
    """Si ambos proveedores son inciertos, Navigator y PolicyEngine emiten ABSTAIN formal."""
    primary = MockReasoningProvider(name="laya_fast", confidence=0.20)
    secondary = MockReasoningProvider(name="typesafe_supervisor", confidence=0.22)

    router = ConfidenceAwareRouter(
        primary_provider=primary,
        secondary_provider=secondary,
        default_confidence_threshold=0.70,
        secondary_confidence_threshold=0.50,
    )

    nav = Navigator(provider=router)
    nav.start_session(Goal(objective="Test dual uncertainty abstention"))

    results = nav.evaluate([sample_action])
    _, assessment, decision, receipt = results[0]

    assert assessment.metadata["dual_uncertainty"] is True
    # PolicyEngine debe abstenerse debido a baja confianza
    assert decision.status == DecisionStatus.ABSTAIN
    assert "LOW_PROVIDER_CONFIDENCE_ESCALATE" in decision.reason_codes[0]
    assert receipt.decision_status == DecisionStatus.ABSTAIN
