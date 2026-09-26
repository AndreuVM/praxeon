"""Módulo de servidor web FastAPI y streaming WebSocket para PRAXEON 1.0."""

from praxeon.server.app import app, create_app, start

__all__ = ["app", "create_app", "start"]
