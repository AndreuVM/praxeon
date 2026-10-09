"""Runtime de decisión System-1 encapsulado por sesión/misión (PRAXEON 1.1).

Especificación Técnica:
- Cada misión/sesión posee su propia instancia de DecisionRuntime.
- Contiene su propia configuración tipada DecisionModelConfig y su propio provider instanciado.
- Mide latencia con alta precisión, enriquece metadatos de auditoría y controla degradación / fallback seguro.
- Elimina cualquier dependencia de estado mutable global o singletons.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from praxeon.domain.assessment import ProviderAssessment
from praxeon.domain.decision_provider import (
    DecisionModelConfig,
    DecisionProvider,
    DecisionProviderError,
    DecisionProviderMetadata,
)
from praxeon.domain.models import ActionCandidate
from praxeon.providers.registry import DecisionProviderRegistry, default_registry

logger = logging.getLogger("praxeon.runtime.decision_runtime")


class DecisionRuntime:
    """Componente de evaluación de decisiones aislado por sesión o misión."""

    def __init__(
        self,
        config: DecisionModelConfig,
        provider: DecisionProvider,
        registry: Optional[DecisionProviderRegistry] = None,
    ):
        self.config = config
        self.provider = provider
        self.registry = registry or default_registry

        self._call_count: int = 0
        self._fallback_count: int = 0
        self._total_latency_ms: float = 0.0
        self._last_assessment_timestamp: Optional[float] = None

    @classmethod
    def from_config(
        cls,
        config: DecisionModelConfig,
        registry: Optional[DecisionProviderRegistry] = None,
    ) -> "DecisionRuntime":
        """Construye un DecisionRuntime instanciando el provider a través del registro."""
        reg = registry or default_registry
        provider_instance = reg.create(config)
        return cls(config=config, provider=provider_instance, registry=reg)

    @property
    def provider_id(self) -> str:
        """Identificador del proveedor en ejecución."""
        return self.provider.provider_id

    @property
    def model_id(self) -> str:
        """Identificador del modelo en ejecución."""
        return self.config.model_id

    def is_available(self) -> bool:
        """Verifica disponibilidad operativa del proveedor."""
        fn = getattr(self.provider, "is_available", None)
        if callable(fn):
            return fn()
        return bool(getattr(self.provider, "is_available", getattr(self.provider, "available", True)))

    def get_metadata(self) -> DecisionProviderMetadata:
        """Obtiene metadatos completos del proveedor."""
        base_meta = self.provider.metadata()
        # Enriquecer con configuración efectiva
        return DecisionProviderMetadata(
            provider_id=base_meta.provider_id,
            model_id=self.config.model_id or base_meta.model_id,
            model_version=self.config.model_version or base_meta.model_version,
            backend=self.config.backend or base_meta.backend,
            device=self.config.device or base_meta.device,
            is_available=base_meta.is_available,
            capabilities=base_meta.capabilities,
            calibration_profile=self.config.calibration_profile,
            extra_info={
                **base_meta.extra_info,
                "timeout_seconds": self.config.timeout_seconds,
                "fallback_policy": self.config.fallback_policy,
            },
        )

    def evaluate(
        self,
        state: Any,
        actions: List[ActionCandidate],
    ) -> List[ProviderAssessment]:
        """Evalúa semánticamente las acciones candidatas, midiendo latencia y enriqueciendo metadatos."""
        self._call_count += 1
        start_time = time.perf_counter()
        is_fallback_active = False
        degradation_reason = None

        raw_assessments: List[ProviderAssessment] = []

        try:
            if not self.provider.is_available():
                raise DecisionProviderError(f"Provider '{self.provider_id}' is not operational or reachable.")

            raw_assessments = self.provider.evaluate(state, actions)

        except Exception as exc:
            logger.warning(
                f"Fallo durante evaluación con '{self.provider_id}' ({exc}). Evaluando política de fallback..."
            )
            if self.config.fallback_policy != "none":
                # Aplicar fallback seguro
                self._fallback_count += 1
                is_fallback_active = True
                degradation_reason = str(exc)
                target_fb = self.config.fallback_provider or (
                    self.config.fallback_policy if self.registry.is_registered(self.config.fallback_policy) else "mock"
                )
                logger.info(f"Activando provider de fallback '{target_fb}' para la sesión.")
                try:
                    fallback_prov = self.registry.create(
                        DecisionModelConfig(provider=target_fb, model_id="fallback-safe", fallback_policy="none")
                    )
                    raw_assessments = fallback_prov.evaluate(state, actions)
                except Exception as fb_exc:
                    logger.error(f"Fallo en provider de fallback '{target_fb}': {fb_exc}")
                    raw_assessments = [
                        ProviderAssessment(
                            provider=self.provider_id,
                            provider_id=self.provider_id,
                            model=self.model_id,
                            model_name=self.model_id,
                            available=False,
                            confidence=0.0,
                            failure_reason=f"Primary failed ({exc}); Fallback failed ({fb_exc})",
                            reason_codes=["PROVIDER_AND_FALLBACK_FAILED"],
                        )
                        for _ in actions
                    ]
            else:
                raw_assessments = [
                    ProviderAssessment(
                        provider=self.provider_id,
                        provider_id=self.provider_id,
                        model=self.model_id,
                        model_name=self.model_id,
                        available=False,
                        confidence=0.0,
                        failure_reason=str(exc),
                        reason_codes=["EVALUATION_ERROR"],
                    )
                    for _ in actions
                ]

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        self._total_latency_ms += elapsed_ms
        self._last_assessment_timestamp = time.time()

        # Enriquecer cada ProviderAssessment con los metadatos de auditoría estandarizados (DMI-07)
        enriched: List[ProviderAssessment] = []
        for ass in raw_assessments:
            # Crear nueva instancia con campos enriquecidos si faltan
            item_data = ass.model_dump()
            item_data["provider_id"] = self.provider_id
            item_data["model_name"] = self.config.model_id or ass.model
            item_data["model_version"] = self.config.model_version
            item_data["backend"] = self.config.backend
            item_data["latency_ms"] = round(elapsed_ms, 2)
            item_data["is_fallback"] = is_fallback_active
            if degradation_reason:
                item_data["degradation_reason"] = degradation_reason
            item_data["calibration_profile"] = self.config.calibration_profile
            enriched.append(ProviderAssessment(**item_data))

        return enriched

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
            provider=self.provider_id,
            provider_id=self.provider_id,
            model=self.model_id,
            model_name=self.model_id,
            available=False,
            confidence=0.0,
            failure_reason="Empty evaluation result",
        )

    def get_telemetry(self) -> Dict[str, Any]:
        """Devuelve las métricas de rendimiento y resiliencia del runtime de decisión."""
        avg_lat = (
            round(self._total_latency_ms / self._call_count, 2)
            if self._call_count > 0
            else 0.0
        )
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "backend": self.config.backend,
            "call_count": self._call_count,
            "fallback_count": self._fallback_count,
            "avg_latency_ms": avg_lat,
            "total_latency_ms": round(self._total_latency_ms, 2),
            "last_assessment_timestamp": self._last_assessment_timestamp,
        }
