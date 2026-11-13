"""PRAXEON

Runtime supervision for autonomous AI agents.
"""

__version__ = "1.2.0"   # fuente única de la versión (servidor, /health, métricas, OpenAPI)

from praxeon.config import PraxeonConfig, JEVConfig, default_config

__all__ = [
    "__version__",
    "PraxeonConfig",
    "JEVConfig",
    "default_config",
]
