"""Visor y gestión de archivos: codificaciones de texto, imágenes/SVG/PDF y crear, renombrar y eliminar."""

import os

import pytest

from praxeon import workspace_files, workspace_store
from praxeon.workspace_files import WorkspaceExists, WorkspaceFileNotFound, WorkspacePathError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "carpeta").mkdir()
    monkeypatch.setattr(workspace_store, "resolve_default_workspace", lambda: (str(root), "selected"))
    return root


# ---- lectura: .txt de Windows, imágenes, SVG, PDF -----------------------------------------------------------------

def test_cp1252_txt_is_readable_and_saved_back_in_the_same_encoding(ws):
    (ws / "nota.txt").write_bytes("canción — año\r\n".encode("cp1252"))
    info = workspace_files.read_file("nota.txt")
    assert info["kind"] == "text" and info["content"] == "canción — año\r\n" and info["encoding"] == "cp1252"
    res = workspace_files.write_file("nota.txt", info["content"] + "ñ", info["etag"], info["encoding"])
    assert res["encoding"] == "cp1252"
    assert (ws / "nota.txt").read_bytes() == "canción — año\r\nñ".encode("cp1252")


def test_utf16_with_bom_txt_is_readable(ws):
    (ws / "u16.txt").write_bytes("hola ñ".encode("utf-16"))
    info = workspace_files.read_file("u16.txt")
    assert info["kind"] == "text" and info["content"] == "hola ñ" and info["encoding"] == "utf-16"
    workspace_files.write_file("u16.txt", "adiós", info["etag"], "utf-16")
    assert (ws / "u16.txt").read_bytes() == "adiós".encode("utf-16")


def test_utf8_bom_is_preserved(ws):
    (ws / "b.txt").write_bytes(b"\xef\xbb\xbfhola")
    info = workspace_files.read_file("b.txt")
    assert info["content"] == "hola" and info["encoding"] == "utf-8-sig"
    workspace_files.write_file("b.txt", "hola!", info["etag"], "utf-8-sig")
    assert (ws / "b.txt").read_bytes() == b"\xef\xbb\xbfhola!"


def test_text_that_no_longer_fits_the_original_encoding_falls_back_to_utf8(ws):
    (ws / "c.txt").write_bytes("año".encode("cp1252"))
    info = workspace_files.read_file("c.txt")
    res = workspace_files.write_file("c.txt", info["content"] + " ✓", info["etag"], "cp1252")
    assert res["encoding"] == "utf-8" and (ws / "c.txt").read_bytes() == "año ✓".encode("utf-8")


def test_empty_txt_is_text(ws):
    (ws / "vacio.txt").write_bytes(b"")
    info = workspace_files.read_file("vacio.txt")
    assert info["kind"] == "text" and info["content"] == ""


def test_images_report_kind_image_and_serve_raw_bytes(ws):
    (ws / "foto.png").write_bytes(PNG)
    info = workspace_files.read_file("foto.png")
    assert info["kind"] == "image" and info["content"] is None and info["mime"] == "image/png"
    path, mime = workspace_files.read_raw("foto.png")
    assert mime == "image/png" and path.read_bytes() == PNG


