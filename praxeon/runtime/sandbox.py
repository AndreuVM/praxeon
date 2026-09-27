"""Adaptadores de aislamiento y ejecución en sandbox (SandboxAdapter) para v0.2.1.

Separa formalmente la arquitectura:
Policy decision -> Capability -> Sandbox / Tool Adapter -> Host / Isolated Execution

Garantiza:
1. Contención de directorio de trabajo (jail path / workspace boundaries).
2. Sanitización y depuración de variables de entorno (eliminación de API keys y secretos del proceso hijo).
3. Ejecución tokenizada segura sin shell=True arbitrario.
4. Límite estricto de timeout y control de errores.
"""

from abc import ABC, abstractmethod
from enum import Enum
import os
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, ConfigDict, Field
from praxeon.policy.egress import EgressMode, EgressPolicy, EgressViolation


class SandboxTier(str, Enum):
    """Niveles formales de aislamiento y contención del runtime."""
    CONTAINER = "container"
    LOCAL_PROCESS = "local_process"
    DRY_RUN = "dry_run"


class SandboxViolation(Exception):
    """Excepción lanzada ante un intento de evasión de sandbox o violación de límites."""
    pass


class SandboxExecutionResult(BaseModel):
    """Resultado estructurado de una invocación dentro del sandbox."""
    model_config = ConfigDict(frozen=True)

    output: str
    success: bool
    is_error: bool
    exit_code: int = 0
    execution_time_ms: float = 0.0
    sandboxed: bool = True
    tier: SandboxTier = SandboxTier.LOCAL_PROCESS
    fallback_occurred: bool = False
    container_runtime: Optional[str] = None


class SandboxAdapter(ABC):
    """Interfaz base para adaptadores de sandbox y aislamiento operacional."""

    @abstractmethod
    def execute_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: float = 15.0,
    ) -> SandboxExecutionResult:
        """Ejecuta un comando en un proceso aislado y devuelve el resultado estructurado."""
        pass

    @abstractmethod
    def read_file(self, path: str, max_bytes: int = 100_000) -> SandboxExecutionResult:
        """Lee un archivo comprobando contención de ruta (path containment)."""
        pass

    @abstractmethod
    def edit_file(self, path: str, content: str) -> SandboxExecutionResult:
        """Modifica un archivo comprobando contención de ruta."""
        pass


