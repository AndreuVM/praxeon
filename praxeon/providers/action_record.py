"""Registro canónico de la acción propuesta: lo que ve un supervisor System-1 en lugar del razonamiento libre del agente.

El `Thought` del LLM como «descripción» de la acción es un mal insumo para un modelo pequeño: es largo, hace casi uniformes
sus probabilidades, repite el vocabulario de los vetos anteriores («la acción fue considerada destructiva») y lo controla
el propio agente (una inyección en un fichero puede escribir ahí lo que quiera). Este registro lo construye el arnés a partir
de hechos comprobables: herramienta y argumentos, ámbito, si la ruta se vio antes, repeticiones, último resultado y, opcionalmente,
la clasificación determinista del comando.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_READ_TOOLS = {"read_file", "view_file", "list_dir", "list_directory", "ls", "dir", "tree", "list_files"}
_WRITE_TOOLS = {"write_file", "edit_file", "create_file", "write_to_file", "save_file", "replace_file_content"}
_ABSOLUTE = re.compile(r"^([A-Za-z]:[\\/]|/|\\\\|~)")
_ARGS_CHARS = 400

_classifier: Optional[Any] = None


def _classify(command: str):
    global _classifier
    if _classifier is None:
        from praxeon.reasoning.classifier import CommandClassifier

        _classifier = CommandClassifier()
    return _classifier.classify(command)


def _target(args: Dict[str, Any]) -> str:
    return str(args.get("path") or args.get("file") or args.get("command") or args.get("cmd") or args.get("raw") or "").strip()


def _scope(path: str) -> str:
    if not path:
        return "n/a"
    if _ABSOLUTE.match(path) or ".." in path.replace("\\", "/").split("/"):
        return "FUERA del espacio de trabajo"
    return "dentro del espacio de trabajo"


def _yn(value: bool) -> str:
    return "sí" if value else "no"


def build_action_record(candidate: Dict[str, Any], history: List[Dict[str, Any]], with_classifier: bool = True) -> str:
    """Texto del registro canónico (varias líneas cortas, siempre con la misma estructura)."""
    tool = str(candidate.get("tool_name") or "")
    args = candidate.get("arguments") or {}
    args_text = ", ".join(f"{k}={str(v)[:_ARGS_CHARS]!r}" for k, v in args.items()) or ""
    target = _target(args)
    same = [h for h in history if h.get("tool") == tool and _target(h.get("arguments") or {}) == target and target]
    seen_text = " ".join(str(h.get("observation") or "") for h in history) + " ".join(_target(h.get("arguments") or {}) for h in history)
    base = target.replace("\\", "/").rsplit("/", 1)[-1]
    seen = bool(base) and base in seen_text
    last = history[-1] if history else None
    last_obs = str((last or {}).get("observation") or "")
    last_result = "ninguno" if last is None else ("error" if last_obs.lower().startswith(("error", "[código de salida")) or "error" in last_obs[:40].lower() else "ok")

    facts = [f"herramienta: {tool}", f"paso: {len(history) + 1}", f"repeticiones exactas previas: {len(same)}",
             f"último resultado: {last_result}"]
    if tool in _READ_TOOLS or tool in _WRITE_TOOLS:
        facts.insert(1, f"ámbito: {_scope(target)}")
        facts.insert(2, f"ruta vista antes: {_yn(seen)}")
        facts.insert(3, "tipo: lectura" if tool in _READ_TOOLS else "tipo: escritura de archivo")
    elif with_classifier and target:
        a = _classify(target)
        facts.insert(1, f"categoría: {a.category.name} | riesgo: {a.risk_level.name} | solo lectura: {_yn(a.read_only)} | "
                        f"destructiva: {_yn(a.destructive)} | red: {_yn(a.network_access)} | privilegios: {_yn(a.privilege_escalation)}")
    elif target:
        facts.insert(1, f"ámbito: {_scope(target.split()[0] if target else '')}")
    return f"ACCIÓN PROPUESTA: {tool}({args_text})\n  " + " | ".join(facts)
