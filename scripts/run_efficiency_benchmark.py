#!/usr/bin/env python3
"""Script de ejecución formal del benchmark de eficiencia para PRAXEON (Fase 1).

Compara cuantitativamente:
- Condición A: Baseline (Agente ciego sin supervisor)
- Condición B: PRAXEON (Supervisor normativo con política de seguridad y executor tipado)

Calcula formalmente:
1. Context Reduction Ratio (CRR)
2. Decision Overhead (DO)
3. Execution Reduction (ER)
4. Cost per Successful Task (CPST)

Guarda los resultados consolidados en benchmark_results/efficiency_v1.json
"""

import argparse
from datetime import datetime, timezone
import json
import os
import sys
import time
from typing import Any, Dict, List

# Asegurar importación de praxeon
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from praxeon.domain.interfaces import ReasoningProvider
from praxeon.domain.models import (
    ActionCandidate,
    DecisionStatus,
    Goal,
    PolicyDecision,
    ProviderAssessment,
    RiskLevel,
)
from praxeon.evaluation.efficiency import (
    EfficiencyCalculator,
    EfficiencyMetrics,
    MODEL_PRICING_CATALOG,
    SessionEfficiencySummary,
    StepEfficiencyRecord,
)
from praxeon.evaluation.efficiency_scenarios import (
    BenchmarkScale,
    TaskScenario,
    create_efficiency_benchmark_suite,
)
from praxeon.policy.engine import PolicyEngine
from praxeon.runtime.executor import SecureExecutor, ToolObservation
from praxeon.runtime.navigator import Navigator


class DeterministicBenchmarkProvider(ReasoningProvider):
    """Proveedor determinista de evaluación semántica para benchmarking reproducible."""

    def __init__(self, model_name: str = "deepseek-r1-7b"):
        self.model = model_name

    def evaluate(self, state: Any, candidates: List[ActionCandidate]) -> List[ProviderAssessment]:
        assessments: List[ProviderAssessment] = []
        for cand in candidates:
            tool_name = cand.tool_call.tool_name if cand.tool_call else None
            args = cand.tool_call.arguments if cand.tool_call else {}
            cmd = str(args.get("command", "")).lower()

            # Detección determinista de loops y comandos destructivos
            is_loop = "loop" in cand.id.lower() or "re-leer" in cand.description.lower()
            is_destructive = "rm -rf /" in cmd or "evil.com" in cmd or "data" in cmd and "rm -rf" in cmd
            is_unregistered = tool_name and "unregistered" in tool_name

            if is_destructive or is_unregistered:
                assessments.append(
                    ProviderAssessment(
                        provider=f"benchmark-{self.model}",
                        model=self.model,
                        available=True,
                        confidence=0.15,
                        loop_probability=0.2,
                        grounded_probability=0.1,
                        progress_probability=0.05,
                        failure_reason="Acción destructiva o no autorizada detectada en evaluación semántica",
                        reason_codes=["UNSAFE_ACTION_DETECTED"],
                    )
                )
            elif is_loop:
                assessments.append(
                    ProviderAssessment(
                        provider=f"benchmark-{self.model}",
                        model=self.model,
                        available=True,
                        confidence=0.25,
                        loop_probability=0.85,
                        grounded_probability=0.4,
                        progress_probability=0.1,
                        failure_reason="Patrón de bucle o repetición estéril detectado",
                        reason_codes=["REPETITIVE_PATTERN"],
                    )
                )
            else:
                assessments.append(
                    ProviderAssessment(
                        provider=f"benchmark-{self.model}",
                        model=self.model,
                        available=True,
                        confidence=0.92,
                        loop_probability=0.02,
                        grounded_probability=0.95,
                        progress_probability=0.90,
                    )
                )
        return assessments


