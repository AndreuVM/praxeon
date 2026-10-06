"""Entidades inmutables de acción y llamada a herramientas físicas."""

import hashlib
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


from praxeon.domain.governance import ActionProvenance, FallbackMode


class ToolCall(BaseModel):
    """Llamada a herramienta física propuesta."""
    model_config = ConfigDict(frozen=True)

    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ActionCandidate(BaseModel):
    """Acción candidata propuesta por el agente sujeta a autorización estricta de política."""
    model_config = ConfigDict(frozen=True)

    id: str
    description: str
    tool_call: Optional[ToolCall] = None
    rationale: Optional[str] = None
    requires_evidence: List[str] = Field(default_factory=list)
    estimated_cost: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)
    synthetic_fallback: bool = False
    model_source: Optional[str] = None
    provenance: Optional[ActionProvenance] = None


def compute_action_hash(action: ActionCandidate) -> str:
    """Calcula un hash SHA-256 determinista para una acción candidata."""
    payload = {
        "id": action.id,
        "description": action.description,
        "tool_call": action.tool_call.model_dump() if action.tool_call else None,
        "requires_evidence": sorted(action.requires_evidence),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
