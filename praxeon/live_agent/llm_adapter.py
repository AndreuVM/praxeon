"""Adaptador de LLM y utilidades de procesamiento de texto para el Live Agent de PRAXEON.

Maneja decodificación de procesos, truncado inteligente de observaciones,
compactación de memoria histórica y extracción estructurada de pasos (Thought/Action).
"""

from __future__ import annotations

import io
import json
import re
import sys
from typing import Any, Dict, List, Optional

# Asegurar codificación UTF-8 en Windows para evitar errores con charmap cp1252
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer") and sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def decode_process_bytes(raw_bytes: bytes) -> str:
    """Decodifica de manera inteligente la salida binaria de procesos en Windows y Linux evitando caracteres corruptos."""
    if not raw_bytes:
        return ""
    try:
        text = raw_bytes.decode("utf-8")
        if "\ufffd" not in text:
            return text
    except UnicodeDecodeError:
        pass
    for enc in ("cp1252", "cp850", "latin-1"):
        try:
            return raw_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def prune_observation_output(output: str, max_chars: int = 2000) -> str:
    """Trunca salidas extensas de comandos preservando el inicio y el final relevante para optimizar tokens."""
    cleaned = (output or "Comando ejecutado sin salida").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    keep_head = int(max_chars * 0.65)
    keep_tail = int(max_chars * 0.35)
    omitted = len(cleaned) - (keep_head + keep_tail)
    return (
        f"{cleaned[:keep_head]}\n\n"
        f"[... {omitted} caracteres intermedios omitidos por JEV Token Pruner para optimizar contexto ...]\n\n"
        f"{cleaned[-keep_tail:]}"
    )


def compact_conversation_history(
    conversation_history: List[Dict[str, str]],
    context_manager: Optional[Any] = None,
    max_recent_turns: int = 4,
) -> List[Dict[str, str]]:
    """Compacta el historial conversacional utilizando ContextManager o heurística interna.

    Mantiene el prompt de sistema y objetivo inicial, compacta los pasos
    intermedios antiguos en un resumen estructurado y preserva los turnos recientes.
    """
    max_recent_msgs = max_recent_turns * 2
    if len(conversation_history) <= (2 + max_recent_msgs):
        return list(conversation_history)

    header = conversation_history[:2]
    intermediate = conversation_history[2:-max_recent_msgs]
    recent = conversation_history[-max_recent_msgs:]

    summary_lines = []
    for msg in intermediate:
        role = msg.get("role", "")
        content = msg.get("content", "").strip()
        if role == "assistant":
            for line in content.splitlines():
                if line.lower().startswith("action:"):
                    summary_lines.append(f"• Ejecutado: {line[7:].strip()}")
                    break
        elif role == "user":
            first_line = content.splitlines()[0] if content.splitlines() else content
            if len(first_line) > 160:
                first_line = first_line[:150] + "..."
            summary_lines.append(f"  Observación: {first_line}")

    summary_content = "\n".join(summary_lines)
    summary_msg = {
        "role": "user",
        "content": (
            f"📋 [MEMORIA INTERMEDIA COMPACTADA POR PRAXEON CONTEXT MANAGER ({len(intermediate)//2} pasos)]:\n"
            f"{summary_content}\n"
            "Continúa la resolución a partir de este punto."
        ),
    }

    return header + [summary_msg] + recent


def build_optimized_prompt(conversation_history: List[Dict[str, str]], max_recent_turns: int = 8) -> str:
    """Construye el prompt optimizado para el LLM aplicando compresión a turnos antiguos si la conversación es larga."""
    compacted = compact_conversation_history(conversation_history, max_recent_turns=max_recent_turns)
    return "\n\n".join(f"[{m['role'].upper()}]: {m['content']}" for m in compacted) + "\n\n[ASSISTANT]:\n"


