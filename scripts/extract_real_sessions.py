"""Extrae de las sesiones reales (events.db) los casos que verá el supervisor. Salida local: data/finetune/real_cases.jsonl."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from praxeon.evaluation.finetune.real_sessions import extract_all  # noqa: E402

DEFAULT_DBS = [".jev_cache/events.db", "web/.jev_cache/events.db", ".praxeon_cache/events.db"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", action="append", help="events.db (repetible); por defecto las del repositorio")
    ap.add_argument("--out", default="data/finetune/real_cases.jsonl")
    ap.add_argument("--all-sources", action="store_true", help="incluir también agentes simulados/guionizados (pruebas)")
    ap.add_argument("--include-simulator", action="store_true")
    args = ap.parse_args()

    paths = [Path(p) for p in (args.db or DEFAULT_DBS) if Path(p).exists()]
    rows = extract_all(paths, only_real=not args.all_sources, include_simulator=args.include_simulator)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    print(f"{len(rows)} casos de {len(paths)} base(s) -> {out}")
    print("por fuente:", dict(Counter(r["source"] for r in rows)))
    print("herramienta:", dict(Counter(r["tool"] for r in rows).most_common(8)))
    print("decisión observada:", dict(Counter(r["observed"]["policy_status"] for r in rows)))
    print("revisión humana:", dict(Counter(str(r["observed"]["approved_by_human"]) for r in rows)))
    print("sesiones:", len({(r["db"], r["session"]) for r in rows}), "| objetivos distintos:", len({r["goal"] for r in rows}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
