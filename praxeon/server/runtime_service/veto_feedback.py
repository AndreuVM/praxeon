"""Explicación, en lenguaje llano, de por qué el supervisor no ejecutó una acción.

Con solo el código del motivo («SUPERVISOR_LAYA_DESTRUCTIVE_BLOCK…») el agente no sabía qué cambiar: repetía la acción,
se acumulaban fallos seguidos y la misión se cortaba. El mensaje dice qué se vetó, por qué y qué puede hacer ahora, sin
reutilizar el vocabulario interno del supervisor.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

_RULES = (
    (("DESTRUCTIVE", "CRITICAL_OPERATIONAL_RISK", "PRIVILEGE", "ADVERSARIAL"),
     "se considera destructiva o de alto riesgo (borra datos, cambia permisos, escala privilegios o ejecuta código construido)",
     "No la repitas. Si la tarea de verdad lo exige, termina con finish explicando qué necesitaría aprobar una persona; si no, "
     "usa una alternativa menos destructiva (por ejemplo, tocar solo los archivos concretos que hagan falta)."),
    (("HIGH_LOOP_PROBABILITY", "SEMANTIC_CIRCUIT_BREAKER", "LOOP"),
     "repite algo que ya hiciste sin que haya cambiado nada entre medias (mismo archivo o mismo comando)",
     "Su resultado ya está en el historial: úsalo. Si no te basta, cambia de enfoque (otro archivo, otro comando) o modifica "
     "algo antes de volver a comprobarlo."),
    (("PREMATURE_COMPLETION",),
     "intentas terminar sin haber consultado nada del proyecto",
     "Inspecciona primero los archivos relevantes (list_dir, read_file o un comando) y vuelve a llamar a finish con lo que veas."),
    (("LOW_GROUNDED", "UNGROUNDED", "MISSING_REQUIRED_EVIDENCE"),
     "no se apoya en nada que hayas observado (una ruta o un dato que no has visto)",
     "Mira antes lo que existe con list_dir o read_file y trabaja solo con eso."),
    (("UNNECESSARY_ACTION",),
     "se considera innecesaria o desviada del objetivo",
     "Comprueba si aporta algo a la tarea; si no, pasa al siguiente paso."),
)


def _short_action(tool: str, args: Optional[Dict[str, Any]], limit: int = 160) -> str:
    text = json.dumps(args or {}, ensure_ascii=False)
    return f"{tool}({text[:limit]}{'…' if len(text) > limit else ''})"


def explain_veto(status: str, reason_codes: Optional[List[str]], tool: str, args: Optional[Dict[str, Any]] = None) -> str:
    """Mensaje para el agente tras un BLOCK o un REPLAN."""
    codes = " ".join(str(c).upper() for c in (reason_codes or []))
    why, what = None, None
    for needles, reason, advice in _RULES:
        if any(n in codes for n in needles):
            why, what = reason, advice
            break
    if why is None:
        why = "el supervisor la rechazó" if status == "BLOCK" else "el supervisor pide otra aproximación"
        what = "No repitas la misma acción: elige un paso distinto."
    return f"La acción {_short_action(tool, args)} NO se ha ejecutado: {why}. {what}"