def parse_llm_steps(llm_output: str) -> List[Dict[str, Any]]:
    """Extrae uno o múltiples pasos (Thought + Action) de la salida del LLM."""
    steps: List[Dict[str, Any]] = []

    # 1. Normalizar etiquetas markdown (**Thought:**, **Action:**, etc.)
    text = re.sub(r"[\*_]{1,2}(Step\s+\d+|Paso\s+\d+|Thought|Action)[\*_]{0,2}\s*:", r"\1:", llm_output, flags=re.IGNORECASE)
    # 2. Insertar saltos de línea antes de palabras clave si vienen en línea continua o sin salto
    text = re.sub(r"(?i)(?<!\n)\s*(action\s*:)", r"\n\1", text)
    text = re.sub(r"(?i)(?<!\n)\s*(thought\s*:)", r"\n\1", text)
    text = re.sub(r"(?i)(?<!\n)\s*(step\s+\d+\s*:)", r"\n\1", text)
    text = re.sub(r"(?i)(?<!\n)\s*(paso\s+\d+\s*:)", r"\n\1", text)

    current_thought = ""
    current_tool = None
    current_args: Dict[str, Any] = {}

    lines = text.strip().splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        lower_line = stripped.lower()
        if lower_line.startswith("thought:"):
            if current_tool:
                steps.append({
                    "thought_rationale": current_thought,
                    "tool_name": current_tool,
                    "tool_args": current_args,
                })
                current_thought = ""
                current_tool = None
                current_args = {}
            current_thought = stripped[len("thought:"):].strip()
        elif lower_line.startswith("action:"):
            action_content = stripped[len("action:"):].strip()
            # 1. Comprobar sintaxis estilo llamada de función: tool_name(...)
            m = re.match(r"^([a-zA-Z0-9_]+)\s*\((.*)\)$", action_content, re.DOTALL)
            if m:
                current_tool = m.group(1).strip()
                raw_arg = m.group(2).strip()
                try:
                    parsed = json.loads(raw_arg)
                    if isinstance(parsed, dict):
                        current_args = parsed
                    elif isinstance(parsed, str):
                        clean_str = parsed.strip()
                        if current_tool == "run_command":
                            current_args = {"command": clean_str}
                        elif current_tool in ("read_file", "view_file"):
                            current_args = {"path": clean_str}
                        elif current_tool == "finish":
                            current_args = {"summary": clean_str}
                        else:
                            current_args = {"raw": clean_str}
                    else:
                        current_args = {"raw": str(parsed)}
                except Exception:
                    # Detectar si viene con sintaxis Python kwargs: tool(param1="val1", param2="val2")
                    kw_pairs = re.findall(r'([a-zA-Z0-9_]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s,)]+))', raw_arg)
                    if kw_pairs:
                        current_args = {}
                        for k, v1, v2, v3 in kw_pairs:
                            current_args[k] = v1 or v2 or v3
                    else:
                        clean_arg = raw_arg.strip('"').strip("'")
                        for prefix in ("path=", "command=", "summary=", "diff="):
                            if clean_arg.startswith(prefix):
                                clean_arg = clean_arg[len(prefix):].strip('"').strip("'")
                                break
                        if current_tool == "run_command":
                            current_args = {"command": clean_arg}
                        elif current_tool in ("read_file", "view_file"):
                            current_args = {"path": clean_arg}
                        elif current_tool == "finish":
                            current_args = {"summary": clean_arg}
                        else:
                            current_args = {"raw": clean_arg, "command": clean_arg}
            else:
                # 2. Sintaxis estándar: tool_name <json_o_string>
                parts = action_content.split(maxsplit=1)
                if parts:
                    current_tool = parts[0].strip()
                    if len(parts) > 1:
                        raw_arg = parts[1].strip()
                        try:
                            parsed = json.loads(raw_arg)
                            if isinstance(parsed, dict):
                                current_args = parsed
                            else:
                                current_args = {"raw": str(parsed)}
                        except Exception:
                            kw_pairs = re.findall(r'([a-zA-Z0-9_]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s,)]+))', raw_arg)
                            if kw_pairs:
                                current_args = {}
                                for k, v1, v2, v3 in kw_pairs:
                                    current_args[k] = v1 or v2 or v3
                            else:
                                clean_arg = raw_arg.strip('"').strip("'")
                                for prefix in ("path=", "command=", "summary=", "diff="):
                                    if clean_arg.startswith(prefix):
                                        clean_arg = clean_arg[len(prefix):].strip('"').strip("'")
                                        break
                                if current_tool == "run_command":
                                    current_args = {"command": clean_arg}
                                elif current_tool in ("read_file", "view_file"):
                                    current_args = {"path": clean_arg}
                                elif current_tool == "finish":
                                    current_args = {"summary": clean_arg}
                                else:
                                    current_args = {"raw": clean_arg, "command": clean_arg}
                    else:
                        current_args = {}
        elif (lower_line.startswith("step ") or lower_line.startswith("paso ")) and ":" in stripped:
            if current_tool:
                steps.append({
                    "thought_rationale": current_thought,
                    "tool_name": current_tool,
                    "tool_args": current_args,
                })
                current_thought = ""
                current_tool = None
                current_args = {}

    if current_tool:
        steps.append({
            "thought_rationale": current_thought,
            "tool_name": current_tool,
            "tool_args": current_args,
        })
    elif current_thought:
        # Solo interpretar como 'finish' si el pensamiento es explícitamente concluyente
        lower_th = current_thought.lower()
        is_conclusion = any(w in lower_th for w in ("conclu", "finaliz", "resuelt", "solución", "solucion", "terminad", "completad", "finish"))
        if is_conclusion:
            steps.append({
                "thought_rationale": current_thought,
                "tool_name": "finish",
                "tool_args": {"summary": current_thought},
            })
    elif not steps and llm_output.strip():
        lower_out = llm_output.lower()
        if any(w in lower_out for w in ("conclu", "finaliz", "resuelt", "solución", "solucion", "terminad", "completad")):
            steps.append({
                "thought_rationale": "Conclusión directa emitida por el agente LLM",
                "tool_name": "finish",
                "tool_args": {"summary": llm_output.strip()},
            })

    return steps
