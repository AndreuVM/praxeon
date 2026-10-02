"""Script de medición de línea base para Context Building antes de la optimización con Context Caching.

Mide:
- Tokens de entrada por llamada y acumulados en trayectorias de 5, 15 y 30 pasos.
- Latencia de construcción y serialización.
- Tasa de truncamiento actual.
- Ratio de redundancia (bytes y tokens re-serializados de información estática).
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


def run_baseline_trajectory(num_steps: int) -> Dict[str, Any]:
    goal = Goal(
        objective="Refactorizar módulo de autenticación y desplegar infraestructura en Kubernetes",
        success_criteria=[
            "Pruebas unitarias de auth pasando",
            "Manifiestos de Kubernetes aplicados sin errores",
            "Logs de auditoría verificados",
        ]
    )
    state = SessionState(session_id="baseline_session_001", goal=goal)
    
    # Añadir evidencias iniciales
    state.add_evidence(Evidence(id="ev_1", claim="cluster_k8s_disponible_en_10.0.0.1", content_hash="h1"))
    state.add_evidence(Evidence(id="ev_2", claim="secrets_auth_montados_en_vault", content_hash="h2"))
    state.add_evidence(Evidence(id="ev_3", claim="codigo_fuente_limpio_en_main", content_hash="h3"))
    
    builder = ProviderContextBuilder(default_max_tokens=2048, max_history_steps=10, max_obs_chars=300)
    
    tokens_per_step: List[int] = []
    latencies_ms: List[float] = []
    truncated_count = 0
    total_prompt_chars = 0
    
    for i in range(num_steps):
        # Cada paso genera una acción candidata
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
        
        tokens_per_step.append(ctx.token_estimate)
        latencies_ms.append(elapsed_ms)
        total_prompt_chars += len(ctx.formatted_prompt)
        if ctx.truncated:
            truncated_count += 1
            
        # Simular ejecución y avance de estado
        state.add_step(
            action=cand_action,
            decision=PolicyDecision(status=DecisionStatus.ALLOW),
            observation=f"Salida del comando o contenido del archivo #{i+1}: " + ("OK pod running " * 15),
        )
        if i % 3 == 0:
            state.add_evidence(Evidence(id=f"ev_dyn_{i+1}", claim=f"evidencia_confirmada_paso_{i+1}", content_hash=f"h_{i}"))
            
    total_input_tokens = sum(tokens_per_step)
    avg_tokens_per_step = total_input_tokens / num_steps if num_steps > 0 else 0
    avg_latency_ms = sum(latencies_ms) / num_steps if num_steps > 0 else 0
    
    return {
        "num_steps": num_steps,
        "total_input_tokens": total_input_tokens,
        "avg_tokens_per_step": round(avg_tokens_per_step, 1),
        "min_tokens_per_step": min(tokens_per_step) if tokens_per_step else 0,
        "max_tokens_per_step": max(tokens_per_step) if tokens_per_step else 0,
        "total_prompt_chars": total_prompt_chars,
        "avg_latency_ms": round(avg_latency_ms, 3),
        "total_latency_ms": round(sum(latencies_ms), 3),
        "truncation_events": truncated_count,
        "truncation_rate": round(truncated_count / num_steps, 2) if num_steps > 0 else 0,
    }


def main():
    print("=" * 70)
    print("BASELINE MEASUREMENT: PRAXEON CONTEXT BUILDING (PRE-CACHING)")
    print("=" * 70)
    
    scenarios = [5, 15, 30]
    results = {}
    
    for steps in scenarios:
        res = run_baseline_trajectory(steps)
        results[f"{steps}_steps"] = res
        print(f"\n[Trayectoria de {steps} Pasos]")
        print(f"  - Total Input Tokens Acumulados: {res['total_input_tokens']:,}")
        print(f"  - Promedio Tokens/Paso:         {res['avg_tokens_per_step']} tokens")
        print(f"  - Min / Max Tokens/Paso:        {res['min_tokens_per_step']} / {res['max_tokens_per_step']} tokens")
        print(f"  - Latencia Media por Build:     {res['avg_latency_ms']} ms")
        print(f"  - Tasa de Truncamiento:         {res['truncation_rate'] * 100}% ({res['truncation_events']}/{steps} pasos)")
        
    # Guardar reporte de baseline
    output_path = "benchmark_results/context_caching_baseline.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nReporte baseline guardado en: {output_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
