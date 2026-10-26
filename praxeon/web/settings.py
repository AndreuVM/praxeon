"""Ajustes del acceso a internet (`<caché>/web.json`): activado, dominios permitidos fuera de Full Access y clave de Brave."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

KEY_ENV = "BRAVE_API_KEY"


def _clean_domain(value: str) -> str:
    host = str(value or "").strip().lower()
    for prefix in ("https://", "http://"):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host.split("/")[0].strip(".")


class WebSettingsStore:
    """Por defecto el acceso está activado. En Full Access es abierto (cualquier dominio público); en los demás modos solo
    se leen los dominios de la lista. La búsqueda necesita la clave de Brave (guardada aquí o en BRAVE_API_KEY)."""

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._cache_dir = cache_dir
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        from praxeon.config import resolve_cache_dir

        return Path(self._cache_dir or resolve_cache_dir()) / "web.json"

    def _load(self) -> Dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        return raw if isinstance(raw, dict) else {}

    def enabled(self) -> bool:
        return bool(self._load().get("enabled", True))

    def allowed_domains(self) -> List[str]:
        return [d for d in (_clean_domain(x) for x in self._load().get("allowed_domains", [])) if d]

    def api_key(self) -> str:
        return str(self._load().get("brave_api_key") or os.environ.get(KEY_ENV) or "").strip()

    def public(self) -> Dict[str, Any]:
        """Estado para la interfaz: nunca incluye la clave."""
        data = self._load()
        own = bool(str(data.get("brave_api_key") or "").strip())
        return {
            "enabled": self.enabled(),
            "allowed_domains": self.allowed_domains(),
            "has_key": bool(self.api_key()),
            "key_source": "settings" if own else ("env" if os.environ.get(KEY_ENV) else None),
        }

    def update(self, enabled: Optional[bool] = None, allowed_domains: Optional[List[str]] = None, brave_api_key: Optional[str] = None) -> Dict[str, Any]:
        """`brave_api_key`: None conserva la guardada, cadena vacía la borra."""
        with self._lock:
            data = self._load()
            if enabled is not None:
                data["enabled"] = bool(enabled)
            if allowed_domains is not None:
                data["allowed_domains"] = sorted({d for d in (_clean_domain(x) for x in allowed_domains) if d})
            if brave_api_key is not None:
                data["brave_api_key"] = brave_api_key.strip()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)
        return self.public()
