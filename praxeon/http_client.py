"""Cliente HTTP compartido para los proveedores de modelos y supervisores (praxeon/http_client.py).

Sustituye a `urllib.request` (auditoría P-02 y S-12):

- un único `httpx.Client` con conexiones persistentes (keep-alive), en vez de abrir una conexión TCP/TLS por llamada;
- solo admite URLs `http`/`https` con host: `urllib` abría también `file://`, `ftp://`… si un `base_url` lo pedía;
- sigue redirecciones a mano y retira las cabeceras con credenciales si cambia el host (como el antiguo
  `SafeNoAuthForwardRedirectHandler`); `httpx` solo retiraría `Authorization`, no `x-api-key`.

Los errores imitan a los de `urllib` (`urllib.error.HTTPError` con `.code` y `.read()`, `urllib.error.URLError` con
`.reason`) para que los clientes conserven su tratamiento de errores y sus mensajes.
"""

from __future__ import annotations

import io
import json
import threading
import urllib.error
from typing import Any
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

ALLOWED_SCHEMES = ("http", "https")
MAX_REDIRECTS = 5
SECRET_HEADERS = ("authorization", "x-api-key", "api-key", "x-goog-api-key", "proxy-authorization")
USER_AGENT = "Praxeon/1.0.0 (Autonomous Agent Runtime)"

_client: httpx.Client | None = None
_client_lock = threading.Lock()


def validate_http_url(url: str) -> str:
    """Devuelve la URL si es http(s) con host; si no, ValueError (no se hace ninguna petición)."""
    parts = urlsplit(str(url or ""))
    if parts.scheme.lower() not in ALLOWED_SCHEMES or not parts.hostname:
        raise ValueError(f"URL no permitida para un proveedor (solo http/https con host): {url!r}")
    return str(url)


def shared_client() -> httpx.Client:
    """Cliente con conexiones persistentes, compartido por todos los proveedores (httpx.Client es seguro entre hilos)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                follow_redirects=False,                      # se siguen a mano (ver _send)
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
                headers={"User-Agent": USER_AGENT},
            )
        return _client


def _host(url: httpx.URL) -> str:
    return f"{url.host}:{url.port or ''}".lower()


def _send(request: httpx.Request) -> httpx.Response:
    client = shared_client()
    origin = _host(request.url)
    for _ in range(MAX_REDIRECTS + 1):
        try:
            response = client.send(request)
        except httpx.TimeoutException as exc:
            raise urllib.error.URLError("timed out") from exc
        except httpx.HTTPError as exc:
            raise urllib.error.URLError(str(exc) or type(exc).__name__) from exc
        if not response.is_redirect or response.next_request is None:
            return response
        nxt = response.next_request
        validate_http_url(str(nxt.url))
        nxt.extensions["timeout"] = request.extensions.get("timeout")
        if _host(nxt.url) != origin:
            for name in SECRET_HEADERS:
                nxt.headers.pop(name, None)
        response.close()
        request = nxt
    raise urllib.error.URLError(f"demasiadas redirecciones (> {MAX_REDIRECTS})")


def _raise_for_status(url: str, response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise urllib.error.HTTPError(
            url, response.status_code, response.reason_phrase, response.headers, io.BytesIO(response.content),  # type: ignore[arg-type]
        )


def request_json(
    method: str,
    url: str,
    payload: Any = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Envía una petición (con cuerpo JSON si hay `payload`) y devuelve la respuesta JSON.

    Errores: ValueError (URL no permitida), urllib.error.HTTPError (status >= 400), urllib.error.URLError (red/timeout).
    """
    validate_http_url(url)
    hdrs = dict(headers or {})
    content = None
    if payload is not None:
        content = json.dumps(payload).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    request = shared_client().build_request(method, url, content=content, headers=hdrs, timeout=timeout)
    response = _send(request)
    _raise_for_status(url, response)
    if not response.content:
        return {}
    return json.loads(response.content.decode("utf-8"))


def post_json(url: str, payload: Any, headers: Mapping[str, str] | None = None, timeout: float = 60.0) -> dict[str, Any]:
    return request_json("POST", url, payload, headers, timeout)


def get_json(url: str, headers: Mapping[str, str] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    return request_json("GET", url, None, headers, timeout)


def get_status(url: str, timeout: float = 1.0) -> int:
    """Código HTTP de un GET (para sondeos de disponibilidad); URLError si no hay respuesta."""
    validate_http_url(url)
    response = _send(shared_client().build_request("GET", url, timeout=timeout))
    return response.status_code
