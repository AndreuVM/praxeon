"""Contexto de la llamada en curso (modo de ejecución y sesión), que el ejecutor fija antes de invocar un handler."""

from contextvars import ContextVar
from typing import Any

CALL_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("praxeon_call_context", default={})
