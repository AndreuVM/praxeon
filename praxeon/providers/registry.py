"""Registro central y factorías dinámicas de DecisionProvider (PRAXEON 1.1).

Especificación Técnica:
- Soporte para registro diferido (lazy loading) y factorías configurables.
- Validación preventiva de dependencias opcionales instaladas (extras de packaging) vía importlib.
- Resolución estricta de fallback sin auto-concesión permisiva (evita ALLOWs silenciosos).
- Registro formal de metadatos de capacidades por modelo y backend.
"""

from __future__ import annotations

import importlib.util
import logging
from typing import Any, Callable, Dict, List, Optional

from praxeon.domain.decision_provider import (
    DecisionModelConfig,
    DecisionProvider,
    DecisionProviderError,
    DecisionProviderMetadata,
    DecisionProviderUnavailableError,
)

logger = logging.getLogger("praxeon.providers.registry")

# Tipo de factoría: Callable que recibe DecisionModelConfig y devuelve un DecisionProvider
ProviderFactory = Callable[[DecisionModelConfig], DecisionProvider]


class ProviderFactoryEntry:
    """Entrada de registro para un proveedor de decisión."""

    def __init__(
        self,
        provider_id: str,
        factory: ProviderFactory,
        required_extra: Optional[str] = None,
        required_module: Optional[str] = None,
        description: str = "",
        default_backend: str = "local",
    ):
        self.provider_id = provider_id.lower().strip()
        self.factory = factory
        self.required_extra = required_extra
        self.required_module = required_module
        self.description = description
        self.default_backend = default_backend

    def is_module_installed(self) -> bool:
        """Comprueba si el módulo requerido para este proveedor está instalado en el entorno."""
        if not self.required_module:
            return True
        return importlib.util.find_spec(self.required_module) is not None


class DecisionProviderRegistry:
    """Registro unificado y extensible de proveedores de juicio semántico System-1."""

    def __init__(self):
        self._entries: Dict[str, ProviderFactoryEntry] = {}

    def register(
        self,
        provider_id: str,
        factory: ProviderFactory,
        required_extra: Optional[str] = None,
        required_module: Optional[str] = None,
        description: str = "",
        default_backend: str = "local",
    ) -> None:
        """Registra una factoría para un identificador de proveedor."""
        pid = provider_id.lower().strip()
        self._entries[pid] = ProviderFactoryEntry(
            provider_id=pid,
            factory=factory,
            required_extra=required_extra,
            required_module=required_module,
            description=description,
            default_backend=default_backend,
        )
        logger.debug(f"Registrado DecisionProvider: '{pid}' (extra={required_extra}, module={required_module})")

    def unregister(self, provider_id: str) -> None:
        """Elimina un proveedor del registro."""
        pid = provider_id.lower().strip()
        self._entries.pop(pid, None)

    def is_registered(self, provider_id: str) -> bool:
        """Determina si un proveedor ha sido registrado."""
        return provider_id.lower().strip() in self._entries

    def is_available(self, provider_id: str) -> bool:
        """Indica si el proveedor está registrado y sus dependencias de entorno están instaladas."""
        pid = provider_id.lower().strip()
        entry = self._entries.get(pid)
        if not entry:
            return False
        return entry.is_module_installed()

    def list_providers(self) -> List[Dict[str, Any]]:
        """Lista todos los proveedores registrados y su disponibilidad efectiva."""
        results = []
        for pid, entry in sorted(self._entries.items()):
            installed = entry.is_module_installed()
            results.append({
                "provider_id": pid,
                "description": entry.description,
                "required_extra": entry.required_extra,
                "required_module": entry.required_module,
                "installed": installed,
                "default_backend": entry.default_backend,
            })
        return results

    def create(self, config: DecisionModelConfig) -> DecisionProvider:
        """Crea e inicializa una instancia de DecisionProvider según la configuración especificada.

        Aplica las reglas arquitectónicas de PRAXEON 1.1:
        1. Valida si el proveedor está registrado.
        2. Verifica que las dependencias requeridas (extras) estén instaladas.
        3. Aplica fallback únicamente si está configurado explícitamente ante indisponibilidad.
        4. NUNCA concede ALLOW automático o silencioso ante indisponibilidad.
        """
        pid = config.provider.lower().strip()
        entry = self._entries.get(pid)

        if not entry:
            # Si el provider no existe en el registro
            if config.fallback_policy != "none" and config.fallback_provider:
                logger.warning(
                    f"Proveedor '{config.provider}' desconocido. Aplicando fallback a '{config.fallback_provider}'"
                )
                fallback_config = config.model_copy(update={"provider": config.fallback_provider, "fallback_policy": "none"})
                return self.create(fallback_config)
            registered = list(self._entries.keys())
            raise DecisionProviderError(
                f"Unknown decision provider '{config.provider}'. Available registered providers: {registered}"
            )

        # Comprobar si las dependencias del módulo están instaladas
        if not entry.is_module_installed():
            msg = (
                f"Decision provider '{config.provider}' is unavailable: module '{entry.required_module}' is not installed. "
                f"Please install with: pip install \"praxeon[decision-{entry.required_extra or config.provider}]\""
            )
            logger.warning(msg)

            if config.fallback_policy != "none":
                target_fb = config.fallback_provider or (
                    config.fallback_policy if config.fallback_policy in self._entries else "mock"
                )
                logger.warning(f"Aplicando fallback configurado hacia '{target_fb}' debido a indisponibilidad de '{pid}'")
                fallback_config = config.model_copy(update={"provider": target_fb, "fallback_policy": "none"})
                return self.create(fallback_config)

            raise DecisionProviderUnavailableError(msg)

        # Instanciar a través de la factoría
        try:
            provider = entry.factory(config)
        except Exception as e:
            if config.fallback_policy != "none":
                target_fb = config.fallback_provider or "mock"
                logger.warning(f"Error al instanciar '{pid}' ({e}). Activando fallback hacia '{target_fb}'")
                fallback_config = config.model_copy(update={"provider": target_fb, "fallback_policy": "none"})
                return self.create(fallback_config)
            raise DecisionProviderError(f"Failed to instantiate decision provider '{pid}': {e}") from e

        # Verificar disponibilidad operativa
        is_avail = provider.is_available() if callable(getattr(provider, "is_available", None)) else bool(getattr(provider, "is_available", getattr(provider, "available", True)))
        if not is_avail:
            if config.fallback_policy != "none":
                target_fb = config.fallback_provider or "mock"
                logger.warning(f"Proveedor '{pid}' no está operativo. Activando fallback hacia '{target_fb}'")
                fallback_config = config.model_copy(update={"provider": target_fb, "fallback_policy": "none"})
                return self.create(fallback_config)

        return provider


