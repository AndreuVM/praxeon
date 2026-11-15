"""Entidades inmutables de estado de trayectoria e instantáneas de supervisión."""

from datetime import datetime, UTC
from pydantic import BaseModel, ConfigDict, Field

from praxeon.domain.action import ActionCandidate
from praxeon.domain.decision import PolicyDecision
from praxeon.domain.evidence import Evidence
from praxeon.domain.goal import Goal


class StateStepRecord(BaseModel):
    """Registro inmutable de un paso en el historial del estado."""
    model_config = ConfigDict(frozen=True)

    action: ActionCandidate
    decision: PolicyDecision
    observation: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))



class StateSnapshot(BaseModel):
    """Instantánea inmutable serializable del estado de la sesión."""
    model_config = ConfigDict(frozen=True)

    session_id: str
    goal: Goal
    step_count: int
    evidence_count: int
    forbidden_tools: list[str] = Field(default_factory=list)
    state_hash: str = ""


class TrajectoryState(BaseModel):
    """Representación inmutable de la trayectoria de supervisión."""
    model_config = ConfigDict(frozen=True)

    session_id: str
    goal: Goal
    steps: list[StateStepRecord] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    forbidden_tools: set[str] = Field(default_factory=set)
