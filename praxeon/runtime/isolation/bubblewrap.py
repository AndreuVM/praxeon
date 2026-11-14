"""Aislamiento nativo en Linux con bubblewrap (``bwrap``) (AUD-19, S-01).

El comando corre en espacios de nombres propios, sin privilegios y sin instalar un demonio:

- Sin red (``--unshare-net``): solo una interfaz de loopback propia, vacía.
- Sistema de ficheros del host montado de solo lectura; ``/home``, ``/root``, ``/run`` y ``/tmp`` sustituidos por
  carpetas vacías en memoria (fuera quedan ``~/.ssh``, las credenciales y los sockets como el de Docker).
- El workspace montado de lectura y escritura en su misma ruta, y las carpetas de herramientas que viven bajo
  ``/home`` (el Python base de un entorno virtual) montadas de solo lectura.
- ``--die-with-parent`` y ``--new-session``: si PRAXEON corta el comando, no queda nada vivo ni puede inyectar
  pulsaciones en la terminal.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess  # nosec B404 - lanza bwrap con argumentos en lista, sin shell
from typing import Dict, Iterable, List, Optional

# Se sustituyen por carpetas vacías en memoria dentro del aislamiento: no se usan ficheros temporales del host
_HIDDEN = ("/home", "/root", "/run", "/tmp", "/var/tmp")  # nosec B108


def binary() -> Optional[str]:
    return shutil.which("bwrap")


@functools.lru_cache(maxsize=1)
def usable() -> bool:
    """``bwrap`` existe y el núcleo permite espacios de nombres de usuario sin privilegios (no siempre: AppArmor)."""
    path = binary()
    if not path:
        return False
    try:
        probe = subprocess.run(  # nosec B603 - argumentos fijos
            [path, "--ro-bind", "/", "/", "--unshare-all", "--die-with-parent", "true"],
            capture_output=True, timeout=10,
        )
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def wrap(args: List[str], *, workspace_root: str, cwd: str, env: Dict[str, str], allow_network: bool = False,
         read_paths: Iterable[str] = ()) -> List[str]:
    """``args`` envueltos en bwrap con el workspace escribible y el resto del sistema de solo lectura."""
    path = binary()
    if not path:
        raise FileNotFoundError("bwrap no está instalado")
    ws = os.path.realpath(workspace_root)
    cmd = [path, "--die-with-parent", "--new-session", "--unshare-all"]
    if allow_network:
        cmd.append("--share-net")
    cmd += ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    for hidden in _HIDDEN:
        if os.path.isdir(hidden) and not os.path.islink(hidden):
            cmd += ["--tmpfs", hidden]
    # Lo que queda bajo las carpetas ocultas y hace falta: herramientas (solo lectura) y el workspace (escritura)
    for extra in sorted({os.path.realpath(p) for p in read_paths if p and os.path.isdir(p)}):
        if any(extra == h or extra.startswith(h + "/") for h in _HIDDEN):
            cmd += ["--ro-bind", extra, extra]
    cmd += ["--bind", ws, ws, "--chdir", os.path.realpath(cwd), "--clearenv"]
    child_env = dict(env)
    # /tmp es el tmpfs propio del aislamiento (montado arriba), no el del host
    child_env.update({"HOME": ws, "TMPDIR": "/tmp", "TMP": "/tmp", "TEMP": "/tmp"})  # nosec B108
    for key, value in sorted(child_env.items()):
        cmd += ["--setenv", key, value]
    return cmd + ["--"] + list(args)
