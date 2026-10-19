"""Genera los casos sintéticos de ataques y controles. Salida local: data/finetune/synthetic_{train,val,test}.jsonl."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from praxeon.evaluation.finetune.generator import agreement_by_family, generate  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--out-dir", default="data/finetune")
    ap.add_argument("--mask-probability", type=float, default=0.5, help="1.0 = sin banderas del clasificador (ablación)")
    ap.add_argument("--record-version", type=int, default=2, help="1 = sin procedencia «citada en lo leído antes» (ablación)")
    args = ap.parse_args()

    rows = generate(args.seed, args.scale, args.mask_probability, args.record_version)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for split in ("train", "val", "test"):
        part = [r for r in rows if r["split"] == split]
        with (out / f"synthetic_{split}.jsonl").open("w", encoding="utf-8") as fh:
            for r in part:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        labels = Counter(r["expected"]["decision"] for r in part)
        print(f"{split:5} {len(part):5} casos  {dict(labels)}  familias: {len({r['family'] for r in part})}")
    print("\nCoincidencia del oráculo determinista con la etiqueta por construcción (donde baja, las reglas no llegan):")
    for family, t in sorted(agreement_by_family(rows).items(), key=lambda kv: kv[1]["agreement"]):
        print(f"  {family:24} n={t['n']:4}  {t['agreement']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
