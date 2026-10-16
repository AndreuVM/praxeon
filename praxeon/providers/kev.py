"""Supervisor System-1 KEV (praxeon/providers/kev.py).

KEV (https://github.com/jaredpalmer/kev) es una familia de modelos de decisión pequeños (Qwen3.5/3.8) que se sirve con
la API ``POST /v1/systemone`` de TypeSafe: en una sola petición responde preguntas ``choice``, ``noul`` y ``score``.
Este proveedor es un cliente de ese servidor y sustituye ``LAYA`` en la ruta rápida o en cualquier nivel de la cascada.
No simula nada: si el servidor no responde, informa de fallo y nunca concede.

Backends:
- ``local``: servidor KEV en este equipo (``python -m kev.serve --run jaredpalmer/kev-4b --port 8009``). URL por
  ``KEV_ENDPOINT_URL`` (por defecto ``http://127.0.0.1:8009``). Con ``KEV_AUTOSTART=1`` y ``KEV_HOME`` PRAXEON lo lanza
  él mismo (ver ``kev_server``).
- ``hosted``: un servidor KEV remoto (p. ej. el que despliega ``kev-deploy`` en Modal) con clave ``KEV_API_KEY``.

``pip install kev`` instala otro paquete distinto: KEV solo se instala desde su repositorio, y PRAXEON no lo importa.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from praxeon.providers.context import ProviderContext, ProviderContextBuilder
from praxeon.providers.kev_server import DEFAULT_PORT, KevServerManager, server_is_up
from praxeon.providers.laya import LayaDecisionPrimitives, LayaProvider
from praxeon.providers.resilience import CircuitBreaker
from praxeon.providers.systemone import DECISIONS, QUESTIONS, parse_answers  # noqa: F401 (re-exportados)

logger = logging.getLogger("praxeon.providers.kev")

DEFAULT_LOCAL_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
HEALTH_TTL_SECONDS = 15.0
class KevProvider(LayaProvider):
    """Cliente del servidor KEV con las mismas primitivas (choice, score, noul) que LAYA."""

    PROVIDER_ID = "kev"
    CODE_PREFIX = "KEV"

    def __init__(
        self,
        backend: str = "local",
        model_name: str = "kev-latest",
        endpoint_url: Optional[str] = None,
        api_key: Optional[str] = None,
        context_builder: Optional[ProviderContextBuilder] = None,
        max_context_tokens: int = 4096,
        timeout: float = 15.0,
        device: Optional[str] = None,
        server: Optional[KevServerManager] = None,
    ):
        self.name = self.PROVIDER_ID
        self.model_name = model_name
        self.device = device
        self.timeout = timeout
        self.max_context_tokens = max_context_tokens
        self.context_builder = context_builder or ProviderContextBuilder(default_max_tokens=max_context_tokens)
        self._is_warmed_up = False
        self.circuit_breaker = CircuitBreaker()
        self._health: Optional[tuple] = None

        requested = (backend or "local").lower()
        trusted_url = os.getenv("KEV_ENDPOINT_URL") or None
        if requested in ("hosted", "remote", "api") or (requested == "auto" and (endpoint_url or trusted_url)):
            self.backend = "hosted"
        else:
            self.backend = "local"
        self.requested_backend = requested

        self.server = server if server is not None else (KevServerManager.from_env() if self.backend == "local" else None)
        if endpoint_url:
            self.endpoint_url = endpoint_url.rstrip("/")
        elif self.server is not None:
            self.endpoint_url = self.server.base_url
        elif trusted_url:
            self.endpoint_url = trusted_url.rstrip("/")
        else:
            self.endpoint_url = DEFAULT_LOCAL_URL if self.backend == "local" else None

        # La clave del entorno solo viaja al servidor configurado por el administrador, nunca a una URL ajena.
        self.api_key = api_key or (
            os.getenv("KEV_API_KEY") if (endpoint_url is None or endpoint_url.rstrip("/") == (trusted_url or "").rstrip("/")) else None
        )

    # ------------------------------------------------------------------ disponibilidad
    def is_available(self) -> bool:
        if not self.endpoint_url:
            return False
        if self.server is not None and self.endpoint_url == self.server.base_url:
            return True  # el servidor se lanzará bajo demanda
        now = time.monotonic()
        if self._health and now - self._health[0] < HEALTH_TTL_SECONDS:
            return self._health[1]
        up = server_is_up(self.endpoint_url, timeout=min(self.timeout, 3.0))
        self._health = (now, up)
        return up

    def warmup(self) -> bool:
        try:
            self._call({"state": "warmup", "model": self.model_name, "questions": {"q": QUESTIONS["is_loop"]}})
            self._is_warmed_up = True
            return True
        except Exception as exc:
            logger.warning("Fallo en warmup de KEV (%s): %s", self.backend, exc)
            return False

    # ------------------------------------------------------------------ inferencia
    def _call(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self.endpoint_url:
            raise ValueError("KEV hosted sin endpoint: define KEV_ENDPOINT_URL o el endpoint del nivel.")
        if self.server is not None and self.endpoint_url == self.server.base_url and not self.server.ensure_ready():
            raise RuntimeError("El servidor local de KEV no arrancó (revisa .praxeon_cache/kev_server.log).")
        if not self.circuit_breaker.allow_request():
            raise RuntimeError("Circuit breaker abierto tras fallos reiterados del servidor KEV.")

        headers = {"Content-Type": "application/json", "User-Agent": "Praxeon/1.0.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.endpoint_url}/v1/systemone", data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self.circuit_breaker.record_failure(exc)
            detail = exc.read().decode("utf-8", "replace")[:300] if hasattr(exc, "read") else ""
            raise RuntimeError(f"KEV respondió {exc.code}: {detail}") from exc
        except Exception as exc:
            self.circuit_breaker.record_failure(exc)
            raise
        self.circuit_breaker.record_success()
        return data

    def _infer_primitives(self, ctx: ProviderContext) -> LayaDecisionPrimitives:
        data = self._call({"state": ctx.formatted_prompt, "model": self.model_name, "questions": QUESTIONS})
        return parse_answers(data)


__all__ = ["KevProvider", "QUESTIONS", "DEFAULT_LOCAL_URL"]
