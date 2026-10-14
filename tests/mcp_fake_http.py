"""Servidor MCP remoto falso (solo librería estándar) para los tests: SSE clásico y HTTP «streamable».

Modos:
- "sse": GET {sse_path} abre el flujo (evento `endpoint`) y los mensajes se envían por POST a /mcp/messages?session_id=…
- "streamable": POST {path} responde con JSON y cabecera Mcp-Session-Id.
- "streamable_sse": igual, pero cada respuesta va en un flujo text/event-stream.
Autenticación: token en la query (`?token=`) o en la cabecera Authorization: Bearer.
"""

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TOOLS = [
    {
        "name": "echo",
        "description": "Devuelve el texto recibido.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        "annotations": {"readOnlyHint": True},
    },
    {"name": "write_note", "description": "Guarda una nota (tiene efectos).", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "boom", "description": "Siempre falla.", "inputSchema": {"type": "object", "properties": {}}},
]


def rpc(method, params):
    if method == "initialize":
        return {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake-remote", "version": "1"}}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "ping":
        return {}
    if method == "tools/call":
        name, args = params["name"], params.get("arguments") or {}
        if name == "echo":
            return {"content": [{"type": "text", "text": args["text"]}]}
        if name == "write_note":
            return {"content": [{"type": "text", "text": "nota guardada"}]}
        return {"isError": True, "content": [{"type": "text", "text": "explotó a propósito"}]}
    return None


class FakeRemoteMcp:
    def __init__(self, mode="sse", token="tok-secreto-123", sse_path="/mcp/sse", path="/mcp", endpoint_override=None, content_type="text/event-stream"):
        self.mode, self.token, self.sse_path, self.path = mode, token, sse_path, path
        self.endpoint_override, self.content_type = endpoint_override, content_type
        self.requests = []
        self.sessions = {}      # session_id -> Queue de mensajes para el flujo SSE
        self.deleted = False
        self.stopping = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _record(self):
                parts = urlsplit(self.path)
                rec = {"method": self.command, "path": parts.path, "query": parse_qs(parts.query), "headers": {k.lower(): v for k, v in self.headers.items()}}
                outer.requests.append(rec)
                return rec

            def _authorized(self, rec):
                return rec["query"].get("token", [None])[0] == outer.token or rec["headers"].get("authorization") == f"Bearer {outer.token}"

            def _reply(self, status, body=b"", ctype="application/json", extra=None):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                rec = self._record()
                if outer.mode != "sse" or rec["path"] != outer.sse_path:
                    return self._reply(405, b"{}")
                if not self._authorized(rec):
                    return self._reply(401, b'{"error":"token invalido"}')
                self.send_response(200)
                self.send_header("Content-Type", outer.content_type)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                if outer.content_type != "text/event-stream":
                    self.wfile.write(b"<html>no soy SSE</html>")
                    return
                sid = f"S{len(outer.sessions) + 1}"
                q = queue.Queue()
                outer.sessions[sid] = q
                endpoint = outer.endpoint_override or f"/mcp/messages?session_id={sid}"
                try:
                    self.wfile.write(f": hola\n\nevent: endpoint\ndata: {endpoint}\n\n".encode())
                    self.wfile.flush()
                    while not outer.stopping:
                        try:
                            msg = q.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        if msg is None:
                            break
                        self.wfile.write(f": ping\n\nevent: message\ndata: {json.dumps(msg)}\n\n".encode())
                        self.wfile.flush()
                except (OSError, ValueError):
                    pass
                self.close_connection = True

            def do_DELETE(self):  # noqa: N802
                self._record()
                outer.deleted = True
                self._reply(200, b"{}")

            def do_POST(self):  # noqa: N802
                rec = self._record()
                length = int(self.headers.get("Content-Length", 0))
                msg = json.loads(self.rfile.read(length) or b"{}")
                rec["body"] = msg
                if outer.mode == "sse":
                    if rec["path"] != "/mcp/messages":
                        return self._reply(405, b"{}")
                    sid = rec["query"].get("session_id", [None])[0]
                    if sid not in outer.sessions:
                        return self._reply(404, b'{"error":"sesion desconocida"}')
                    if msg.get("id") is not None:
                        result = rpc(msg["method"], msg.get("params") or {})
                        outer.sessions[sid].put({"jsonrpc": "2.0", "id": msg["id"], "result": result})
                    return self._reply(202, b"Accepted", "text/plain")
                # streamable
                if rec["path"] != outer.path:
                    return self._reply(404, b"{}")
                if not self._authorized(rec):
                    return self._reply(401, b'{"error":"token invalido"}')
                extra = {}
                if msg.get("method") == "initialize":
                    extra["Mcp-Session-Id"] = "SESION-1"
                elif rec["headers"].get("mcp-session-id") != "SESION-1":
                    return self._reply(400, b'{"error":"falta sesion"}')
                if msg.get("id") is None:
                    return self._reply(202, b"", "text/plain", extra)
                reply = {"jsonrpc": "2.0", "id": msg["id"], "result": rpc(msg["method"], msg.get("params") or {})}
                if outer.mode == "streamable_sse":
                    return self._reply(200, f"event: message\ndata: {json.dumps(reply)}\n\n".encode(), "text/event-stream", extra)
                return self._reply(200, json.dumps(reply).encode(), "application/json", extra)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def drop_streams(self):
        """Cierra los flujos SSE abiertos (simula que el servidor se cae)."""
        for q in self.sessions.values():
            q.put(None)
        time.sleep(0.3)

    def close(self):
        self.stopping = True
        self.server.shutdown()
        self.server.server_close()
