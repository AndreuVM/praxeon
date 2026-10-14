"""Clientes MCP síncronos y sin dependencias: stdio, SSE clásico (2024-11-05) y HTTP «streamable» (2025-03-26+).

El runtime de PRAXEON trabaja con hilos, así que los clientes son síncronos: las respuestas se enrutan a quien las espera
y cada petición tiene su propio plazo. Solo se implementa lo necesario para consumir herramientas (initialize,
tools/list, tools/call); ante peticiones del servidor (sampling, roots…) se responde «método no soportado».

Los transportes remotos manejan secretos (tokens en la URL o en cabeceras): nunca aparecen en los mensajes de error ni en
la API, y el endpoint de envío que anuncia un servidor SSE debe ser del mismo origen que la URL configurada.
"""

from __future__ import annotations

import collections
import http.client
import json
import os
import re
import shutil
import subprocess
import threading
from typing import Any, Deque, Dict, List, Optional
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

PROTOCOL_VERSION = "2025-06-18"

# Entorno mínimo para que un servidor local pueda arrancar (npx/uvx necesitan PATH y las carpetas del usuario).
# No se hereda nada más: así no se filtran claves de API u otros secretos del proceso de PRAXEON.
_BASE_ENV = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE",
    "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA", "HOMEDRIVE", "HOMEPATH",
    "USERNAME", "LANG", "LC_ALL", "TERM", "SHELL", "USER", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
)
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class McpError(RuntimeError):
    """Error del protocolo o del servidor MCP. `status` es el código HTTP si el fallo vino de un transporte remoto."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


def build_env(declared: Dict[str, str]) -> Dict[str, str]:
    env = {k: os.environ[k] for k in _BASE_ENV if k in os.environ}
    for key, value in declared.items():
        if value.startswith("${") and value.endswith("}"):
            value = os.environ.get(value[2:-1], "")
        env[key] = value
    return env


def expand_vars(text: str, what: str = "la configuración") -> str:
    """Sustituye «${VARIABLE}» por el valor del entorno de PRAXEON; si falta, falla con un mensaje claro."""
    def repl(match: "re.Match[str]") -> str:
        value = os.environ.get(match.group(1))
        if value is None:
            raise McpError(f"Falta la variable de entorno «{match.group(1)}» que usa {what}. Defínela en el entorno o en tu .env.")
        return value

    return _VAR_RE.sub(repl, text)


def redact_url(url: str) -> str:
    """URL apta para mostrar: sin credenciales de usuario, con los valores de la query y los tramos largos del path ocultos.
    Las referencias «${VARIABLE}» no son secretas y se dejan visibles."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{parts.port}" if parts.port else "")
    path = "/".join(seg if (len(seg) <= 20 or _VAR_RE.fullmatch(seg)) else "•••" for seg in parts.path.split("/"))
    query = "&".join(
        f"{k}={v if _VAR_RE.fullmatch(v) else '•••'}" for k, v in parse_qsl(parts.query, keep_blank_values=True)
    )
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def url_secrets(url: str, headers: Dict[str, str]) -> List[str]:
    """Valores que no deben aparecer en mensajes de error (query, credenciales y cabeceras)."""
    parts = urlsplit(url)
    secrets = [v for _, v in parse_qsl(parts.query, keep_blank_values=True)] + list(headers.values())
    if parts.password:
        secrets.append(parts.password)
    return [s for s in secrets if len(s) >= 4]


# ---------------------------------------------------------------------------------------------------------
# Núcleo JSON-RPC común
# ---------------------------------------------------------------------------------------------------------

