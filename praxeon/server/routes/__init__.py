"""Registro consolidado de routers de la API REST de PRAXEON 1.0."""

from fastapi import APIRouter, Depends

from praxeon.server.dependencies import verify_api_key
from praxeon.server.routes.decisions import router as decisions_router
from praxeon.server.routes.events import router as events_router
from praxeon.server.routes.health import router as health_router
from praxeon.server.routes.metrics import router as metrics_router
from praxeon.server.routes.sessions import router as sessions_router

api_router = APIRouter()
api_router.include_router(sessions_router, dependencies=[Depends(verify_api_key)])
api_router.include_router(decisions_router, dependencies=[Depends(verify_api_key)])
api_router.include_router(events_router, dependencies=[Depends(verify_api_key)])
api_router.include_router(health_router)
api_router.include_router(metrics_router, dependencies=[Depends(verify_api_key)])


__all__ = ["api_router"]
