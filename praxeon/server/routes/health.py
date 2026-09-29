"""Ruta de comprobación de salud del Web Server (praxeon/server/routes/health.py)."""

from datetime import datetime
import time
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends

from praxeon.server.dependencies import RuntimeApplicationService, get_runtime_service
from praxeon.server.schemas.common import APIResponse

router = APIRouter(prefix="/v1", tags=["Health"])
_START_TIME = time.time()


import os
import platform
from praxeon.core.session_context import SessionContextManager, get_default_workspace_root

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
        "default_workspace": get_default_workspace_root(),
        "environment": "local_development",
        "timestamp": datetime.utcnow().isoformat(),
    }
    return APIResponse(data=data)


@router.get("/context", response_model=APIResponse[Dict[str, Any]])
def get_system_context(
    workspace_root: Optional[str] = None,
):
    """Obtiene información contextual del entorno, sistema operativo y archivos del workspace."""
    effective_root = workspace_root or get_default_workspace_root()
    ctx_mgr = SessionContextManager()
    tree = ctx_mgr.get_directory_tree(effective_root, max_files=100)
    return APIResponse(data={
        "workspace_root": effective_root,
        "os": os.name,
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "files_count": len(tree),
        "sample_files": tree[:30],
    })
