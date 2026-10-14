"""PRAXEON como cliente MCP: arranque, revisión/aprobación, registro con riesgo, manejadores y entorno aislado."""

import json
import os
import sys
from pathlib import Path

import pytest

from praxeon.integrations.mcp_client import McpConfigStore, McpError, McpManager, McpServerConfig, ToolOverride
from praxeon.policy.registry import ToolRegistry

FAKE = str(Path(__file__).with_name("mcp_fake_server.py"))


class FakeExecutor:
    def __init__(self):
        self.handlers = {}

    def register_handler(self, name, fn):
        self.handlers[name] = fn

    def unregister_handler(self, name):
        self.handlers.pop(name, None)


@pytest.fixture
def mcp(tmp_path):
    store = McpConfigStore(str(tmp_path / "mcp.json"))
    manager = McpManager(store, ToolRegistry(register_defaults=True), FakeExecutor())
    yield manager
    manager.close_all()


def add(mcp, name="fake", **extra):
    cfg = McpServerConfig(name=name, command=sys.executable, args=[FAKE, *extra.pop("args", [])], **extra)
    mcp.store.save(cfg)
    return cfg


def approved(mcp, name="fake"):
    info = mcp.enable(name)
    assert info["status"] == "pending_approval", info
    return mcp.approve(name, info["tools_hash"])


# ---------------------------------------------------------------- aprobación

def test_a_new_server_is_off_and_exposes_nothing_until_it_is_reviewed_and_approved(mcp):
    add(mcp)
    mcp.ensure_started()
    assert mcp.describe("fake")["status"] == "disabled" and mcp.registry.get_tool("mcp__fake__echo") is None

    info = mcp.enable("fake")
    assert info["status"] == "pending_approval" and not info["approved"]
    assert {t["name"] for t in info["tools"]} == {"echo", "write_note", "boom", "env_has", "inject"}
    assert all(not t["exposed"] for t in info["tools"])
    assert mcp.registry.get_tool("mcp__fake__echo") is None and mcp.prompt_section() == ""

    with pytest.raises(ValueError, match="cambiado"):
        mcp.approve("fake", "huella-que-no-es")
    final = mcp.approve("fake", info["tools_hash"])
    assert final["status"] == "running" and final["approved"] and all(t["exposed"] for t in final["tools"])
    assert mcp.registry.get_tool("mcp__fake__echo") is not None


def test_changed_tools_after_approval_are_not_exposed_until_reapproved(mcp, tmp_path):
    variant = tmp_path / "variant.txt"
    variant.write_text("v1", encoding="utf-8")
    add(mcp, args=[str(variant)])
    approved(mcp)
    assert mcp.registry.get_tool("mcp__fake__echo") is not None

    variant.write_text("v2: ahora hace otra cosa", encoding="utf-8")   # el servidor cambia su definición
    info = mcp.start_server("fake")
    assert info["status"] == "changed" and not info["approved"]
    assert mcp.registry.get_tool("mcp__fake__echo") is None and "mcp__fake__echo" not in mcp.executor.handlers

    assert mcp.approve("fake", info["tools_hash"])["status"] == "running"


def test_changing_what_is_executed_cancels_the_approval_and_disables_the_server(mcp):
    add(mcp)
    approved(mcp)
    mcp.store.save(McpServerConfig(name="fake", command=sys.executable, args=[FAKE, "--otro"], enabled=True, approved_hash=mcp.store.get("fake").approved_hash))
    saved = mcp.store.get("fake")
    assert saved.approved_hash is None and saved.enabled is False


# ---------------------------------------------------------------- riesgo

def test_every_tool_needs_confirmation_by_default_and_effects_are_never_auto_confirmed(mcp):
    add(mcp)
    approved(mcp)
    for tool in ("mcp__fake__echo", "mcp__fake__write_note"):
        spec = mcp.registry.get_tool(tool)
        assert spec.risk_level.value.upper() == "MEDIUM" and spec.requires_confirmation and spec.auto_confirm is False
        assert spec.source == "mcp:fake" and spec.category == "mcp"
        assert mcp.registry.assess_risk(tool).requires_confirmation


def test_server_hints_are_only_trusted_when_the_operator_allows_it(mcp):
    add(mcp, trust_hints=True)
    approved(mcp)
    echo = mcp.registry.get_tool("mcp__fake__echo")                     # readOnlyHint anunciado
    assert echo.risk_level.value.upper() == "LOW" and echo.read_only and not echo.requires_confirmation and echo.auto_confirm
    note = mcp.registry.get_tool("mcp__fake__write_note")               # sin pistas: se asume que destruye
    assert note.risk_level.value.upper() == "HIGH" and note.requires_confirmation and not note.auto_confirm


def test_operator_overrides_win_over_what_the_server_says(mcp):
    add(mcp, trust_hints=True, tools={"echo": ToolOverride(risk="CRITICAL"), "boom": ToolOverride(disabled=True)})
    approved(mcp)
    assert mcp.registry.get_tool("mcp__fake__echo").risk_level.value.upper() == "CRITICAL"
    assert mcp.registry.get_tool("mcp__fake__echo").requires_confirmation       # riesgo alto fuerza confirmación
    assert mcp.registry.get_tool("mcp__fake__boom") is None and "mcp__fake__boom" not in mcp.executor.handlers

    mcp.store.update("fake", tools={"echo": ToolOverride(risk="LOW", read_only=True, confirm=False)})
    mcp.reconfigure("fake")
    spec = mcp.registry.get_tool("mcp__fake__echo")
    assert spec.risk_level.value.upper() == "LOW" and not spec.requires_confirmation and mcp.registry.get_tool("mcp__fake__boom") is not None


