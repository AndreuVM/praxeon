"""Módulo histórico preservado para retrocompatibilidad controlada (REF-01).

El motor canónico ha migrado a `praxeon.core.decision_engine.DecisionEngine`.
Este módulo reexporta los componentes históricos `JEVEngine` y `PraxeonEngine`.
"""

from praxeon.core.decision_engine import (
    DecisionEngine,
    DecisionEngine as JEVEngine,
    DecisionEngine as PraxeonEngine,
)

__all__ = ["DecisionEngine", "JEVEngine", "PraxeonEngine"]
