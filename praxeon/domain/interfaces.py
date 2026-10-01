"""Protocolos e interfaces abstractas del dominio para JEV Reasoning Navigator v0.2.

Garantiza el desacoplamiento total respecto a SDKs específicos como typesafe-sdk.
"""

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable
from praxeon.domain.models import (
    ActionCandidate,
    Checkpoint,
    DecisionReceipt,
    Evidence,
    Goal,
    PolicyDecision,
    ProviderAssessment,
    StateSnapshot,
)


@runtime_checkable
class ReasoningProvider(Protocol):
    """Protocolo abstracto para proveedores de evaluación semántica (TypeSafe AI, Replay, etc.)."""

    def evaluate(
        self,
        state: Any,
        actions: List[ActionCandidate],
    ) -> List[ProviderAssessment]:
        """Evalúa semánticamente una lista de acciones candidatas bajo el estado actual."""
        ...


@runtime_checkable
class EvidenceProvider(Protocol):
    """Protocolo abstracto para consultar o verificar evidencia empírica."""

    def assess(
        self,
        state: Any,
        action: ActionCandidate,
    ) -> List[Evidence]:
        """Recupera la evidencia empírica que respalda una acción candidata."""
        ...


@runtime_checkable
class EvidenceStore(Protocol):
    """Protocolo para almacenamiento e indexación de evidencias."""

    def get_all_active_evidence(self) -> List[Evidence]:
        ...

    def has_evidence(self, claim: str) -> bool:
        ...


@runtime_checkable
class Executor(Protocol):
    """Protocolo abstracto para la ejecución física autorizada de herramientas."""

    def execute(
        self,
        action: ActionCandidate,
        state: Any,
        decision: Optional[PolicyDecision] = None,
    ) -> Any:
        """Ejecuta físicamente la herramienta autorizada y captura la observación resultante."""
        ...


@runtime_checkable
class CheckpointStore(Protocol):
    """Protocolo para persistencia y restauración de checkpoints atómicos."""

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        ...

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Checkpoint]:
        ...

    def get_latest_checkpoint(self) -> Optional[Checkpoint]:
        ...


@runtime_checkable
class StateStore(Protocol):
    """Protocolo para persistencia y consulta del estado de sesión."""

    def save_state(self, state: Any) -> None:
        ...

    def load_state(self, session_id: str) -> Optional[Any]:
        ...

    def list_sessions(self) -> List[str]:
        ...

    def delete_session(self, session_id: str) -> bool:
        ...


@runtime_checkable
class PolicyEngineProtocol(Protocol):
    """Protocolo para evaluación operacional de políticas."""

    def evaluate_action(
        self,
        action: ActionCandidate,
        state: Dict[str, Any],
        provider_assessment: Optional[ProviderAssessment] = None,
        available_evidence: Optional[List[Evidence]] = None,
    ) -> Any:
        ...


@runtime_checkable
class CompletionVerifierProtocol(Protocol):
    """Protocolo para verificación de culminación legítima de objetivos."""

    def is_finish_action(self, action: ActionCandidate) -> bool:
        ...

    def verify(self, goal: Goal, state: Any, action: ActionCandidate) -> Any:
        ...
