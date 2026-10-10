"""Ruta de comprobación de salud del Web Server (praxeon/server/routes/health.py)."""

from datetime import datetime, timezone
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from praxeon.server.dependencies import (
    RuntimeApplicationService,
    get_runtime_service,
    verify_api_key,
)
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
        "version": "1.1.0",
        "runtime": "PRAXEON Autonomous Supervision Runtime",
        "uptime_seconds": round(uptime, 2),
        "database": "SQLite (WAL)",
        "active_provider": service.config.provider.name,
        "default_workspace": get_default_workspace_root(),
        "environment": "local_development",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return APIResponse(data=data)


@router.get("/context", response_model=APIResponse[Dict[str, Any]])
def get_system_context(
    workspace_root: Optional[str] = None,
    _auth: None = Depends(verify_api_key),
):
    """Obtiene información contextual del entorno, sistema operativo y archivos del workspace garantizando contención."""
    default_root = os.path.realpath(get_default_workspace_root())
    
    if workspace_root:
        # Finding 19: Validar y confinar workspace_root a rutas autorizadas
        target_root = os.path.realpath(os.path.abspath(workspace_root))
        if not os.path.exists(target_root) or not os.path.isdir(target_root):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Directorio de workspace no válido o inexistente: '{workspace_root}'",
            )

        allowed_roots_env = os.environ.get("PRAXEON_ALLOWED_WORKSPACE_ROOTS", "")
        allowed_roots = [os.path.realpath(r.strip()) for r in allowed_roots_env.split(",") if r.strip()]
        allowed_roots.append(default_root)
        allowed_roots.append(os.path.realpath(os.getcwd()))

        is_allowed = False
        for allowed in allowed_roots:
            try:
                common = os.path.commonpath([allowed, target_root])
                if common == allowed:
                    is_allowed = True
                    break
            except ValueError:
                continue

        if not is_allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado: el directorio especificado '{workspace_root}' está fuera de los límites de workspace autorizados.",
            )
        effective_root = target_root
    else:
        effective_root = default_root

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
