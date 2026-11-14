"""Módulos obsoletos: siguen funcionando, avisan de su retirada y nadie del propio paquete los importa (auditoría A-02)."""

import importlib
import pathlib
import sys

import pytest

DEPRECATED = {
    "praxeon.policy.risk": "praxeon.policy.registry",
}

ROOT = pathlib.Path(__file__).resolve().parents[1] / "praxeon"


@pytest.mark.parametrize("module, canonical", DEPRECATED.items())
def test_a_deprecated_module_warns_and_still_works(module, canonical):
    sys.modules.pop(module, None)
    with pytest.warns(DeprecationWarning, match="v2.0.0"):
        mod = importlib.import_module(module)
    assert mod.__all__


@pytest.mark.parametrize("module", DEPRECATED)
def test_the_package_does_not_import_deprecated_modules(module):
    users = [
        str(path.relative_to(ROOT.parent))
        for path in ROOT.rglob("*.py")
        if path.with_suffix("").relative_to(ROOT.parent).as_posix().replace("/", ".") != module
        and (f"from {module} import" in path.read_text(encoding="utf-8") or f"import {module}" in path.read_text(encoding="utf-8"))
    ]
    assert users == []
