"""Entrypoints CLI legacy obsoletos (jev-*) con advertencias de deprecación hacia praxeon*.

Especificación de Gobernanza PRAXEON 1.1 / Tarea DEP-01:
Proporciona retrocompatibilidad completa mientras advierte formalmente a los
operadores y herramientas de automatización sobre el reemplazo canónico praxeon*
y la eliminación programada para la versión 2.0.
"""

import sys
import warnings
from typing import Any, Callable


def _warn_and_delegate(legacy_cmd: str, canonical_cmd: str, target_fn: Callable[..., Any]) -> Any:
    """Emite advertencia formal de deprecación y delega en el comando canónico."""
    warning_message = (
        f"El binario o entrypoint '{legacy_cmd}' está obsoleto (deprecated) y será "
        f"eliminado definitivamente en PRAXEON v2.0. Por favor, utiliza el comando "
        f"canónico '{canonical_cmd}'."
    )
    warnings.warn(warning_message, DeprecationWarning, stacklevel=2)
    # Imprimir advertencia visible en stderr para ejecuciones interactivas de terminal
    print(f"\n[DEPRECATION WARNING] {warning_message}\n", file=sys.stderr)
    return target_fn()


def jev_nav_main() -> Any:
    """Entrypoint legacy para 'jev-nav' -> redirige a 'praxeon'."""
    from praxeon.cli import main
    return _warn_and_delegate("jev-nav", "praxeon", main)


def jev_live_main() -> Any:
    """Entrypoint legacy para 'jev-live' -> redirige a 'praxeon-live'."""
    from praxeon.live_agent import main
    return _warn_and_delegate("jev-live", "praxeon-live", main)


def jev_dash_main() -> Any:
    """Entrypoint legacy para 'jev-dash' -> redirige a 'praxeon-dash'."""
    from praxeon.dashboard import main
    return _warn_and_delegate("jev-dash", "praxeon-dash", main)


def jev_mcp_main() -> Any:
    """Entrypoint legacy para 'jev-mcp' -> redirige a 'praxeon-mcp'."""
    from praxeon.integrations.mcp.server import main
    return _warn_and_delegate("jev-mcp", "praxeon-mcp", main)
