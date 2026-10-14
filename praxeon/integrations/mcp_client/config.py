"""Configuración persistente de los servidores MCP que PRAXEON consume (`<cache>/mcp.json`).

Un servidor MCP es código de terceros que se ejecuta con los permisos del usuario. Por eso cada servidor nace
desactivado, sin aprobar y con un entorno mínimo: solo recibe las variables que se le declaran aquí.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from praxeon.config import resolve_cache_dir

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_RISKS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


class ToolOverride(BaseModel):
    """Ajustes del operador sobre una herramienta concreta (prevalecen sobre lo que anuncie el servidor)."""

    risk: Optional[str] = None
    read_only: Optional[bool] = None
    confirm: Optional[bool] = None
    disabled: bool = False

    @field_validator("risk")
    @classmethod
    def _risk(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.upper()
        if value not in _RISKS:
            raise ValueError(f"risk debe ser uno de {', '.join(_RISKS)}")
        return value


class McpServerConfig(BaseModel):
    name: str
    command: str = Field(..., min_length=1)
    args: List[str] = Field(default_factory=list)
    # Variables del entorno del servidor: literales o «${VARIABLE}» para tomarlas del entorno de PRAXEON (.env)
    env: Dict[str, str] = Field(default_factory=dict)
    cwd: Optional[str] = None
    enabled: bool = False
    # Si True se fían las pistas que anuncia el servidor (readOnlyHint…); por defecto no, porque las declara él mismo
    trust_hints: bool = False
    timeout_s: float = Field(default=60.0, gt=0, le=3600)
    # Huella de las herramientas que el operador revisó. Si el servidor las cambia, deja de exponerse hasta reaprobar
    approved_hash: Optional[str] = None
    tools: Dict[str, ToolOverride] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _NAME_RE.match(value) or "__" in value:
            raise ValueError("el nombre debe ser minúsculas, dígitos y '_' (empezando por letra, sin '__', máx. 32)")
        return value

    def launch_fingerprint(self) -> str:
        """Identifica QUÉ se ejecuta (comando, argumentos, entorno, carpeta): si cambia, la aprobación deja de valer."""
        raw = json.dumps([self.command, self.args, sorted(self.env.items()), self.cwd], ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class McpConfigStore:
    """Lee y escribe `mcp.json`. Seguro entre hilos; la escritura es atómica."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return Path(self._path) if self._path else Path(resolve_cache_dir()) / "mcp.json"

    def _read(self) -> Dict[str, McpServerConfig]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        servers: Dict[str, McpServerConfig] = {}
        raw = data.get("servers") if isinstance(data, dict) else None
        for name, cfg in (raw or {}).items():
            try:
                servers[name] = McpServerConfig(name=name, **cfg)
            except Exception:  # entrada corrupta: se ignora sin tumbar el resto
                continue
        return servers

    def _write(self, servers: Dict[str, McpServerConfig]) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"servers": {n: c.model_dump(exclude={"name"}) for n, c in sorted(servers.items())}}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def list(self) -> List[McpServerConfig]:
        with self._lock:
            return list(self._read().values())

    def get(self, name: str) -> Optional[McpServerConfig]:
        with self._lock:
            return self._read().get(name)

    def save(self, cfg: McpServerConfig) -> McpServerConfig:
        """Guarda un servidor. Si cambia lo que se ejecuta, se anula la aprobación y se desactiva."""
        with self._lock:
            servers = self._read()
            previous = servers.get(cfg.name)
            if previous is not None and previous.launch_fingerprint() != cfg.launch_fingerprint():
                cfg = cfg.model_copy(update={"approved_hash": None, "enabled": False})
            servers[cfg.name] = cfg
            self._write(servers)
            return cfg

    def update(self, name: str, **changes) -> Optional[McpServerConfig]:
        with self._lock:
            servers = self._read()
            current = servers.get(name)
            if current is None:
                return None
            servers[name] = current.model_copy(update=changes)
            self._write(servers)
            return servers[name]

    def delete(self, name: str) -> bool:
        with self._lock:
            servers = self._read()
            if name not in servers:
                return False
            del servers[name]
            self._write(servers)
            return True
