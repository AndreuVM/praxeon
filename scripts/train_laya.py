"""Ajuste fino de LAYA con laya.train.finetune. Local; la salida va a data/finetune/models/<nombre> (no se sube al repositorio).

Ejemplo: python scripts/train_laya.py --name run1 --micro-batch 2 --grad-accum 32 [--freeze-encoder] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CHECKPOINTS = {
    "typed-decisions": r"C:\Users\adria\.cache\huggingface\hub\models--convaiinnovations--laya\snapshots\7b928d828b7b0e022f929d9bd2e44165aa270148\typed-decisions",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="typed-decisions")
    ap.add_argument("--name", default="run1")
    ap.add_argument("--data", default="data/finetune/train.jsonl")
    ap.add_argument("--eval-data", default="data/finetune/val.jsonl")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--micro-batch", type=int, default=2)
    ap.add_argument("--grad-accum", type=int, default=32)
    ap.add_argument("--loss", default="rlcd", choices=["rlcd", "soft-ce"])
    ap.add_argument("--freeze-encoder", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import laya.train as lt

    base = CHECKPOINTS.get(args.base, args.base)
    out = Path("data/finetune/models") / args.name
    cfg = lt.TrainConfig(epochs=args.epochs, micro_batch=args.micro_batch, grad_accum=args.grad_accum, loss=args.loss,
                         freeze_encoder=args.freeze_encoder, seed=args.seed, shuffle_options=("choice",), eval_data=args.eval_data)
    if args.dry_run:
        print(json.dumps(lt.dry_run(args.data, base, cfg), indent=1, default=str))
        return 0
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    result = lt.finetune(args.data, base, str(out), cfg)
    minutes = (time.time() - started) / 60
    print(json.dumps(result, indent=1, default=str)[:3000])
    (out / "praxeon_run.json").write_text(json.dumps({"base": args.base, "args": vars(args), "minutes": round(minutes, 1)}, indent=1), encoding="utf-8")
    print(f"listo en {minutes:.1f} min -> {out}")
    try:
        import torch

        print(f"pico de memoria GPU: {torch.cuda.max_memory_allocated() / 2**20:.0f} MB")
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
