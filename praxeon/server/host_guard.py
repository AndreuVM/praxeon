"""Validación de la cabecera `Host` (defensa frente a DNS-rebinding, auditoría S-05).

CORS no impide que una web ajena resuelva su dominio a 127.0.0.1 y hable con la API local: el navegador la trata como mismo origen.
Lo que la delata es la cabecera `Host`, que seguirá siendo el dominio del atacante. Solo se atienden los hosts de confianza.
"""

from __future__ import annotations

import os
from typing import Iterable, List, Optional

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "testserver", "testclient")     # los dos últimos: clientes de prueba de Starlette


def _bare_host(value: str) -> str:
    """Quita el puerto y los corchetes de IPv6: `[::1]:8000` → `::1`, `localhost:8000` → `localhost`."""
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def configured_hosts() -> Optional[List[str]]:
    """Hosts definidos por el operador en PRAXEON_ALLOWED_HOSTS (separados por comas), o None si no los definió."""
    raw = [h.strip().lower() for h in (os.environ.get("PRAXEON_ALLOWED_HOSTS") or "").split(",") if h.strip()]
    return raw or None


def host_is_allowed(host_header: str, allowed: Iterable[str]) -> bool:
    host = _bare_host(host_header)
    for pattern in allowed:
        if pattern == "*" or host == pattern or (pattern.startswith("*.") and host.endswith(pattern[1:]) and host != pattern[2:]):
            return True
    return False


class HostGuardMiddleware:
    """Middleware ASGI (HTTP y WebSocket): responde 400 si `Host` no es de confianza. Lee el entorno en cada petición."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers") or [])
            host = headers.get(b"host", b"").decode("latin-1")
            if not host_is_allowed(host, configured_hosts() or LOOPBACK_HOSTS):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                body = b"Invalid host header"
                await send({"type": "http.response.start", "status": 400,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
