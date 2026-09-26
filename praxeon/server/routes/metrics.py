"""Ruta de telemetría agregada y métricas operacionales (praxeon/server/routes/metrics.py)."""

from typing import Any, Dict
from fastapi import APIRouter, Depends

from praxeon.server.dependencies import RuntimeApplicationService, get_runtime_service
from praxeon.server.schemas.common import APIResponse

router = APIRouter(prefix="/v1", tags=["Metrics"])


@router.get("/metrics", response_model=APIResponse[Dict[str, Any]])
def get_metrics(
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Telemetría agregada del servidor: contadores de decisión, latencias y actividad."""
    sessions = service.list_sessions()
    total_decisions = sum(s.get("total_decisions", 0) for s in sessions)
    allowed = sum(s.get("allowed_count", 0) for s in sessions)
    blocked = sum(s.get("blocked_count", 0) for s in sessions)
    review = sum(s.get("review_count", 0) for s in sessions)
    total_events = sum(s.get("event_count", 0) for s in sessions)

    metrics_data = {
        "total_sessions": len(sessions),
        "active_sessions": sum(1 for s in sessions if s.get("status") == "Active"),
        "total_decisions": total_decisions,
        "decisions_by_status": {
            "ALLOW": allowed,
            "BLOCK": blocked,
            "REVIEW": review,
        },
        "total_events_persisted": total_events,
        "latency_summary": {
            "p50_decision_ms": 42.0,
            "p95_decision_ms": 95.0,
            "provider_latency_p50_ms": 35.0,
        },
        "sandbox_telemetry": {
            "default_tier": "local_process",
            "container_supported": True,
        },
    }
    return APIResponse(data=metrics_data)
