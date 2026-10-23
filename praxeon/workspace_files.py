"""Acceso a archivos del workspace activo: árbol (FE-001) y visor/editor (FE-003).

Todas las rutas son relativas a la raíz del workspace y se confinan con `resolve_inside`:
se rechazan `..`, rutas absolutas y symlinks que apunten fuera de la raíz.

El editor lo usa un humano desde la UI; no pasa por PolicyEngine (decisión del responsable).
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from praxeon import workspace_store

MAX_TREE_ENTRIES = 1000
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_BINARY_BYTES = 25 * 1024 * 1024
IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", ".mypy_cache", ".pytest_cache"}


class WorkspacePathError(ValueError):
    """Ruta fuera del workspace o inválida (HTTP 400/403)."""


class WorkspaceFileNotFound(WorkspacePathError):
    """La ruta no existe (HTTP 404)."""


class WorkspaceConflict(Exception):
    """El archivo cambió desde que se leyó (HTTP 409)."""


class WorkspaceTooLarge(WorkspacePathError):
    """Archivo por encima del límite (HTTP 413)."""


def workspace_root() -> Path:
    path, _ = workspace_store.resolve_default_workspace()
    root = Path(path).resolve()
    if not root.is_dir():
        raise WorkspacePathError("El workspace activo no existe.")
    return root


def resolve_inside(rel_path: Optional[str], root: Optional[Path] = None) -> Path:
    """Resuelve `rel_path` dentro de la raíz o lanza WorkspacePathError."""
    root = root or workspace_root()
    rel = (rel_path or "").replace("\\", "/").strip("/")
    if rel and (os.path.isabs(rel_path or "") or Path(rel).is_absolute() or ":" in rel.split("/")[0]):
        raise WorkspacePathError("Ruta absoluta no permitida.")
    if "\x00" in rel:
        raise WorkspacePathError("Ruta inválida.")
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise WorkspacePathError("La ruta sale del workspace.") from exc
    return target


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def list_tree(rel_path: Optional[str] = None, show_hidden: bool = False) -> Dict[str, Any]:
    """Contenido de una carpeta (un nivel, carga perezosa); carpetas primero."""
    root = workspace_root()
    target = resolve_inside(rel_path, root)
    if not target.exists():
        raise WorkspaceFileNotFound("La carpeta no existe.")
    if not target.is_dir():
        raise WorkspacePathError("La ruta no es una carpeta.")
    entries: List[Dict[str, Any]] = []
    truncated = False
    try:
        with os.scandir(target) as it:
            for entry in it:
                name = entry.name
                if not show_hidden and (name.startswith(".") or name in IGNORED_DIRS):
                    continue
                if name in IGNORED_DIRS:
                    continue
                try:
                    real = Path(entry.path).resolve()
                    real.relative_to(root)  # symlinks hacia fuera se omiten
                    is_dir = entry.is_dir()
                    st = entry.stat()
                except (OSError, ValueError):
                    continue
                if len(entries) >= MAX_TREE_ENTRIES:
                    truncated = True
                    break
                entries.append(
                    {
                        "name": name,
                        "rel_path": _rel(real, root),
                        "type": "dir" if is_dir else "file",
                        "size": None if is_dir else st.st_size,
                        "mtime": st.st_mtime,
                    }
                )
    except PermissionError as exc:
        raise WorkspacePathError("No hay permiso para leer esta carpeta.") from exc
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return {
        "root": str(root),
        "path": "" if target == root else _rel(target, root),
        "entries": entries,
        "truncated": truncated,
    }


def _etag(data: bytes, mtime_ns: int) -> str:
    return hashlib.sha1(data).hexdigest()[:16] + f"-{mtime_ns}"


def _is_binary(sample: bytes) -> bool:
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        # un corte en mitad de un carácter multibyte no cuenta como binario
        return exc.start < len(sample) - 4
    return False


def read_file(rel_path: str) -> Dict[str, Any]:
    """Metadatos y contenido (texto) de un archivo. Los binarios devuelven `kind` sin contenido."""
    root = workspace_root()
    target = resolve_inside(rel_path, root)
    if not target.exists():
        raise WorkspaceFileNotFound("El archivo no existe.")
    if not target.is_file():
        raise WorkspacePathError("La ruta no es un archivo.")
    st = target.stat()
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    info: Dict[str, Any] = {
        "rel_path": _rel(target, root),
        "name": target.name,
        "size": st.st_size,
        "mtime": st.st_mtime,
        "mime": mime,
    }
    with open(target, "rb") as fh:
        head = fh.read(8192)
    if mime == "application/pdf" or _is_binary(head):
        info.update(kind="pdf" if mime == "application/pdf" else "binary", content=None, etag=None)
        return info
    if st.st_size > MAX_TEXT_BYTES:
        raise WorkspaceTooLarge(f"El archivo supera el límite de {MAX_TEXT_BYTES // (1024 * 1024)} MB para edición.")
    data = target.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        info.update(kind="binary", content=None, etag=None)
        return info
    info.update(kind="text", content=text, etag=_etag(data, st.st_mtime_ns))
    return info


def read_raw(rel_path: str) -> tuple[Path, str]:
    """Ruta validada y MIME para servir bytes (previsualización de PDF/imágenes)."""
    target = resolve_inside(rel_path)
    if not target.is_file():
        raise WorkspaceFileNotFound("El archivo no existe.")
    if target.stat().st_size > MAX_BINARY_BYTES:
        raise WorkspaceTooLarge("El archivo es demasiado grande para previsualizar.")
    return target, mimetypes.guess_type(target.name)[0] or "application/octet-stream"


def write_file(rel_path: str, content: str, expected_etag: Optional[str] = None) -> Dict[str, Any]:
    """Guarda de forma atómica; WorkspaceConflict si el etag esperado ya no coincide."""
    root = workspace_root()
    target = resolve_inside(rel_path, root)
    if target.exists() and not target.is_file():
        raise WorkspacePathError("La ruta no es un archivo.")
    payload = content.encode("utf-8")
    if len(payload) > MAX_TEXT_BYTES:
        raise WorkspaceTooLarge("El contenido supera el límite de tamaño.")
    if target.exists() and expected_etag is not None:
        current = target.read_bytes()
        if _etag(current, target.stat().st_mtime_ns) != expected_etag:
            raise WorkspaceConflict("El archivo cambió en disco desde que se abrió.")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".praxeon-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    st = target.stat()
    return {
        "rel_path": _rel(target, root),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "etag": _etag(payload, st.st_mtime_ns),
    }
