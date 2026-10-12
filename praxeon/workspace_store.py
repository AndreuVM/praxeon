"""Workspace del proyecto elegido por el operador.

El workspace es la carpeta sobre la que trabajan las misiones: base de las rutas relativas,
límite de contención del sandbox y raíz de las comprobaciones de existencia. Se guarda en
`<caché>/workspace.json` junto con los últimos usados, de modo que sobrevive a los reinicios.

Orden de resolución del workspace por defecto:
    1. el elegido por el operador (este módulo),
    2. `PRAXEON_DEFAULT_WORKSPACE_ROOT` / `PRAXEON_WORKSPACE_ROOT`,
    3. la raíz del proyecto detectada desde el directorio actual (.git / pyproject.toml).
"""

from __future__ import annotations

import json
import os
import string
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from praxeon.config import resolve_cache_dir

STORE_FILENAME = "workspace.json"
MAX_RECENT = 12
MAX_BROWSE_ENTRIES = 500
PROJECT_MARKERS = (".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml")

SOURCE_SELECTED = "selected"
SOURCE_ENV = "env"
SOURCE_AUTO = "auto"

_lock = threading.Lock()


def _store_path() -> Path:
    return Path(resolve_cache_dir()) / STORE_FILENAME


def _load() -> Dict[str, Any]:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"current": None, "recent": []}
    if not isinstance(data, dict):
        return {"current": None, "recent": []}
    recent = [r for r in data.get("recent", []) if isinstance(r, dict) and isinstance(r.get("path"), str)]
    current = data.get("current") if isinstance(data.get("current"), str) else None
    return {"current": current, "recent": recent}


def _save(data: Dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _norm(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path.strip()))


def validate_directory(path: str) -> Tuple[str, Optional[str]]:
    """Devuelve (ruta normalizada, motivo del rechazo o None si es una carpeta válida)."""
    if not path or not path.strip():
        return "", "Indica una ruta."
    norm = _norm(path)
    if not os.path.exists(norm):
        return norm, "La carpeta no existe."
    if not os.path.isdir(norm):
        return norm, "La ruta es un archivo, no una carpeta."
    if not os.access(norm, os.R_OK):
        return norm, "No hay permiso de lectura sobre la carpeta."
    return norm, None


def project_hints(path: str) -> List[str]:
    """Marcadores de proyecto presentes en la carpeta (para reconocerla de un vistazo)."""
    try:
        return [m for m in PROJECT_MARKERS if os.path.exists(os.path.join(path, m))]
    except OSError:
        return []


def get_selected_workspace() -> Optional[str]:
    """Workspace elegido por el operador, si sigue existiendo."""
    with _lock:
        current = _load()["current"]
    if current and os.path.isdir(current):
        return current
    return None


def _auto_root() -> str:
    check = os.getcwd()
    while True:
        if os.path.exists(os.path.join(check, ".git")) or os.path.exists(os.path.join(check, "pyproject.toml")):
            return os.path.abspath(check)
        parent = os.path.dirname(check)
        if parent == check:
            break
        check = parent
    return os.path.abspath(os.getcwd())


def resolve_default_workspace() -> Tuple[str, str]:
    """(ruta, origen) del workspace por defecto: elegido, variable de entorno o autodetectado."""
    selected = get_selected_workspace()
    if selected:
        return selected, SOURCE_SELECTED
    env_root = os.environ.get("PRAXEON_DEFAULT_WORKSPACE_ROOT") or os.environ.get("PRAXEON_WORKSPACE_ROOT")
    if env_root and os.path.exists(env_root):
        return os.path.abspath(env_root), SOURCE_ENV
    return _auto_root(), SOURCE_AUTO


def select_workspace(path: str) -> str:
    """Fija el workspace por defecto y lo recuerda entre los recientes. ValueError si no es válido."""
    norm, reason = validate_directory(path)
    if reason:
        raise ValueError(reason)
    with _lock:
        data = _load()
        data["current"] = norm
        key = os.path.normcase(norm)
        recent = [r for r in data["recent"] if os.path.normcase(r["path"]) != key]
        recent.insert(0, {"path": norm, "last_used": time.time()})
        data["recent"] = recent[:MAX_RECENT]
        _save(data)
    return norm


def clear_selection() -> None:
    """Vuelve a la detección automática (los recientes se conservan)."""
    with _lock:
        data = _load()
        data["current"] = None
        _save(data)


def forget_recent(path: str) -> None:
    key = os.path.normcase(_norm(path))
    with _lock:
        data = _load()
        data["recent"] = [r for r in data["recent"] if os.path.normcase(r["path"]) != key]
        if data["current"] and os.path.normcase(data["current"]) == key:
            data["current"] = None
        _save(data)


def list_recent() -> List[Dict[str, Any]]:
    with _lock:
        recent = _load()["recent"]
    return [
        {
            "path": r["path"],
            "name": os.path.basename(r["path"].rstrip("\\/")) or r["path"],
            "exists": os.path.isdir(r["path"]),
            "last_used": r.get("last_used"),
        }
        for r in recent
    ]


def describe_workspace() -> Dict[str, Any]:
    path, source = resolve_default_workspace()
    return {
        "path": path,
        "name": os.path.basename(path.rstrip("\\/")) or path,
        "source": source,
        "exists": os.path.isdir(path),
        "hints": project_hints(path),
        "recent": list_recent(),
    }


def _roots() -> List[Dict[str, str]]:
    roots: List[Dict[str, str]] = []
    home = str(Path.home())
    roots.append({"path": home, "name": "Carpeta personal"})
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                roots.append({"path": drive, "name": f"Unidad {letter}:"})
    else:
        roots.append({"path": "/", "name": "Raíz del sistema"})
    return roots


def browse_directory(path: Optional[str] = None, show_hidden: bool = False) -> Dict[str, Any]:
    """Subcarpetas de `path` (o de la carpeta personal si no se indica). ValueError si no se puede listar."""
    target = _norm(path) if path and path.strip() else str(Path.home())
    if not os.path.isdir(target):
        raise ValueError("La carpeta no existe.")
    entries: List[Dict[str, Any]] = []
    truncated = False
    try:
        with os.scandir(target) as it:
            for entry in it:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                if not show_hidden and entry.name.startswith("."):
                    continue
                if len(entries) >= MAX_BROWSE_ENTRIES:
                    truncated = True
                    break
                entries.append({"name": entry.name, "path": entry.path, "hints": project_hints(entry.path)})
    except PermissionError as exc:
        raise ValueError("No hay permiso para leer esta carpeta.") from exc
    entries.sort(key=lambda e: e["name"].lower())
    parent = os.path.dirname(target.rstrip("\\/")) or None
    if parent and os.path.normcase(parent) == os.path.normcase(target):
        parent = None
    return {
        "path": target,
        "parent": parent,
        "entries": entries,
        "truncated": truncated,
        "hints": project_hints(target),
        "roots": _roots(),
    }
