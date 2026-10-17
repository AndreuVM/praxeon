"""Etiqueta casos con el profesor (Claude CLI). Reanudable: no repite los ids ya etiquetados.

Piloto: python scripts/teacher_label.py --sample 20 --from data/finetune/synthetic_val.jsonl --sample-real 20 --out data/finetune/teacher_pilot.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from praxeon.agent_llm import ClaudeCLILLM  # noqa: E402
from praxeon.evaluation.finetune.teacher import ask_teacher  # noqa: E402


def read(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="source", default="data/finetune/synthetic_val.jsonl")
    ap.add_argument("--sample", type=int, default=20, help="casos sintéticos (con etiqueta del generador) a etiquetar")
    ap.add_argument("--real", default="data/finetune/real_cases.jsonl")
    ap.add_argument("--sample-real", type=int, default=20)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--out", default="data/finetune/teacher_pilot.jsonl")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    pool = []
    if args.sample:
        syn = read(args.source)
        rng.shuffle(syn)
        pool += syn[: args.sample]
    if args.sample_real and Path(args.real).exists():
        real = read(args.real)
        rng.shuffle(real)
        pool += real[: args.sample_real]

    out = Path(args.out)
    done = {r["id"] for r in map(json.loads, out.read_text(encoding="utf-8").splitlines()) if "label" in r} if out.exists() else set()
    todo = [r for r in pool if r["id"] not in done]
    print(f"{len(todo)} casos por etiquetar ({len(done)} ya hechos) con {args.model}, {args.workers} en paralelo")

    lock = threading.Lock()
    local = threading.local()

    def work(row):
        if not hasattr(local, "llm"):
            local.llm = ClaudeCLILLM(model=args.model)
        result = ask_teacher(local.llm, row["state"])
        record = {"id": row["id"], "origin": row.get("origin"), "family": row.get("family"), "tool": row.get("tool"),
                  "gold": row.get("expected"), "oracle": row.get("oracle"), "observed": (row.get("observed") or {}).get("policy_status"), **result}
        with lock:
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    with ThreadPoolExecutor(max_workers=args.workers) as pool_exec:
        results = list(pool_exec.map(work, todo))

    ok = [r for r in results if "label" in r]
    print(f"etiquetados {len(ok)}/{len(results)} | errores: {Counter(r.get('error') for r in results if 'error' in r)}")
    cost = [r["cost_usd"] for r in ok if isinstance(r.get("cost_usd"), (int, float))]
    if cost:
        print(f"coste notificado: {sum(cost):.3f} USD ({sum(cost) / len(cost):.4f} por caso)")
    syn_ok = [r for r in ok if r.get("gold")]
    if syn_ok:
        agree = sum(r["label"]["decision"] == r["gold"]["decision"] for r in syn_ok)
        print(f"sintéticos: el profesor coincide con la etiqueta del generador en {agree}/{len(syn_ok)}")
        for r in syn_ok:
            if r["label"]["decision"] != r["gold"]["decision"]:
                print(f"  {r['family']:24} generador {r['gold']['decision']:8} profesor {r['label']['decision']:8} {r['label']['reason'][:90]}")
    real_ok = [r for r in ok if not r.get("gold")]
    if real_ok:
        print("reales: profesor", dict(Counter(r["label"]["decision"] for r in real_ok)), "| política observada", dict(Counter(r.get("observed") for r in real_ok)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
