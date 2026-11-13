"""Lectura de variables de entorno con el prefijo PRAXEON_ y compatibilidad con el prefijo histórico JEV_ (auditoría A-08).

Módulo sin dependencias ni efectos al importarse, para poder usarlo desde cualquier capa.
"""

from __future__ import annotations

import os
import warnings


def env_setting(name: str, default: str) -> str:
    """Lee `PRAXEON_<name>`; acepta el nombre histórico `JEV_<name>` con un aviso de obsolescencia (se retira en v2.0.0)."""
    value = os.environ.get(f"PRAXEON_{name}")
    if value is not None:
        return value
    legacy = os.environ.get(f"JEV_{name}")
    if legacy is not None:
        warnings.warn(
            f"La variable de entorno JEV_{name} está en desuso; use PRAXEON_{name} (se retira en v2.0.0).",
            DeprecationWarning,
            stacklevel=2,
        )
        return legacy
    return default
