"""La aplicación se crea en un único punto (`create_app`), no al importar el módulo (auditoría F-03/F-04)."""

import os
import subprocess
import sys

import pytest
from fastapi import FastAPI


def _run(code: str, **env: str) -> subprocess.CompletedProcess:
    full_env = dict(os.environ, **env)
    return subprocess.run([sys.executable, "-c", code], env=full_env, capture_output=True, text=True, timeout=120)


def test_importing_the_server_has_no_side_effects_even_with_a_broken_production_profile():
    # Antes, `import praxeon.server.app` construía la app y fallaba con este perfil mal configurado
    res = _run("import praxeon.server.app, praxeon.server; print('ok')",
               PRAXEON_PROFILE="production", PRAXEON_SECRET_KEY="", PRAXEON_JWT_SECRET="", PRAXEON_RECEIPT_KEY="")
    assert res.returncode == 0 and "ok" in res.stdout, res.stderr


def test_the_broken_profile_still_fails_when_the_app_is_created(monkeypatch):
    from praxeon.server.app import create_app

    for name in ("PRAXEON_SECRET_KEY", "PRAXEON_JWT_SECRET", "PRAXEON_RECEIPT_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError):
        create_app(profile="production")


def test_the_default_app_is_still_available_for_uvicorn_module_app():
    import praxeon.server.app as module

    assert isinstance(module.app, FastAPI)
    assert module.app is module.app          # se crea una vez
    with pytest.raises(AttributeError):
        module.no_existe  # noqa: B018
