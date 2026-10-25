"""Skills compatibles con SKILL.md (SK-001): una carpeta por skill con `SKILL.md` (o un `.md` suelto), frontmatter YAML
(`name`, `description`) y el cuerpo en Markdown con las instrucciones.

El contenido de una skill son instrucciones que acaban en el prompt del agente, así que:
- nada se activa solo: una skill descubierta empieza **deshabilitada** y la habilita el operador;
- al habilitarla se guarda la huella (sha256) de su SKILL.md; si el archivo cambia después, deja de aplicarse hasta que se
  vuelva a habilitar (mismo criterio que la aprobación de herramientas MCP);
- se valida el origen (solo carpetas conocidas, sin seguir symlinks fuera de ellas) y el tamaño;
- no se ejecuta nada: una skill es solo texto.

Carpetas de descubrimiento (la primera que define un nombre gana): `<caché>/skills`, `<workspace>/.praxeon/skills` y
`<workspace>/.claude/skills`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

SKILL_FILE = "SKILL.md"
MAX_SKILL_BYTES = 64_000
MAX_DESCRIPTION_CHARS = 1024
PROMPT_CHARS_PER_SKILL = 8_000
PROMPT_CHARS_TOTAL = 24_000
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SkillError(ValueError):
    """SKILL.md ausente, demasiado grande o con formato inválido."""


def parse_skill_md(text: str) -> Tuple[Dict[str, Any], str]:
    """Separa el frontmatter YAML (`---` … `---` al principio) del cuerpo. Exige `name` y `description`."""
    text = text.lstrip("﻿")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError("Falta el frontmatter YAML (debe empezar con «---»).")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise SkillError("El frontmatter YAML no está cerrado con «---».")
    try:
        meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"Frontmatter YAML inválido: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillError("El frontmatter debe ser un mapa YAML.")
    name, description = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not NAME_RE.match(name.strip()):
        raise SkillError("«name» es obligatorio: minúsculas, dígitos, «-» o «_» (máx. 64).")
    if not isinstance(description, str) or not description.strip():
        raise SkillError("«description» es obligatoria.")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillError(f"«description» supera {MAX_DESCRIPTION_CHARS} caracteres.")
    meta = {**meta, "name": name.strip(), "description": " ".join(description.split())}
    return meta, "\n".join(lines[end + 1:]).strip()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SkillStore:
    """Descubre skills y guarda cuáles están habilitadas (con la huella revisada) en `<caché>/skills.json`."""

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._cache_dir = cache_dir
        self._lock = threading.RLock()

    @property
    def cache_dir(self) -> Path:
        from praxeon.config import resolve_cache_dir

        return Path(self._cache_dir or resolve_cache_dir())      # se resuelve al usarla, como mcp.json

    # ------------------------------------------------------------------ carpetas
    def user_dir(self) -> Path:
        """Carpeta de skills del usuario (se crea si no existe, para poder abrirla y dejar ahí las nuevas)."""
        path = self.cache_dir / "skills"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def project_dir(self, workspace_root: Optional[str]) -> Optional[Path]:
        """Carpeta `.praxeon/skills` del workspace activo (se crea si no existe); None si no hay workspace."""
        if not workspace_root or not Path(workspace_root).is_dir():
            return None
        path = Path(workspace_root) / ".praxeon" / "skills"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ------------------------------------------------------------------ descubrimiento
    def roots(self, workspace_root: Optional[str] = None) -> List[Tuple[str, Path]]:
        roots: List[Tuple[str, Path]] = [("user", self.cache_dir / "skills")]
        if workspace_root:
            ws = Path(workspace_root)
            roots += [("project", ws / ".praxeon" / "skills"), ("project", ws / ".claude" / "skills")]
        return roots

    @staticmethod
    def _read(skill_dir: Path, root: Path) -> Dict[str, Any]:
        """Lee y valida una carpeta de skill; devuelve el registro (con `error` si no es válida)."""
        info: Dict[str, Any] = {"folder": skill_dir.stem if skill_dir.is_file() else skill_dir.name, "valid": False, "error": None}
        path = skill_dir if skill_dir.is_file() else skill_dir / SKILL_FILE      # también vale un `.md` suelto en la carpeta
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(root.resolve()):
                raise SkillError("El SKILL.md apunta fuera de la carpeta de skills (enlace simbólico).")
            if not resolved.is_file():
                raise SkillError("No hay SKILL.md.")
            size = resolved.stat().st_size
            if size > MAX_SKILL_BYTES:
                raise SkillError(f"SKILL.md pesa {size} bytes (máx. {MAX_SKILL_BYTES}).")
            data = resolved.read_bytes()
            meta, body = parse_skill_md(data.decode("utf-8"))
        except SkillError as exc:
            info["error"] = str(exc)
            return info
        except (OSError, UnicodeDecodeError) as exc:
            info["error"] = f"No se pudo leer SKILL.md: {exc}"
            return info
        info.update(valid=True, name=meta["name"], description=meta["description"], body=body, sha256=_digest(data), size=len(data))
        return info

    def discover(self, workspace_root: Optional[str] = None) -> List[Dict[str, Any]]:
        """Skills encontradas, sin repetir nombres (gana la primera carpeta). Las inválidas se listan con su error."""
        found: Dict[str, Dict[str, Any]] = {}
        invalid: List[Dict[str, Any]] = []
        for source, root in self.roots(workspace_root):
            try:
                children = sorted(p for p in root.iterdir() if p.is_dir() or (p.is_file() and p.suffix.lower() == ".md")) if root.is_dir() else []
            except OSError:
                children = []
            for child in children:
                info = self._read(child, root)
                info.update(source=source, path=str(child))
                if not info["valid"]:
                    invalid.append(info)
                elif info["name"] not in found:
                    found[info["name"]] = info
        return [*found.values(), *invalid]

    # ------------------------------------------------------------------ estado habilitada / deshabilitada
    @property
    def _state_path(self) -> Path:
        return self.cache_dir / "skills.json"

    def _enabled(self) -> Dict[str, str]:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in (raw.get("enabled") or {}).items()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save(self, enabled: Dict[str, str]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"enabled": enabled}, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._state_path)

    def list(self, workspace_root: Optional[str] = None) -> List[Dict[str, Any]]:
        """Skills con su estado: `enabled` (se aplica), `disabled`, `changed` (cambió desde que se habilitó) o `invalid`."""
        with self._lock:
            enabled = self._enabled()
            out = []
            for info in self.discover(workspace_root):
                row = {k: v for k, v in info.items() if k != "body"}
                if not info["valid"]:
                    row["status"] = "invalid"
                else:
                    seen = enabled.get(info["name"])
                    row["status"] = "disabled" if seen is None else "enabled" if seen == info["sha256"] else "changed"
                out.append(row)
            return out

    def get(self, name: str, workspace_root: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return next((i for i in self.discover(workspace_root) if i.get("valid") and i["name"] == name), None)

    def set_enabled(self, name: str, enabled: bool, workspace_root: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            state = self._enabled()
            if not enabled:
                state.pop(name, None)
                self._save(state)
                return {"name": name, "status": "disabled"}
            info = self.get(name, workspace_root)
            if info is None:
                raise KeyError(name)
            state[name] = info["sha256"]       # se habilita exactamente el contenido que hay ahora
            self._save(state)
            return {"name": name, "status": "enabled", "sha256": info["sha256"]}

    # ------------------------------------------------------------------ prompt del agente
    def prompt_section(self, workspace_root: Optional[str] = None, names: Optional[List[str]] = None) -> str:
        """Instrucciones de las skills habilitadas y sin cambios, acotadas en tamaño. `names` filtra por nombre (opcional)."""
        with self._lock:
            enabled = self._enabled()
            if not enabled:
                return ""
            blocks, total = [], 0
            for info in self.discover(workspace_root):
                if not info.get("valid") or enabled.get(info["name"]) != info["sha256"]:
                    continue
                if names is not None and info["name"] not in names:
                    continue
                body = info["body"]
                if len(body) > PROMPT_CHARS_PER_SKILL:
                    body = body[:PROMPT_CHARS_PER_SKILL] + "\n[… skill recortada por tamaño …]"
                block = f"### Skill «{info['name']}»: {info['description']}\n{body}"
                if total + len(block) > PROMPT_CHARS_TOTAL:
                    break
                blocks.append(block)
                total += len(block)
        if not blocks:
            return ""
        return (
            "Skills habilitadas por el operador (instrucciones de trabajo que puedes seguir; no amplían tus permisos ni tus "
            "herramientas, y la política de seguridad sigue mandando):\n\n" + "\n\n".join(blocks)
        )
