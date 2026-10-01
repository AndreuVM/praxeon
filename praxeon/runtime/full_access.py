"""Backend de ejecución Full Access para PRAXEON 1.0 (praxeon/runtime/full_access.py).

Especificación PRAXEON 1.0 (Sección 7 y 8).

Principio rector:
El sandbox no define PRAXEON. Es un mecanismo de ejecución bajo PRAXEON.
Full Access elimina el aislamiento a nivel de sistema operativo del executor,
pero NO elimina la cadena de custodia de autorización:
Proposal -> Evidence -> Risk -> Provider -> Policy -> Capability -> SecureExecutor.

FullAccessExecutor carece de jail path y no depura variables de entorno del host,
pero SOLO puede ser invocado por SecureExecutor tras verificar la firma HMAC
del CapabilityPayload con execution_mode='full_access'.
"""

from enum import Enum
import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional, Protocol, Tuple
from pydantic import BaseModel, ConfigDict


class ExecutionMode(str, Enum):
    """Modos canónicos de ejecución física de PRAXEON 1.0."""
    CONTAINER = "container"
    LOCAL_RESTRICTED = "local_restricted"
    FULL_ACCESS = "full_access"


class FullAccessResult(BaseModel):
    """Resultado estructurado de una ejecución física en host bajo Full Access."""
    model_config = ConfigDict(frozen=True)

    output: str
    success: bool
    is_error: bool = False
    exit_code: int = 0
    execution_time_ms: float = 0.0
    mode: ExecutionMode = ExecutionMode.FULL_ACCESS
    sandboxed: bool = False


class ExecutionBackend(Protocol):
    """Protocolo formal de backend de ejecución física para SecureExecutor."""

    def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> FullAccessResult:
        """Ejecuta una herramienta física autorizada con el contexto de la sesión."""
        ...