class _RpcClient:
    def __init__(self, name: str, client_version: str = "1.0", secrets: Optional[List[str]] = None) -> None:
        self.name = name
        self._client_version = client_version
        self._secrets = list(secrets or [])
        self._pending: Dict[Any, Dict[str, Any]] = {}
        self._next_id = 0
        self._id_lock = threading.Lock()
        self.tools_changed = False
        self.server_info: Dict[str, Any] = {}

    # -- a implementar por cada transporte
    def start(self, timeout: float = 30.0) -> None:
        raise NotImplementedError

    def alive(self) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def _send(self, message: Dict[str, Any], timeout: Optional[float] = None) -> None:
        raise NotImplementedError

    @property
    def stderr_tail(self) -> str:
        return ""

    # -- común
    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def _initialize(self, timeout: float) -> None:
        result = self._request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "praxeon", "version": self._client_version}},
            timeout,
        )
        self.server_info = result.get("serverInfo") or {}
        self._notify("notifications/initialized")

    def list_tools(self, timeout: float = 30.0) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        for _ in range(50):  # tope de páginas
            result = self._request("tools/list", {"cursor": cursor} if cursor else {}, timeout)
            tools.extend(t for t in result.get("tools", []) if isinstance(t, dict) and t.get("name"))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self.tools_changed = False
        return tools

    def call_tool(self, name: str, arguments: Dict[str, Any], timeout: float = 60.0) -> Dict[str, Any]:
        return self._request("tools/call", {"name": name, "arguments": arguments or {}}, timeout)

    def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        message: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            message["params"] = params
        self._send(message)

    def _request(self, method: str, params: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        with self._id_lock:
            self._next_id += 1
            req_id = self._next_id
        slot: Dict[str, Any] = {"event": threading.Event(), "response": None}
        self._pending[req_id] = slot
        try:
            self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}, timeout)
            if not slot["event"].wait(timeout):
                raise McpError(self._scrub(f"El servidor MCP «{self.name}» no respondió a {method} en {timeout:.0f}s. {self.stderr_tail[-300:]}"))
        finally:
            self._pending.pop(req_id, None)
        response = slot["response"] or {}
        if "error" in response:
            err = response["error"] or {}
            raise McpError(self._scrub(f"{method}: {err.get('message', err)}"))
        return response.get("result") or {}

    def _handle_message(self, msg: Any) -> None:
        if isinstance(msg, list):  # lote JSON-RPC
            for item in msg:
                self._handle_message(item)
            return
        if not isinstance(msg, dict):
            return
        if "method" in msg:
            self._on_server_message(msg)
        elif "id" in msg:
            slot = self._pending.get(msg["id"])
            if slot is not None:
                slot["response"] = msg
                slot["event"].set()

    def _on_server_message(self, msg: Dict[str, Any]) -> None:
        method = msg.get("method")
        if method == "notifications/tools/list_changed":
            self.tools_changed = True
        if "id" not in msg:
            return  # notificación
        if method == "ping":
            reply: Dict[str, Any] = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
        else:  # sampling, roots, elicitation…: PRAXEON no los ofrece
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "Método no soportado por PRAXEON"}}
        try:
            self._send(reply)
        except McpError:
            pass

    def _fail_pending(self, reason: str) -> None:
        for slot in list(self._pending.values()):
            if slot["response"] is None:
                slot["response"] = {"error": {"message": self._scrub(f"{reason}. {self.stderr_tail[-300:]}")}}
            slot["event"].set()


# ---------------------------------------------------------------------------------------------------------
# stdio
# ---------------------------------------------------------------------------------------------------------

class StdioMcpClient(_RpcClient):
    def __init__(
        self,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        name: str = "mcp",
        client_version: str = "1.0",
    ) -> None:
        super().__init__(name, client_version)
        self._command = command
        self._args = list(args or [])
        self._env = env
        self._cwd = cwd
        self._proc: Optional[subprocess.Popen] = None
        self._write_lock = threading.Lock()
        self._stderr: Deque[str] = collections.deque(maxlen=40)

    def start(self, timeout: float = 30.0) -> None:
        exe = shutil.which(self._command) or self._command
        try:
            self._proc = subprocess.Popen(
                [exe, *self._args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=self._env if self._env is not None else build_env({}), cwd=self._cwd or None,
            )
        except OSError as exc:
            raise McpError(f"No se pudo ejecutar «{self._command}»: {exc}") from exc
        threading.Thread(target=self._read_loop, name=f"mcp-{self.name}-out", daemon=True).start()
        threading.Thread(target=self._err_loop, name=f"mcp-{self.name}-err", daemon=True).start()
        try:
            self._initialize(timeout)
        except Exception:
            self.close()
            raise

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=2)
        except Exception:
            self._kill_tree(proc)
        self._fail_pending("El servidor MCP se cerró")

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        if os.name == "nt":  # npx/uvx lanzan hijos: taskkill /T cierra todo el árbol
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
        else:
            proc.kill()
        try:
            proc.wait(timeout=3)
        except Exception:
            pass

    def _send(self, message: Dict[str, Any], timeout: Optional[float] = None) -> None:
        if not self.alive():
            raise McpError(f"El servidor MCP «{self.name}» no está en ejecución. {self.stderr_tail[-300:]}")
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                assert self._proc is not None and self._proc.stdin is not None
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise McpError(f"No se pudo escribir en el servidor MCP «{self.name}»: {exc}") from exc

    def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue  # algunos servidores escriben logs en stdout: no son mensajes
                self._handle_message(msg)
        except (OSError, ValueError):
            pass
        self._fail_pending("El servidor MCP terminó")

    def _err_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        try:
            for raw in proc.stderr:
                self._stderr.append(raw.decode("utf-8", errors="replace").rstrip())
        except (OSError, ValueError):
            pass


