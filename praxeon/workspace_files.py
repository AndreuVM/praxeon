"""Acceso a archivos del workspace activo: árbol (FE-001) y visor/editor (FE-003).

Todas las rutas son relativas a la raíz del workspace y se confinan con `resolve_inside`:
se rechazan `..`, rutas absolutas y symlinks que apunten fuera de la raíz.

El editor lo usa un humano desde la UI; no pasa por PolicyEngine (decisión del responsable).
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from praxeon import workspace_store

MAX_TREE_ENTRIES = 1000
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_BINARY_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
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


def _decode_text(data: bytes) -> Optional[tuple]:
    """(texto, codificación) si `data` es texto; None si es binario.

    Reconoce BOM UTF-8/UTF-16 y, como último recurso, Windows-1252 (los .txt de Windows con acentos), para poder leerlos y
    volver a guardarlos con la misma codificación sin estropearlos.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            return None
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        return None


def _media_kind(mime: str, name: str) -> Optional[str]:
    """Tipos que se previsualizan como bytes: pdf o imagen (el SVG es texto: se edita y además se previsualiza)."""
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("image/") and not name.lower().endswith(".svg"):
        return "image"
    return None


def read_file(rel_path: str) -> Dict[str, Any]:
    """Metadatos y contenido (texto) de un archivo. PDF e imágenes devuelven `kind` sin contenido (se ven con /file/raw)."""
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
    media = _media_kind(mime, target.name)
    if media:
        info.update(kind=media, content=None, etag=None)
        return info
    with open(target, "rb") as fh:
        head = fh.read(8192)
    if st.st_size > MAX_TEXT_BYTES:
        if _decode_text(head) is None:
            info.update(kind="binary", content=None, etag=None)
            return info
        raise WorkspaceTooLarge(f"El archivo supera el límite de {MAX_TEXT_BYTES // (1024 * 1024)} MB para edición.")
    data = target.read_bytes()
    decoded = _decode_text(data)
    if decoded is None:
        info.update(kind="binary", content=None, etag=None)
        return info
    text, encoding = decoded
    info.update(kind="text", content=text, etag=_etag(data, st.st_mtime_ns), encoding=encoding)
    return info


def read_raw(rel_path: str) -> tuple[Path, str]:
    """Ruta validada y MIME para servir bytes (previsualización de PDF/imágenes)."""
    target = resolve_inside(rel_path)
    if not target.is_file():
        raise WorkspaceFileNotFound("El archivo no existe.")
    if target.stat().st_size > MAX_BINARY_BYTES:
        raise WorkspaceTooLarge("El archivo es demasiado grande para previsualizar.")
    return target, mimetypes.guess_type(target.name)[0] or "application/octet-stream"


def _encode(content: str, encoding: str) -> tuple:
    """(bytes, codificación usada). Si el texto ya no cabe en la codificación original, se pasa a UTF-8."""
    if encoding not in ("utf-8", "utf-8-sig", "utf-16", "cp1252"):
        encoding = "utf-8"
    try:
        return content.encode(encoding), encoding
    except UnicodeEncodeError:
        return content.encode("utf-8"), "utf-8"


def write_file(rel_path: str, content: str, expected_etag: Optional[str] = None, encoding: str = "utf-8") -> Dict[str, Any]:
    """Guarda de forma atómica; WorkspaceConflict si el etag esperado ya no coincide. Conserva la codificación leída."""
    root = workspace_root()
    target = resolve_inside(rel_path, root)
    if target.exists() and not target.is_file():
        raise WorkspacePathError("La ruta no es un archivo.")
    payload, used = _encode(content, encoding)
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
        "encoding": used,
    }


# --- gestión de archivos y carpetas ----------------------------------------------------------------------------------


class WorkspaceExists(Exception):
    """Ya existe algo con ese nombre (HTTP 409)."""


