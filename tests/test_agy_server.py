"""FE-015 de extremo a extremo con la API: activar herramientas de agy exige confirmación en el SERVIDOR, queda en el log y las imágenes llegan al workspace."""

from __future__ import annotations

import io
import json
import sys
import time

import pytest

from praxeon import workspace_store
from tests.test_agy_tools import FAKE

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from praxeon.server.app import create_app
    from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service

    for key in ("PRAXEON_API_KEY", "PRAXEON_SECRET_KEY", "PRAXEON_PROFILE", "PRAXEON_ENV", "PRAXEON_REQUIRE_AUTH"):
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(workspace_store, "resolve_default_workspace", lambda: (str(root), "selected"))
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    yield TestClient(create_app(profile="dev")), service, root
    for sid in list(service._running_missions):
        service.stop_mission(sid)
    deadline = time.time() + 15
    while service.active_mission_count() and time.time() < deadline:
        time.sleep(0.2)
    set_runtime_service(None)


@pytest.fixture
def fake_agy(tmp_path, monkeypatch):
    base = tmp_path / "bin con espacios"
    base.mkdir()
    script = base / "fake_agy.py"
    script.write_text(FAKE, encoding="utf-8")
    if sys.platform == "win32":
        exe = base / "agy.cmd"
        exe.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = base / "agy"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    home = base / "agy home"
    (home / "brain").mkdir(parents=True)
    monkeypatch.setenv("PRAXEON_AGY_HOME", str(home))
    monkeypatch.setenv("FAKE_AGY_LOG", str(base / "log.jsonl"))
    monkeypatch.setattr("praxeon.agent_llm.shutil.which", lambda name: str(exe) if name == "agy" else None)
    return base


def photo_with_exif(path):
    exif = Image.Exif()
    exif[0x010F] = "MarcaSecretaDeCamara"
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 20, 20)).save(buf, "JPEG", exif=exif.tobytes())
    path.write_bytes(buf.getvalue())
    return path


def run(client, root, **extra):
    body = {"goal": "dibuja un círculo rojo", "execution_mode": "local_restricted", "max_steps": 4, "step_delay_ms": 200, "workspace_root": str(root), "llm_provider": "agy", **extra}
    return client.post("/v1/sessions/run", json=body)


