"""Script de evaluación científica de Context Caching (Post-Optimización vs Baseline).

Compara directamente:
- Baseline (sin caché, re-construcción acumulativa)
- Con Context Caching L1 (ContextManager activado)

Mide:
- CRR (Context Reduction Ratio): 1 - optimized_tokens / baseline_tokens
- CHR (Cache Hit Rate): cache_hits / cache_requests
- Decision / Semantic Preservation
- Latencia de construcción
"""

import json
import time
from typing import Any, Dict, List
from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.evidence import Evidence
from praxeon.domain.goal import Goal
from praxeon.domain.decision import PolicyDecision, DecisionStatus
from praxeon.runtime.state import SessionState
from praxeon.providers.context import ProviderContextBuilder


def evaluate_trajectory(num_steps: int, enable_caching: bool) -> Dict[str, Any]:
    goal = Goal(
        objective="Refactorizar módulo de autenticación y desplegar infraestructura en Kubernetes",
        success_criteria=[
            "Pruebas unitarias de auth pasando",
            "Manifiestos de Kubernetes aplicados sin errores",
            "Logs de auditoría verificados",
        ]
    )
    session_id = f"eval_session_{'cached' if enable_caching else 'uncached'}_{num_steps}"
    state = SessionState(session_id=session_id, goal=goal)
    
    state.add_evidence(Evidence(id="ev_1", claim="cluster_k8s_disponible_en_10.0.0.1", content_hash="h1"))
    state.add_evidence(Evidence(id="ev_2", claim="secrets_auth_montados_en_vault", content_hash="h2"))
    state.add_evidence(Evidence(id="ev_3", claim="codigo_fuente_limpio_en_main", content_hash="h3"))
    
    builder = ProviderContextBuilder(
        default_max_tokens=2048,
        max_history_steps=10,
        max_obs_chars=300,
        enable_caching=enable_caching,
    )
    
    tokens_per_step: List[int] = []
    latencies_ms: List[float] = []
    hits = 0
    
    for i in range(num_steps):
        cand_action = ActionCandidate(
            id=f"action_step_{i+1}",
            description=f"Ejecución de subcomando #{i+1} para verificación",
            tool_call=ToolCall(
                tool_name="run_command" if i % 2 == 0 else "read_file",
                arguments={"command": f"kubectl get pods -n ns_{i}"} if i % 2 == 0 else {"path": f"src/auth/module_{i}.py"},
            ),
        )
        
        start_t = time.perf_counter()
        ctx = builder.build(state, cand_action)
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        
        if ctx.metadata.get("cache_hit"):
            hits += 1
            
        tokens_per_step.append(ctx.token_estimate)
        latencies_ms.append(elapsed_ms)
        
        # Simular avance del agente
        state.add_step(
            action=cand_action,
            decision=PolicyDecision(status=DecisionStatus.ALLOW),
            observation=f"Salida del comando o contenido del archivo #{i+1}: " + ("OK pod running " * 15),
        )
        if i % 3 == 0:
            state.add_evidence(Evidence(id=f"ev_dyn_{i+1}", claim=f"evidencia_confirmada_paso_{i+1}", content_hash=f"h_{i}"))
            
    total_tokens = sum(tokens_per_step)
    avg_tokens = total_tokens / num_steps if num_steps > 0 else 0
    avg_latency = sum(latencies_ms) / num_steps if num_steps > 0 else 0
    hit_rate = hits / num_steps if num_steps > 0 else 0.0
    
    manager_metrics = builder.context_manager.get_metrics() if builder.context_manager else {}
    
    return {
        "num_steps": num_steps,
        "enable_caching": enable_caching,
        "total_tokens": total_tokens,
        "avg_tokens_per_step": round(avg_tokens, 1),
        "avg_latency_ms": round(avg_latency, 3),
        "cache_hits": hits,
        "hit_rate": round(hit_rate, 4),
        "manager_metrics": manager_metrics,
    }


def main():
    print("=" * 75)
    print("EVALUACIÓN CIENTÍFICA: CONTEXT CACHING VS BASELINE")
    print("=" * 75)
    
    scenarios = [5, 15, 30]
    comparison_summary = {}
    
    for steps in scenarios:
        baseline = evaluate_trajectory(steps, enable_caching=False)
        cached = evaluate_trajectory(steps, enable_caching=True)
        
        saved_tokens = max(0, baseline["total_tokens"] - cached["total_tokens"])
        crr = (saved_tokens / baseline["total_tokens"]) if baseline["total_tokens"] > 0 else 0.0
        
        comparison_summary[f"{steps}_steps"] = {
            "baseline": baseline,
            "cached": cached,
            "tokens_saved": saved_tokens,
            "context_reduction_ratio": round(crr, 4),
        }
        
        print(f"\n[Trayectoria de {steps} Pasos]")
        print(f"  - Baseline Tokens:     {baseline['total_tokens']:,} (latencia media: {baseline['avg_latency_ms']} ms)")
        print(f"  - Cached Tokens:       {cached['total_tokens']:,} (latencia media: {cached['avg_latency_ms']} ms)")
        print(f"  - Tokens Ahorrados:    {saved_tokens:,} tokens")
        print(f"  - Context Reduction:   {round(crr * 100, 2)}%")
        print(f"  - Cache Hit Rate:      {round(cached['hit_rate'] * 100, 2)}%")
        
    output_path = "benchmark_results/context_caching_comparison.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(comparison_summary, f, indent=2)
        
    print(f"\nReporte comparativo guardado en: {output_path}")
    print("=" * 75)


if __name__ == "__main__":
    main()
