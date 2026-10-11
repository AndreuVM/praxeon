"""Entidades inmutables de observación y resultados empíricos del entorno."""

import hashlib
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field


class Observation(BaseModel):
    """Observación estructurada del entorno."""
    model_config = ConfigDict(frozen=True)

    id: str
    source_step_id: Optional[str] = None
    content: str
    content_hash: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.content_hash:
            h = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
            object.__setattr__(self, "content_hash", h)


class ToolObservation(BaseModel):
    """Resultado estructurado de la ejecución física de una herramienta."""
    model_config = ConfigDict(frozen=True)

    output: str
    success: bool = True
    execution_time_ms: float = 0.0
    tool_name: Optional[str] = None
    is_error: bool = False
