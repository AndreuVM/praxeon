"""Arranque opcional del servidor local de KEV (`python -m kev.serve`) desde PRAXEON (praxeon/providers/kev_server.py).

KEV (https://github.com/jaredpalmer/kev) se ejecuta como un servidor HTTP con la API `/v1/systemone` de TypeSafe.
Si el operador lo pide (``KEV_AUTOSTART=1``) y dice dónde está el repo (``KEV_HOME``), PRAXEON lo lanza una sola vez,
lo comparte entre todas las sesiones y lo cierra al salir. Si no, el servidor se arranca a mano y PRAXEON solo se conecta.

Variables de entorno (todas de confianza: las fija quien administra el servidor, nunca una petición):
- ``KEV_AUTOSTART``: ``1`` para activar el arranque automático.
- ``KEV_HOME``: carpeta del repo de KEV clonado (donde está su ``pyproject.toml``).
- ``KEV_RUN``: checkpoint a servir (por defecto ``jaredpalmer/kev-4b``).
- ``KEV_PORT``: puerto (por defecto 8009). El servidor escucha siempre en 127.0.0.1.
- ``KEV_SERVE_COMMAND``: orden completa si no usas uv; admite ``{run}`` y ``{port}``.
- ``KEV_STARTUP_TIMEOUT``: segundos máximos de espera (por defecto 300: la primera vez descarga los pesos).
"""

from __future__ import annotations

import atexit
import logging
import os
import shlex
import shutil
import subprocess  # nosec B404
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from praxeon.providers.hardware import kev_device

logger = logging.getLogger("praxeon.providers.kev_server")

DEFAULT_RUN = "jaredpalmer/kev-4b"
DEFAULT_PORT = 8009
LOOPBACK = "127.0.0.1"


def server_is_up(base_url: str, timeout: float = 2.0) -> bool:
    """Un servidor KEV responde a ``GET /v1/models`` con 200 cuando el modelo ya está cargado."""
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/v1/models", headers={"User-Agent": "Praxeon/1.0.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            return resp.getcode() == 200
    except urllib.error.HTTPError as exc:
        return exc.code in (401, 403)  # está vivo pero exige clave
    except Exception:
        return False


class KevServerManager:
    """Lanza y vigila un único proceso ``kev.serve`` por combinación (carpeta, checkpoint, puerto)."""

    _instances: Dict[Tuple[str, str, int], "KevServerManager"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, home: str, run: str = DEFAULT_RUN, port: int = DEFAULT_PORT,
                 command: Optional[str] = None, startup_timeout: float = 300.0):
        self.home = Path(home)
        self.run = run
        self.port = int(port)
        self.command = command
        self.startup_timeout = startup_timeout
        self._proc: Optional[subprocess.Popen] = None
        self._cuda_graphs: Optional[bool] = None
        self._lock = threading.Lock()
        self._log_handle = None

    @property
    def base_url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}"

    @classmethod
    def from_env(cls) -> Optional["KevServerManager"]:
        if os.getenv("KEV_AUTOSTART", "").strip().lower() not in ("1", "true", "yes"):
            return None
        home = os.getenv("KEV_HOME", "").strip()
        if not home or not Path(home).is_dir():
            logger.warning("KEV_AUTOSTART activo pero KEV_HOME no apunta a una carpeta existente: %r", home)
            return None
        run = os.getenv("KEV_RUN", DEFAULT_RUN).strip() or DEFAULT_RUN
        try:
            port = int(os.getenv("KEV_PORT", str(DEFAULT_PORT)))
            timeout = float(os.getenv("KEV_STARTUP_TIMEOUT", "300"))
        except ValueError:
            logger.warning("KEV_PORT o KEV_STARTUP_TIMEOUT no son numéricos; se desactiva el arranque automático.")
            return None
        key = (str(Path(home).resolve()), run, port)
        with cls._instances_lock:
            if key not in cls._instances:
                cls._instances[key] = cls(home, run, port, os.getenv("KEV_SERVE_COMMAND") or None, timeout)
            return cls._instances[key]

    def _argv(self) -> List[str]:
        if self.command:
            tokens = shlex.split(self.command.format(run=self.run, port=self.port), posix=(os.name != "nt"))
            # en Windows shlex conserva las comillas de las rutas con espacios: se quitan
            return [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t for t in tokens]
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("No se encuentra 'uv' en el PATH: instálalo o define KEV_SERVE_COMMAND.")
        # El reparto GPU/CPU se adapta al equipo (ver hardware.py); KEV_DEVICE lo fuerza
        device, self._cuda_graphs, reason = kev_device(run=self.run)
        logger.info("KEV en %s (CUDA graphs: %s): %s", device, self._cuda_graphs, reason)
        # Con un entorno ya montado no se resincroniza: `uv run` volvería a instalar el PyTorch del lockfile (solo CPU)
        # encima del que el operador haya puesto con CUDA.
        sync = [] if (self.home / ".venv").is_dir() else ["--extra", "serve"]
        flags = ["--no-sync"] if not sync else sync
        return [uv, "run", *flags, "python", "-m", "kev.serve",
                "--run", self.run, "--host", LOOPBACK, "--port", str(self.port), "--device", device]

    def is_running(self) -> bool:
        return server_is_up(self.base_url)

    def ensure_ready(self) -> bool:
        """Devuelve True cuando el servidor responde; lo lanza si hace falta y espera a que cargue el modelo."""
        if self.is_running():
            return True
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                logger.error("kev.serve terminó con código %s antes de estar listo (revisa el log).", self._proc.returncode)
                return False
            if self.is_running():
                return True
            time.sleep(1.0)
        logger.error("kev.serve no respondió en %.0f s.", self.startup_timeout)
        return False

    def _start(self) -> None:
        argv = self._argv()
        log_dir = Path(os.getenv("PRAXEON_CACHE_DIR", ".praxeon_cache"))
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_handle = open(log_dir / "kev_server.log", "ab")  # noqa: SIM115 (vive lo que el proceso)
        logger.info("Arrancando KEV: %s (cwd=%s)", " ".join(argv), self.home)
        env = dict(os.environ)
        if self._cuda_graphs is not None:
            # con poca VRAM libre los CUDA graphs de KEV (activos por defecto) lo vuelven el doble de lento
            env.setdefault("KEV_CUDA_GRAPHS", "1" if self._cuda_graphs else "0")
        if os.name == "nt":
            # Sin modo desarrollador Windows no permite enlaces simbólicos y la descarga de pesos de Hugging Face falla
            env.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
        self._proc = subprocess.Popen(  # nosec B603
            argv, cwd=str(self.home), env=env, stdout=self._log_handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
        atexit.register(self.stop)

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            if os.name == "nt":
                # 'uv run' lanza el servidor como proceso hijo: sin matar el árbol entero quedaría huérfano
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)  # nosec B603 B607
            else:
                proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None


__all__ = ["KevServerManager", "server_is_up", "DEFAULT_PORT", "DEFAULT_RUN"]
