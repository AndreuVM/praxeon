"""Módulo de servidor web FastAPI, streaming WebSocket y coordinadores modulares para PRAXEON 1.0."""

from praxeon.server.app import app, create_app, start
from praxeon.server.dependencies import RuntimeApplicationService, get_runtime_service, set_runtime_service
from praxeon.server.coordinators import (
    SessionLifecycleCoordinator,
    StepExecutionCoordinator,
    CheckpointCoordinator,
    DiagnosticsCoordinator,
)

__all__ = [
    "app",
    "create_app",
    "start",
    "RuntimeApplicationService",
    "get_runtime_service",
    "set_runtime_service",
    "SessionLifecycleCoordinator",
    "StepExecutionCoordinator",
    "CheckpointCoordinator",
    "DiagnosticsCoordinator",
]
