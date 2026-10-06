"""Servicio Modular de Ejecución Segura y Sandboxing (F3/SEC).

Centraliza:
- Ejecución aislada de herramientas y comandos en LocalProcessSandbox.
- Verificación de límites de ejecución y captura de observaciones.
- Generación y verificación de diffs y parches unificados.
"""

import os
from typing import Any, Dict, Optional

from praxeon.domain.models import ActionCandidate
from praxeon.runtime.sandbox import (
    LocalProcessSandbox,
    SandboxExecutionResult,
    SandboxTier,
    get_default_workspace_root,
)


class ExecutionService:
    """Gestiona el entorno de ejecución en sandbox y la aplicación de herramientas."""

    def __init__(self, default_workspace: Optional[str] = None):
        self.default_workspace = default_workspace or get_default_workspace_root()

    def get_sandbox(
        self,
        execution_mode: str = "local_restricted",
        workspace_root: Optional[str] = None,
    ) -> LocalProcessSandbox:
        """Instancia un sandbox configurado según el perfil de aislamiento solicitado."""
        tier = SandboxTier.RESTRICTED_SUBPROCESS
        if execution_mode == "full_access":
            tier = SandboxTier.HOST_ELEVATED
        elif execution_mode == "container":
            tier = SandboxTier.CONTAINER_ISOLATED

        effective_ws = workspace_root or self.default_workspace
        return LocalProcessSandbox(
            workspace_root=effective_ws,
            tier=tier,
        )

    def execute_action(
        self,
        action: ActionCandidate,
        execution_mode: str = "local_restricted",
        workspace_root: Optional[str] = None,
    ) -> SandboxExecutionResult:
        """Ejecuta una acción aprobada dentro del sandbox correspondiente."""
        sandbox = self.get_sandbox(execution_mode=execution_mode, workspace_root=workspace_root)
        tool_name = action.tool_call.tool_name if action.tool_call else "unknown"
        args = action.tool_call.arguments if action.tool_call else {}
        return sandbox.execute(tool_name, args)
