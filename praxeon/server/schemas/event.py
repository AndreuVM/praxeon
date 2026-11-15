"""Esquemas para eventos y streaming de PRAXEON 1.0 (praxeon/server/schemas/event.py)."""

from typing import Any
from pydantic import BaseModel, ConfigDict


class EventsListResponse(BaseModel):
    """Respuesta paginada del stream de eventos para una sesión."""
    model_config = ConfigDict(frozen=True)

    session_id: str
    events: list[dict[str, Any]]
    after_sequence: int
    latest_sequence: int
    has_more: bool = False


class WebSocketMessage(BaseModel):
    """Estructura de mensaje transportado sobre el canal WebSocket."""
    action: str  # "event", "sync", "pong", "error", "session_state"
    session_id: str | None = None
    data: dict[str, Any] | None = None
