"""Clase base abstracta y contratos formales para proveedores de razonamiento semántico en PRAXEON."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List, Optional
from praxeon.domain.models import ActionCandidate, ProviderAssessment


class BaseReasoningProvider(ABC):
    """Clase base canónica para adaptadores de proveedores semánticos."""

    name: str = "base"
    model_name: str = "default"

    @abstractmethod
    def evaluate(
        self,
        state: Any,
        actions: List[ActionCandidate],
    ) -> List[ProviderAssessment]:
        """Evalúa semánticamente las acciones candidatas bajo el estado provisto."""
        pass

    def evaluate_step(
        self,
        state: Any,
        action: ActionCandidate,
    ) -> ProviderAssessment:
        """Evalúa un único paso o acción candidata."""
        results = self.evaluate(state, [action])
        if results:
            return results[0]
        return ProviderAssessment(
            provider=self.name,
            available=False,
            confidence=0.0,
            failure_reason="No assessment produced",
        )

    def is_available(self) -> bool:
        """Indica si el proveedor está operativo y listo para responder."""
        return True


# Alias canónico formal
Provider = BaseReasoningProvider

__all__ = ["BaseReasoningProvider", "Provider"]
