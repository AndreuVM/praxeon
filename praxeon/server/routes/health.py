"""Ruta de comprobación de salud del Web Server (praxeon/server/routes/health.py)."""

from datetime import datetime
import time
from typing import Any, Dict
from fastapi import APIRouter, Depends

from praxeon.server.dependencies import RuntimeApplicationService, get_runtime_service
from praxeon.server.schemas.common import APIResponse

router = APIRouter(prefix="/v1", tags=["Health"])
_START_TIME = time.time()


@router.get("/health", response_model=APIResponse[Dict[str, Any]])
def healthcheck(
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Comprobación de salud y estado de los subsistemas del runtime."""
    uptime = time.time() - _START_TIME
    data = {
        "status": "healthy",
        "version": "1.0.0",
        "runtime": "PRAXEON Autonomous Supervision Runtime",
        "uptime_seconds": round(uptime, 2),
        "database": "SQLite (WAL)",
        "active_provider": service.config.provider.name,
        "environment": "local_development",
        "timestamp": datetime.utcnow().isoformat(),
    }
    return APIResponse(data=data)