# ---------------------------------------------------------------------------------------------------------
# Transportes remotos
# ---------------------------------------------------------------------------------------------------------

def _connection(parts: Any, timeout: Optional[float]) -> http.client.HTTPConnection:
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    return cls(parts.hostname, parts.port, timeout=timeout)


def _target(parts: Any) -> str:
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def _origin(parts: Any) -> tuple:
    return (parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))


class _RemoteClient(_RpcClient):
    def __init__(self, url: str, headers: Optional[Dict[str, str]], name: str, client_version: str = "1.0") -> None:
        headers = dict(headers or {})
        super().__init__(name, client_version, url_secrets(url, headers))
        self._url = url
        self._parts = urlsplit(url)
        if self._parts.scheme not in ("http", "https") or not self._parts.hostname:
            raise McpError("La URL del servidor MCP debe ser http:// o https://")
        self._headers = headers
        self._closed = False

    @property
    def shown_url(self) -> str:
        return redact_url(self._url)

    def _post(self, url_parts: Any, message: Dict[str, Any], extra: Dict[str, str], timeout: Optional[float]):
        """POST de un mensaje JSON-RPC; devuelve (estado, cabeceras, conexión, respuesta) sin leer el cuerpo."""
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self._headers, **extra}
        conn = _connection(url_parts, timeout)
        try:
            conn.request("POST", _target(url_parts), body=body, headers=headers)
            return conn, conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise McpError(self._scrub(f"No se pudo conectar con el servidor MCP «{self.name}» ({self.shown_url}): {exc}")) from exc

    def _http_error(self, resp: http.client.HTTPResponse, what: str) -> McpError:
        try:
            detail = resp.read(300).decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        hint = " (credenciales rechazadas)" if resp.status in (401, 403) else ""
        return McpError(self._scrub(f"{what}: HTTP {resp.status}{hint}. {detail}".strip()), status=resp.status)

    @staticmethod
    def _iter_sse(resp: Any):
        """Eventos SSE (tipo, datos) de una respuesta; termina al cerrarse la conexión."""
        event: Optional[str] = None
        data: List[str] = []
        while True:
            raw = resp.readline()
            if not raw:
                if data:
                    yield event or "message", "\n".join(data)
                return
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == "":
                if data:
                    yield event or "message", "\n".join(data)
                event, data = None, []
            elif line.startswith(":"):
                continue
            else:
                field, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if field == "event":
                    event = value
                elif field == "data":
                    data.append(value)

    def alive(self) -> bool:
        return not self._closed


