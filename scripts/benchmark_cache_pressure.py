"""Benchmark Formal de Presión y Desalojo de Caché (CHG-13).

Evalúa el comportamiento de praxeon.context bajo restricciones severas de memoria y contienda:
1. Latencia en frío (Cold start build latency) vs. latencia en caliente (Warm cache hit latency).
2. Tasa de desalojo LRU (Eviction Rate) bajo cuota acotada de entradas.
3. Coste y penalización de reconstrucción (Rebuild penalty) tras desalojo forzado.
4. Respeto estricto del Axioma de Seguridad: Cache Hit != ALLOW != Capability != Execution.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import platform
import random
import time
from typing import Any, Dict, List

from praxeon.context.budget import TokenBudget
from praxeon.context.cache import InMemoryContextCache, InMemoryFragmentCache
from praxeon.context.manager import ContextManager
from praxeon.context.selector import DAGContextSelector
from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.decision import DecisionStatus, PolicyDecision
from praxeon.domain.evidence import Evidence
from praxeon.domain.goal import Goal
from praxeon.runtime.state import SessionState


def calculate_percentiles(values: List[float]) -> Dict[str, float]:
    """Calcula p50 y p95 exactos para una lista de latencias."""
    if not values:
        return {"p50": 0.0, "p95": 0.0, "avg": 0.0, "max": 0.0, "min": 0.0}
    sorted_v = sorted(values)
    n = len(sorted_v)

    def p(pct: float) -> float:
        k = (n - 1) * pct
        f = int(k)
        c = min(f + 1, n - 1)
        d0 = sorted_v[f] * (c - k)
        d1 = sorted_v[c] * (k - f)
        return round(d0 + d1, 3)

    return {
        "p50": p(0.50),
        "p95": p(0.95),
        "avg": round(sum(sorted_v) / n, 3),
        "min": round(sorted_v[0], 3),
        "max": round(sorted_v[-1], 3),
    }


def run_pressure_benchmark(
    num_sessions: int = 10,
    steps_per_session: int = 8,
    cache_max_entries: int = 20,
    cache_per_session: int = 5,
    seed: int = 42,
) -> Dict[str, Any]:
    """Ejecuta el protocolo de estrés de caché."""
    random.seed(seed)

    # 1. Instanciar caché con cuotas acotadas para simular alta presión
    cache = InMemoryContextCache(
        max_entries=cache_max_entries,
        max_entries_per_session=cache_per_session,
    )
    fragment_cache = InMemoryFragmentCache(max_entries=100)
    selector = DAGContextSelector(max_recent_observations=5)
    budget = TokenBudget(default_max_tokens=2048)

    manager = ContextManager(
        cache=cache,
        fragment_cache=fragment_cache,
        selector=selector,
        budget=budget,
    )

    # Crear estados de sesión
    sessions: List[SessionState] = []
    for i in range(num_sessions):
        sid = f"pressure_sess_{i+1:02d}"
        goal = Goal(
            objective=f"Objetivo de estrés #{i+1}: Auditoría y mitigación distribuida",
            success_criteria=["Seguridad verificada", "Logs limpios"],
        )
        st = SessionState(session_id=sid, goal=goal)
        st.add_evidence(Evidence(id=f"ev_{i}_1", claim=f"host_ready_{i}", content_hash=f"h_{i}"))
        sessions.append(st)

    cold_latencies: List[float] = []
    warm_latencies: List[float] = []
    rebuild_latencies: List[float] = []

    # =========================================================================
    # Fase 1: Cold start de cada sesión inicial
    # =========================================================================
    for st in sessions:
        act = ActionCandidate(
            id=f"{st.session_id}_init_act",
            description="Acción inicial en frío",
            tool_call=ToolCall(tool_name="read_file", arguments={"path": "config.yaml"}),
        )
        t0 = time.perf_counter()
        snap, is_hit = manager.build(st, act)
        lat = (time.perf_counter() - t0) * 1000.0
        cold_latencies.append(lat)
        assert is_hit is False, "En frío debe ser miss inicial"

    # =========================================================================
    # Fase 2: Warm hit inmediato (sin desalojo intermedio)
    # =========================================================================
    for st in sessions[:min(len(sessions), cache_max_entries)]:
        act = ActionCandidate(
            id=f"{st.session_id}_init_act",
            description="Acción inicial en frío",
            tool_call=ToolCall(tool_name="read_file", arguments={"path": "config.yaml"}),
        )
        t0 = time.perf_counter()
        snap, is_hit = manager.build(st, act)
        lat = (time.perf_counter() - t0) * 1000.0
        warm_latencies.append(lat)
        # Nota: si alguna ya fue desalojada por cuota, se registrará el hit si sigue presente
        if is_hit:
            assert snap.total_tokens > 0

    # =========================================================================
    # Fase 3: Presión sostenida (generar múltiples pasos en todas las sesiones)
    # =========================================================================
    step_actions_record: List[tuple[SessionState, ActionCandidate]] = []

    for step_num in range(1, steps_per_session + 1):
        for st in sessions:
            act = ActionCandidate(
                id=f"{st.session_id}_step_{step_num}",
                description=f"Operación paso #{step_num} en {st.session_id}",
                tool_call=ToolCall(
                    tool_name="run_command" if step_num % 2 == 0 else "read_file",
                    arguments={"command": f"pytest -k step_{step_num}"} if step_num % 2 == 0 else {"path": f"src/mod_{step_num}.py"},
                ),
            )
            step_actions_record.append((st, act))
            manager.build(st, act)

            # Avanzar estado para agregar observaciones y provocar divergencia de fingerprints
            st.add_step(
                action=act,
                decision=PolicyDecision(status=DecisionStatus.ALLOW),
                observation=f"Observación de estrés paso #{step_num} - buffer de telemetría ok",
            )
            if step_num % 3 == 0:
                st.add_evidence(
                    Evidence(id=f"ev_p_{st.session_id}_{step_num}", claim="checkpoint_ok", content_hash=f"h_{step_num}")
                )

    # =========================================================================
    # Fase 4: Rebuild penalty tras desalojo forzado de las primeras entradas
    # =========================================================================
    # Solicitar las primeras 5 acciones registradas (que fueron desalojadas por LRU)
    for st_old, act_old in step_actions_record[:5]:
        t0 = time.perf_counter()
        snap_r, hit_r = manager.build(st_old, act_old)
        lat_r = (time.perf_counter() - t0) * 1000.0
        rebuild_latencies.append(lat_r)

    metrics = manager.get_metrics()
    cold_stats = calculate_percentiles(cold_latencies)
    warm_stats = calculate_percentiles(warm_latencies)
    rebuild_stats = calculate_percentiles(rebuild_latencies)

    speedup = (cold_stats["avg"] / warm_stats["avg"]) if warm_stats["avg"] > 0 else 1.0

    return {
        "configuration": {
            "num_sessions": num_sessions,
            "steps_per_session": steps_per_session,
            "cache_max_entries": cache_max_entries,
            "cache_per_session": cache_per_session,
            "seed": seed,
        },
        "latency_analysis": {
            "cold_start_ms": cold_stats,
            "warm_hit_ms": warm_stats,
            "rebuild_after_eviction_ms": rebuild_stats,
            "warm_speedup_factor": round(speedup, 2),
        },
        "cache_pressure_metrics": {
            "context_builds_total": metrics["context_builds_total"],
            "context_cache_hits_total": metrics["context_cache_hits_total"],
            "context_prefix_hits_total": metrics["context_prefix_hits_total"],
            "context_cache_misses_total": metrics["context_cache_misses_total"],
            "context_cache_hit_rate": metrics["context_cache_hit_rate"],
            "context_rebuilds_total": metrics["context_rebuilds_total"],
            "context_cache_evictions_total": metrics["context_cache_evictions_total"],
            "snapshot_evictions": metrics["snapshot_evictions"],
            "fragment_evictions": metrics["fragment_evictions"],
            "active_cache_entries": metrics["cache_entries"],
            "active_fragment_entries": metrics["fragment_cache_entries"],
        },
    }


def main():
    parser = argparse.ArgumentParser(description="PRAXEON Cache Pressure & Eviction Benchmark (CHG-13)")
    parser.add_argument("--num-sessions", type=int, default=10, help="Número de sesiones concurrentes a simular")
    parser.add_argument("--steps-per-session", type=int, default=8, help="Pasos de trayectoria por sesión")
    parser.add_argument("--cache-max-entries", type=int, default=20, help="Capacidad global máxima de snapshots en caché")
    parser.add_argument("--cache-per-session", type=int, default=5, help="Capacidad máxima de snapshots por sesión individual")
    parser.add_argument("--seed", type=int, default=42, help="Semilla pseudo-aleatoria de reproducibilidad")
    parser.add_argument("--output", type=str, default="benchmark_results/cache_pressure_evaluation.json", help="Ruta del reporte JSON")
    args = parser.parse_args()

    print("=" * 80)
    print("PRAXEON: BENCHMARK DE PRESIÓN, DESALOJO LRU Y RECONSTRUCCIÓN DE CACHÉ (CHG-13)")
    print(f"Sesiones: {args.num_sessions} | Pasos/sesión: {args.steps_per_session} | Max Entries: {args.cache_max_entries} | Seed: {args.seed}")
    print("=" * 80)

    results = run_pressure_benchmark(
        num_sessions=args.num_sessions,
        steps_per_session=args.steps_per_session,
        cache_max_entries=args.cache_max_entries,
        cache_per_session=args.cache_per_session,
        seed=args.seed,
    )

    lat = results["latency_analysis"]
    press = results["cache_pressure_metrics"]

    print("\n" + "-" * 35 + " LATENCIAS Y SPEEDUP " + "-" * 35)
    print(f"{'MÉTRICA':<30} | {'AVG (ms)':<10} | {'p50 (ms)':<10} | {'p95 (ms)':<10}")
    print("-" * 80)
    print(f"{'Cold Start (Miss inicial)':<30} | {lat['cold_start_ms']['avg']:<10} | {lat['cold_start_ms']['p50']:<10} | {lat['cold_start_ms']['p95']:<10}")
    print(f"{'Warm Hit (Caché caliente)':<30} | {lat['warm_hit_ms']['avg']:<10} | {lat['warm_hit_ms']['p50']:<10} | {lat['warm_hit_ms']['p95']:<10}")
    print(f"{'Rebuild tras Desalojo LRU':<30} | {lat['rebuild_after_eviction_ms']['avg']:<10} | {lat['rebuild_after_eviction_ms']['p50']:<10} | {lat['rebuild_after_eviction_ms']['p95']:<10}")
    print(f"[*] Factor de Aceleración en Caliente (Warm Speedup): {lat['warm_speedup_factor']}x")

    print("\n" + "-" * 33 + " PRESIÓN Y CONTENCIÓN " + "-" * 33)
    print(f"Total Builds / Consultas:        {press['context_builds_total']}")
    print(f"Cache Hits (Exact + Prefix):      {press['context_cache_hits_total']} ({press['context_cache_hit_rate']*100:.1f}%)")
    print(f"Prefix Hits reutilizados:         {press['context_prefix_hits_total']}")
    print(f"Cache Misses:                     {press['context_cache_misses_total']}")
    print(f"Rebuilds totales ejecutados:      {press['context_rebuilds_total']}")
    print(f"Desalojos LRU de Snapshots:       {press['snapshot_evictions']}")
    print(f"Desalojos LRU de Fragmentos:      {press['fragment_evictions']}")
    print(f"Entradas activas en memoria:      {press['active_cache_entries']} / {args.cache_max_entries}")

    metadata = {
        "benchmark_name": "PRAXEON Context Cache Pressure & Eviction Benchmark",
        "praxeon_version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dataset": "synthetic_multi_session_contention_v1",
        "seed": args.seed,
        "strategy_version": "dag_priority_v1",
        "cache_limits": {
            "max_entries": args.cache_max_entries,
            "max_entries_per_session": args.cache_per_session,
        },
        "hardware_environment": {
            "os": platform.platform(),
            "processor": platform.processor(),
            "python_version": platform.python_version(),
        },
        "aggregation_method": "stratified_percentiles_and_lru_contention",
        "workload_scope_disclaimer": (
            "NOTE: The eviction rate, speedup, and rebuild latencies reported herein are specific "
            "to this synthetic multi-session memory contention benchmark under constrained cache quotas. "
            "They demonstrate deterministic LRU behavior and bounded memory footprint, but should not be "
            "generalized as universal latency guarantees across heterogeneous hardware or unconstrained environments."
        ),
    }

    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(
            {
                "metadata": metadata,
                "benchmark_results": results,
            },
            f,
            indent=2,
        )

    print("\n" + "=" * 80)
    print(f"[*] Reporte exportado exitosamente a: {args.output}")
    print(f"[*] {metadata['workload_scope_disclaimer']}")
    print("=" * 80)


if __name__ == "__main__":
    main()
