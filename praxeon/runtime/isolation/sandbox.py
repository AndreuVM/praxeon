"""``IsolatedSandbox``: el sandbox del modo aislado (``container``), sobre el backend que haya en el sistema (AUD-19).

Reutiliza de ``LocalProcessSandbox`` todo lo que no es lanzar el proceso: la política de egress, el filtro léxico de
red (redundante aquí, pero da un error más claro), la contención del ``cwd`` y de ``read_file``/``edit_file`` (que
PRAXEON ejecuta en su propio proceso, fuera del aislamiento) y el entorno filtrado. Solo cambia cómo se lanza el
comando: dentro de un AppContainer, de bubblewrap o de un contenedor. Sin backend, falla cerrado.
"""

from __future__ import annotations

import os
import sys
import subprocess  # nosec B404 - solo TimeoutExpired; los procesos los lanzan los backends
from typing import Any

from praxeon.policy.egress import EgressPolicy
from praxeon.runtime import isolation
from praxeon.runtime.isolation import bubblewrap
from praxeon.runtime.sandbox import (
    ContainerSandboxAdapter,
    ContainerSandboxConfig,
    LocalProcessSandbox,
    SandboxExecutionResult,
    SandboxTier,
    SandboxViolation,
)


class IsolatedSandbox(LocalProcessSandbox):
    """Ejecuta los comandos sin red y sin acceso fuera del workspace (salvo lectura de herramientas y sistema)."""

    def __init__(
        self,
        workspace_root: str | None = None,
        backend: str | None = None,
        egress_policy: EgressPolicy | None = None,
        read_paths: list[str] | None = None,
    ):
        super().__init__(workspace_root=workspace_root, allow_network=False, egress_policy=egress_policy)
        current = isolation.status()
        self.backend = backend if backend is not None else current.backend
        self.unavailable_reason = None if self.backend else current.reason
        self.read_paths = list(read_paths) if read_paths is not None else isolation.tool_read_paths()
        self._appcontainer: Any | None = None                     # AppContainer (solo Windows)
        self._container: ContainerSandboxAdapter | None = None
        if self.backend == "container":
            self._container = ContainerSandboxAdapter(
                config=ContainerSandboxConfig(fallback_to_local=False), workspace_root=self.workspace_root,
                egress_policy=egress_policy,
            )

    def execute_command(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 15.0,
    ) -> SandboxExecutionResult:
        if not self.backend:
            raise SandboxViolation(f"Aislamiento fuerte requerido, pero no hay backend disponible: {self.unavailable_reason}.")
        if self._container is not None:
            return self._container.execute_command(command, cwd=cwd, env=env, timeout=timeout)
        result = super().execute_command(command, cwd=cwd, env=env, timeout=timeout)
        return result.model_copy(update={"tier": SandboxTier.CONTAINER, "container_runtime": self.backend})

    def _run_bounded_subprocess(
        self,
        args: list[str],
        cwd: str,
        env: dict[str, str],
        timeout: float,
        max_bytes: int = 524288,
    ) -> tuple[int, str, bool]:
        env = dict(env)
        # git no debe subir más allá del workspace: las carpetas de encima no son accesibles desde el aislamiento
        env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(self.workspace_root)
        env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "1"
        if self.backend == "appcontainer":
            return self._run_in_appcontainer(args, cwd, env, timeout, max_bytes)
        if self.backend == "bubblewrap":
            wrapped = bubblewrap.wrap(args, workspace_root=self.workspace_root, cwd=cwd, env=env,
                                      read_paths=self.read_paths)
            return super()._run_bounded_subprocess(wrapped, cwd, {"PATH": env.get("PATH", "/usr/bin:/bin")}, timeout, max_bytes)
        raise SandboxViolation(f"Backend de aislamiento desconocido: {self.backend}")

    def _run_in_appcontainer(self, args: list[str], cwd: str, env: dict[str, str], timeout: float,
                             max_bytes: int) -> tuple[int, str, bool]:
        if self._appcontainer is None:
            self._appcontainer = self._open_appcontainer()
        if args and os.path.basename(args[0]).lower().startswith("powershell") and len(args) >= 2:
            args = args[:-1] + [_powershell_in(self.workspace_root, cwd) + args[-1]]
        try:
            return self._appcontainer.run(args, cwd, env, timeout, max_bytes)
        except subprocess.TimeoutExpired:
            raise
        except OSError as exc:
            raise SandboxViolation(f"No se pudo ejecutar en el AppContainer: {exc}") from exc

    def _open_appcontainer(self) -> Any:
        """El AppContainer del workspace, con los permisos ya concedidos."""
        if sys.platform == "win32":
            from praxeon.runtime.isolation.appcontainer import AppContainer

            try:
                container = AppContainer(self.workspace_root)
                container.ensure_access(self.read_paths)
                return container
            except OSError as exc:
                raise SandboxViolation(f"No se pudo preparar el AppContainer: {exc}") from exc
        raise SandboxViolation("El backend AppContainer solo existe en Windows.")


def _powershell_in(workspace_root: str, cwd: str) -> str:
    """Prefijo que sitúa a PowerShell en ``cwd`` a través de una unidad con raíz en el workspace.

    Dentro de un AppContainer, ``Set-Location`` sobre la ruta real falla: PowerShell necesita consultar las carpetas
    de encima (``C:\\Users``), que no son accesibles. Una unidad ``PRAXEON:`` con raíz en el workspace no las necesita.
    """
    rel = os.path.relpath(os.path.realpath(cwd), workspace_root)
    rel = "" if rel == "." else rel
    root = workspace_root.replace("'", "''")
    target = ("PRAXEON:\\" + rel).replace("'", "''")
    return (f"$null = New-PSDrive -Name PRAXEON -PSProvider FileSystem -Root '{root}'; "
            f"Set-Location -LiteralPath '{target}'; ")