class SseMcpClient(_RemoteClient):
    """SSE clásico: un GET mantiene el flujo de respuestas y un POST por mensaje al endpoint que anuncia el servidor."""

    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None, name: str = "mcp", client_version: str = "1.0") -> None:
        super().__init__(url, headers, name, client_version)
        self._endpoint_parts: Optional[Any] = None
        self._endpoint_ready = threading.Event()
        self._stream_conn: Optional[http.client.HTTPConnection] = None
        self._reader: Optional[threading.Thread] = None
        self._endpoint_error: Optional[str] = None

    def start(self, timeout: float = 30.0) -> None:
        conn = _connection(self._parts, timeout)
        try:
            conn.request("GET", _target(self._parts), headers={"Accept": "text/event-stream", "Cache-Control": "no-cache", **self._headers})
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise McpError(self._scrub(f"No se pudo conectar con el servidor MCP «{self.name}» ({self.shown_url}): {exc}")) from exc
        if resp.status != 200:
            err = self._http_error(resp, "El servidor rechazó la conexión SSE")
            conn.close()
            raise err
        if "text/event-stream" not in (resp.getheader("Content-Type") or ""):
            conn.close()
            raise McpError(f"«{self.shown_url}» no es un flujo SSE (Content-Type {resp.getheader('Content-Type')}).")
        if conn.sock is not None:
            conn.sock.settimeout(None)   # el flujo puede estar callado mucho rato; lo cierra close()
        self._stream_conn = conn
        self._reader = threading.Thread(target=self._read_loop, args=(resp,), name=f"mcp-{self.name}-sse", daemon=True)
        self._reader.start()
        try:
            if not self._endpoint_ready.wait(timeout):
                raise McpError("El servidor SSE no anunció su endpoint de envío.")
            if self._endpoint_error:
                raise McpError(self._endpoint_error)
            self._initialize(timeout)
        except Exception:
            self.close()
            raise

    def _read_loop(self, resp: Any) -> None:
        try:
            for kind, data in self._iter_sse(resp):
                if kind == "endpoint":
                    self._accept_endpoint(data.strip())
                elif kind == "message":
                    try:
                        self._handle_message(json.loads(data))
                    except ValueError:
                        continue
        except Exception:   # al cerrar, http.client puede fallar de formas internas (AttributeError…) mientras el hilo lee
            pass
        self._closed = True
        self._endpoint_ready.set()
        self._fail_pending("La conexión SSE con el servidor MCP se cerró")

    def _accept_endpoint(self, value: str) -> None:
        target = urlsplit(urljoin(self._url, value))
        if _origin(target) != _origin(self._parts):
            # un servidor no puede redirigir los mensajes (que pueden llevar datos del agente) a otro destino
            self._endpoint_error = "El servidor SSE anunció un endpoint de otro origen; se rechaza por seguridad."
        else:
            self._endpoint_parts = target
        self._endpoint_ready.set()

    def _send(self, message: Dict[str, Any], timeout: Optional[float] = None) -> None:
        if self._closed or self._endpoint_parts is None:
            raise McpError(f"El servidor MCP «{self.name}» no está conectado.")
        conn, resp = self._post(self._endpoint_parts, message, {}, timeout or 60.0)
        try:
            if resp.status not in (200, 201, 202, 204):
                raise self._http_error(resp, "El servidor rechazó el mensaje")
            body = resp.read()
            if resp.status == 200 and body.strip().startswith((b"{", b"[")):   # algunos servidores responden en el propio POST
                try:
                    self._handle_message(json.loads(body))
                except ValueError:
                    pass
        finally:
            conn.close()

    def close(self) -> None:
        self._closed = True
        conn = self._stream_conn
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        self._fail_pending("El cliente MCP se cerró")


class StreamableHttpMcpClient(_RemoteClient):
    """HTTP «streamable»: un único endpoint; cada POST se responde con JSON o con un flujo SSE que acaba con la respuesta."""

    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None, name: str = "mcp", client_version: str = "1.0") -> None:
        super().__init__(url, headers, name, client_version)
        self._session_id: Optional[str] = None
        self._negotiated = False

    def start(self, timeout: float = 30.0) -> None:
        try:
            self._initialize(timeout)
        except Exception:
            self.close()
            raise

    def _send(self, message: Dict[str, Any], timeout: Optional[float] = None) -> None:
        if self._closed:
            raise McpError(f"El servidor MCP «{self.name}» no está conectado.")
        extra: Dict[str, str] = {}
        if self._session_id:
            extra["Mcp-Session-Id"] = self._session_id
        if self._negotiated:
            extra["MCP-Protocol-Version"] = PROTOCOL_VERSION
        conn, resp = self._post(self._parts, message, extra, timeout or 60.0)
        try:
            session = resp.getheader("Mcp-Session-Id")
            if session:
                self._session_id = session
            if resp.status == 404 and self._session_id:
                self._session_id = None   # sesión caducada: hay que reiniciar el cliente
                self._closed = True
                raise McpError(f"La sesión con el servidor MCP «{self.name}» caducó.", status=404)
            if resp.status not in (200, 201, 202, 204):
                raise self._http_error(resp, "El servidor rechazó el mensaje")
            if resp.status in (202, 204):
                return
            self._negotiated = True
            ctype = resp.getheader("Content-Type") or ""
            if "text/event-stream" in ctype:
                for kind, data in self._iter_sse(resp):
                    if kind == "message":
                        try:
                            self._handle_message(json.loads(data))
                        except ValueError:
                            continue
            else:
                body = resp.read()
                if body.strip():
                    try:
                        self._handle_message(json.loads(body))
                    except ValueError as exc:
                        raise McpError(self._scrub(f"Respuesta no válida del servidor MCP «{self.name}»: {body[:200]!r}")) from exc
        finally:
            conn.close()

    def close(self) -> None:
        if not self._closed and self._session_id:
            try:   # cierre amable de la sesión (opcional en el protocolo)
                conn = _connection(self._parts, 5.0)
                conn.request("DELETE", _target(self._parts), headers={"Mcp-Session-Id": self._session_id, **self._headers})
                conn.getresponse().read()
                conn.close()
            except Exception:
                pass
        self._closed = True
        self._fail_pending("El cliente MCP se cerró")