def wait_for(client, sid, event_type, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = client.get(f"/v1/sessions/{sid}/events", params={"limit": 1000}).json()["data"]["events"]
        if any(e["type"] == event_type for e in events):
            return events
        time.sleep(0.3)
    raise AssertionError(f"no llegó {event_type}; eventos: {[e['type'] for e in events]}")


def test_the_catalog_endpoint_lists_the_groups_for_the_interface(api):
    client, _, _ = api
    data = client.get("/v1/providers/agy/tools").json()["data"]
    assert {g["id"] for g in data["groups"]} == {"images", "web_search"} and data["images_available"] is True
    assert all(g["risk"] and g["warning"] and g["title"] for g in data["groups"])


def test_activating_tools_without_confirmation_is_rejected_by_the_server(api, fake_agy):
    client, service, root = api
    res = run(client, root, agy_tools=["web_search"])
    assert res.status_code == 400 and "confirmación explícita" in res.json()["detail"]
    res = run(client, root, agy_tools=["web_search"], agy_tools_ack=[])
    assert res.status_code == 400
    res = run(client, root, agy_tools=["images", "web_search"], agy_tools_ack=["images"])      # confirmar uno no confirma el otro
    assert res.status_code == 400 and "Búsqueda web" in res.json()["detail"]
    assert service.active_mission_count() == 0


def test_unknown_groups_and_other_providers_are_rejected(api, fake_agy):
    client, service, root = api
    for bad in (["run_command"], ["web_search", "web_search"]):
        res = run(client, root, agy_tools=bad, agy_tools_ack=bad)
        assert res.status_code == 400, bad
    res = run(client, root, llm_provider="groq", agy_tools=["web_search"], agy_tools_ack=["web_search"])
    assert res.status_code == 400 and "solo se pueden activar con el proveedor agy" in res.json()["detail"]
    res = run(client, root, llm_provider="simulator", agy_tools=["web_search"], agy_tools_ack=["web_search"])
    assert res.status_code == 400
    assert service.active_mission_count() == 0


def test_images_need_the_active_workspace_and_pillow(api, fake_agy, monkeypatch, tmp_path):
    client, _, root = api
    other = tmp_path / "otro"
    other.mkdir()
    res = run(client, other, agy_tools=["images"], agy_tools_ack=["images"])
    assert res.status_code == 400 and "workspace activo" in res.json()["detail"]
    from praxeon import vision

    monkeypatch.setattr(vision, "available", lambda: False)
    res = run(client, root, agy_tools=["images"], agy_tools_ack=["images"])
    assert res.status_code == 400 and "Pillow" in res.json()["detail"]


def test_a_mission_with_images_logs_the_activation_collects_the_picture_and_blocks_the_rest(api, fake_agy, monkeypatch):
    client, service, root = api
    src = photo_with_exif(fake_agy / "origen.jpg")
    assert b"MarcaSecretaDeCamara" in src.read_bytes()
    monkeypatch.setenv("FAKE_AGY_IMAGE", str(src))
    monkeypatch.setenv("FAKE_AGY_CALLS", json.dumps([
        {"name": "invoke_subagent", "args": {"TypeName": "image-generator", "Workspace": "inherit", "Prompt": "dibuja"}},
        {"name": "generate_image", "args": {"ImageName": "circulo", "Prompt": "un circulo rojo"}},
        {"name": "run_command", "args": {"CommandLine": "echo hecho > cmd.txt"}},
        {"name": "write_file", "args": {}},
    ]))
    monkeypatch.setenv("FAKE_AGY_RESPONSES", json.dumps([
        'Thought: miro la carpeta\nAction: list_dir(".")',
        'Thought: listo\nAction: finish("He generado el círculo rojo")',
    ]))
    res = run(client, root, agy_tools=["images"], agy_tools_ack=["images"], session_id="agyimg")
    assert res.status_code == 201, res.text
    events = wait_for(client, "agyimg", "session.completed")
    by_type = {}
    for e in events:
        by_type.setdefault(e["type"], []).append(e["payload"])

    enabled = by_type["agy.tools_enabled"][0]                                       # la activación queda anotada, con su nivel de riesgo
    assert enabled["groups"] == ["images"] and enabled["confirmed"] is True and enabled["risks"] == {"images": "medio"}
    assert "sin pasar por la política" in enabled["message"]

    used = by_type["agy.tool_used"][0]
    assert [t["name"] for t in used["tools"]] == ["invoke_subagent", "generate_image"] and used["denied"] == 2   # run_command y write_file, bloqueadas

    (img,) = by_type["agy.image_generated"][:1]
    assert img["rel_path"].startswith(".praxeon/generated/agyimg/") and (img["width"], img["height"]) == (64, 48)
    saved = (root / img["rel_path"]).read_bytes()
    assert b"MarcaSecretaDeCamara" not in saved and b"Exif" not in saved                 # en el workspace solo hay píxeles
    assert not (root / "cmd.txt").exists()                                              # y lo que no se activó no ocurrió
    assert client.get("/v1/workspace/file/raw", params={"path": img["rel_path"]}).status_code == 200   # la interfaz puede mostrarla
    assert "base64" not in json.dumps(events)


def test_a_mission_without_tools_behaves_like_fe_014(api, fake_agy, monkeypatch):
    client, _, root = api
    monkeypatch.setenv("FAKE_AGY_CALLS", json.dumps([{"name": "search_web", "args": {"query": "x"}}]))
    monkeypatch.setenv("FAKE_AGY_RESPONSES", json.dumps(['Thought: miro\nAction: list_dir(".")', 'Thought: listo\nAction: finish("hecho")']))
    res = run(client, root, session_id="agynone")
    assert res.status_code == 201, res.text
    events = wait_for(client, "agynone", "session.completed")
    types = {e["type"] for e in events}
    assert not types & {"agy.tools_enabled", "agy.tool_used", "agy.image_generated"}
    assert not (root / ".praxeon" / "generated").exists()
