"""¿Está compilada la interfaz web? (`web/dist` o `praxeon/server/static`, que ya no se versiona: scripts/build_frontend.py).

En CI se compila antes de los tests, así que estos tests sí se ejecutan allí; en local se saltan si no se ha compilado.
"""

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_BUILT = any(
    (_ROOT / folder / "index.html").is_file() for folder in (Path("web", "dist"), Path("praxeon", "server", "static"))
)
requires_frontend = pytest.mark.skipif(
    not FRONTEND_BUILT, reason="la interfaz web no está compilada (uv run python scripts/build_frontend.py)"
)
