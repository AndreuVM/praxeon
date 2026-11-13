"""Acceso a internet de solo lectura para los agentes: `web_search` (Brave) y `web_fetch` (texto de una página).

Los datos web son contexto, nunca instrucciones: llegan marcados como datos externos y no conceden ningún permiso.
Ver docs/plans/acceso_internet.md.
"""

from praxeon.webtools.fetcher import WebError, fetch_page
from praxeon.webtools.settings import WebSettingsStore
from praxeon.webtools.tools import WebTools

__all__ = ["WebError", "WebSettingsStore", "WebTools", "fetch_page"]
