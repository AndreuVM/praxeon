"""Ensambla train/val/test para el ajuste fino: sintéticos + sesiones reales con sus etiquetas, estados ajustados al modelo.

Las sesiones reales se reparten por sesión (nunca por fila). Salida: data/finetune/{train,val,test}.jsonl en el formato de
`laya.train.finetune` ({state, questions, expected}); la prueba es la sintética de familias nuevas más sesiones reales retenidas.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from praxeon.evaluation.finetune.real_sessions import extract_all  # noqa: E402
from praxeon.evaluation.finetune.render import load_tokenizer, make_renderer  # noqa: E402
from praxeon.providers.systemone import QUESTIONS  # noqa: E402

DEFAULT_DBS = [".jev_cache/events.db", "web/.jev_cache/events.db", ".praxeon_cache/events.db"]
CHECKPOINT = r"C:\Users\adria\.cache\huggingface\hub\models--convaiinnovations--laya\snapshots\7b928d828b7b0e022f929d9bd2e44165aa270148\typed-decisions"


def session_split(db: str, session: str, val: float = 0.15, test: float = 0.15) -> str:
    x = int(hashlib.sha1(f"{db}:{session}".encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "test" if x < test else "val" if x < test + val else "train"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--checkpoint", default=CHECKPOINT)
    ap.add_argument("--labels", default="data/finetune/labels_real.jsonl")
    ap.add_argument("--out-dir", default="data/finetune")
    ap.add_argument("--real-weight", type=int, default=1, help="veces que se repite cada caso real en entrenamiento")
    ap.add_argument("--mask-probability", type=float, default=0.5)
    ap.add_argument("--record-version", type=int, default=2)
    args = ap.parse_args()

    out = Path(args.out_dir)
    labels = {}
    for line in Path(args.labels).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            labels[r["id"]] = r
    tok = load_tokenizer(args.checkpoint)
    cases = extract_all([Path(p) for p in DEFAULT_DBS if Path(p).exists()], renderer=make_renderer(tok, args.record_version, args.mask_probability))
    splits = {"train": [], "val": [], "test": []}
    kept = Counter()
    for c in cases:
        lab = labels.get(c["id"])
        if lab is None:
            continue
        split = session_split(c["db"], c["session"])
        row = {"id": c["id"], "origin": "real", "family": f"real_{c['tool']}", "split": split, "label_source": lab["source"],
               "state": c["state"], "questions": QUESTIONS,
               "expected": {"decision": lab["decision"], "is_loop": lab["is_loop"], "is_grounded": lab["is_grounded"], "progress": lab["progress"]}}
        splits[split].append(row)
        kept[split] += 1
    for name in splits:
        for line in Path(out / f"synthetic_{name}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                splits[name].append(json.loads(line))
    for name, rows in splits.items():
        rows_out = rows + ([r for r in rows if r["origin"] == "real"] * (args.real_weight - 1) if name == "train" else [])
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
            for r in rows_out:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{name:5} {len(rows_out):5} filas | reales {sum(r['origin'] == 'real' for r in rows_out)} | "
              f"{dict(Counter(r['expected']['decision'] for r in rows_out))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
