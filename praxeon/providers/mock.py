"""Proveedor simulado determinista y configurable para pruebas unitarias y de integración sin red."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from praxeon.domain.models import ActionCandidate, ProviderAssessment
from praxeon.providers.base import BaseReasoningProvider


class MockProvider(BaseReasoningProvider):
    """Proveedor mock completamente desacoplado de dependencias de red o claves API externas."""

    def __init__(
        self,
        name: str = "mock",
        model_name: str = "mock-semantic-v1",
        default_confidence: float = 0.90,
        default_grounded_probability: float = 0.95,
        default_loop_probability: float = 0.05,
        default_progress_probability: Optional[float] = None,
        default_novelty_probability: float = 0.85,
        default_reason_codes: Optional[List[str]] = None,
        available: bool = True,
    ):
        self.name = name
        self.model_name = model_name
        self.default_confidence = default_confidence
        self.default_grounded_probability = default_grounded_probability
        self.default_loop_probability = default_loop_probability
        self.default_progress_probability = (
            default_progress_probability
            if default_progress_probability is not None
            else default_confidence
        )
        self.default_novelty_probability = default_novelty_probability
        self.default_reason_codes = default_reason_codes or ["DETERMINISTIC_PROGRESS"]
        self.available = available

        self._action_overrides: Dict[str, ProviderAssessment] = {}
        self._tool_overrides: Dict[str, ProviderAssessment] = {}
        self._queued_assessments: List[ProviderAssessment] = []
        self._simulated_failure: Optional[str] = None
        self.calls_received: List[Dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        """Número de invocaciones recibidas por este mock."""
        return len(self.calls_received)

    def is_available(self) -> bool:
        """Comprueba disponibilidad técnica simulada."""
        return self.available and (self._simulated_failure is None)

    def simulate_failure(self, failure_reason: Optional[str] = "Simulated outage") -> None:
        """Simula una caída de servicio del proveedor."""
        self._simulated_failure = failure_reason

    def restore(self) -> None:
        """Restaura la disponibilidad tras un fallo simulado."""
        self._simulated_failure = None
        self.available = True

    def set_action_assessment(self, action_id: str, assessment: ProviderAssessment) -> None:
        """Asigna una evaluación específica para un action_id dado."""
        self._action_overrides[action_id] = assessment

    def set_tool_assessment(self, tool_name: str, assessment: ProviderAssessment) -> None:
        """Asigna una evaluación específica para todas las acciones con un tool_name dado."""
        self._tool_overrides[tool_name] = assessment

    def queue_assessment(self, assessment: ProviderAssessment) -> None:
        """Encola una respuesta que se consumirá en la próxima evaluación (FIFO)."""
        self._queued_assessments.append(assessment)

    def reset(self) -> None:
        """Limpia el historial de llamadas y overrides."""
        self._action_overrides.clear()
        self._tool_overrides.clear()
        self._queued_assessments.clear()
        self._simulated_failure = None
        self.calls_received.clear()
        self.available = True

    def evaluate(
        self,
        state: Any,
        actions: List[ActionCandidate],
    ) -> List[ProviderAssessment]:
        """Evalúa las acciones candidatas de forma determinista respetando overrides o colas."""
        self.calls_received.append({
            "state": state,
            "actions": actions,
        })

        results: List[ProviderAssessment] = []

        if not self.is_available():
            reason = self._simulated_failure or "Provider unavailable"
            for a in actions:
                results.append(
                    ProviderAssessment(
                        provider=self.name,
                        available=False,
                        confidence=0.0,
                        failure_reason=reason,
                        reason_codes=["MOCK_OUTAGE"],
                    )
                )
            return results

        for action in actions:
            if self._queued_assessments:
                results.append(self._queued_assessments.pop(0))
                continue

            if action.id in self._action_overrides:
                results.append(self._action_overrides[action.id])
                continue

            tool_name = action.tool_call.tool_name if action.tool_call else None
            if tool_name and tool_name in self._tool_overrides:
                results.append(self._tool_overrides[tool_name])
                continue

            # Evaluación por defecto determinista
            results.append(
                ProviderAssessment(
                    provider=self.name,
                    available=True,
                    confidence=self.default_confidence,
                    progress_probability=self.default_progress_probability,
                    loop_probability=self.default_loop_probability,
                    grounded_probability=self.default_grounded_probability,
                    novelty_probability=self.default_novelty_probability,
                    reason_codes=list(self.default_reason_codes),
                )
            )

        return results
