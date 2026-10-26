"""Búsqueda web con la API de Brave Search (https://api.search.brave.com)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Optional
from urllib.parse import parse_qs, urlencode, urlsplit

from praxeon.web.fetcher import WebError, fetch_raw

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_TAGS = re.compile(r"<[^>]+>")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    age: str = ""


class BraveSearch:
    name = "Brave"

    def __init__(self, api_key: str, endpoint: str = ENDPOINT, allow_private: bool = False) -> None:
        self._key = api_key
        self._endpoint = endpoint
        self._allow_private = allow_private      # solo para pruebas con un servidor local

    def search(self, query: str, count: int = 5) -> List[SearchResult]:
        if not self._key:
            raise WebError("Falta la clave de Brave Search (Ajustes → Internet, o la variable BRAVE_API_KEY).")
        url = f"{self._endpoint}?{urlencode({'q': query, 'count': max(1, min(int(count), 10))})}"
        _, status, _, body, _ = fetch_raw(url, headers={"Accept": "application/json", "X-Subscription-Token": self._key}, allow_private=self._allow_private)
        if status in (401, 403):
            raise WebError(f"Brave rechazó la clave (HTTP {status}): revísala en Ajustes → Internet.")
        if status == 429:
            raise WebError("Brave Search: se agotó la cuota o hay demasiadas peticiones (HTTP 429).")
        if status >= 400:
            raise WebError(f"Brave Search respondió HTTP {status}.")
        try:
            items = (json.loads(body.decode("utf-8", "replace")).get("web") or {}).get("results") or []
        except ValueError as exc:
            raise WebError("Respuesta de Brave Search no válida.") from exc
        return [
            SearchResult(
                title=_TAGS.sub("", str(i.get("title") or "")).strip(),
                url=str(i.get("url") or ""),
                snippet=" ".join(_TAGS.sub("", str(i.get("description") or "")).split()),
                age=str(i.get("age") or i.get("page_age") or ""),
            )
            for i in items if i.get("url")
        ]


DDG_ENDPOINT = "https://html.duckduckgo.com/html/"


class _DdgParser(HTMLParser):
    """Extrae (título, url, extracto) de la página HTML de DuckDuckGo; ignora los anuncios."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: List[SearchResult] = []
        self._field: Optional[str] = None      # "title" | "snippet" mientras se lee ese enlace
        self._buf: List[str] = []
        self._href = ""
        self._ad = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "div" and "result--ad" in cls:
            self._ad = True
        if tag == "a" and "result__a" in cls:
            self._field, self._buf, self._href = "title", [], a.get("href") or ""
        elif tag in ("a", "div") and "result__snippet" in cls:
            self._field, self._buf = "snippet", []

    def handle_data(self, data):
        if self._field:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if self._field is None or tag not in ("a", "div"):
            return
        text = " ".join("".join(self._buf).split())
        if self._field == "title":
            url = _real_url(self._href)
            if url and not self._ad:
                self.items.append(SearchResult(title=text, url=url, snippet=""))
            self._ad = False
        elif self.items and not self.items[-1].snippet:
            self.items[-1].snippet = text
        self._field = None


def _real_url(href: str) -> str:
    """Los enlaces de DuckDuckGo pasan por //duckduckgo.com/l/?uddg=<url real>; se devuelve la real."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if parts.netloc.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        target = (parse_qs(parts.query).get("uddg") or [""])[0]
        return target if target.startswith(("http://", "https://")) else ""
    if parts.netloc.endswith("duckduckgo.com"):
        return ""           # enlaces internos y anuncios (y.js)
    return href if href.startswith(("http://", "https://")) else ""


class DuckDuckGoSearch:
    """Búsqueda sin clave a través de la página HTML de DuckDuckGo. No es una API oficial: si DuckDuckGo cambia su HTML o limita
    las peticiones automáticas, falla con un mensaje claro y `auto` pasa a la clave de Brave si existe."""

    name = "DuckDuckGo"

    def __init__(self, endpoint: str = DDG_ENDPOINT, allow_private: bool = False) -> None:
        self._endpoint = endpoint
        self._allow_private = allow_private

    def search(self, query: str, count: int = 5) -> List[SearchResult]:
        url = f"{self._endpoint}?{urlencode({'q': query})}"
        _, status, _, body, _ = fetch_raw(url, headers={"Accept": "text/html", "Accept-Language": "es-ES,es;q=0.9,en;q=0.5"}, allow_private=self._allow_private)
        if status != 200:
            raise WebError(f"DuckDuckGo respondió HTTP {status} (puede estar limitando las búsquedas automáticas).")
        parser = _DdgParser()
        parser.feed(body.decode("utf-8", "replace"))
        if not parser.items and re.search(r"anomaly|captcha|unusual traffic", body.decode("utf-8", "replace")[:200000], re.I):
            raise WebError("DuckDuckGo pide verificación (captcha): ahora mismo no permite búsquedas automáticas.")
        return parser.items[: max(1, min(int(count), 10))]
