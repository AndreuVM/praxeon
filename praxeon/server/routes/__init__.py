"""Registro consolidado de routers de la API REST de PRAXEON 1.0."""

from fastapi import APIRouter, Depends

from praxeon.server.dependencies import require_route_permission
from praxeon.server.services.auth_service import Permission as P
from praxeon.server.routes.agents import router as agents_router
from praxeon.server.routes.auth import auth_router
from praxeon.server.routes.decisions import router as decisions_router
from praxeon.server.routes.events import router as events_router
from praxeon.server.routes.health import router as health_router
from praxeon.server.routes.mcp import router as mcp_router
from praxeon.server.routes.metrics import router as metrics_router
from praxeon.server.routes.providers import router as providers_router
from praxeon.server.routes.sessions import router as sessions_router
from praxeon.server.routes.workflows import router as workflows_router
from praxeon.server.routes.workspace import router as workspace_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(sessions_router, dependencies=[Depends(require_route_permission(P.SESSION_READ, P.SESSION_EXECUTE))])
api_router.include_router(agents_router, dependencies=[Depends(require_route_permission(P.AGENT_READ, P.AGENT_WRITE))])
api_router.include_router(decisions_router, dependencies=[Depends(require_route_permission(P.SESSION_READ, P.SESSION_EXECUTE))])
api_router.include_router(events_router, dependencies=[Depends(require_route_permission(P.SESSION_READ, P.SESSION_EXECUTE))])
api_router.include_router(health_router)
api_router.include_router(metrics_router, dependencies=[Depends(require_route_permission(P.METRICS_READ, P.ADMIN_SETTINGS))])
api_router.include_router(providers_router, dependencies=[Depends(require_route_permission(P.AGENT_READ, P.ADMIN_SETTINGS))])
api_router.include_router(workflows_router, dependencies=[Depends(require_route_permission(P.WORKFLOW_READ, P.WORKFLOW_WRITE))])
api_router.include_router(mcp_router, dependencies=[Depends(require_route_permission(P.ADMIN_SETTINGS, P.ADMIN_SETTINGS))])
api_router.include_router(workspace_router, dependencies=[Depends(require_route_permission(P.SESSION_READ, P.ADMIN_SETTINGS))])



__all__ = ["api_router"]