class LocalProcessSandbox(SandboxAdapter):
    """Sandbox de proceso local con depuración de entorno y contención de rutas."""

    BLOCKED_ENV_VARS: Set[str] = {
        "TYPESAFE_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "SECRET_KEY",
        "API_KEY",
        "PASSWORD",
        "SSH_AUTH_SOCK",
    }

    BLOCKED_NETWORK_COMMANDS: Set[str] = {
        "curl",
        "wget",
        "nc",
        "netcat",
        "ncat",
        "socat",
        "ssh",
        "scp",
        "sftp",
        "ftp",
        "telnet",
        "ping",
        "tracert",
        "traceroute",
        "nslookup",
        "dig",
        "invoke-webrequest",
        "invoke-restmethod",
    }

    def __init__(
        self,
        workspace_root: Optional[str] = None,
        allow_external_cwd: bool = False,
        allow_network: bool = False,
        extra_blocked_vars: Optional[Set[str]] = None,
        egress_policy: Optional[EgressPolicy] = None,
    ):
        self.workspace_root = os.path.realpath(workspace_root or os.getcwd())
        self.allow_external_cwd = allow_external_cwd
        self.allow_network = allow_network
        self.egress_policy = egress_policy or EgressPolicy(
            mode=EgressMode.ALLOW_ALL if allow_network else EgressMode.BLOCK_ALL
        )
        self.blocked_vars = self.BLOCKED_ENV_VARS.union(extra_blocked_vars or set())

    def _sanitize_environment(self, custom_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Limpia las variables de entorno para que el proceso hijo no tenga acceso a claves privadas ni tokens."""
        clean_env = dict(os.environ)
        # Purgar variables sensibles
        for var in list(clean_env.keys()):
            upper_var = var.upper()
            if any(blocked in upper_var for blocked in self.blocked_vars):
                clean_env.pop(var, None)

        # Inyectar indicador de ejecución en sandbox
        clean_env["JEV_SANDBOX_ACTIVE"] = "1"
        clean_env["PYTHONUNBUFFERED"] = "1"

        # Política de red: bloquear egress por variables proxy si allow_network es False
        if not self.allow_network:
            clean_env["http_proxy"] = "http://127.0.0.1:0"
            clean_env["https_proxy"] = "http://127.0.0.1:0"
            clean_env["all_proxy"] = "http://127.0.0.1:0"
            clean_env["HTTP_PROXY"] = "http://127.0.0.1:0"
            clean_env["HTTPS_PROXY"] = "http://127.0.0.1:0"
            clean_env["ALL_PROXY"] = "http://127.0.0.1:0"
            clean_env["NO_PROXY"] = ""

        if custom_env:
            for k, v in custom_env.items():
                if not any(blocked in k.upper() for blocked in self.blocked_vars):
                    clean_env[k] = v

        return clean_env

    def _validate_path_containment(self, path: str) -> str:
        """Valida que una ruta esté estrictamente contenida dentro del workspace_root delimitado resolviendo symlinks."""
        if not path:
            raise SandboxViolation("Ruta de archivo vacía.")

        # Resolver enlaces simbólicos canónicos para prevenir symlink traversal
        candidate = os.path.join(self.workspace_root, path) if not os.path.isabs(path) else path
        real_path = os.path.realpath(candidate)
        canon_workspace = os.path.realpath(self.workspace_root)

        if not self.allow_external_cwd:
            try:
                common = os.path.commonpath([canon_workspace, real_path])
            except ValueError:
                raise SandboxViolation(
                    f"Evasión de ruta inter-volumen detectada: '{path}' resuelve en '{real_path}', "
                    f"fuera del workspace delimitado '{canon_workspace}'."
                )
            if common != canon_workspace:
                raise SandboxViolation(
                    f"Evasión de ruta detectada (symlink/traversal): '{path}' resuelve en '{real_path}', "
                    f"fuera del workspace delimitado '{canon_workspace}'."
                )
        return real_path

    def _is_network_command(self, cmd_str: str) -> bool:
        """Determina si un comando invoca utilidades o protocolos de red no autorizados."""
        lowered = cmd_str.lower()
        tokens = lowered.replace(";", " ").replace("|", " ").replace("&", " ").split()
        for token in tokens:
            # Eliminar prefijos de ruta
            base_token = os.path.basename(token).replace(".exe", "")
            if base_token in self.BLOCKED_NETWORK_COMMANDS:
                return True
        if "http://" in lowered or "https://" in lowered or "system.net.sockets" in lowered:
            return True
        return False

    def execute_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: float = 15.0,
    ) -> SandboxExecutionResult:
        """Ejecuta un comando en un proceso hijo sanitizado y con timeout forzado."""
        cmd_str = (command or "").strip()
        if not cmd_str:
            return SandboxExecutionResult(
                output="Comando vacío.",
                success=False,
                is_error=True,
                exit_code=1,
            )

        allowed, reason = self.egress_policy.evaluate_command_egress(cmd_str)
        if not allowed:
            raise SandboxViolation(f"Violación de política de red: {reason}")

        if not self.allow_network and self._is_network_command(cmd_str):
            raise SandboxViolation(
                f"Violación de política de red: Se intentó ejecutar el comando de red '{cmd_str[:60]}' "
                "con allow_network=False en el sandbox local."
            )

        target_cwd = self._validate_path_containment(cwd) if cwd else self.workspace_root
        clean_env = self._sanitize_environment(env)

        start_t = time.perf_counter()
        try:
            if sys.platform == "win32":
                # En Windows se aísla con PowerShell sin perfiles de usuario ni scripts globales
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd_str],
                    cwd=target_cwd,
                    env=clean_env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                )
            else:
                # En Unix se ejecuta tokenizado o en subshell sin variables heredadas sensibles
                args = shlex.split(cmd_str)
                proc = subprocess.run(
                    args,
                    cwd=target_cwd,
                    env=clean_env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                )

            elapsed = (time.perf_counter() - start_t) * 1000.0
            out = (proc.stdout or proc.stderr or "Comando ejecutado sin salida").strip()
            success = (proc.returncode == 0)
            return SandboxExecutionResult(
                output=out[:20000],
                success=success,
                is_error=not success,
                exit_code=proc.returncode,
                execution_time_ms=round(elapsed, 2),
                sandboxed=True,
            )
        except subprocess.TimeoutExpired:
            elapsed = (time.perf_counter() - start_t) * 1000.0
            return SandboxExecutionResult(
                output=f"Timeout superado ({timeout}s) durante la ejecución de: {cmd_str[:100]}",
                success=False,
                is_error=True,
                exit_code=124,
                execution_time_ms=round(elapsed, 2),
                sandboxed=True,
            )
        except Exception as e:
            elapsed = (time.perf_counter() - start_t) * 1000.0
            return SandboxExecutionResult(
                output=f"Error en sandbox ejecutando comando: {e}",
                success=False,
                is_error=True,
                exit_code=1,
                execution_time_ms=round(elapsed, 2),
                sandboxed=True,
            )

    def read_file(self, path: str, max_bytes: int = 100_000) -> SandboxExecutionResult:
        """Lee un archivo garantizando contención de ruta."""
        start_t = time.perf_counter()
        try:
            safe_path = self._validate_path_containment(path)
            if not os.path.exists(safe_path):
                return SandboxExecutionResult(
                    output=f"Archivo '{path}' no existe en workspace.",
                    success=False,
                    is_error=True,
                    exit_code=1,
                )
            with open(safe_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_bytes)
                if len(content) >= max_bytes:
                    content += f"\n\n[... Truncado a {max_bytes} bytes por política de sandbox ...]"
            elapsed = (time.perf_counter() - start_t) * 1000.0
            return SandboxExecutionResult(
                output=f"Contenido de '{path}':\n{content}",
                success=True,
                is_error=False,
                exit_code=0,
                execution_time_ms=round(elapsed, 2),
                sandboxed=True,
            )
        except SandboxViolation as sv:
            elapsed = (time.perf_counter() - start_t) * 1000.0
            return SandboxExecutionResult(
                output=str(sv),
                success=False,
                is_error=True,
                exit_code=1,
                execution_time_ms=round(elapsed, 2),
                sandboxed=True,
            )
        except Exception as e:
            elapsed = (time.perf_counter() - start_t) * 1000.0
            return SandboxExecutionResult(
                output=f"Error leyendo '{path}': {e}",
                success=False,
                is_error=True,
                exit_code=1,
                execution_time_ms=round(elapsed, 2),
                sandboxed=True,
            )

    def edit_file(self, path: str, content: str) -> SandboxExecutionResult:
        """Modifica un archivo verificando que se mantenga dentro del workspace."""
        start_t = time.perf_counter()
        try:
            safe_path = self._validate_path_containment(path)
            os.makedirs(os.path.dirname(safe_path), exist_ok=True)
            with open(safe_path, "w", encoding="utf-8") as f:
                f.write(content)
            elapsed = (time.perf_counter() - start_t) * 1000.0
            return SandboxExecutionResult(
                output=f"Archivo '{path}' modificado satisfactoriamente dentro del sandbox.",
                success=True,
                is_error=False,
                exit_code=0,
                execution_time_ms=round(elapsed, 2),
                sandboxed=True,
            )
        except SandboxViolation as sv:
            elapsed = (time.perf_counter() - start_t) * 1000.0
            return SandboxExecutionResult(
                output=str(sv),
                success=False,
                is_error=True,
                exit_code=1,
                execution_time_ms=round(elapsed, 2),
                sandboxed=True,
            )
        except Exception as e:
            elapsed = (time.perf_counter() - start_t) * 1000.0
            return SandboxExecutionResult(
                output=f"Error escribiendo en '{path}': {e}",
                success=False,
                is_error=True,
                exit_code=1,
                execution_time_ms=round(elapsed, 2),
                sandboxed=True,
            )


class DryRunSandbox(SandboxAdapter):
    """Sandbox simulado para pruebas, benchmarks y modo seguro sin efectos en disco o SO."""

    def execute_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: float = 15.0,
    ) -> SandboxExecutionResult:
        return SandboxExecutionResult(
            output=f"[DRY-RUN] [SANDBOX] Comando '{command}' validado y ejecutado en modo simulación.",
            success=True,
            is_error=False,
            exit_code=0,
            execution_time_ms=0.1,
            sandboxed=True,
            tier=SandboxTier.DRY_RUN,
        )

    def read_file(self, path: str, max_bytes: int = 100_000) -> SandboxExecutionResult:
        return SandboxExecutionResult(
            output=f"[DRY-RUN] [SANDBOX] Lectura simulada de '{path}'.",
            success=True,
            is_error=False,
            exit_code=0,
            execution_time_ms=0.1,
            sandboxed=True,
            tier=SandboxTier.DRY_RUN,
        )

    def edit_file(self, path: str, content: str) -> SandboxExecutionResult:
        return SandboxExecutionResult(
            output=f"[DRY-RUN] [SANDBOX] Edición simulada de '{path}'.",
            success=True,
            is_error=False,
            exit_code=0,
            execution_time_ms=0.1,
            sandboxed=True,
            tier=SandboxTier.DRY_RUN,
        )


class ContainerSandboxConfig(BaseModel):
    """Configuración para aislamiento fuerte de host mediante contenedores (Docker / Podman)."""
    model_config = ConfigDict(frozen=True)

    image: str = "python:3.11-slim"
    memory_limit: str = "512m"
    cpu_quota: float = 1.0
    network_mode: str = "none"
    pids_limit: int = 64
    read_only_root: bool = True
    tmpfs_size: str = "64m"
    container_workspace: str = "/workspace"
    runtime_binary: str = "auto"  # "auto", "docker", "podman"
    fallback_to_local: bool = False


class ContainerSandboxAdapter(SandboxAdapter):
    """Adaptador de sandbox con aislamiento real a nivel de kernel y namespaces de contenedor.
    
    Provee fronteras de aislamiento reales:
    - Aislamiento de red mediante namespace (--network=none).
    - Cuotas de CPU y memoria física delimitadas con cgroups.
    - Raíz de solo lectura (--read-only) y contención de workspace montado.
    - Límite de procesos concurrentes (--pids-limit) para mitigar fork-bombs.
    - Detección automática y fallback seguro a LocalProcessSandbox cuando sea requerido.
    """

    def __init__(
        self,
        config: Optional[ContainerSandboxConfig] = None,
        workspace_root: Optional[str] = None,
        allow_network: bool = False,
        egress_policy: Optional[EgressPolicy] = None,
    ):
        self.config = config or ContainerSandboxConfig()
        self.workspace_root = os.path.realpath(workspace_root or os.getcwd())
        self.allow_network = allow_network
        self.egress_policy = egress_policy or EgressPolicy(
            mode=EgressMode.ALLOW_ALL if allow_network else EgressMode.BLOCK_ALL
        )
        self._local_fallback = LocalProcessSandbox(
            workspace_root=self.workspace_root,
            allow_network=self.allow_network,
            egress_policy=self.egress_policy,
        )
        self.runtime_binary = self._detect_runtime()

    def _detect_runtime(self) -> Optional[str]:
        if self.config.runtime_binary in ("docker", "podman"):
            return self.config.runtime_binary if shutil.which(self.config.runtime_binary) else None
        for candidate in ("docker", "podman"):
            if shutil.which(candidate):
                return candidate
        return None

    def is_runtime_available(self) -> bool:
        """Verifica si el runtime de contenedor está instalado y respondiendo."""
        if not self.runtime_binary:
            return False
        try:
            res = subprocess.run(
                [self.runtime_binary, "version"],
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            return res.returncode == 0
        except Exception:
            return False

    def execute_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: float = 15.0,
    ) -> SandboxExecutionResult:
        cmd_str = (command or "").strip()
        if not cmd_str:
            return SandboxExecutionResult(
                output="Comando vacío.",
                success=False,
                is_error=True,
                exit_code=1,
            )

        # 1. Verificación previa de política de egress
        allowed, reason = self.egress_policy.evaluate_command_egress(cmd_str)
        if not allowed:
            raise SandboxViolation(f"Violación de política de red: {reason}")

        # 2. Comprobar disponibilidad de runtime externo
        if not self.is_runtime_available():
            if self.config.fallback_to_local:
                res = self._local_fallback.execute_command(cmd_str, cwd=cwd, env=env, timeout=timeout)
                return res.model_copy(update={
                    "tier": SandboxTier.LOCAL_PROCESS,
                    "fallback_occurred": True,
                    "container_runtime": None,
                })
            raise SandboxViolation(
                f"Aislamiento fuerte requerido, pero el runtime de contenedor '{self.runtime_binary or self.config.runtime_binary}' "
                "no está disponible o no responde en el host."
            )

        # 3. Preparación de comando dentro de contenedor
        start_t = time.perf_counter()
        target_cwd = self._local_fallback._validate_path_containment(cwd) if cwd else self.workspace_root
        rel = os.path.relpath(target_cwd, self.workspace_root)
        c_work = (
            self.config.container_workspace
            if rel == "."
            else f"{self.config.container_workspace}/{rel}".replace("\\", "/")
        )

        net_mode = "none" if not self.allow_network else "bridge"
        args = [
            self.runtime_binary,
            "run",
            "--rm",
            "-i",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "-v",
            f"{self.workspace_root}:{self.config.container_workspace}:rw",
            "-w",
            c_work,
            f"--network={net_mode}",
            f"--memory={self.config.memory_limit}",
            f"--cpus={self.config.cpu_quota}",
            f"--pids-limit={self.config.pids_limit}",
        ]
        if self.config.read_only_root:
            args.extend(["--read-only", f"--tmpfs=/tmp:rw,size={self.config.tmpfs_size}"])

        clean_env = self._local_fallback._sanitize_environment(env)
        for k, v in clean_env.items():
            if k in ("JEV_SANDBOX_ACTIVE", "PYTHONUNBUFFERED"):
                args.extend(["-e", f"{k}={v}"])

        args.extend([self.config.image, "sh", "-c", cmd_str])

        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            elapsed = (time.perf_counter() - start_t) * 1000.0
            out = (proc.stdout or proc.stderr or "Comando ejecutado en contenedor").strip()
            success = (proc.returncode == 0)
            return SandboxExecutionResult(
                output=out[:20000],
                success=success,
                is_error=not success,
                exit_code=proc.returncode,
                execution_time_ms=round(elapsed, 2),
                sandboxed=True,
                tier=SandboxTier.CONTAINER,
                fallback_occurred=False,
                container_runtime=self.runtime_binary,
            )
        except subprocess.TimeoutExpired:
            elapsed = (time.perf_counter() - start_t) * 1000.0
            return SandboxExecutionResult(
                output=f"Timeout superado ({timeout}s) en contenedor ejecutando: {cmd_str[:100]}",
                success=False,
                is_error=True,
                exit_code=124,
                execution_time_ms=round(elapsed, 2),
                sandboxed=True,
                tier=SandboxTier.CONTAINER,
                fallback_occurred=False,
                container_runtime=self.runtime_binary,
            )
        except Exception as e:
            elapsed = (time.perf_counter() - start_t) * 1000.0
            return SandboxExecutionResult(
                output=f"Error en ejecución de contenedor: {e}",
                success=False,
                is_error=True,
                exit_code=1,
                execution_time_ms=round(elapsed, 2),
                sandboxed=True,
                tier=SandboxTier.CONTAINER,
                fallback_occurred=False,
                container_runtime=self.runtime_binary,
            )

    def read_file(self, path: str, max_bytes: int = 100_000) -> SandboxExecutionResult:
        res = self._local_fallback.read_file(path, max_bytes)
        return res.model_copy(update={"tier": SandboxTier.LOCAL_PROCESS, "fallback_occurred": True})

    def edit_file(self, path: str, content: str) -> SandboxExecutionResult:
        res = self._local_fallback.edit_file(path, content)
        return res.model_copy(update={"tier": SandboxTier.LOCAL_PROCESS, "fallback_occurred": True})

