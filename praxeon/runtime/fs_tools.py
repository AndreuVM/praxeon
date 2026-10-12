"""Utilidades de sistema de archivos compartidas por los ejecutores (listado de carpetas y contenido a escribir)."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

MAX_LIST_ENTRIES = 200
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode", ".praxeon_cache", ".jev_cache"}

# Nombres con los que los modelos piden listar o escribir; se normalizan a las herramientas reales
LIST_ALIASES = {"list_dir", "list_directory", "ls", "dir", "list_files", "listdir", "tree"}
WRITE_ALIASES = {"write_file", "write_to_file", "create_file", "save_file", "edit_file", "replace_file_content"}
CONTENT_KEYS = ("content", "contents", "text", "body", "file_text", "file_content", "code", "diff")


def list_directory(path: str, label: str, depth: int = 3, limit: int = MAX_LIST_ENTRIES) -> Tuple[str, bool]:
    """Árbol de una carpeta hasta `depth` niveles. Devuelve (texto, éxito)."""
    if not os.path.exists(path):
        return f"La ruta '{label}' NO EXISTE. Comprueba el nombre con list_dir sobre la carpeta superior.", False
    if not os.path.isdir(path):
        return f"'{label}' es un archivo, no una carpeta. Usa read_file para leerlo.", False

    lines: List[str] = []
    truncated = False

    def walk(current: str, level: int) -> None:
        nonlocal truncated
        try:
            entries = sorted(os.scandir(current), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
        except OSError as exc:
            lines.append(f"{'  ' * level}(no se puede leer: {exc.strerror or exc})")
            return
        for entry in entries:
            if len(lines) >= limit:
                truncated = True
                return
            is_dir = entry.is_dir(follow_symlinks=False)
            lines.append(f"{'  ' * level}{entry.name}{'/' if is_dir else ''}")
            if is_dir and level + 1 < depth and entry.name not in SKIP_DIRS:
                walk(entry.path, level + 1)

    walk(path, 0)
    if not lines:
        return f"'{label}' está vacía (sin archivos ni carpetas).", True
    tail = f"\n[... listado recortado a {limit} entradas ...]" if truncated else ""
    return f"Contenido de '{label}' (hasta {depth} niveles):\n" + "\n".join(lines) + tail, True


def content_from_args(args: Dict[str, Any]) -> Optional[str]:
    """Texto a escribir en un archivo: acepta `content` y los nombres equivalentes que usan los modelos."""
    for key in CONTENT_KEYS:
        value = args.get(key)
        if isinstance(value, str):
            return value
    return None
