"""Compila la interfaz web (web/) y la copia a praxeon/server/static para empaquetarla en el wheel (auditoría A-08).

`praxeon/server/static` no se versiona: se genera aquí, antes de `python -m build`, para que el wheel lleve siempre la
interfaz que corresponde a las fuentes de `web/src` (antes se versionaba a mano y quedaba desfasada).

Uso:
    uv run python scripts/build_frontend.py            # npm ci + npm run build + copia
    uv run python scripts/build_frontend.py --no-install   # reutiliza web/node_modules
"""

from __future__ import annotations

import argparse
import shutil
import subprocess  # noqa: S404 - ejecuta npm, una herramienta de desarrollo fija
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DIST = WEB / "dist"
STATIC = ROOT / "praxeon" / "server" / "static"


def _npm(*args: str) -> None:
    npm = shutil.which("npm")
    if npm is None:
        sys.exit("No se encuentra npm: instale Node.js 20 o superior para compilar la interfaz.")
    subprocess.run([npm, *args], cwd=WEB, check=True)  # noqa: S603 - argumentos fijos, sin entrada externa


def build(install: bool = True) -> Path:
    if install:
        _npm("ci")
    _npm("run", "build")
    if not (DIST / "index.html").is_file():
        sys.exit(f"La compilación no produjo {DIST / 'index.html'}.")
    if STATIC.exists():
        shutil.rmtree(STATIC)
    shutil.copytree(DIST, STATIC)
    return STATIC


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-install", action="store_true", help="no ejecutar `npm ci` (usa web/node_modules)")
    target = build(install=not parser.parse_args().no_install)
    print(f"[OK] Interfaz compilada en {target}")


if __name__ == "__main__":
    main()
