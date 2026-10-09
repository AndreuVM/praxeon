"""Clase base abstracta y contratos formales para proveedores de decisión en PRAXEON (PRAXEON 1.1)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from praxeon.domain.decision_provider import DecisionModelConfig, DecisionProviderMetadata
from praxeon.domain.models import ActionCandidate, ProviderAssessment


class BaseReasoningProvider(ABC):
    """Clase base canónica para adaptadores de proveedores semánticos System-1."""

    name: str = "base"
    model_name: str = "default"
    model_version: Optional[str] = None
    backend: str = "local"
    device: Optional[str] = None
    capabilities: List[str] = []

    @property
    def provider_id(self) -> str:
        """Identificador del proveedor (implementación de DecisionProvider.provider_id)."""
        return self.name

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
            provider_id=self.provider_id,
            model=self.model_name,
            model_name=self.model_name,
            available=False,
            confidence=0.0,
            failure_reason="No assessment produced",
        )

    def is_available(self) -> bool:
        """Indica si el proveedor está operativo y listo para responder."""
        val = getattr(self, "available", True)
        return bool(val)

    def metadata(self) -> DecisionProviderMetadata:
        """Retorna los metadatos estructurados del proveedor."""
        avail_attr = getattr(self, "is_available", True)
        avail_bool = avail_attr() if callable(avail_attr) else bool(avail_attr)
        return DecisionProviderMetadata(
            provider_id=self.provider_id,
            model_id=self.model_name,
            model_version=self.model_version,
            backend=self.backend,
            device=self.device,
            is_available=avail_bool,
            capabilities=list(self.capabilities),
            extra_info={"name": self.name},
        )


# Alias canónico formal
Provider = BaseReasoningProvider

__all__ = ["BaseReasoningProvider", "Provider"]
