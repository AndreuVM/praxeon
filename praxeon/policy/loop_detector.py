"""Detección de bucles por reglas, compartida por todos los supervisores.

Es una propiedad del arnés, no de ningún modelo: un bucle es repetir la MISMA acción sobre el MISMO objetivo sin que nada
haya cambiado entre medias. Los modelos System-1 separan mal «bucle» de «paso nuevo», así que esta señal la calcula el
arnés para cualquier proveedor (LAYA, KEV, TypeSafe, mock…) y la política toma el mayor valor. Así la comparación entre
supervisores mide el modelo, no quién trae reglas de bucle.

Reglas:
- Un cambio de estado (escribir un archivo o ejecutar algo que modifica) invalida lo observado antes: releer un archivo
  o volver a lanzar los tests DESPUÉS de editar no es repetir.
- Una repetición aislada es un reintento razonable (0,5, por debajo del umbral de veto); la segunda ya es un bucle (0,88).
- Estancamiento: fallos seguidos SIN cambiar nada entre medias.
- Las palabras del propio agente («reintentar», «bucle»…) no deciden nada.

Es la detección canónica de bucles del runtime (pipeline de propuestas, LAYA, generador de fine-tuning).
``praxeon.reasoning.loop_detector`` es otra cosa: el análisis de anomalías de las ramas del árbol de búsqueda.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from praxeon.reasoning.observations import is_failure_observation

if TYPE_CHECKING:
    from praxeon.reasoning.classifier import CommandClassifier

WRITE_TOOLS = ("edit_file", "write_file", "create_file", "write_to_file", "save_file", "replace_file_content")
SHELL_TOOLS = ("run_command", "run_script", "git")

ISOLATED_REPEAT = 0.50
CONFIRMED_LOOP = 0.88
STAGNATION = 0.85


def _target(args: dict[str, Any]) -> str:
    return str(args.get("path") or args.get("file") or args.get("command") or args.get("cmd") or args.get("raw")
               or args.get("query") or "").strip().lower()


def _content(args: dict[str, Any]) -> str:
    return str(args.get("content") or args.get("diff") or "")


def is_mutation(tool: str, args: dict[str, Any]) -> bool:
    """¿Esta acción cambia el estado del proyecto (escribe un archivo o ejecuta algo que modifica)?"""
    tool = (tool or "").lower()
    if tool in WRITE_TOOLS:
        return True
    if tool in SHELL_TOOLS:

        command = str((args or {}).get("command") or (args or {}).get("cmd") or (args or {}).get("raw") or "")
        return bool(command) and not _classifier().classify(command).read_only
    return False


_CLASSIFIER: CommandClassifier | None = None


def _classifier() -> CommandClassifier:
    global _CLASSIFIER
    if _CLASSIFIER is None:
        from praxeon.reasoning.classifier import CommandClassifier

        _CLASSIFIER = CommandClassifier()
    return _CLASSIFIER


def _is_failure(observation: str) -> bool:
    """Criterio único del arnés (``praxeon.reasoning.observations``): un mensaje de fallo es corto o empieza como tal; el contenido de un
    archivo largo puede contener «error» o «failed» (código, tests, logs) sin que la lectura haya fallado."""
    return is_failure_observation(observation)


def estimate_loop_probability(history: list[dict[str, Any]], tool_name: str, args: dict[str, Any]) -> float:
    """Probabilidad de que la acción candidata sea un bucle, dado el historial (dicts con tool, arguments, observation)."""
    tool_name = (tool_name or "").lower()
    args = args or {}
    history = [h for h in history if isinstance(h, dict)]
    my_target = _target(args)

    candidate_mutates = is_mutation(tool_name, args)
    last_change = max((i for i, h in enumerate(history) if is_mutation(str(h.get("tool") or ""), h.get("arguments") or {})), default=-1)
    window = history if candidate_mutates else history[last_change + 1:]

    same = [h for h in window if (h.get("tool") or "").lower() == tool_name and _target(h.get("arguments") or {}) == my_target]
    if tool_name in WRITE_TOOLS:
        # Reescribir un archivo con contenido distinto es refinar; solo el contenido idéntico es repetición
        same = [h for h in same if _content(h.get("arguments") or {}) == _content(args)]
    repetitions = len(same) if (tool_name and my_target) else 0

    loop = 0.05
    if repetitions >= 2:
        loop = CONFIRMED_LOOP
    elif repetitions >= 1:
        loop = ISOLATED_REPEAT

    failures = [obs for obs in (str(h.get("observation", "")) for h in window[-3:]) if _is_failure(obs)]
    if len(failures) >= 2 and repetitions >= 1:
        loop = max(loop, STAGNATION)
    return loop
