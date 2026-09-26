"""Rutas REST para consulta paginada del stream de eventos (praxeon/server/routes/events.py)."""

from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, Query, status

from praxeon.server.dependencies import RuntimeApplicationService, get_runtime_service
from praxeon.server.schemas.common import APIResponse
from praxeon.server.schemas.event import EventsListResponse

router = APIRouter(prefix="/v1/sessions", tags=["Events"])


@router.get("/{session_id}/events", response_model=APIResponse[EventsListResponse])
def get_session_events(
    session_id: str,
    after_sequence: int = Query(0, ge=0, description="Recuperar solo eventos con secuencia mayor a este valor"),
    limit: int = Query(100, ge=1, le=1000, description="Límite máximo de eventos devueltos"),
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Consulta el historial de eventos ordenados por secuencia monótona para gap recovery."""
    events = service.event_bus.get_events(session_id, after_sequence=after_sequence, limit=limit + 1)
    has_more = len(events) > limit
    returned_events = events[:limit]

    latest_seq = returned_events[-1].sequence if returned_events else after_sequence

    resp = EventsListResponse(
        session_id=session_id,
        events=[e.to_dict() for e in returned_events],
        after_sequence=after_sequence,
        latest_sequence=latest_seq,
        has_more=has_more,
    )
    return APIResponse(data=resp)
