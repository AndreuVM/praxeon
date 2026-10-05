"""Entidades de Búsqueda y Bifurcación (Multi-Path Reasoning & Tree Search) para PRAXEON (F3-01).

Define las estructuras canónicas para la exploración en árbol/grafo de razonamiento:
- BranchStatus: Ciclo de vida y estados observables de una rama.
- BranchScore: Puntuación multidimensional (confianza, evidencia, riesgo, alineamiento, parsimonia).
- BranchStep: Paso individual dentro de una rama (acción candidata, decisión, observación y modo especulativo).
- BranchPath: Estructura de rama completa con trazabilidad de parentesco y checkpoints.
- RollbackCheckpoint: Punto de restauración atómico para backtracking y reversión de estado en ramas.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, computed_field

from praxeon.domain.action import ActionCandidate
from praxeon.domain.decision import PolicyDecision


class BranchStatus(str, Enum):
    """Estados canónicos de ciclo de vida de una rama en el árbol de búsqueda."""
    ACTIVE = "ACTIVE"              # Rama actualmente seleccionada en foco
    EXPLORING = "EXPLORING"        # Rama en exploración especulativa paralela
    PRUNED = "PRUNED"              # Podada tempranamente por violación de política, riesgo o ciclo
    COMMITTED = "COMMITTED"        # Rama consolidada y ejecutada sobre el entorno real
    ABANDONED = "ABANDONED"        # Descartada por suboptimalidad frente a otra rama mejor
    SUCCEEDED = "SUCCEEDED"        # Alcanzó con éxito los criterios de finalización de la meta
    FAILED = "FAILED"              # Concluyó en un estado irrecuperable o fallo de ejecución


class BranchScore(BaseModel):
    """Puntuación multidimensional para priorización y ordenamiento de ramas (Search Heuristic)."""
    model_config = ConfigDict(frozen=True)

    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confianza epistémica del modelo [0, 1]")
    evidence_support: float = Field(default=0.5, ge=0.0, le=1.0, description="Grado de sustento en evidencia contrastada [0, 1]")
    risk_penalty: float = Field(default=0.0, ge=0.0, le=1.0, description="Penalización por riesgo operacional calculado [0, 1]")
    goal_alignment: float = Field(default=0.8, ge=0.0, le=1.0, description="Alineamiento semántico con la meta [0, 1]")
    depth_penalty: float = Field(default=0.0, ge=0.0, description="Penalización acumulativa por profundidad o coste")

    # Ponderaciones fijas por defecto
    weight_confidence: float = 0.25
    weight_evidence: float = 0.30
    weight_alignment: float = 0.35
    weight_risk: float = 0.40

    @computed_field
    @property
    def composite_score(self) -> float:
        """Calcula el score compuesto normalizado [0.0, 1.0]."""
        raw = (
            (self.confidence * self.weight_confidence)
            + (self.evidence_support * self.weight_evidence)
            + (self.goal_alignment * self.weight_alignment)
            - (self.risk_penalty * self.weight_risk)
            - self.depth_penalty
        )
        return max(0.0, min(1.0, round(raw, 4)))


class BranchStep(BaseModel):
    """Paso unitario ejecutado o simulado dentro de una trayectoria de rama."""
    model_config = ConfigDict(frozen=True)

    step_id: str
    action: ActionCandidate
    decision: Optional[PolicyDecision] = None
    observation: Optional[str] = None
    is_speculative: bool = True
    step_score: Optional[float] = None
    token_estimate: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action": self.action.model_dump() if hasattr(self.action, "model_dump") else str(self.action),
            "decision": self.decision.model_dump() if self.decision and hasattr(self.decision, "model_dump") else None,
            "observation": self.observation,
            "is_speculative": self.is_speculative,
            "step_score": self.step_score,
            "token_estimate": self.token_estimate,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


class RollbackCheckpoint(BaseModel):
    """Punto de restauración atómico para backtracking y reversión de estado en ramas."""
    model_config = ConfigDict(frozen=True)

    checkpoint_id: str
    branch_id: str
    session_id: str
    step_index: int
    state_snapshot_hash: str
    evidence_ids: List[str] = Field(default_factory=list)
    forbidden_tools: List[str] = Field(default_factory=list)
    active_variables: Dict[str, Any] = Field(default_factory=dict)
    reason: str = "Branch checkpoint"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "branch_id": self.branch_id,
            "session_id": self.session_id,
            "step_index": self.step_index,
            "state_snapshot_hash": self.state_snapshot_hash,
            "evidence_ids": self.evidence_ids,
            "forbidden_tools": self.forbidden_tools,
            "active_variables": self.active_variables,
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
        }


class BranchPath(BaseModel):
    """Ruta o rama estructurada en el árbol/grafo de razonamiento multirruta."""
    branch_id: str
    parent_branch_id: Optional[str] = None
    root_session_id: str
    status: BranchStatus = BranchStatus.EXPLORING
    steps: List[BranchStep] = Field(default_factory=list)
    score: BranchScore = Field(default_factory=BranchScore)
    prune_reason: Optional[str] = None
    checkpoint_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def depth(self) -> int:
        """Profundidad en número de pasos de la rama."""
        return len(self.steps)

    @property
    def actions(self) -> List[ActionCandidate]:
        """Secuencia de acciones contenidas en esta rama."""
        return [s.action for s in self.steps]

    def add_step(self, step: BranchStep) -> None:
        """Añade un nuevo paso a la rama."""
        self.steps.append(step)

    def mark_pruned(self, reason: str) -> None:
        """Marca la rama como podada registrando la causa formal."""
        self.status = BranchStatus.PRUNED
        self.prune_reason = reason

    def mark_committed(self) -> None:
        """Marca la rama como la ganadora consolidada para ejecución real."""
        self.status = BranchStatus.COMMITTED

    def mark_abandoned(self, reason: Optional[str] = None) -> None:
        """Marca la rama como abandonada por suboptimalidad."""
        self.status = BranchStatus.ABANDONED
        if reason:
            self.prune_reason = reason

    def update_score(self, new_score: BranchScore) -> None:
        """Actualiza la puntuación multidimensional de la rama."""
        self.score = new_score

    def to_dict(self) -> Dict[str, Any]:
        """Serialización estructurada para el árbol y la API."""
        return {
            "branch_id": self.branch_id,
            "parent_branch_id": self.parent_branch_id,
            "root_session_id": self.root_session_id,
            "status": self.status.value,
            "depth": self.depth,
            "composite_score": self.score.composite_score,
            "score_details": self.score.model_dump(),
            "prune_reason": self.prune_reason,
            "checkpoint_id": self.checkpoint_id,
            "created_at": self.created_at.isoformat(),
            "step_count": len(self.steps),
            "steps": [s.to_dict() for s in self.steps],
            "metadata": self.metadata,
        }
