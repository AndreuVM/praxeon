"""FE-002: varios workspaces con acceso rápido: favoritos con orden fijo (Alt+1…9), alias y alternar con el anterior."""

import pytest
from fastapi.testclient import TestClient

from praxeon import workspace_store as store
from praxeon.server.app import create_app
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    monkeypatch.delenv("PRAXEON_DEFAULT_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("PRAXEON_WORKSPACE_ROOT", raising=False)
    path = store._store_path()
    path.unlink(missing_ok=True)
    yield
    path.unlink(missing_ok=True)


@pytest.fixture
def dirs(tmp_path):
    made = []
    for name in ("alfa", "beta", "gamma", "delta"):
        d = tmp_path / name
        d.mkdir()
        made.append(str(d))
    return made


def names():
    return [(w["name"], w["pinned"], w["slot"]) for w in store.list_recent()]


def test_pinned_workspaces_keep_a_fixed_order_with_slots_even_when_switching(dirs):
    a, b, c, d = dirs
    for p in (a, b, c, d):
        store.select_workspace(p)
    store.pin_workspace(c)
    store.pin_workspace(a)
    assert names()[:2] == [("gamma", True, 1), ("alfa", True, 2)]
    store.select_workspace(a)                       # usar un favorito no lo mueve
    store.select_workspace(b)
    store.select_workspace(c)
    assert names()[:2] == [("gamma", True, 1), ("alfa", True, 2)]
    assert [n for n, pinned, _ in names() if not pinned] == ["beta", "delta"]     # recientes por último uso, sin slot
    assert all(slot is None for _, pinned, slot in names() if not pinned)


def test_pinning_an_unknown_folder_adds_it_without_selecting_it(dirs):
    a, b = dirs[:2]
    store.select_workspace(a)
    store.pin_workspace(b)
    assert store.get_selected_workspace() == a
    assert ("beta", True, 1) in names()


def test_unpinning_and_forgetting(dirs):
    a, b = dirs[:2]
    store.pin_workspace(a)
    store.pin_workspace(b)
    store.pin_workspace(a, False)
    assert names() == [("beta", True, 1), ("alfa", False, None)]
    store.forget_recent(b)
    assert [n for n, _, _ in names()] == ["alfa"]


def test_at_most_nine_favorites_and_invalid_folders_are_refused(tmp_path):
    folders = []
    for i in range(10):
        d = tmp_path / f"w{i}"
        d.mkdir()
        folders.append(str(d))
    for p in folders[:9]:
        store.pin_workspace(p)
    with pytest.raises(ValueError, match="9 favoritos"):
        store.pin_workspace(folders[9])
    with pytest.raises(ValueError):
        store.pin_workspace(str(tmp_path / "no-existe"))
    store.pin_workspace(folders[0])                 # volver a fijar uno que ya lo está no cuenta de nuevo
    assert len([w for w in store.list_recent() if w["pinned"]]) == 9


def test_recents_are_trimmed_but_favorites_never_are(tmp_path):
    folders = []
    for i in range(store.MAX_RECENT + 5):
        d = tmp_path / f"r{i}"
        d.mkdir()
        folders.append(str(d))
    store.pin_workspace(folders[0])
    for p in folders:
        store.select_workspace(p)
    listed = store.list_recent()
    assert len([w for w in listed if not w["pinned"]]) == store.MAX_RECENT
    assert listed[0]["pinned"] and listed[0]["path"] == folders[0]


def test_alias_distinguishes_folders_with_the_same_name(tmp_path):
    one, two = tmp_path / "uno" / "app", tmp_path / "dos" / "app"
    one.mkdir(parents=True)
    two.mkdir(parents=True)
    store.select_workspace(str(one))
    store.select_workspace(str(two))
    store.set_alias(str(one), "  web   cliente ")
    listed = {w["path"]: w for w in store.list_recent()}
    assert listed[str(one)]["name"] == "web cliente" and listed[str(one)]["folder"] == "app" and listed[str(two)]["name"] == "app"
    store.select_workspace(str(one))
    assert store.describe_workspace()["name"] == "web cliente"
    store.set_alias(str(one), "")
    assert store.describe_workspace()["name"] == "app"
    with pytest.raises(ValueError):
        store.set_alias(str(tmp_path), "desconocido")
    with pytest.raises(ValueError):
        store.set_alias(str(one), "x" * 41)


def test_previous_workspace_allows_toggling_between_two(dirs):
    a, b, c = dirs[:3]
    assert store.describe_workspace()["previous"] is None
    store.select_workspace(a)
    assert store.describe_workspace()["previous"] is None
    store.select_workspace(b)
    assert store.describe_workspace()["previous"] == a
    store.select_workspace(b)                       # elegir el mismo no cambia el anterior
    assert store.describe_workspace()["previous"] == a
    store.select_workspace(a)
    assert store.describe_workspace()["previous"] == b
    store.select_workspace(c)
    store.forget_recent(a)
    assert store.describe_workspace()["previous"] is None or store.describe_workspace()["previous"] != a


def test_old_store_files_without_the_new_fields_still_load(tmp_path, dirs):
    import json
    store._store_path().parent.mkdir(parents=True, exist_ok=True)
    store._store_path().write_text(json.dumps({"current": dirs[0], "recent": [{"path": dirs[0], "last_used": 1.0}]}), encoding="utf-8")
    info = store.describe_workspace()
    assert info["path"] == dirs[0] and info["previous"] is None
    assert [(w["pinned"], w["slot"]) for w in info["recent"]] == [(False, None)]


def test_http_pin_alias_and_switch(dirs):
    set_runtime_service(RuntimeApplicationService())
    client = TestClient(create_app(profile="dev"))
    a, b = dirs[:2]
    assert client.put("/v1/workspace", json={"path": a}).status_code == 200
    data = client.post("/v1/workspace/pin", json={"path": b}).json()["data"]
    assert [(w["name"], w["slot"]) for w in data["recent"] if w["pinned"]] == [("beta", 1)]
    assert client.post("/v1/workspace/pin", json={"path": "no-existe-xyz"}).status_code == 400
    data = client.post("/v1/workspace/alias", json={"path": b, "alias": "Servidor"}).json()["data"]
    assert next(w for w in data["recent"] if w["path"] == b)["name"] == "Servidor"
    assert client.post("/v1/workspace/alias", json={"path": dirs[3], "alias": "x"}).status_code == 400
    data = client.put("/v1/workspace", json={"path": b}).json()["data"]
    assert data["name"] == "Servidor" and data["previous"] == a
    data = client.post("/v1/workspace/pin", json={"path": b, "pinned": False}).json()["data"]
    assert not any(w["pinned"] for w in data["recent"])
