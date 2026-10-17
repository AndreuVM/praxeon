"""Benchmark de agente real: PRAXEON frente al mismo LLM sin arnés.

Ejemplos:
  uv run python scripts/run_agent_benchmark.py --models ollama:qwen2.5-coder:7b --arms bare,praxeon:laya --repeats 3
  uv run python scripts/run_agent_benchmark.py --models claude_cli:sonnet --arms bare,praxeon:laya --tasks qa_locate,fix_bug
  uv run python scripts/run_agent_benchmark.py --report-only          # solo regenera el informe desde el JSONL

Cada ejecución se guarda en benchmark_results/agentbench/runs.jsonl (se puede interrumpir y reanudar).
"""

import argparse
import io
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    for stream in ("stdout", "stderr"):
        s = getattr(sys, stream)
        if hasattr(s, "buffer") and s.encoding.lower() != "utf-8":
            setattr(sys, stream, io.TextIOWrapper(s.buffer, encoding="utf-8", errors="replace"))

from praxeon.agent_llm import create_agent_llm
from praxeon.evaluation.agentbench import PriceTable, build_suite, render_markdown, run_matrix


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="ollama:qwen2.5-coder:7b", help="lista proveedor:modelo separada por comas")
    ap.add_argument("--arms", default="bare,praxeon:laya", help="bare | praxeon[:supervisor[:modo]] separados por comas")
    ap.add_argument("--tasks", default="", help="ids separados por comas (por defecto, toda la suite)")
    ap.add_argument("--kind", choices=("capability", "safety"), default=None)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=900.0, help="segundos máximos por misión PRAXEON")
    ap.add_argument("--llm-timeout", type=float, default=300.0)
    ap.add_argument("--out", default="benchmark_results/agentbench")
    ap.add_argument("--prices", default=None, help="JSON {subcadena_de_modelo: {input, output, cached}} en USD por millón de tokens")
    ap.add_argument("--reference-price", default=None, help="'entrada,salida' USD/Mtok para comparar el coste de modelos locales")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--list", action="store_true", help="solo lista las tareas")
    args = ap.parse_args()

    tasks = build_suite(kind=args.kind, only=[t for t in args.tasks.split(",") if t] or None)
    if args.list:
        for t in tasks:
            print(f"{t.id:20} {t.kind:10} {t.goal[:90]}")
        return 0
    out_dir = Path(args.out)
    runs = out_dir / "runs.jsonl"
    prices = PriceTable.load(args.prices, args.reference_price)
    if not args.report_only:
        models = [tuple(m.split(":", 1)) for m in args.models.split(",") if m]
        run_matrix(
            models, [a for a in args.arms.split(",") if a], tasks, args.repeats, runs,
            lambda provider, model: create_agent_llm(provider=provider, model=model, timeout=args.llm_timeout, max_tokens=1024),
            prices, max_steps=args.max_steps, timeout_s=args.timeout,
        )
    rows = [json.loads(line) for line in runs.read_text(encoding="utf-8").splitlines() if line.strip()]
    (out_dir / "REPORT.md").write_text(render_markdown(rows), encoding="utf-8")
    print(f"\nInforme: {out_dir / 'REPORT.md'} ({len(rows)} ejecuciones)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
