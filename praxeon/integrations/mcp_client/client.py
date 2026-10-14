"""Cliente MCP por stdio (JSON-RPC 2.0 delimitado por saltos de línea), síncrono y sin dependencias.

El runtime de PRAXEON trabaja con hilos, así que el cliente es síncrono: un hilo lector enruta las respuestas a quien
las espera y cada petición tiene su propio plazo. Solo implementa lo necesario para consumir herramientas
(initialize, tools/list, tools/call); ante peticiones del servidor (sampling, roots…) responde «método no soportado».
"""

from __future__ import annotations

import collections
import json
import os
import shutil
import subprocess
import threading
from typing import Any, Deque, Dict, List, Optional

PROTOCOL_VERSION = "2025-06-18"

# Entorno mínimo para que un servidor pueda arrancar (npx/uvx necesitan PATH y las carpetas del usuario).
# No se hereda nada más: así no se filtran claves de API u otros secretos del proceso de PRAXEON.
_BASE_ENV = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE",
    "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA", "HOMEDRIVE", "HOMEPATH",
    "USERNAME", "LANG", "LC_ALL", "TERM", "SHELL", "USER", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
)


class McpError(RuntimeError):
    """Error del protocolo o del servidor MCP."""


def build_env(declared: Dict[str, str]) -> Dict[str, str]:
    env = {k: os.environ[k] for k in _BASE_ENV if k in os.environ}
    for key, value in declared.items():
        if value.startswith("${") and value.endswith("}"):
            value = os.environ.get(value[2:-1], "")
        env[key] = value
    return env


class StdioMcpClient:
    def __init__(
        self,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        name: str = "mcp",
        client_version: str = "1.0",
    ) -> None:
        self._command = command
        self._args = list(args or [])
        self._env = env
        self._cwd = cwd
        self.name = name
        self._client_version = client_version
        self._proc: Optional[subprocess.Popen] = None
        self._write_lock = threading.Lock()
        self._pending: Dict[int, Dict[str, Any]] = {}
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._stderr: Deque[str] = collections.deque(maxlen=40)
        self.tools_changed = False
        self.server_info: Dict[str, Any] = {}

    # ------------------------------------------------------------------ ciclo de vida
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
            result = self._request(
                "initialize",
                {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "praxeon", "version": self._client_version}},
                timeout,
            )
            self.server_info = result.get("serverInfo") or {}
            self._notify("notifications/initialized")
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

    # ------------------------------------------------------------------ API
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

    # ------------------------------------------------------------------ JSON-RPC
    def _send(self, message: Dict[str, Any]) -> None:
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
            self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            if not slot["event"].wait(timeout):
                raise McpError(f"El servidor MCP «{self.name}» no respondió a {method} en {timeout:.0f}s. {self.stderr_tail[-300:]}")
        finally:
            self._pending.pop(req_id, None)
        response = slot["response"] or {}
        if "error" in response:
            err = response["error"] or {}
            raise McpError(f"{method}: {err.get('message', err)}")
        return response.get("result") or {}

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
                if not isinstance(msg, dict):
                    continue
                if "method" in msg:
                    self._on_server_message(msg)
                elif "id" in msg:
                    slot = self._pending.get(msg["id"])
                    if slot is not None:
                        slot["response"] = msg
                        slot["event"].set()
        except (OSError, ValueError):
            pass
        self._fail_pending("El servidor MCP terminó")

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

    def _err_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        try:
            for raw in proc.stderr:
                self._stderr.append(raw.decode("utf-8", errors="replace").rstrip())
        except (OSError, ValueError):
            pass

    def _fail_pending(self, reason: str) -> None:
        for slot in list(self._pending.values()):
            if slot["response"] is None:
                slot["response"] = {"error": {"message": f"{reason}. {self.stderr_tail[-300:]}"}}
            slot["event"].set()
