"""Módulo de Context Management y Caching Optimizado para PRAXEON.

Proporciona fragmentación tipada de contexto, fingerprints deterministas,
selección DAG-aware, presupuesto de tokens por prioridades y caché L1/L2.

Axioma de Seguridad:
Un cache hit únicamente optimiza y reutiliza contexto para el razonador;
NUNCA equivale a ALLOW, NUNCA emite una Capability y NUNCA ejecuta una acción.
"""

from praxeon.context.fragments import (
    ContextFragment,
    FragmentType,
    GoalFragment,
    ConstraintFragment,
    EvidenceFragment,
    ObservationFragment,
    DecisionFragment,
    TaskFragment,
    EnvironmentFragment,
    FileFragment,
    SummaryFragment,
)
from praxeon.context.fingerprint import ContextFingerprint
from praxeon.context.cache import ContextCache, InMemoryContextCache, InMemoryFragmentCache, ContextSnapshot
from praxeon.context.budget import TokenBudget
from praxeon.context.selector import DAGContextSelector
from praxeon.context.builder import ContextSnapshotBuilder
from praxeon.context.manager import ContextManager

__all__ = [
    "ContextFragment",
    "FragmentType",
    "GoalFragment",
    "ConstraintFragment",
    "EvidenceFragment",
    "ObservationFragment",
    "DecisionFragment",
    "TaskFragment",
    "EnvironmentFragment",
    "FileFragment",
    "SummaryFragment",
    "ContextFingerprint",
    "ContextCache",
    "InMemoryContextCache",
    "InMemoryFragmentCache",
    "ContextSnapshot",
    "TokenBudget",
    "DAGContextSelector",
    "ContextSnapshotBuilder",
    "ContextManager",
]
