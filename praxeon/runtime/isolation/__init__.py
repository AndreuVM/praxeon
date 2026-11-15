"""Aislamiento fuerte de la ejecución (auditoría 2026-11-01 S-01, AUD-19).

El modo de ejecución ``container`` significa «aislado»: el comando corre sin red, sin acceso al perfil del usuario y
con el sistema de ficheros de solo lectura salvo el workspace. El mecanismo depende del sistema:

=============== ========================================== ============================================
Backend          Sistema                                    Requisitos
=============== ========================================== ============================================
``appcontainer`` Windows 8+                                 ninguno (API del sistema)
``bubblewrap``   Linux                                      ``bwrap`` y espacios de nombres de usuario
``container``    cualquiera                                 Docker o Podman y una imagen con las herramientas
=============== ========================================== ============================================

``PRAXEON_ISOLATION`` elige: ``auto`` (por defecto: el nativo del sistema si funciona), un backend concreto u ``off``.
Docker/Podman no se elige solo: la imagen no tiene las herramientas del proyecto, hay que pedirlo expresamente.

Una sesión sin modo explícito (o con ``auto``) es aislada si hay backend; si no, ``local_restricted`` con un aviso.
"""

from __future__ import annotations

import functools
import hashlib
import os
import shutil
import sys
from dataclasses import dataclass
from praxeon.runtime.isolation import bubblewrap

BACKENDS = ("appcontainer", "bubblewrap", "container")
ISOLATED_MODE = "container"
LOCAL_MODE = "local_restricted"
AUTO_MODES = ("", "auto", "default")


@dataclass(frozen=True)
class IsolationStatus:
    requested: str                 # lo que pide PRAXEON_ISOLATION
    backend: str | None         # el que se usará, o None
    reason: str                    # por qué (para la interfaz y los eventos)

    @property
    def available(self) -> bool:
        return self.backend is not None


def appcontainer_supported() -> bool:
    """Windows 8 o posterior: existe la API de AppContainer."""
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        return hasattr(ctypes.WinDLL("userenv"), "CreateAppContainerProfile")
    except OSError:
        return False


def container_name(workspace_root: str) -> str:
    """Nombre del AppContainer de un workspace: uno por carpeta, para que una sesión no alcance otro proyecto."""
    digest = hashlib.sha256(os.path.normcase(os.path.realpath(workspace_root)).encode("utf-8")).hexdigest()[:16]
    return "praxeon.ws." + digest


def requested_backend() -> str:
    value = (os.environ.get("PRAXEON_ISOLATION") or "auto").strip().lower()
    if value in ("off", "none", "no", "0", "false", "local"):
        return "off"
    return value if value in BACKENDS else "auto"


def _container_ready() -> bool:
    from praxeon.runtime.sandbox import ContainerSandboxAdapter

    return ContainerSandboxAdapter.runtime_ready()


def _check(backend: str) -> tuple[bool, str]:
    if backend == "appcontainer":
        ok = appcontainer_supported()
        return ok, "AppContainer de Windows" if ok else "AppContainer solo existe en Windows 8 o posterior"
    if backend == "bubblewrap":
        if sys.platform != "linux":
            return False, "bubblewrap solo funciona en Linux"
        if not bubblewrap.binary():
            return False, "bubblewrap (bwrap) no está instalado"
        return (bubblewrap.usable(), "bubblewrap" if bubblewrap.usable()
                else "el núcleo no permite espacios de nombres de usuario sin privilegios (bwrap no arranca)")
    if backend == "container":
        ready = _container_ready()
        return ready, "Docker/Podman" if ready else "Docker o Podman no está instalado o no responde"
    return False, f"backend desconocido: {backend}"


@functools.lru_cache(maxsize=1)
def status() -> IsolationStatus:
    """Backend de aislamiento disponible según ``PRAXEON_ISOLATION`` y el sistema (se calcula una vez por proceso)."""
    requested = requested_backend()
    if requested == "off":
        return IsolationStatus(requested, None, "aislamiento desactivado (PRAXEON_ISOLATION=off)")
    if requested != "auto":
        ok, reason = _check(requested)
        return IsolationStatus(requested, requested if ok else None, reason)
    native = "appcontainer" if sys.platform == "win32" else "bubblewrap" if sys.platform == "linux" else None
    if native is None:
        return IsolationStatus("auto", None, "no hay aislamiento nativo en este sistema; use PRAXEON_ISOLATION=container")
    ok, reason = _check(native)
    return IsolationStatus("auto", native if ok else None, reason)


def refresh() -> IsolationStatus:
    """Vuelve a detectar (tras instalar bwrap/Docker o cambiar ``PRAXEON_ISOLATION``)."""
    status.cache_clear()
    bubblewrap.usable.cache_clear()
    return status()


def resolve_execution_mode(requested: str | None) -> tuple[str, str | None]:
    """Modo efectivo de una sesión nueva y, si hace falta, el aviso para el usuario.

    Sin modo (o ``auto``): aislado si hay backend; si no, ``local_restricted`` con aviso. Un modo explícito se respeta;
    ``container`` sin backend se mantiene (los comandos fallarán cerrados) y también lleva aviso.
    """
    mode = (requested or "").strip().lower()
    current = status()
    if mode in AUTO_MODES:
        if current.available:
            return ISOLATED_MODE, None
        return LOCAL_MODE, (f"Sin aislamiento fuerte ({current.reason}): los comandos se ejecutan en el sandbox local, "
                            "que no aísla la red ni el sistema de ficheros.")
    if mode == ISOLATED_MODE and not current.available:
        return mode, f"Modo aislado pedido, pero no está disponible ({current.reason}): los comandos se rechazarán."
    return mode, None


def tool_read_paths() -> list[str]:
    """Carpetas de herramientas que el comando aislado puede leer y ejecutar aunque estén en el perfil del usuario.

    El Python base (un entorno virtual del workspace apunta a él), ``git`` y ``node`` si están instalados bajo el
    perfil, y lo que añada ``PRAXEON_ISOLATION_READ_PATHS`` (separado por ``os.pathsep``).
    """
    home = os.path.realpath(os.path.expanduser("~"))
    paths = [sys.base_prefix]
    for tool in ("git", "node"):
        found = shutil.which(tool)
        if found:
            root = os.path.dirname(os.path.dirname(os.path.realpath(found)))
            if os.path.realpath(root).startswith(home):
                paths.append(root)
    extra = os.environ.get("PRAXEON_ISOLATION_READ_PATHS", "")
    paths += [p for p in extra.split(os.pathsep) if p.strip()]
    seen: list[str] = []
    for p in paths:
        real = os.path.realpath(p)
        if os.path.isdir(real) and real not in seen:
            seen.append(real)
    return seen
