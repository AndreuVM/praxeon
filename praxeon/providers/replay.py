"""Proveedor determinista de reproducción (ReplayProvider) para pruebas y benchmarks.

Permite simular respuestas semánticas precisas sin costo de API ni dependencia de red.
"""

from typing import Any, Dict, List, Optional
from praxeon.domain.models import ActionCandidate, ProviderAssessment
from praxeon.providers.base import BaseReasoningProvider


class ReplayProvider(BaseReasoningProvider):
    """Proveedor determinista con escenarios pregrabados y control de disponibilidad."""

    DEFAULT_SCENARIOS: Dict[str, Dict[str, Any]] = {
        "safe_read": {
            "available": True,
            "confidence": 0.90,
            "progress_probability": 0.85,
            "loop_probability": 0.05,
            "grounded_probability": 0.95,
            "novelty_probability": 0.80,
            "reason_codes": ["STEADY_PROGRESS"],
        },
        "loop_detected": {
            "available": True,
            "confidence": 0.92,
            "progress_probability": 0.10,
            "loop_probability": 0.88,
            "grounded_probability": 0.40,
            "novelty_probability": 0.05,
            "reason_codes": ["LOOP_REPETITION"],
        },
        "missing_evidence": {
            "available": True,
            "confidence": 0.85,
            "progress_probability": 0.20,
            "loop_probability": 0.15,
            "grounded_probability": 0.12,
            "novelty_probability": 0.25,
            "reason_codes": ["UNGROUNDED_PREMISE"],
        },
        "provider_down": {
            "available": False,
            "confidence": 0.0,
            "failure_reason": "Connection timeout / Service unavailable",
            "reason_codes": ["TIMEOUT"],
        },
        "uncertain": {
            "available": True,
            "confidence": 0.30,
            "progress_probability": 0.45,
            "loop_probability": 0.40,
            "grounded_probability": 0.50,
            "novelty_probability": 0.40,
            "reason_codes": ["HIGH_ENTROPY"],
        },
    }

    def __init__(
        self,
        default_scenario: str = "safe_read",
        provider_name: str = "replay",
        model_name: str = "replay-mock-v1",
        trace_path: Optional[str] = None,
    ):
        self.provider_name = provider_name
        self.name = provider_name
        self.model_name = model_name
        self.default_scenario = default_scenario
        self.trace_path = trace_path
        self.is_available = True
        self.available = True
        self.action_overrides: Dict[str, ProviderAssessment] = {}
        self.scenario_queue: List[str] = []
        self.evaluation_history: List[Dict[str, Any]] = []

    def set_available(self, available: bool) -> None:
        """Modifica dinámicamente la disponibilidad del proveedor."""
        self.is_available = available
        self.available = available

    def override_for_action(self, action_id: str, assessment: ProviderAssessment) -> None:
        """Asigna una evaluación específica para un action_id concreto."""
        self.action_overrides[action_id] = assessment

    def queue_scenario(self, scenario_name: str) -> None:
        """Encola un escenario pregrabado para la siguiente llamada de evaluación."""
        self.scenario_queue.append(scenario_name)

    def evaluate(
        self,
        state: Any,
        actions: List[ActionCandidate],
    ) -> List[ProviderAssessment]:
        """Devuelve evaluaciones deterministas para las acciones provistas."""
        results: List[ProviderAssessment] = []

        for action in actions:
            self.evaluation_history.append({
                "action_id": action.id,
                "tool_name": action.tool_call.tool_name if action.tool_call else None,
                "state": state,
            })

            if not self.is_available:
                results.append(
                    ProviderAssessment(
                        provider=self.provider_name,
                        model=self.model_name,
                        available=False,
                        confidence=0.0,
                        failure_reason="Provider offline / Fail-safe triggered",
                        reason_codes=["PROVIDER_UNAVAILABLE"],
                    )
                )
                continue

            if action.id in self.action_overrides:
                results.append(self.action_overrides[action.id])
                continue

            # Seleccionar escenario de la cola o por defecto
            scenario_key = self.scenario_queue.pop(0) if self.scenario_queue else self.default_scenario
            scenario_data = self.DEFAULT_SCENARIOS.get(
                scenario_key,
                self.DEFAULT_SCENARIOS["safe_read"],
            )

            results.append(
                ProviderAssessment(
                    provider=self.provider_name,
                    model=self.model_name,
                    available=scenario_data.get("available", True),
                    confidence=scenario_data.get("confidence", 0.9),
                    loop_probability=scenario_data.get("loop_probability"),
                    grounded_probability=scenario_data.get("grounded_probability"),
                    progress_probability=scenario_data.get("progress_probability"),
                    novelty_probability=scenario_data.get("novelty_probability"),
                    failure_reason=scenario_data.get("failure_reason"),
                    reason_codes=scenario_data.get("reason_codes", []),
                )
            )

        return results


ReplayReasoningProvider = ReplayProvider
