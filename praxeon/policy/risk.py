"""Obsoleto: usa ``praxeon.policy.registry`` (ToolRegistry, ToolSpec) y ``praxeon.domain.models`` (RiskLevel,
RiskAssessment). Se retira en v2.0.0."""

import warnings

from praxeon.domain.models import RiskAssessment, RiskLevel
from praxeon.policy.registry import ToolRegistry, ToolSpec

warnings.warn(
    "praxeon.policy.risk está en desuso: importa ToolRegistry y ToolSpec de praxeon.policy.registry, y RiskLevel y "
    "RiskAssessment de praxeon.domain.models (se retira en v2.0.0).",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["ToolSpec", "ToolRegistry", "RiskLevel", "RiskAssessment"]
