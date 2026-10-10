"""Tests de seguridad y funcionalidad para WasmSandbox (tests/security/test_wasm_sandbox.py)."""

import os
from pathlib import Path
import pytest

from praxeon.runtime.sandbox import (
    SandboxTier,
    WasmSandbox,
    WasmSandboxConfig,
)


def test_wasm_sandbox_initialization(tmp_path: Path):
    """Valida la configuración e inicialización de WasmSandbox."""
    cfg = WasmSandboxConfig(
        memory_limit_bytes=32 * 1024 * 1024,
        timeout=5.0,
        allow_network=False,
    )
    sandbox = WasmSandbox(config=cfg, workspace_root=str(tmp_path))

    assert sandbox.workspace_root == os.path.abspath(str(tmp_path))
    assert isinstance(sandbox.is_native_available, bool)


def test_wasm_sandbox_file_containment(tmp_path: Path):
    """Valida la contención de rutas WASI dentro del workspace."""
    sandbox = WasmSandbox(workspace_root=str(tmp_path))

    # 1. Escritura y lectura válida dentro del workspace
    edit_res = sandbox.edit_file("sample_wasi.txt", "Contenido seguro en sandbox WASI")
    assert edit_res.success is True
    assert edit_res.tier == SandboxTier.WASM

    read_res = sandbox.read_file("sample_wasi.txt")
    assert read_res.success is True
    assert "Contenido seguro en sandbox WASI" in read_res.output
    assert read_res.tier == SandboxTier.WASM

    # 2. Intento de evasión hacia afuera del workspace
    evasion_res = sandbox.read_file("../../secret_key.pem")
    assert evasion_res.success is False
    assert evasion_res.is_error is True


def test_wasm_sandbox_command_execution(tmp_path: Path):
    """Valida la ejecución de comandos asignando el tier WASM."""
    sandbox = WasmSandbox(workspace_root=str(tmp_path))

    res = sandbox.execute_command("python -c \"print('WASI Confined Computation')\"")
    assert res.success is True
    assert res.tier == SandboxTier.WASM
    assert "WASI Confined Computation" in res.output


def test_wasm_sandbox_module_execution_emulated(tmp_path: Path):
    """Valida la invocación de execute_wasm con un módulo o bytecode binario."""
    sandbox = WasmSandbox(workspace_root=str(tmp_path))

    # Módulo binario WASM mínimo (magic bytes: \x00asm\x01\x00\x00\x00)
    fake_wasm_bytes = b"\x00asm\x01\x00\x00\x00"
    res = sandbox.execute_wasm(fake_wasm_bytes, args=["--verify"])

    assert res.tier == SandboxTier.WASM
    assert res.success is True
    assert "[WASM" in res.output
