"""¿Una observación de herramienta es un FALLO o solo CONTIENE palabras de fallo? Criterio único del arnés.

El supervisor decidía «esto falló» buscando «error», «no existe»… en cualquier parte de la observación. Pero la observación de leer un
documento o de ejecutar un comando es el CONTENIDO de lo leído: una guía que dice «los diagramas aún no existen», un log con «error»
o un test con «failed» no son un fallo de la lectura. Efectos reales (misión de edición de documentación): una lectura correcta no contaba
como evidencia, así que las ediciones y el `finish` posteriores se vetaban por «falta de evidencia», y el cortacircuitos anunciaba «fallos
consecutivos intentando acceder a rutas que no existen» sobre archivos que existían.

Los mensajes de fallo de las herramientas son cortos o empiezan por «error»/«fallo»/«traceback»; el contenido de un archivo largo no.
"""

from __future__ import annotations

FAILURE_WORDS = ("no existe", "no encontrado", "error", "failed", "fail", "not found")
FAILURE_PREFIXES = ("error", "fallo", "traceback", "❌")
SHORT_FAILURE_MAX_CHARS = 300


def is_failure_observation(observation: str | None) -> bool:
    """True si la observación es un mensaje de fallo (corto, o que empieza como tal); False si solo menciona esas palabras dentro de un contenido."""
    obs = (observation or "").strip().lower()
    if not any(word in obs for word in FAILURE_WORDS):
        return False
    return len(obs) <= SHORT_FAILURE_MAX_CHARS or obs.startswith(FAILURE_PREFIXES)


def reports_missing_path(observation: str | None) -> bool:
    """True si la observación es un fallo que dice que una ruta no existe (no si el contenido leído menciona «no existe»/«no existen»)."""
    return is_failure_observation(observation) and "no existe" in (observation or "").lower()
