"""Selección del workspace: elegir carpeta, recordarla, explorar y que las misiones la usen."""

import os

import pytest
from fastapi.testclient import TestClient

from praxeon import workspace_store
from praxeon.runtime.sandbox import get_default_workspace_root
from praxeon.server.app import create_app
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    monkeypatch.delenv("PRAXEON_DEFAULT_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("PRAXEON_WORKSPACE_ROOT", raising=False)
    path = workspace_store._store_path()
    path.unlink(missing_ok=True)
    yield
    path.unlink(missing_ok=True)


@pytest.fixture
def client():
    set_runtime_service(RuntimeApplicationService())
    return TestClient(create_app(profile="dev"))


def test_without_selection_falls_back_to_auto_detection():
    info = workspace_store.describe_workspace()
    assert info["source"] == workspace_store.SOURCE_AUTO
    assert info["path"] == get_default_workspace_root()


def test_env_variable_is_used_when_nothing_selected(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXEON_WORKSPACE_ROOT", str(tmp_path))
    info = workspace_store.describe_workspace()
    assert (info["path"], info["source"]) == (str(tmp_path), workspace_store.SOURCE_ENV)


def test_selection_wins_over_env_and_is_what_missions_use(tmp_path, monkeypatch):
    env_dir = tmp_path / "env"
    chosen = tmp_path / "proyecto"
    env_dir.mkdir()
    chosen.mkdir()
    monkeypatch.setenv("PRAXEON_WORKSPACE_ROOT", str(env_dir))
    workspace_store.select_workspace(str(chosen))
    assert get_default_workspace_root() == str(chosen)
    from praxeon.server.coordinators import get_default_workspace_root as coord_root
    from praxeon.core.session_context import get_default_workspace_root as ctx_root

    assert coord_root() == ctx_root() == str(chosen)


def test_selection_is_persisted_and_recents_are_ordered(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    workspace_store.select_workspace(str(a))
    workspace_store.select_workspace(str(b))
    workspace_store.select_workspace(str(a))  # vuelve a subir, sin duplicarse
    assert [r["name"] for r in workspace_store.list_recent()] == ["a", "b"]
    assert workspace_store.get_selected_workspace() == str(a)


def test_deleted_folder_stops_being_the_default(tmp_path):
    gone = tmp_path / "gone"
    gone.mkdir()
    workspace_store.select_workspace(str(gone))
    gone.rmdir()
    assert workspace_store.get_selected_workspace() is None
    assert workspace_store.describe_workspace()["source"] == workspace_store.SOURCE_AUTO
    assert workspace_store.list_recent()[0]["exists"] is False


@pytest.mark.parametrize("make", ["missing", "file"])
def test_invalid_paths_are_rejected_with_a_reason(tmp_path, make):
    target = tmp_path / "x.txt"
    if make == "file":
        target.write_text("hola")
    with pytest.raises(ValueError):
        workspace_store.select_workspace(str(target))
    assert workspace_store.get_selected_workspace() is None


def test_api_flow_select_validate_browse_forget(client, tmp_path):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text("{}")
    (tmp_path / ".oculta").mkdir()
    (tmp_path / "nota.txt").write_text("x")

    ok = client.put("/v1/workspace", json={"path": str(tmp_path)})
    assert ok.status_code == 200
    data = ok.json()["data"]
    assert data["path"] == str(tmp_path) and data["source"] == "selected"
    assert client.get("/v1/workspace").json()["data"]["path"] == str(tmp_path)

    bad = client.put("/v1/workspace", json={"path": str(tmp_path / "no_existe")})
    assert bad.status_code == 400 and "no existe" in bad.json()["detail"]

    v = client.get("/v1/workspace/validate", params={"path": str(tmp_path / "nota.txt")}).json()["data"]
    assert v["valid"] is False and "archivo" in v["reason"]

    listing = client.get("/v1/workspace/browse", params={"path": str(tmp_path)}).json()["data"]
    names = [e["name"] for e in listing["entries"]]
    assert names == ["web"]  # sin archivos ni carpetas ocultas
    assert listing["entries"][0]["hints"] == ["package.json"]
    assert listing["parent"] == os.path.dirname(str(tmp_path))
    hidden = client.get("/v1/workspace/browse", params={"path": str(tmp_path), "show_hidden": True}).json()["data"]
    assert ".oculta" in [e["name"] for e in hidden["entries"]]

    assert client.post("/v1/workspace/forget", json={"path": str(tmp_path)}).json()["data"]["recent"] == []
    cleared = client.delete("/v1/workspace").json()["data"]
    assert cleared["source"] != "selected"


def test_new_mission_without_explicit_workspace_uses_the_selected_one(client, tmp_path):
    client.put("/v1/workspace", json={"path": str(tmp_path)})
    sid = client.post(
        "/v1/sessions/run",
        json={"goal": "g", "llm_provider": "simulator", "max_steps": 1, "step_delay_ms": 200, "session_id": "ws-default"},
    ).json()["data"]["session_id"]
    from praxeon.server.dependencies import get_runtime_service

    assert get_runtime_service()._sessions_meta[sid]["metadata"]["workspace_root"] == str(tmp_path)
