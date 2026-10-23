"""FE-001 / FE-003: árbol, lectura y escritura de archivos confinados al workspace."""

import os

import pytest

from praxeon import workspace_files, workspace_store
from praxeon.workspace_files import (
    WorkspaceConflict,
    WorkspaceFileNotFound,
    WorkspacePathError,
    WorkspaceTooLarge,
)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "main.py").write_bytes(b"print('hola')\n")   # bytes: write_text traduciría \n a \r\n en Windows
    (root / "README.md").write_text("# Titulo\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / "node_modules").mkdir()
    (root / ".env").write_text("X=1", encoding="utf-8")
    monkeypatch.setattr(workspace_store, "resolve_default_workspace", lambda: (str(root), "selected"))
    return root


def test_tree_lists_folders_first_and_hides_ignored(ws):
    data = workspace_files.list_tree("")
    names = [e["name"] for e in data["entries"]]
    assert names == ["src", "README.md"]
    assert data["entries"][0]["type"] == "dir"
    assert data["truncated"] is False


def test_tree_show_hidden_keeps_ignored_dirs_out(ws):
    names = [e["name"] for e in workspace_files.list_tree("", show_hidden=True)["entries"]]
    assert ".env" in names
    assert ".git" not in names and "node_modules" not in names


def test_tree_subfolder_returns_rel_paths(ws):
    data = workspace_files.list_tree("src")
    assert data["path"] == "src"
    assert data["entries"][0]["rel_path"] == "src/main.py"


def test_tree_truncates(ws, monkeypatch):
    monkeypatch.setattr(workspace_files, "MAX_TREE_ENTRIES", 3)
    for i in range(10):
        (ws / f"f{i}.txt").write_text("x", encoding="utf-8")
    data = workspace_files.list_tree("")
    assert len(data["entries"]) == 3
    assert data["truncated"] is True


@pytest.mark.parametrize("bad", ["..", "../x", "src/../../x", "..\\x"])
def test_traversal_rejected(ws, bad):
    with pytest.raises(WorkspacePathError):
        workspace_files.list_tree(bad)
    with pytest.raises(WorkspacePathError):
        workspace_files.read_file(bad)
    with pytest.raises(WorkspacePathError):
        workspace_files.write_file(bad, "x")


def test_absolute_path_rejected(ws, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secreto", encoding="utf-8")
    with pytest.raises(WorkspacePathError):
        workspace_files.read_file(str(outside))


def test_symlink_outside_is_rejected_and_hidden(ws, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secreto", encoding="utf-8")
    link = ws / "link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("No se pueden crear symlinks en este entorno")
    with pytest.raises(WorkspacePathError):
        workspace_files.read_file("link.txt")
    assert "link.txt" not in [e["name"] for e in workspace_files.list_tree("")["entries"]]


def test_read_text_returns_content_and_etag(ws):
    info = workspace_files.read_file("src/main.py")
    assert info["kind"] == "text"
    assert info["content"] == "print('hola')\n"
    assert info["etag"]


def test_read_missing_raises_not_found(ws):
    with pytest.raises(WorkspaceFileNotFound):
        workspace_files.read_file("nada.txt")


def test_binary_is_not_loaded_as_text(ws):
    (ws / "blob.bin").write_bytes(b"\x00\x01\x02\xff")
    info = workspace_files.read_file("blob.bin")
    assert info["kind"] == "binary" and info["content"] is None


def test_pdf_reports_kind_without_content(ws):
    (ws / "doc.pdf").write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    info = workspace_files.read_file("doc.pdf")
    assert info["kind"] == "pdf" and info["content"] is None


def test_large_text_is_rejected(ws, monkeypatch):
    monkeypatch.setattr(workspace_files, "MAX_TEXT_BYTES", 10)
    (ws / "big.txt").write_text("a" * 100, encoding="utf-8")
    with pytest.raises(WorkspaceTooLarge):
        workspace_files.read_file("big.txt")


def test_write_persists_and_updates_etag(ws):
    first = workspace_files.read_file("README.md")
    saved = workspace_files.write_file("README.md", "# Nuevo\n", first["etag"])
    assert (ws / "README.md").read_text(encoding="utf-8") == "# Nuevo\n"
    assert saved["etag"] != first["etag"]
    assert not [p for p in ws.iterdir() if p.suffix == ".tmp"]


def test_write_conflict_when_file_changed(ws):
    first = workspace_files.read_file("README.md")
    (ws / "README.md").write_text("cambio externo", encoding="utf-8")
    with pytest.raises(WorkspaceConflict):
        workspace_files.write_file("README.md", "mi version", first["etag"])
    assert (ws / "README.md").read_text(encoding="utf-8") == "cambio externo"


def test_write_creates_new_file(ws):
    workspace_files.write_file("src/nuevo.py", "x = 1\n")
    assert (ws / "src" / "nuevo.py").read_text(encoding="utf-8") == "x = 1\n"
