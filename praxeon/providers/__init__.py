"""Capa de Proveedores de Razonamiento y Decisión para PRAXEON (PRAXEON 1.1)."""

from praxeon.providers.base import BaseReasoningProvider, Provider
from praxeon.providers.context import ProviderContext, ProviderContextBuilder
from praxeon.providers.laya import LayaProvider
from praxeon.providers.mock import MockProvider
from praxeon.providers.registry import (
    DecisionProviderRegistry,
    build_default_registry,
    default_registry,
)
from praxeon.providers.replay import ReplayProvider
from praxeon.providers.router import ConfidenceAwareRouter, RoutingStrategy, RoutingTelemetry
from praxeon.providers.typesafe import TypeSafeAdapter, TypeSafeProvider

__all__ = [
    "BaseReasoningProvider",
    "Provider",
    "MockProvider",
    "ConfidenceAwareRouter",
    "LayaProvider",
    "ProviderContext",
    "ProviderContextBuilder",
    "ReplayProvider",
    "RoutingStrategy",
    "RoutingTelemetry",
    "TypeSafeAdapter",
    "TypeSafeProvider",
    "DecisionProviderRegistry",
    "default_registry",
    "build_default_registry",
]