# ---------------------------------------------------------------- ejecución

def test_tool_calls_return_marked_external_data_and_report_errors(mcp):
    add(mcp)
    approved(mcp)
    out = mcp.executor.handlers["mcp__fake__echo"]({"text": "hola", "times": 2})
    assert "holahola" in out and "NO instrucciones" in out and "«fake»" in out

    with pytest.raises(McpError, match="explotó a propósito"):
        mcp.executor.handlers["mcp__fake__boom"]({})
    injected = mcp.executor.handlers["mcp__fake__inject"]({})
    assert injected.startswith("[Datos externos")                       # el texto hostil llega marcado como dato, no como orden


def test_arguments_are_validated_against_the_tool_schema(mcp):
    add(mcp)
    approved(mcp)
    echo = mcp.executor.handlers["mcp__fake__echo"]
    with pytest.raises(ValueError, match="text"):
        echo({})
    with pytest.raises(ValueError, match="times"):
        echo({"text": "x", "times": "tres"})


def test_the_server_only_receives_the_environment_it_was_given(mcp, monkeypatch):
    monkeypatch.setenv("PRAXEON_TEST_SECRET", "no-debe-llegar")
    monkeypatch.setenv("PRAXEON_TEST_SHARED", "si-debe-llegar")
    add(mcp, env={"DECLARADA": "valor", "COMPARTIDA": "${PRAXEON_TEST_SHARED}"})
    approved(mcp)
    has = mcp.executor.handlers["mcp__fake__env_has"]
    assert "False" in has({"name": "PRAXEON_TEST_SECRET"})               # el entorno de PRAXEON no se hereda
    assert "True" in has({"name": "DECLARADA"}) and "True" in has({"name": "COMPARTIDA"}) and "True" in has({"name": "PATH"})
    assert "valor" not in json.dumps(mcp.describe("fake"))               # la API nunca devuelve valores de env
    assert mcp.describe("fake")["env_keys"] == ["COMPARTIDA", "DECLARADA"]


def test_a_crashed_server_is_restarted_on_the_next_call(mcp):
    add(mcp)
    approved(mcp)
    state = mcp._states["fake"]
    state.client._proc.kill()
    state.client._proc.wait(timeout=5)
    assert "ok" in mcp.executor.handlers["mcp__fake__echo"]({"text": "ok"})
    assert mcp.describe("fake")["status"] == "running"


def test_a_server_that_cannot_start_reports_the_error_without_breaking_anything(mcp):
    mcp.store.save(McpServerConfig(name="roto", command="no-existe-este-comando-mcp", enabled=True))
    mcp.ensure_started()
    info = mcp.describe("roto")
    assert info["status"] == "error" and "no-existe" in info["error"]
    mcp.ensure_started()                                                  # no reintenta en bucle
    assert mcp.describe("roto")["status"] == "error"


# ---------------------------------------------------------------- prompt

def test_prompt_lists_only_allowed_tools_in_a_stable_order(mcp):
    add(mcp)
    approved(mcp)
    section = mcp.prompt_section()
    names = [line.split("(")[0][2:] for line in section.splitlines() if line.startswith("- ")]
    assert names == sorted(names) and "mcp__fake__echo" in names and "datos externos no confiables" in section
    assert "mcp__fake__echo(text: string, times?: integer)" in section

    only_echo = mcp.prompt_section(lambda tool: None if tool == "mcp__fake__echo" else "no permitida")
    assert "mcp__fake__echo" in only_echo and "write_note" not in only_echo


def test_disabling_a_server_removes_its_tools(mcp):
    add(mcp)
    approved(mcp)
    mcp.disable("fake")
    assert mcp.registry.get_tool("mcp__fake__echo") is None and not mcp.executor.handlers and mcp.prompt_section() == ""
    assert mcp.remove("fake") and mcp.store.get("fake") is None


def test_config_validation_and_corrupt_file_tolerance(tmp_path):
    with pytest.raises(ValueError):
        McpServerConfig(name="Mal Nombre", command="x")
    with pytest.raises(ValueError):
        McpServerConfig(name="doble__guion", command="x")
    path = tmp_path / "mcp.json"
    store = McpConfigStore(str(path))
    store.save(McpServerConfig(name="bueno", command="x"))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["servers"]["corrupto"] = {"command": 123, "args": "no-es-lista"}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert [s.name for s in store.list()] == ["bueno"]                    # la entrada corrupta se ignora
    assert os.path.exists(path)


def test_interoperates_with_a_server_built_with_the_official_sdk(mcp):
    pytest.importorskip("mcp")
    sdk_server = str(Path(__file__).with_name("mcp_sdk_server.py"))
    mcp.store.save(McpServerConfig(name="sdk", command=sys.executable, args=[sdk_server], timeout_s=60))
    info = mcp.enable("sdk")
    assert info["status"] == "pending_approval", info
    assert {t["name"] for t in info["tools"]} == {"add", "shout", "fail"}
    mcp.approve("sdk", info["tools_hash"])
    assert "5" in mcp.executor.handlers["mcp__sdk__add"]({"a": 2, "b": 3})
    assert "HOLA HOLA" in mcp.executor.handlers["mcp__sdk__shout"]({"text": "hola", "times": 2})
    with pytest.raises(ValueError, match="a"):
        mcp.executor.handlers["mcp__sdk__add"]({"a": "dos", "b": 3})          # validado contra el esquema generado por el SDK
    with pytest.raises(McpError, match="fail"):
        mcp.executor.handlers["mcp__sdk__fail"]({})
