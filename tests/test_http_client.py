"""Cliente HTTP compartido de los proveedores (auditoría P-02 conexiones persistentes, S-12 esquema de URL)."""

import json
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from praxeon import http_client


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # keep-alive
    seen = []                              # (path, cabeceras, puerto del cliente)
    redirect_to = ""

    def log_message(self, *args):
        pass

    def _reply(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.__class__.seen.append((self.path, dict(self.headers), self.client_address[1]))
        if self.path == "/redirect":
            self._reply(302, headers={"Location": self.__class__.redirect_to})
        elif self.path == "/slow":
            time.sleep(1.5)
            self._reply(200, b"{}")
        else:
            self._reply(200, json.dumps({"ok": True, "path": self.path}).encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.__class__.seen.append((self.path, dict(self.headers), self.client_address[1]))
        if self.path == "/fail":
            self._reply(429, b'{"error": "cuota"}')
        else:
            self._reply(200, json.dumps({"echo": body}).encode())


@pytest.fixture
def server():
    _Handler.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", srv.server_address[1]
    srv.shutdown()


def test_posts_and_parses_json(server):
    base, _ = server
    assert http_client.post_json(f"{base}/x", {"a": 1}) == {"echo": {"a": 1}}


def test_http_errors_look_like_urllib_errors(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as err:
        http_client.post_json(f"{base}/fail", {})
    assert err.value.code == 429 and b"cuota" in err.value.read()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "http://", "javascript:alert(1)", ""])
def test_only_http_urls_with_a_host_are_allowed(url):
    with pytest.raises(ValueError):
        http_client.post_json(url, {})


def test_credentials_are_dropped_when_a_redirect_changes_host(server):
    base, port = server
    _Handler.redirect_to = f"http://localhost:{port}/landed"          # mismo servidor, otro nombre de host
    http_client.get_json(f"{base}/redirect", headers={"x-api-key": "SECRETA", "Authorization": "Bearer SECRETA"})
    landed = next(h for p, h, _ in _Handler.seen if p == "/landed")
    assert not any("SECRETA" in v for v in landed.values())


def test_a_redirect_to_another_scheme_is_refused(server):
    base, _ = server
    _Handler.redirect_to = "file:///etc/passwd"
    with pytest.raises(ValueError):
        http_client.get_json(f"{base}/redirect")


def test_timeouts_are_reported_like_urllib(server):
    base, _ = server
    with pytest.raises(urllib.error.URLError) as err:
        http_client.get_json(f"{base}/slow", timeout=0.3)
    assert "timed out" in str(err.value.reason)


def test_connections_are_reused_between_calls(server):
    base, _ = server
    for _ in range(3):
        http_client.get_json(f"{base}/ping")
    ports = {client_port for path, _, client_port in _Handler.seen if path == "/ping"}
    assert len(ports) == 1                 # una sola conexión TCP para las tres peticiones