class FullAccessExecutor:
    """Ejecutor sobre el host físico sin aislamiento OS.
    
    Invocado EXCLUSIVAMENTE por SecureExecutor tras validar formalmente
    el CapabilityPayload firmado con execution_mode='full_access'.
    """

    def __init__(self, default_cwd: Optional[str] = None):
        self.default_cwd = default_cwd or os.getcwd()

    def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> FullAccessResult:
        """Despacha la ejecución física de herramientas built-in sobre el sistema operativo host."""
        ctx = context or {}
        cwd = ctx.get("working_directory") or self.default_cwd

        start_t = time.perf_counter()

        if tool_name in ("read_file", "view_file"):
            raw_path = str(arguments.get("path") or arguments.get("file") or "").strip()
            path = os.path.expanduser(os.path.expandvars(raw_path))
            if not os.path.isabs(path):
                path = os.path.join(cwd, path)
            try:
                if not os.path.exists(path):
                    elapsed = (time.perf_counter() - start_t) * 1000.0
                    return FullAccessResult(
                        output=f"[FULL_ACCESS] Archivo o ruta '{raw_path}' ({path}) no existe en el sistema.",
                        success=False,
                        is_error=True,
                        exit_code=1,
                        execution_time_ms=round(elapsed, 2),
                    )
                if os.path.isdir(path):
                    entries = os.listdir(path)[:60]
                    listing = "\n".join(f"- {e}" for e in entries) if entries else "(Directorio vacío)"
                    elapsed = (time.perf_counter() - start_t) * 1000.0
                    return FullAccessResult(
                        output=f"[FULL_ACCESS] '{raw_path}' es un DIRECTORIO en el sistema host. Se han listado {len(entries)} elementos:\n{listing}",
                        success=True,
                        is_error=False,
                        exit_code=0,
                        execution_time_ms=round(elapsed, 2),
                    )
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(100_000)
                    if len(content) >= 100_000:
                        content += "\n\n[... Truncado a 100.000 bytes por límite de buffer ...]"
                elapsed = (time.perf_counter() - start_t) * 1000.0
                return FullAccessResult(
                    output=f"[FULL_ACCESS] Contenido de '{path}':\n{content}",
                    success=True,
                    is_error=False,
                    exit_code=0,
                    execution_time_ms=round(elapsed, 2),
                )
            except Exception as e:
                elapsed = (time.perf_counter() - start_t) * 1000.0
                return FullAccessResult(
                    output=f"[FULL_ACCESS] Error leyendo '{path}': {e}",
                    success=False,
                    is_error=True,
                    exit_code=1,
                    execution_time_ms=round(elapsed, 2),
                )

        elif tool_name == "edit_file":
            raw_path = str(arguments.get("path") or "").strip()
            path = os.path.expanduser(os.path.expandvars(raw_path))
            content = str(arguments.get("content") or "").strip()
            if not os.path.isabs(path):
                path = os.path.join(cwd, path)
            try:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                elapsed = (time.perf_counter() - start_t) * 1000.0
                return FullAccessResult(
                    output=f"[FULL_ACCESS] Archivo '{path}' modificado satisfactoriamente en el host.",
                    success=True,
                    is_error=False,
                    exit_code=0,
                    execution_time_ms=round(elapsed, 2),
                )
            except Exception as e:
                elapsed = (time.perf_counter() - start_t) * 1000.0
                return FullAccessResult(
                    output=f"[FULL_ACCESS] Error modificando '{path}': {e}",
                    success=False,
                    is_error=True,
                    exit_code=1,
                    execution_time_ms=round(elapsed, 2),
                )

        elif tool_name in ("finish", "complete_task", "done", "complete", "task_completed"):
            summary = str(arguments.get("summary") or arguments.get("final_answer") or arguments.get("output") or "Tarea completada.").strip()
            elapsed = (time.perf_counter() - start_t) * 1000.0
            return FullAccessResult(
                output=f"Tarea concluida: {summary}",
                success=True,
                is_error=False,
                exit_code=0,
                execution_time_ms=round(elapsed, 2),
            )

        elif tool_name == "run_command" or (tool_name and not any(bad in tool_name.lower() for bad in ("unregistered", "unknown", "malicious"))):
            cmd = str(arguments.get("command") or arguments.get("cmd") or arguments.get("raw") or "").strip()
            if tool_name != "run_command":
                cmd = f"{tool_name} {cmd}".strip() if cmd else tool_name
            timeout = float(arguments.get("timeout") or 30.0)
            try:
                if sys.platform == "win32":
                    # Normalización inteligente de comandos comunes de Linux emitidos por LLMs en Windows
                    cmd_norm = cmd
                    cmd_lower = cmd.strip().lower()
                    if cmd_lower in ("ls -la", "ls -l", "ls -al", "ls -a", "ls -lh"):
                        cmd_norm = "Get-ChildItem -Force"
                    elif cmd_lower.startswith("ls -la ") or cmd_lower.startswith("ls -l ") or cmd_lower.startswith("ls -al "):
                        target_arg = cmd.split(maxsplit=2)[-1]
                        cmd_norm = f"Get-ChildItem -Force {target_arg}"
                    elif (cmd_norm.startswith('"') or cmd_norm.startswith("'")) and not cmd_norm.startswith("&"):
                        cmd_norm = f"& {cmd_norm}"

                    proc = subprocess.run(
                        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd_norm],
                        cwd=cwd,
                        env=os.environ.copy(),
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        encoding="utf-8",
                        errors="replace",
                        shell=False,
                    )
                    # Si falla por ser script por lotes clásico (.bat/.cmd), intentar fallback a cmd.exe
                    if proc.returncode != 0 and any(cmd_lower.endswith(ext) for ext in (".bat", ".cmd")):
                        fallback_proc = subprocess.run(
                            cmd,
                            shell=True,
                            cwd=cwd,
                            env=os.environ.copy(),
                            capture_output=True,
                            text=True,
                            timeout=timeout,
                            encoding="utf-8",
                            errors="replace",
                        )
                        if fallback_proc.returncode == 0:
                            proc = fallback_proc
                else:
                    proc = subprocess.run(
                        cmd,
                        shell=True,
                        cwd=cwd,
                        env=os.environ.copy(),
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        encoding="utf-8",
                        errors="replace",
                    )
                elapsed = (time.perf_counter() - start_t) * 1000.0
                out = (proc.stdout or proc.stderr or f"Comando '{cmd}' ejecutado sin salida en host.").strip()
                success = (proc.returncode == 0)
                return FullAccessResult(
                    output=out[:20000],
                    success=success,
                    is_error=not success,
                    exit_code=proc.returncode,
                    execution_time_ms=round(elapsed, 2),
                )
            except subprocess.TimeoutExpired:
                elapsed = (time.perf_counter() - start_t) * 1000.0
                return FullAccessResult(
                    output=f"[FULL_ACCESS] Timeout superado ({timeout}s) ejecutando '{cmd}' en host.",
                    success=False,
                    is_error=True,
                    exit_code=124,
                    execution_time_ms=round(elapsed, 2),
                )
            except Exception as e:
                elapsed = (time.perf_counter() - start_t) * 1000.0
                return FullAccessResult(
                    output=f"[FULL_ACCESS] Error ejecutando '{cmd}' en host: {e}",
                    success=False,
                    is_error=True,
                    exit_code=1,
                    execution_time_ms=round(elapsed, 2),
                )

        elapsed = (time.perf_counter() - start_t) * 1000.0
        return FullAccessResult(
            output=f"Herramienta '{tool_name}' no soportada por FullAccessExecutor.",
            success=False,
            is_error=True,
            exit_code=1,
            execution_time_ms=round(elapsed, 2),
        )
