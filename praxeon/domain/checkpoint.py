"""Entidades inmutables de Checkpoint atómico y entorno de ejecución recuperable."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, ConfigDict, Field


class ExecutionEnvironment(BaseModel):
    """Entorno de ejecución capturado en un checkpoint atómico."""
    model_config = ConfigDict(frozen=True)

    policy_hash: str
    active_tools: List[str] = Field(default_factory=list)
    forbidden_tools: List[str] = Field(default_factory=list)
    variables: Dict[str, Any] = Field(default_factory=dict)


class Checkpoint(BaseModel):
    """Instantánea atómica recuperable de la sesión y trayectoria del agente."""
    model_config = ConfigDict(frozen=True)

    id: str
    session_id: str = ""
    step_index: int = 0
    state_hash: str = ""
    evidence_ids: List[str] = Field(default_factory=list)
    forbidden_tools: List[str] = Field(default_factory=list)
    environment: Optional[ExecutionEnvironment] = None
    reason: str = "Paso regular validado"
    snapshot_data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