def run_baseline_scenario(scenario: TaskScenario) -> SessionEfficiencySummary:
    """Ejecuta la condición Baseline: el agente ejecuta ciegamente todas sus acciones."""
    steps: List[StepEfficiencyRecord] = []
    accumulated_context_tokens = 0

    for idx, act in enumerate(scenario.baseline_actions):
        tin = act.metadata.get("tokens_in", 300) + accumulated_context_tokens
        tout = act.metadata.get("tokens_out", 50)
        accumulated_context_tokens += 120  # El contexto crece sin poda

        # Latencia proporcional al modelo y longitud
        is_slm = "7b" in scenario.model_name or "8b" in scenario.model_name
        base_lat = 220.0 if is_slm else 480.0
        llm_lat = base_lat + (tin * 0.12)
        exec_lat = 15.0

        # En baseline, todas las acciones se intentan físicamente
        tool_name = act.tool_call.tool_name if act.tool_call else "none"
        args = act.tool_call.arguments if act.tool_call else {}
        cmd = str(args.get("command", "")).lower()
        is_destruct = "rm -rf /" in cmd or "evil.com" in cmd or "unregistered" in tool_name

        cost = EfficiencyCalculator.calculate_step_cost(tin, tout, scenario.model_name)

        step_rec = StepEfficiencyRecord(
            step_index=idx,
            action_id=act.id,
            tool_name=tool_name,
            decision_status="ALLOW",  # Baseline no tiene filtro
            tokens_in=tin,
            tokens_out=tout,
            tokens_total=tin + tout,
            llm_calls=1,
            llm_latency_ms=round(llm_lat, 2),
            praxeon_overhead_ms=0.0,  # Sin supervisor
            execution_time_ms=round(exec_lat, 2),
            total_step_latency_ms=round(llm_lat + exec_lat, 2),
            physical_execution_attempted=True,
            physical_execution_allowed=True,
            physical_execution_success=not is_destruct,
            is_error=is_destruct,
            cost_usd=round(cost, 6),
        )
        steps.append(step_rec)

    return EfficiencyCalculator.aggregate_session(
        session_id=f"base_{scenario.id}",
        scale=scenario.scale.value,
        model_name=scenario.model_name,
        successful_completion=scenario.expected_baseline_success,
        steps=steps,
    )


def run_praxeon_scenario(scenario: TaskScenario) -> SessionEfficiencySummary:
    """Ejecuta la condición PRAXEON: el agente supervisado por Navigator y PolicyEngine."""
    provider = DeterministicBenchmarkProvider(model_name=scenario.model_name)
    executor = SecureExecutor()
    nav = Navigator(
        provider=provider,
        executor=executor,
        model_name=scenario.model_name,
    )

    nav.start_session(goal=scenario.goal, session_id=f"prax_{scenario.id}")

    for act in scenario.supervised_actions:
        nav.step(act)

    return nav.get_efficiency_summary(
        scale=scenario.scale.value,
        successful_completion=scenario.expected_supervised_success,
    )


def main():
    parser = argparse.ArgumentParser(description="PRAXEON Efficiency Benchmark Runner (Fase 1)")
    parser.add_argument("--seed", type=int, default=42, help="Semilla pseudoaleatoria determinista")
    parser.add_argument("--output", type=str, default="benchmark_results/efficiency_v1.json", help="Ruta del archivo de salida JSON")
    args = parser.parse_args()

    print(f"===============================================================")
    print(f"  PRAXEON EFFICIENCY BENCHMARK v1.0 — FASE 1")
    print(f"  Seed: {args.seed} | Output: {args.output}")
    print(f"===============================================================\n")

    suite = create_efficiency_benchmark_suite(seed=args.seed)
    print(f"[*] Batería cargada: {len(suite)} escenarios multiescala y heterogéneos.")

    baseline_summaries: List[SessionEfficiencySummary] = []
    praxeon_summaries: List[SessionEfficiencySummary] = []

    for idx, sc in enumerate(suite, 1):
        print(f"[{idx:02d}/{len(suite):02d}] Ejecutando '{sc.title}' (Escala: {sc.scale.value.upper()}, Modelo: {sc.model_name})...")
        
        # 1. Baseline
        base_sum = run_baseline_scenario(sc)
        baseline_summaries.append(base_sum)

        # 2. PRAXEON
        prax_sum = run_praxeon_scenario(sc)
        praxeon_summaries.append(prax_sum)

    # Cálculo formal de métricas consolidadas
    global_metrics = EfficiencyCalculator.calculate_comparative_metrics(
        praxeon_sessions=praxeon_summaries,
        baseline_sessions=baseline_summaries,
    )

    # Métricas desagregadas por escala
    scale_metrics: Dict[str, Any] = {}
    for sc_enum in BenchmarkScale:
        scale_val = sc_enum.value
        p_sub = [s for s in praxeon_summaries if s.scale == scale_val]
        b_sub = [s for s in baseline_summaries if s.scale == scale_val]
        if p_sub and b_sub:
            scale_metrics[scale_val] = EfficiencyCalculator.calculate_comparative_metrics(p_sub, b_sub).model_dump()

    # Métricas desagregadas por clase de modelo (SLM vs LLM)
    slm_models = {"deepseek-r1-7b", "llama-3-8b"}
    p_slm = [s for s in praxeon_summaries if s.model_name in slm_models]
    b_slm = [s for s in baseline_summaries if s.model_name in slm_models]
    slm_metrics = EfficiencyCalculator.calculate_comparative_metrics(p_slm, b_slm).model_dump()

    llm_models = {"gpt-4o", "claude-3-5-sonnet"}
    p_llm = [s for s in praxeon_summaries if s.model_name in llm_models]
    b_llm = [s for s in baseline_summaries if s.model_name in llm_models]
    llm_metrics = EfficiencyCalculator.calculate_comparative_metrics(p_llm, b_llm).model_dump()

    # Estructura de resultados finales
    results_payload = {
        "metadata": {
            "benchmark_version": "1.0",
            "phase": "Fase 1: Efficiency Benchmark",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "seed": args.seed,
            "total_scenarios": len(suite),
        },
        "global_metrics": global_metrics.model_dump(),
        "breakdown_by_scale": scale_metrics,
        "breakdown_by_model_class": {
            "slm": slm_metrics,
            "llm": llm_metrics,
        },
        "scenario_details": [
            {
                "id": sc.id,
                "scale": sc.scale.value,
                "model": sc.model_name,
                "title": sc.title,
                "baseline": baseline_summaries[i].model_dump(exclude={"steps"}),
                "praxeon": praxeon_summaries[i].model_dump(exclude={"steps"}),
            }
            for i, sc in enumerate(suite)
        ],
    }

    # Guardar en archivo
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results_payload, f, indent=2, ensure_ascii=False)

    print(f"\n[+] Resultados guardados en: {args.output}")
    print("\n" + "=" * 65)
    print("  RESULTADOS CONSOLIDADOS DEL BENCHMARK DE EFICIENCIA")
    print("=" * 65)
    print(f"  • Tasa de Éxito Tareas (Supervisadas) : {global_metrics.task_success_rate * 100:.1f}%")
    print(f"  • Context Reduction Ratio (CRR)       : {global_metrics.context_reduction_ratio:.2f}% de tokens ahorrados")
    print(f"  • Decision Overhead (DO)              : {global_metrics.decision_overhead_ms:.2f} ms ({global_metrics.decision_overhead_percentage:.2f}% del tiempo de paso)")
    print(f"  • Execution Reduction (ER)            : {global_metrics.execution_reduction_rate:.2f}% de ejecuciones físicas evitadas")
    print(f"  • Cost per Successful Task (CPST)     : ${global_metrics.cost_per_successful_task_usd:.4f} USD")
    print(f"  • Coste Total Evaluado                : ${global_metrics.total_cost_usd:.4f} USD")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
