"""Búsqueda web con la API de Brave Search (https://api.search.brave.com)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List
from urllib.parse import urlencode

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