def _entry_path(rel_path: str, root: Path) -> Path:
    """Ruta de una entrada **sin resolver el último componente**: borrar o renombrar un enlace simbólico actúa sobre el
    enlace, nunca sobre aquello a lo que apunta. La carpeta que la contiene sí se confina al workspace."""
    if (rel_path or "").replace("\\", "/").startswith("/") or os.path.isabs(rel_path or ""):
        raise WorkspacePathError("Ruta absoluta no permitida.")
    rel = (rel_path or "").replace("\\", "/").strip("/")
    name = rel.rsplit("/", 1)[-1]
    if not rel or name in (".", ".."):
        raise WorkspacePathError("Indica un nombre de archivo o carpeta.")
    parent = resolve_inside(rel.rsplit("/", 1)[0] if "/" in rel else "", root)
    if any(c in name for c in '<>:"|?*') or name.endswith((" ", ".")):
        raise WorkspacePathError("El nombre contiene caracteres no válidos.")
    return parent / name


def create_entry(rel_path: str, kind: str) -> Dict[str, Any]:
    """Crea un archivo vacío o una carpeta; WorkspaceExists si ya hay algo con ese nombre."""
    if kind not in ("file", "dir"):
        raise WorkspacePathError("El tipo debe ser «file» o «dir».")
    root = workspace_root()
    target = _entry_path(rel_path, root)
    if os.path.lexists(target):
        raise WorkspaceExists("Ya existe un archivo o carpeta con ese nombre.")
    if not target.parent.is_dir():
        raise WorkspaceFileNotFound("La carpeta de destino no existe.")
    if kind == "dir":
        target.mkdir()
    else:
        with open(target, "xb"):
            pass
    return {"rel_path": _rel(target, root), "type": kind}


def delete_entry(rel_path: str) -> Dict[str, Any]:
    """Elimina un archivo o una carpeta con todo su contenido (o un enlace simbólico, sin tocar su destino)."""
    root = workspace_root()
    target = _entry_path(rel_path, root)
    if not os.path.lexists(target):
        raise WorkspaceFileNotFound("No existe.")
    if target.name == ".git" and target.parent == root:
        raise WorkspacePathError("No se permite eliminar la carpeta .git desde aquí.")
    rel = _rel(target, root)
    if target.is_symlink() or target.is_file():
        target.unlink()
        return {"rel_path": rel, "type": "file", "removed": 1}
    removed = sum(len(files) + len(dirs) for _, dirs, files in os.walk(target)) + 1
    shutil.rmtree(target)
    return {"rel_path": rel, "type": "dir", "removed": removed}


def rename_entry(rel_path: str, new_rel_path: str) -> Dict[str, Any]:
    """Renombra o mueve dentro del workspace; WorkspaceExists si el destino ya existe."""
    root = workspace_root()
    source = _entry_path(rel_path, root)
    dest = _entry_path(new_rel_path, root)
    if not os.path.lexists(source):
        raise WorkspaceFileNotFound("No existe.")
    if os.path.lexists(dest):
        raise WorkspaceExists("Ya existe un archivo o carpeta con ese nombre.")
    if source.is_dir() and not source.is_symlink() and (dest == source or source in dest.parents):
        raise WorkspacePathError("No se puede mover una carpeta dentro de sí misma.")
    if not dest.parent.is_dir():
        raise WorkspaceFileNotFound("La carpeta de destino no existe.")
    os.rename(source, dest)
    return {"rel_path": _rel(dest, root), "old_rel_path": _rel(source, root)}


def save_upload(rel_path: str, chunks: Iterable[bytes], overwrite: bool = False, max_bytes: int = MAX_UPLOAD_BYTES) -> Dict[str, Any]:
    """Guarda un archivo subido (arrastrar y soltar) en `rel_path`, creando las carpetas intermedias dentro del workspace.

    Escribe en un temporal y lo renombra al terminar: un corte a mitad de subida nunca deja un archivo a medias.
    WorkspaceExists si ya existe y no se pidió sobrescribir; WorkspaceTooLarge si supera `max_bytes`.
    """
    root = workspace_root()
    target = _entry_path(rel_path, root)
    exists = os.path.lexists(target)
    if exists and not overwrite:
        raise WorkspaceExists("Ya existe un archivo o carpeta con ese nombre.")
    if exists and (target.is_dir() or target.is_symlink()):
        raise WorkspacePathError("No se puede sobrescribir una carpeta ni un enlace simbólico.")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".praxeon-", suffix=".upload")
    size = 0
    try:
        with os.fdopen(fd, "wb") as fh:
            for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise WorkspaceTooLarge(f"El archivo supera el límite de {max_bytes // (1024 * 1024)} MB.")
                fh.write(chunk)
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return {"rel_path": _rel(target, root), "size": size, "replaced": exists}
