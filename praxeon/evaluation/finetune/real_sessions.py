"""Reconstruye, desde los `events.db` de sesiones reales, el estado que habría visto el supervisor en cada acción.

Salida: un caso por acción propuesta con el estado ya redactado, lo que decidió entonces la política, el desenlace de la
ejecución y la respuesta humana si la hubo. No son etiquetas: son la materia prima que luego etiqueta el oráculo y revisa
una persona. Todo es local; el texto pasa por `supervisor_trace.redact` antes de guardarse.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

from praxeon.providers.action_record import build_action_record
from praxeon.providers.context import ProviderContext
from praxeon.providers.laya import _STATE_FITS, build_laya_state
from praxeon.providers.supervisor_trace import redact
from praxeon.reasoning.classifier import CommandClassifier

REAL_MARKERS = ("CLAUDE_CLI", "GEMINI", "OLLAMA", "OPENAI", "ANTHROPIC")
_CLASSIFIER = CommandClassifier()


def is_real_llm(source: Any, include_simulator: bool = False) -> bool:
    text = str(source or "")
    if "SIMULATOR" in text:
        return include_simulator
    return text.startswith("LLM (") and any(m in text for m in REAL_MARKERS)


def _payload(raw: str) -> Dict[str, Any]:
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _text(value: Any, limit: int = 2000) -> str:
    if isinstance(value, str):
        return value[:limit]
    return json.dumps(value, ensure_ascii=False, default=str)[:limit]


def _command_of(tool: str, args: Dict[str, Any]) -> str:
    return str(args.get("command") or args.get("cmd") or args.get("raw") or "") if tool in ("run_command", "run_script", "git") else ""


def extract_session(session_id: str, events: List[sqlite3.Row], *, only_real: bool = True, include_simulator: bool = False,
                    db_label: str = "", renderer: Optional[Callable[[ProviderContext], str]] = None) -> Iterator[Dict[str, Any]]:
    goal = ""
    in_workflow = False
    mode = "local_restricted"
    by_action: Dict[str, Dict[str, Any]] = defaultdict(dict)
    approvals: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for e in events:
        p, t, node = _payload(e["payload"]), e["type"], e["node_id"]
        if t == "goal.created" and not goal:
            goal = str(p.get("goal") or "")
        elif t == "workflow.node_started":
            in_workflow = True
        elif t == "session.started":
            mode = str(p.get("execution_mode") or mode)
        elif t == "action.proposed":
            order.append(node)
            by_action[node].update(proposed=p, decision_id=e["decision_id"], sequence=e["sequence"])
        elif t == "policy.decided" and e["parent_id"] in by_action:
            by_action[e["parent_id"]]["policy"] = p
        elif t == "operation.classified":
            by_action[node]["classified"] = p
        elif t == "provider.evaluated":
            by_action[node]["provider"] = p
        elif t == "execution.completed":
            by_action[node]["execution"] = p
        elif t == "observation.recorded":
            by_action[node]["observation"] = p
        elif t == "approval.completed" and e["decision_id"]:
            approvals[e["decision_id"]] = p

    history: List[Dict[str, Any]] = []
    for index, node in enumerate(order, start=1):
        info = by_action[node]
        proposed = info.get("proposed", {})
        tool = str(proposed.get("tool") or "")
        args = proposed.get("arguments") if isinstance(proposed.get("arguments"), dict) else {}
        source = proposed.get("source")
        real = is_real_llm(source, include_simulator)
        if (real or not only_real) and tool:
            cand = {"id": node, "description": str(proposed.get("operation") or ""), "tool_name": tool, "arguments": args}
            ctx = ProviderContext(session_id=session_id, goal=goal or "(sin objetivo)", success_criteria=[],
                                  history_window=list(history), candidate_action=cand,
                                  metadata={"environment": f"cwd=., mode={mode}"})
            steps, chars = _STATE_FITS[0]
            command = _command_of(tool, args)
            flags = _CLASSIFIER.classify(command) if command else None
            execution, observation = info.get("execution", {}), info.get("observation", {})
            policy = info.get("policy", {})
            approval = approvals.get(info.get("decision_id") or "")
            yield {
                "id": f"{db_label}:{session_id}:{node}",
                "origin": "real", "workflow": proposed.get("workflow_node") or None, "source": str(source), "db": db_label, "session": session_id, "step": proposed.get("step"),
                "goal": redact(goal), "tool": tool, "arguments": json.loads(redact(json.dumps(args, ensure_ascii=False, default=str))),
                "state": redact(renderer(ctx) if renderer else build_laya_state(ctx, steps, chars)),
                "history_len": len(history), "in_workflow_session": in_workflow,
                "observed": {
                    "policy_status": str(policy.get("status") or "").upper() or None,
                    "reason": policy.get("reason_code") or policy.get("reason_codes"),
                    "provider": info.get("provider"),
                    "executed": bool(execution),
                    "success": execution.get("success") if execution else None,
                    "approved_by_human": (approval or {}).get("approved") if approval else None,
                    "output": redact(_text(observation.get("output"), 600)) if observation else "",
                },
                "classifier": ({"category": flags.category.name, "risk": flags.risk_level.name, "read_only": flags.read_only,
                                "destructive": flags.destructive, "network": flags.network_access, "rules": list(flags.matched_rules)}
                               if flags else None),
            }
        if info.get("observation") or info.get("execution"):
            history.append({"id": len(history) + 1, "tool": tool, "arguments": args,
                            "observation": _text((info.get("observation") or {}).get("output"), 600)})


def extract_db(path: Path, **kwargs: Any) -> Iterator[Dict[str, Any]]:
    label = kwargs.pop("db_label", None) or path.stem
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        sessions = [r[0] for r in conn.execute("select distinct session_id from runtime_events")]
        for sid in sessions:
            events = conn.execute("select * from runtime_events where session_id=? order by sequence", (sid,)).fetchall()
            yield from extract_session(sid, events, db_label=label, **kwargs)
    finally:
        conn.close()


def extract_all(paths: Iterable[Path], **kwargs: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for p in paths:
        rows.extend(extract_db(p, db_label=str(p.parent.parent.name + "/" + p.parent.name), **kwargs))
    return rows
