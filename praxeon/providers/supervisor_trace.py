"""Volcado local de lo que ve y decide el supervisor en cada paso (materia prima del ajuste fino).

Se activa con `PRAXEON_TRACE_SUPERVISOR=1` (archivo por defecto `data/supervisor_trace.jsonl`) o con una ruta. Cada fila lleva
el estado exacto que recibe el modelo, las preguntas, la decisión cruda y el contexto de la ejecución (etiqueta del
benchmark). Todo se redacta antes de escribir: rutas personales, claves y tokens. Nunca sale del equipo.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

TRACE_ENV = "PRAXEON_TRACE_SUPERVISOR"
RUN_LABEL_ENV = "PRAXEON_TRACE_LABEL"
DEFAULT_PATH = Path("data") / "supervisor_trace.jsonl"
_LOCK = threading.Lock()

_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)\b((?:api[_-]?key|secret|token|password|passwd|pwd)\s*[=:]\s*)(['\"]?)[^\s'\"]{6,}"),
)


def trace_path() -> Optional[Path]:
    value = (os.getenv(TRACE_ENV) or "").strip()
    if not value or value.lower() in ("0", "false", "no", "off"):
        return None
    return DEFAULT_PATH if value.lower() in ("1", "true", "yes", "on") else Path(value)


def redact(text: str) -> str:
    """Quita secretos y datos personales del texto (nombre de usuario en rutas, claves, tokens)."""
    if not text:
        return text
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(lambda m: (m.group(1) + m.group(2) + "[REDACTED]") if m.re.groups >= 2 else "[REDACTED]", out)
    user = os.getenv("USERNAME") or os.getenv("USER") or ""
    home = str(Path.home())
    for needle in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
        if needle:
            out = out.replace(needle, "~")
    if len(user) >= 3:
        out = re.sub(re.escape(user), "[USER]", out, flags=re.I)
    return out


def record_supervisor_step(*, state: str, questions: Any, primitives: Any, ctx: Any, provider: str, backend: str,
                           extra: Optional[Dict[str, Any]] = None) -> None:
    """Añade una fila al volcado si está activado. Nunca falla: el volcado no puede romper una decisión."""
    path = trace_path()
    if path is None:
        return
    try:
        cand = getattr(ctx, "candidate_action", None) or {}
        row: Dict[str, Any] = {
            "ts": round(time.time(), 3),
            "label": os.getenv(RUN_LABEL_ENV, ""),
            "provider": provider,
            "backend": backend,
            "state": redact(state),
            "questions": questions,
            "tool": cand.get("tool_name"),
            "arguments": json.loads(redact(json.dumps(cand.get("arguments") or {}, ensure_ascii=False, default=str))),
            "choice": getattr(primitives, "choice", None),
            "score": getattr(primitives, "score", None),
            "noul": getattr(primitives, "noul", None),
        }
        if extra:
            row.update(extra)
        line = json.dumps(row, ensure_ascii=False, default=str)
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass
