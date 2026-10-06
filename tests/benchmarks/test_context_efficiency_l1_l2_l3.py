"""Test de validación del protocolo de benchmarking de eficiencia de contexto en 3 niveles (L1, L2, L3)."""

import os
import pytest
from scripts.benchmark_context_efficiency import (
    run_l1_benchmark,
    run_l2_benchmark,
    run_l3_benchmark,
    run_full_benchmark,
)


@pytest.mark.benchmark
def test_l1_benchmark_computes_reduction():
    """Valida la métrica L1 de reducción y poda causal de contexto."""
    res = run_l1_benchmark(num_steps=8)
    assert res["level"] == "L1"
    assert res["steps_evaluated"] == 8
    assert res["unpruned_tokens_total"] > res["optimized_tokens_total"]
    assert res["tokens_saved_l1"] > 0
    assert res["context_reduction_ratio_l1"] > 0.0


@pytest.mark.benchmark
def test_l2_benchmark_computes_prefix_hits_and_deltas():
    """Valida la métrica L2 de prefix caching y reutilización de deltas."""
    res = run_l2_benchmark(num_steps=6, branches_per_step=2)
    assert res["level"] == "L2"
    assert res["cache_hit_rate"] > 0.0
    assert res["prefix_cache_hits"] > 0
    assert res["deltas_generated"] == 12


@pytest.mark.benchmark
def test_l3_benchmark_computes_token_and_cost_savings():
    """Valida la métrica L3 de facturación comparativa de tokens y coste."""
    res = run_l3_benchmark(num_steps=5, model_name="gpt-4o")
    assert res["level"] == "L3"
    assert res["praxeon"]["billed_tokens"] < res["baseline"]["total_tokens"]
    assert res["cost_reduction_ratio"] > 0.0
    assert res["cost_savings_usd"] > 0.0


@pytest.mark.benchmark
def test_full_benchmark_persists_artifacts(tmp_path):
    """Valida la ejecución integral y persistencia de artefactos JSON y Markdown."""
    out_dir = str(tmp_path / "bench_out")
    res = run_full_benchmark(output_dir=out_dir)
    assert "L1" in res["levels"]
    assert "L2" in res["levels"]
    assert "L3" in res["levels"]
    assert os.path.exists(os.path.join(out_dir, "context_efficiency_l1_l2_l3.json"))
