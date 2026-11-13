"""Entorno y ciclo de vida de los procesos que lanza PRAXEON (praxeon/runtime/child_env.py).

Los comandos del agente no heredan el entorno completo del servidor: solo una lista blanca de variables necesarias para que
funcionen las herramientas habituales (PATH, carpetas del usuario, idioma, temporales...). Así las claves de API, tokens y
secretos que tenga el proceso de PRAXEON no llegan al comando ni, por tanto, al modelo.

Se amplía con `PRAXEON_CHILD_ENV_ALLOW` (lista separada por comas; admite prefijos con `*`, p. ej. `JAVA_HOME,CONDA_*`).
Incluso lo permitido se descarta si su nombre parece un secreto (KEY, TOKEN, SECRET, PASSWORD...).
"""

from __future__ import annotations

import fnmatch
import os
import signal
import subprocess
import sys
from typing import Any, Dict, Iterable, Mapping, Optional

# Variables que las herramientas comunes necesitan para arrancar (comparación sin distinguir mayúsculas)
BASE_ALLOWED_ENV = frozenset({
    # Ejecutables y shell
    "PATH", "PATHEXT", "COMSPEC", "SHELL", "TERM", "PSMODULEPATH",
    # Sistema Windows
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "OS", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PROGRAMDATA",
    "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432", "ALLUSERSPROFILE", "PUBLIC",
    # Usuario y carpetas
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "USERNAME", "USER", "LOGNAME", "USERDOMAIN",
    "COMPUTERNAME", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
    # Temporales
    "TEMP", "TMP", "TMPDIR",
    # Idioma y zona horaria
    "LANG", "LANGUAGE", "TZ",
    # Python
    "PYTHONIOENCODING", "PYTHONUTF8", "VIRTUAL_ENV",
    # Proxies corporativos (sin ellos nada sale a la red en algunas redes)
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
})
BASE_ALLOWED_PREFIXES = ("LC_",)

# Partes de nombre que delatan un secreto: se retiran aunque estén en la lista blanca o en PRAXEON_CHILD_ENV_ALLOW
SECRET_NAME_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH", "COOKIE", "SESSION")

EXTRA_ALLOW_ENV_VAR = "PRAXEON_CHILD_ENV_ALLOW"


def looks_secret(name: str) -> bool:
    """True si el nombre de la variable sugiere que guarda una credencial."""
    upper = name.upper()
    return any(marker in upper for marker in SECRET_NAME_MARKERS)


def _extra_patterns(source: Mapping[str, str]) -> list:
    raw = source.get(EXTRA_ALLOW_ENV_VAR, "")
    return [p.strip().upper() for p in raw.split(",") if p.strip()]


def _is_allowed(name: str, extra: Iterable[str]) -> bool:
    upper = name.upper()
    if upper in BASE_ALLOWED_ENV or upper.startswith(BASE_ALLOWED_PREFIXES):
        return True
    return any(fnmatch.fnmatchcase(upper, pattern) for pattern in extra)


def child_environment(
    source: Optional[Mapping[str, str]] = None,
    blocked_markers: Iterable[str] = (),
) -> Dict[str, str]:
    """Entorno para un proceso hijo: solo variables de la lista blanca y nunca las que parecen secretos.

    `blocked_markers` añade subcadenas a retirar (p. ej. la lista de bloqueo propia del sandbox).
    """
    env = os.environ if source is None else source
    extra = _extra_patterns(env)
    blocked = tuple(m.upper() for m in blocked_markers)
    clean: Dict[str, str] = {}
    for name, value in env.items():
        if not _is_allowed(name, extra) or looks_secret(name):
            continue
        if any(marker in name.upper() for marker in blocked):
            continue
        clean[name] = value
    return clean


def process_group_kwargs() -> Dict[str, Any]:
    """Argumentos de Popen para que el proceso y sus descendientes formen un grupo que se pueda terminar entero."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Termina el proceso y todos sus descendientes (un `shell=True` deja vivos a los nietos si solo se mata al hijo)."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass


def run_with_tree_timeout(args, *, timeout: float, **popen_kwargs) -> subprocess.CompletedProcess:
    """Como `subprocess.run(..., capture_output=True)`, pero al vencer el timeout mata todo el árbol de procesos.

    Lanza `subprocess.TimeoutExpired` igual que `subprocess.run`.
    """
    popen_kwargs.setdefault("stdout", subprocess.PIPE)
    popen_kwargs.setdefault("stderr", subprocess.PIPE)
    for key, value in process_group_kwargs().items():
        popen_kwargs.setdefault(key, value)
    with subprocess.Popen(args, **popen_kwargs) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_process_tree(proc)
            try:
                proc.communicate(timeout=5)
            except (subprocess.TimeoutExpired, ValueError, OSError):
                pass
            raise
        return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)


def full_access_enabled(source: Optional[Mapping[str, str]] = None) -> bool:
    """Full Access solo está disponible si el proceso lo habilita (la app de escritorio lo hace; servidor y CLI no)."""
    env = os.environ if source is None else source
    return env.get("PRAXEON_ALLOW_FULL_ACCESS", "").strip().lower() in ("1", "true", "yes")
