"""Modelo formal de estado de sesión (SessionState) para JEV Reasoning Navigator v0.2.

Mantiene el estado completo, auditable y serializable con hash determinista.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, ConfigDict, Field
from praxeon.domain.models import (
    ActionCandidate,
    Evidence,
    Goal,
    PolicyDecision,
    compute_state_hash,
)


class StepRecord(BaseModel):
    """Registro inmutable de un paso evaluado y/o ejecutado en la sesión."""
    model_config = ConfigDict(frozen=True)

    id: str
    index: int
    action: ActionCandidate
    decision: PolicyDecision
    observation: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    synthetic_fallback: bool = False
    model_source: Optional[str] = None
    governance_mode: Optional[str] = None



class SessionState(BaseModel):
    """Estado integral y serializable de una sesión de razonamiento del agente."""

    session_id: str
    goal: Goal
    steps: List[StepRecord] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    forbidden_tools: Set[str] = Field(default_factory=set)
    checkpoint_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def compute_hash(self) -> str:
        """Calcula el hash SHA-256 canónico y determinista del estado actual."""
        canonical_dict = {
            "session_id": self.session_id,
            "goal": self.goal.model_dump(),
            "step_count": len(self.steps),
            "step_ids": [s.id for s in self.steps],
            "evidence_hashes": sorted([e.content_hash for e in self.evidence]),
            "forbidden_tools": sorted(list(self.forbidden_tools)),
        }
        return compute_state_hash(canonical_dict)

    def add_step(
        self,
        action: ActionCandidate,
        decision: PolicyDecision,
        observation: Optional[str] = None,
        synthetic_fallback: Optional[bool] = None,
        model_source: Optional[str] = None,
        governance_mode: Optional[str] = None,
    ) -> StepRecord:
        """Registra un nuevo paso completado en la trayectoria."""
        is_synth = synthetic_fallback if synthetic_fallback is not None else action.synthetic_fallback
        m_src = model_source or action.model_source
        record = StepRecord(
            id=f"step_{len(self.steps)}",
            index=len(self.steps),
            action=action,
            decision=decision,
            observation=observation,
            synthetic_fallback=is_synth,
            model_source=m_src,
            governance_mode=governance_mode,
        )
        self.steps.append(record)
        return record

    def add_evidence(self, ev: Evidence) -> None:
        """Añade una pieza de evidencia al estado."""
        # Evitar duplicados por content_hash
        if not any(existing.content_hash == ev.content_hash for existing in self.evidence):
            self.evidence.append(ev)

    def forbid_tool(self, tool_name: str) -> None:
        """Prohíbe físicamente una herramienta para pasos futuros."""
        self.forbidden_tools.add(tool_name.strip())

    def allow_tool(self, tool_name: str) -> None:
        """Desbloquea una herramienta previamente prohibida."""
        self.forbidden_tools.discard(tool_name.strip())

    def to_snapshot(self) -> Dict[str, Any]:
        """Genera un diccionario snapshot profundo del estado actual para restauración."""
        return {
            "session_id": self.session_id,
            "goal": self.goal.model_dump(),
            "steps": [s.model_dump() for s in self.steps],
            "evidence": [e.model_dump() for e in self.evidence],
            "forbidden_tools": list(self.forbidden_tools),
            "checkpoint_ids": list(self.checkpoint_ids),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_snapshot(cls, snapshot: Dict[str, Any]) -> "SessionState":
        """Reconstruye un SessionState idéntico a partir de un snapshot."""
        return cls(
            session_id=snapshot["session_id"],
            goal=Goal(**snapshot["goal"]),
            steps=[StepRecord(**s) for s in snapshot.get("steps", [])],
            evidence=[Evidence(**e) for e in snapshot.get("evidence", [])],
            forbidden_tools=set(snapshot.get("forbidden_tools", [])),
            checkpoint_ids=list(snapshot.get("checkpoint_ids", [])),
            metadata=dict(snapshot.get("metadata", {})),
        )
