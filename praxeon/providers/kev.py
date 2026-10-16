"""Proveedor System-1 local KEV (praxeon/providers/kev.py).

KEV es un supervisor semántico local de la misma familia que LAYA: devuelve las tres primitivas de decisión
(choice, score, noul) y se puede usar solo o como nivel de una cascada (ver ``praxeon.providers.router``).

Backends (igual que LAYA):
- 'auto': endpoint alojado si hay URL, paquete local ``kev`` si está instalado, y si no motor calibrado.
- 'local': paquete Python ``kev`` con pesos locales. Contrato esperado: ``kev.load_model(ruta)`` (o ``kev.Model(ruta)``)
  que devuelve un objeto con ``choice(prompt, options)``, ``score(prompt, scale)`` y ``noul(prompt)``.
- 'hosted': servicio HTTP propio (por ejemplo, uno local en 127.0.0.1) con el mismo esquema JSON que LAYA.
- 'simulated': motor determinista calibrado compartido con LAYA, sin pesos. Es el que se usa mientras no
  haya un paquete ``kev`` instalado, de modo que el proveedor funciona de extremo a extremo sin red.

Variables de entorno: ``KEV_ENDPOINT_URL``, ``KEV_AUTH_TOKEN`` y ``KEV_MODEL_PATH``.
"""

from __future__ import annotations

from praxeon.providers.laya import LayaProvider


class KevProvider(LayaProvider):
    """Supervisor System-1 local KEV (reutiliza la mecánica de primitivas de LAYA con su propia identidad)."""

    PROVIDER_ID = "kev"
    CODE_PREFIX = "KEV"
    SDK_MODULE = "kev"
    ENV_PREFIX = "KEV"
    DEFAULT_MODEL_PATH = "kev-v1"

    def __init__(self, model_name: str = "kev-v1-calibrated", **kwargs):
        super().__init__(model_name=model_name, **kwargs)


__all__ = ["KevProvider"]
