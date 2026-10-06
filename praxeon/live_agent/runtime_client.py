"""Cliente de runtime y mediación para el Live Agent de PRAXEON.

Encapsula la interacción entre el runner del agente y el supervisor de control
(JEVProxyMiddleware o Control Plane), desacoplando la ejecución del agente del runtime subyacente.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from praxeon.config import PraxeonConfig, default_config
from praxeon.interceptor.proxy_middleware import JEVProxyMiddleware


class LiveAgentRuntimeClient:
    """Cliente mediador para supervisar y ejecutar acciones en el entorno del runtime."""

    def __init__(
        self,
        middleware: Optional[JEVProxyMiddleware] = None,
        config: Optional[PraxeonConfig] = None,
    ):
        self.config = config or default_config
        self.middleware = middleware

    def ensure_task(self, task: str) -> JEVProxyMiddleware:
        """Asegura que el middleware esté inicializado para la tarea actual."""
        if self.middleware is None:
            self.middleware = JEVProxyMiddleware(goal=task, config=self.config)
        else:
            self.middleware.start_subtask(task)
        return self.middleware

    def intercept_step_chunk(self, proposed_steps: List[Dict[str, Any]]) -> Any:
        """Envía un bloque de pasos candidatos al middleware de supervisión en una sola llamada."""
        if self.middleware is None:
            raise RuntimeError("LiveAgentRuntimeClient: middleware no inicializado antes de interceptar pasos.")
        return self.middleware.intercept_step_chunk(proposed_steps)

    def execute_tool(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        thought_text: str = "",
    ) -> Any:
        """Ejecuta una herramienta a través del middleware de supervisión con contención de sandbox."""
        if self.middleware is None:
            raise RuntimeError("LiveAgentRuntimeClient: middleware no inicializado antes de ejecutar herramientas.")
        return self.middleware.execute_tool(tool_name, tool_args, thought_text)

    def record_observation(self, observation: str) -> None:
        """Registra una observación empírica en el supervisor."""
        if self.middleware:
            self.middleware.record_observation(observation)