# ==============================================================================
# Factorías estándar predeterminadas (Built-in Factories)
# ==============================================================================

def _create_mock_provider(config: DecisionModelConfig) -> DecisionProvider:
    from praxeon.providers.mock import MockProvider

    return MockProvider(
        name=config.provider,
        model_name=config.model_id or "mock-semantic-v1",
        default_confidence=config.custom_params.get("default_confidence", 0.90),
        available=config.custom_params.get("available", True),
    )


def _create_replay_provider(config: DecisionModelConfig) -> DecisionProvider:
    from praxeon.providers.replay import ReplayProvider

    trace_path = config.model_path or config.custom_params.get("trace_path")
    scenario = config.model_id if config.model_id and config.model_id != "default" else "safe_read"
    return ReplayProvider(
        default_scenario=scenario,
        provider_name=config.provider,
        model_name=config.model_id or "replay-mock-v1",
        trace_path=trace_path,
    )


def _create_laya_provider(config: DecisionModelConfig) -> DecisionProvider:
    from praxeon.providers.laya import LayaProvider

    backend = config.backend or "auto"
    endpoint = config.endpoint or config.custom_params.get("endpoint_url")
    device = config.device or "cpu"
    timeout = config.timeout_seconds or 5.0
    return LayaProvider(
        backend=backend,
        model_name=config.model_id or "laya-v1-calibrated",
        endpoint_url=endpoint,
        auth_token=config.custom_params.get("auth_token"),
        timeout=timeout,
        device=device,
    )


def _create_typesafe_provider(config: DecisionModelConfig) -> DecisionProvider:
    from praxeon.providers.typesafe import TypeSafeProvider

    api_key = config.custom_params.get("api_key")
    timeout = config.timeout_seconds or 10.0
    max_retries = config.max_retries or 2
    return TypeSafeProvider(
        api_key=api_key,
        model_name=config.model_id or "jev-v1",
        timeout=timeout,
        max_retries=max_retries,
    )


def build_default_registry() -> DecisionProviderRegistry:
    """Construye e inicializa el registro canónico con los 4 proveedores principales de PRAXEON."""
    reg = DecisionProviderRegistry()

    # 1. Mock (siempre disponible, sin dependencias externas)
    reg.register(
        provider_id="mock",
        factory=_create_mock_provider,
        description="Proveedor simulado determinista para tests unitarios y CI",
        default_backend="local",
    )

    # 2. Replay (siempre disponible, reproducción de trazas/benchmarks)
    reg.register(
        provider_id="replay",
        factory=_create_replay_provider,
        description="Proveedor determinista de reproducción de trazas grabadas",
        default_backend="local",
    )

    # 3. LAYA (soporta backend 'auto', 'local', 'hosted', 'simulated')
    reg.register(
        provider_id="laya",
        factory=_create_laya_provider,
        required_extra="laya",
        required_module=None,
        description="Modelo no-autorregresivo LAYA (local o hosted)",
        default_backend="auto",
    )

    # 4. TypeSafe (requiere extra 'typesafe' / 'typesafe-sdk')
    reg.register(
        provider_id="typesafe",
        factory=_create_typesafe_provider,
        required_extra="typesafe",
        required_module="typesafe_sdk",
        description="Adaptador hacia TypeSafe AI System One SDK",
        default_backend="api",
    )

    return reg


# Instancia por defecto del registro
default_registry: DecisionProviderRegistry = build_default_registry()

__all__ = [
    "DecisionProviderRegistry",
    "ProviderFactoryEntry",
    "ProviderFactory",
    "default_registry",
    "build_default_registry",
]
