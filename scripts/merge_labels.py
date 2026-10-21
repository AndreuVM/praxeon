"""Une las etiquetas de los casos reales: persona > regla derivada de la revisión > profesor.

Salida: data/finetune/labels_real.jsonl (una fila por caso con `decision`, `is_loop`, `is_grounded`, `progress` y `source`).

Reglas derivadas de la revisión humana (cada una nació de un patrón que la persona corrigió):
- WORKFLOW_FINISH: un agente de workflow tiene un rol acotado y entrega solo su parte (un plan, una revisión); que el
  resultado esté incompleto respecto al objetivo global no es motivo de REPLAN. Se aprueba, salvo resumen vacío o fabricado.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

DEGENERATE = re.compile(r"empty summary|resumen vac|claims a full|no files|invented|inventad|fabricat|error de api|503", re.I)


def read(path):
    p = Path(path)
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()] if p.exists() else []


def workflow_finish_rule(case, label):
    """ALLOW para un `finish` de agente de workflow que el profesor replanificó por estar incompleto."""
    if case["tool"] != "finish" or label["decision"] != "REPLAN":
        return None
    if not (case.get("in_workflow_session") or case.get("workflow")):
        return None
    if DEGENERATE.search(label.get("reason", "")) or not str((case["arguments"] or {}).get("summary") or case["arguments"] or "").strip():
        return None
    return {**label, "decision": "ALLOW", "progress": max(2, label["progress"]), "is_grounded": True, "reason": "WORKFLOW_FINISH: " + label["reason"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--real", default="data/finetune/real_cases.jsonl")
    ap.add_argument("--teacher", default="data/finetune/teacher_real.jsonl")
    ap.add_argument("--human", nargs="*", default=["data/finetune/review_labels.json", "data/finetune/review_finish_labels.json"])
    ap.add_argument("--out", default="data/finetune/labels_real.jsonl")
    args = ap.parse_args()

    cases = {c["id"]: c for c in read(args.real)}
    teacher = {t["id"]: t["label"] for t in read(args.teacher) if "label" in t}
    human = {}
    for path in args.human:                                  # varias hojas; la última gana si repiten un caso
        if Path(path).exists():
            human.update(json.loads(Path(path).read_text(encoding="utf-8")))
    rows, sources = [], Counter()
    for cid, label in teacher.items():
        case = cases.get(cid)
        if case is None:
            continue
        if cid in human:
            h = human[cid]
            final = {"decision": h["decision"], "is_loop": bool(h["is_loop"]), "is_grounded": bool(h["is_grounded"]),
                     "progress": label["progress"], "reason": "revisión humana"}
            source = "human"
        elif (ruled := workflow_finish_rule(case, label)) is not None:
            final, source = ruled, "rule:workflow_finish"
        else:
            final, source = label, "teacher"
        sources[source] += 1
        rows.append({"id": cid, "source": source, "teacher_decision": label["decision"], **{k: final[k] for k in ("decision", "is_loop", "is_grounded", "progress", "reason")}})
    Path(args.out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"{len(rows)} etiquetas -> {args.out} | fuentes: {dict(sources)}")
    print("decisión final:", dict(Counter(r["decision"] for r in rows)))
    changed = [r for r in rows if r["source"] == "rule:workflow_finish"]
    print(f"regla WORKFLOW_FINISH cambió {len(changed)} casos REPLAN -> ALLOW")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
