"""Descarga segura de páginas: solo GET, solo destinos públicos y texto legible.

Protecciones: esquema http/https y puertos 80/443, sin credenciales en la URL, el DNS se resuelve ANTES de conectar y se
conecta a esa IP ya validada (frena el rebinding), cada redirección se revalida, y tamaño y tiempo van acotados. No se
ejecuta JavaScript ni se envían cookies.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import zlib
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 1_000_000
TIMEOUT_S = 10.0
MAX_REDIRECTS = 5
USER_AGENT = "Mozilla/5.0 (compatible; PRAXEON/1.0; +read-only)"
TEXT_TYPES = ("text/html", "application/xhtml", "text/plain", "application/json", "text/xml", "application/xml", "application/rss", "application/atom", "text/markdown", "text/csv")
_REDIRECTS = {301, 302, 303, 307, 308}


class WebError(Exception):
    """Fallo esperable (destino prohibido, red, tamaño…) con un mensaje que el modelo puede entender."""


@dataclass
class Page:
    url: str
    status: int
    content_type: str
    title: str
    text: str
    truncated: bool


def host_allowed(host: str, allowed: Optional[Iterable[str]]) -> bool:
    """`allowed=None` es modo abierto; si no, el host debe ser uno de la lista o un subdominio suyo."""
    if allowed is None:
        return True
    host = host.lower().strip(".")
    return any(host == d or host.endswith("." + d) for d in (x.lower().strip(".") for x in allowed) if d)


def _validate_ip(raw: str) -> None:
    ip = ipaddress.ip_address(raw.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if not ip.is_global:
        raise WebError(f"Destino no permitido: {raw} es una dirección local o privada.")


def _resolve(host: str, port: int, allow_private: bool) -> str:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebError(f"No se pudo resolver «{host}»: {exc}") from exc
    addrs = [i[4][0] for i in infos]
    if not addrs:
        raise WebError(f"«{host}» no tiene dirección.")
    if not allow_private:
        for a in addrs:      # basta una dirección interna para rechazar el nombre entero
            _validate_ip(a)
    return addrs[0]


class _Pinned(http.client.HTTPConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedTls(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _read_body(resp: http.client.HTTPResponse, max_bytes: int) -> Tuple[bytes, bool]:
    encoding = (resp.getheader("Content-Encoding") or "").lower()
    decomp = zlib.decompressobj(16 + zlib.MAX_WBITS) if "gzip" in encoding else None
    out, size = [], 0
    while size <= max_bytes:
        chunk = resp.read(65536)
        if not chunk:
            break
        if decomp is not None:
            chunk = decomp.decompress(chunk, max_bytes + 1 - size)
        out.append(chunk)
        size += len(chunk)
    data = b"".join(out)
    return data[:max_bytes], len(data) > max_bytes


def fetch_raw(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    allowed_hosts: Optional[Iterable[str]] = None,
    max_bytes: int = MAX_BYTES,
    timeout: float = TIMEOUT_S,
    max_redirects: int = MAX_REDIRECTS,
    allow_private: bool = False,
) -> Tuple[str, int, str, bytes, bool]:
    """GET con las reglas de arriba. Devuelve (url final, estado, content-type, cuerpo, truncado)."""
    current = url
    allowed = None if allowed_hosts is None else list(allowed_hosts)
    for _ in range(max_redirects + 1):
        parts = urlsplit(current)
        scheme = (parts.scheme or "").lower()
        if scheme not in ("http", "https"):
            raise WebError(f"Solo se admiten URLs http o https (recibido «{parts.scheme or 'sin esquema'}»).")
        if parts.username or parts.password:
            raise WebError("Las URLs con usuario o contraseña no están permitidas.")
        host = (parts.hostname or "").lower()
        if not host:
            raise WebError("La URL no tiene dominio.")
        try:
            port = parts.port or (443 if scheme == "https" else 80)
        except ValueError as exc:
            raise WebError("Puerto no válido en la URL.") from exc
        if not allow_private and port not in (80, 443):
            raise WebError(f"Puerto {port} no permitido (solo 80 y 443).")
        if not host_allowed(host, allowed):
            raise WebError(f"El dominio «{host}» no está en la lista de dominios permitidos (Ajustes → Internet).")
        ip = _resolve(host, port, allow_private)
        conn = (_PinnedTls if scheme == "https" else _Pinned)(host, port, ip, timeout)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        req_headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/json,text/plain,*/*;q=0.5", "Accept-Encoding": "gzip", "Connection": "close"}
        req_headers.update(headers or {})
        try:
            conn.request("GET", path, headers=req_headers)
            resp = conn.getresponse()
            if resp.status in _REDIRECTS and resp.getheader("Location"):
                current = urljoin(current, resp.getheader("Location"))
                continue
            body, truncated = _read_body(resp, max_bytes)
            return current, resp.status, (resp.getheader("Content-Type") or "").lower(), body, truncated
        except (OSError, http.client.HTTPException, zlib.error) as exc:      # ssl.SSLError es un OSError
            raise WebError(f"No se pudo leer «{host}»: {exc}") from exc
        finally:
            conn.close()
    raise WebError(f"Demasiadas redirecciones (más de {max_redirects}).")


# --------------------------------------------------------------------------- texto legible
_SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "head", "canvas", "form", "select", "button", "nav", "aside", "footer"}
_MAIN = {"main", "article"}
_BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "header", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "hr", "main"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.main: List[str] = []       # texto dentro de <main>/<article>: si hay bastante, es el contenido real de la página
        self.title = ""
        self._skip = 0
        self._main = 0
        self._in_title = False

    def _emit(self, text):
        self.parts.append(text)
        if self._main:
            self.main.append(text)

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag in _SKIP:
            self._skip += 1
        else:
            if tag in _MAIN:
                self._main += 1
            if tag in _BLOCK:
                self._emit("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK:
            self._emit("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        else:
            if tag in _MAIN:
                self._main = max(0, self._main - 1)
            if tag in _BLOCK:
                self._emit("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._emit(data)


def _decode(body: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w\-]+)", content_type)
    charset = m.group(1) if m else None
    if charset is None:
        m2 = re.search(rb"<meta[^>]+charset=[\"']?([\w\-]+)", body[:4096], re.I)
        charset = m2.group(1).decode("ascii", "ignore") if m2 else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def extract_text(body: bytes, content_type: str) -> Tuple[str, str]:
    """(título, texto) de un HTML; el texto plano y JSON se devuelven tal cual."""
    raw = _decode(body, content_type)
    if "html" not in content_type and not raw.lstrip().lower().startswith(("<!doctype html", "<html")):
        return "", raw.strip()
    parser = _Text()
    parser.feed(raw)
    main = "".join(parser.main)
    text = re.sub(r"[ \t\r\f\v]+", " ", main if len(main.strip()) >= 500 else "".join(parser.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r" ?\n ?", "\n", text)).strip()
    return " ".join(parser.title.split()), _drop_menu_lists(text)


def _drop_menu_lists(text: str) -> str:
    """Quita las listas de enlaces (menús, idiomas, categorías): párrafos cortos que no introducen un texto largo."""
    paras = text.split("\n\n")
    kept = [p for i, p in enumerate(paras) if len(p) >= 40 or (len(p) >= 2 and i + 1 < len(paras) and len(paras[i + 1]) >= 80)]
    return "\n\n".join(kept) if sum(map(len, kept)) >= 200 else text      # si casi no queda nada, mejor devolver todo


def fetch_page(url: str, *, max_chars: int = 8000, allowed_hosts: Optional[Iterable[str]] = None, allow_private: bool = False) -> Page:
    final, status, ctype, body, cut = fetch_raw(url, allowed_hosts=allowed_hosts, allow_private=allow_private)
    if status >= 400:
        raise WebError(f"La página respondió HTTP {status}.")
    if ctype and not any(t in ctype for t in TEXT_TYPES):
        raise WebError(f"Tipo de contenido no admitido ({ctype.split(';')[0]}): solo se lee texto, HTML y JSON.")
    title, text = extract_text(body, ctype)
    truncated = cut or len(text) > max_chars
    return Page(url=final, status=status, content_type=ctype.split(";")[0], title=title, text=text[:max_chars], truncated=truncated)
