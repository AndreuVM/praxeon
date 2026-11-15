"""Servicio de ejecución: el workspace por defecto del servidor.

La ejecución de herramientas no pasa por aquí: la hace ``praxeon.runtime.executor.SecureExecutor`` con el sandbox que
corresponde al modo de la sesión (``praxeon.runtime.sandbox`` y ``praxeon.runtime.isolation``).

Hasta AUD-23 este servicio tenía ``get_sandbox`` y ``execute_action``, que ningún código llamaba y que no podían
funcionar (usaban ``SandboxTier.RESTRICTED_SUBPROCESS``, ``HOST_ELEVATED``, ``CONTAINER_ISOLATED`` y ``LocalProcessSandbox.execute``,
que no existen). Se quitaron en vez de arreglarlos: qué sandbox se construye para cada modo lo decide el ejecutor.
"""

from praxeon.runtime.sandbox import get_default_workspace_root


class ExecutionService:
    """Workspace por defecto sobre el que operan las sesiones que no indican uno."""

    def __init__(self, default_workspace: str | None = None):
        self.default_workspace = default_workspace or get_default_workspace_root()