def test_svg_is_text_so_it_can_be_edited_and_previewed(ws):
    (ws / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    info = workspace_files.read_file("logo.svg")
    assert info["kind"] == "text" and info["mime"] == "image/svg+xml"


def test_real_binary_is_still_binary(ws):
    (ws / "datos.bin").write_bytes(b"\x00\x01\x02\xff" * 100)
    assert workspace_files.read_file("datos.bin")["kind"] == "binary"


# ---- gestión ----------------------------------------------------------------------------------------------------

def test_create_file_and_folder(ws):
    assert workspace_files.create_entry("carpeta/nuevo.txt", "file")["type"] == "file"
    assert (ws / "carpeta" / "nuevo.txt").read_bytes() == b""
    assert workspace_files.create_entry("carpeta/sub", "dir")["rel_path"] == "carpeta/sub"
    assert (ws / "carpeta" / "sub").is_dir()


def test_create_refuses_existing_missing_parent_and_bad_names(ws):
    workspace_files.create_entry("a.txt", "file")
    with pytest.raises(WorkspaceExists):
        workspace_files.create_entry("a.txt", "file")
    with pytest.raises(WorkspaceExists):
        workspace_files.create_entry("carpeta", "dir")
    with pytest.raises(WorkspaceFileNotFound):
        workspace_files.create_entry("no/existe/x.txt", "file")
    for bad in ("../fuera.txt", "carpeta/../../fuera.txt", "/abs.txt", "C:/abs.txt", "", "a?b.txt", "con:dos.txt"):
        with pytest.raises(WorkspacePathError):
            workspace_files.create_entry(bad, "file")
    with pytest.raises(WorkspacePathError):
        workspace_files.create_entry("x", "otro")
    assert not (ws.parent / "fuera.txt").exists()


def test_delete_file_and_folder_with_contents(ws):
    workspace_files.create_entry("carpeta/a.txt", "file")
    workspace_files.create_entry("carpeta/sub", "dir")
    workspace_files.create_entry("carpeta/sub/b.txt", "file")
    assert workspace_files.delete_entry("carpeta/a.txt") == {"rel_path": "carpeta/a.txt", "type": "file", "removed": 1}
    res = workspace_files.delete_entry("carpeta")
    assert res["type"] == "dir" and res["removed"] == 3 and not (ws / "carpeta").exists()
    with pytest.raises(WorkspaceFileNotFound):
        workspace_files.delete_entry("carpeta")


def test_delete_cannot_remove_the_root_or_leave_the_workspace(ws, tmp_path):
    outside = tmp_path / "fuera.txt"
    outside.write_text("no me borres", encoding="utf-8")
    for bad in ("", ".", "..", "../fuera.txt", "carpeta/../../fuera.txt"):
        with pytest.raises(WorkspacePathError):
            workspace_files.delete_entry(bad)
    assert outside.exists() and ws.exists()


def test_delete_never_removes_the_dot_git_folder(ws):
    (ws / ".git").mkdir()
    with pytest.raises(WorkspacePathError):
        workspace_files.delete_entry(".git")
    assert (ws / ".git").exists()


def test_deleting_a_symlink_removes_the_link_not_its_target(ws, tmp_path):
    target = ws / "carpeta" / "real.txt"
    target.write_text("contenido", encoding="utf-8")
    link = ws / "enlace.txt"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("sin permisos para crear enlaces simbólicos")
    workspace_files.delete_entry("enlace.txt")
    assert not os.path.lexists(link) and target.read_text(encoding="utf-8") == "contenido"


def test_rename_and_move(ws):
    workspace_files.create_entry("a.txt", "file")
    assert workspace_files.rename_entry("a.txt", "b.txt")["rel_path"] == "b.txt"
    workspace_files.rename_entry("b.txt", "carpeta/c.txt")
    assert (ws / "carpeta" / "c.txt").exists() and not (ws / "b.txt").exists()
    workspace_files.create_entry("otro.txt", "file")
    with pytest.raises(WorkspaceExists):
        workspace_files.rename_entry("otro.txt", "carpeta/c.txt")
    with pytest.raises(WorkspacePathError):
        workspace_files.rename_entry("carpeta", "carpeta/dentro")
    with pytest.raises(WorkspacePathError):
        workspace_files.rename_entry("otro.txt", "../fuera.txt")


# ---- rutas HTTP -------------------------------------------------------------------------------------------------

def test_http_create_rename_delete_and_conflicts(ws, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from praxeon.server import dependencies as deps
    from praxeon.server.app import create_app

    monkeypatch.setenv("PRAXEON_CACHE_DIR", str(tmp_path / "cache"))
    app = create_app()
    app.dependency_overrides[deps.get_current_tenant] = lambda: deps.TenantContext(
        tenant_id="t", user_id="u", role=deps.Role.ADMIN, permissions=deps.ROLE_PERMISSIONS[deps.Role.ADMIN])
    c = TestClient(app)
    assert c.post("/v1/workspace/entry", json={"path": "x.txt", "kind": "file"}).status_code == 201
    assert c.post("/v1/workspace/entry", json={"path": "x.txt", "kind": "file"}).status_code == 409
    assert c.post("/v1/workspace/entry", json={"path": "../x.txt", "kind": "file"}).status_code == 400
    assert c.post("/v1/workspace/entry", json={"path": "y", "kind": "raro"}).status_code == 422
    assert c.patch("/v1/workspace/entry", json={"path": "x.txt", "new_path": "z.txt"}).json()["data"]["rel_path"] == "z.txt"
    assert c.delete("/v1/workspace/entry", params={"path": "z.txt"}).json()["data"]["removed"] == 1
    assert c.delete("/v1/workspace/entry", params={"path": "z.txt"}).status_code == 404
    assert c.delete("/v1/workspace/entry", params={"path": ""}).status_code in (400, 422)
