"""Motor de evaluación de eficiencia, métricas de tokens, latencia y costes para PRAXEON.

Implementa los requisitos formales de la Fase 1 del Roadmap:
- Context Reduction Ratio (CRR)
- Decision Overhead (DO)
- Execution Reduction (ER)
- Cost per Successful Task (CPST)
- Medición de tokens input/output, latencia de inferencia y overhead del supervisor.
"""

from datetime import datetime, timezone
import math
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ModelPricing(BaseModel):
    """Estructura de precios por millón de tokens para estimación monetaria de inferencia."""
    model_config = ConfigDict(frozen=True)

    model_name: str
    price_per_million_input_tokens: float
    price_per_million_output_tokens: float
    supervisor_cost_per_decision: float = 0.0005  # Coste estimado de procesamiento del supervisor local/microservicio


# Catálogo canónico de precios de mercado (USD / 1M tokens) para modelos pequeños y potentes
MODEL_PRICING_CATALOG: Dict[str, ModelPricing] = {
    "claude-3-5-sonnet": ModelPricing(
        model_name="claude-3-5-sonnet",
        price_per_million_input_tokens=3.00,
        price_per_million_output_tokens=15.00,
    ),
    "gpt-4o": ModelPricing(
        model_name="gpt-4o",
        price_per_million_input_tokens=2.50,
        price_per_million_output_tokens=10.00,
    ),
    "deepseek-r1-7b": ModelPricing(
        model_name="deepseek-r1-7b",
        price_per_million_input_tokens=0.55,
        price_per_million_output_tokens=2.19,
    ),
    "llama-3-8b": ModelPricing(
        model_name="llama-3-8b",
        price_per_million_input_tokens=0.20,
        price_per_million_output_tokens=0.20,
    ),
    "typesafe-local": ModelPricing(
        model_name="typesafe-local",
        price_per_million_input_tokens=0.0,
        price_per_million_output_tokens=0.0,
        supervisor_cost_per_decision=0.0001,
    ),
}


class StepEfficiencyRecord(BaseModel):
    """Registro detallado de telemetría de eficiencia de un único paso normativo."""
    model_config = ConfigDict(frozen=True)

    step_index: int
    action_id: str
    tool_name: str
    decision_status: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_total: int = 0
    llm_calls: int = 1
    llm_latency_ms: float = 0.0
    praxeon_overhead_ms: float = 0.0
    execution_time_ms: float = 0.0
    total_step_latency_ms: float = 0.0
    physical_execution_attempted: bool = False
    physical_execution_allowed: bool = False
    physical_execution_success: Optional[bool] = None
    is_error: bool = False
    cost_usd: float = 0.0
    timestamp: float = Field(default_factory=time.time)


class SessionEfficiencySummary(BaseModel):
    """Resumen consolidado de telemetría de eficiencia para una trayectoria completa."""
    model_config = ConfigDict(frozen=True)

    session_id: str
    scale: str  # 'short', 'medium', 'long'
    model_name: str
    total_steps: int
    successful_completion: bool
    total_tokens_in: int
    total_tokens_out: int
    total_tokens: int
    total_llm_calls: int
    total_llm_latency_ms: float
    total_praxeon_overhead_ms: float
    total_execution_time_ms: float
    total_session_latency_ms: float
    decision_overhead_percentage: float
    physical_executions_proposed: int
    physical_executions_allowed: int
    physical_executions_prevented: int
    execution_reduction_percentage: float
    errors_count: int
    total_cost_usd: float
    steps: List[StepEfficiencyRecord] = Field(default_factory=list)


class EfficiencyMetrics(BaseModel):
    """Métricas formales de eficiencia agregadas sobre un conjunto de ejecuciones (Fase 1)."""
    model_config = ConfigDict(frozen=True)

    total_tasks_evaluated: int
    successful_tasks_count: int
    task_success_rate: float
    
    # 1. Context Reduction Ratio (CRR): Ahorro porcentual de tokens frente a baseline ciego
    context_reduction_ratio: float
    avg_tokens_per_task: float
    avg_tokens_in_per_task: float
    avg_tokens_out_per_task: float

    # 2. Decision Overhead (DO): Sobrecoste temporal del supervisor frente al tiempo total
    decision_overhead_ms: float
    decision_overhead_percentage: float
    avg_llm_latency_per_step_ms: float
    avg_step_latency_ms: float

    # 3. Execution Reduction (ER): Proporción de ejecuciones físicas estériles o destructivas evitadas
    execution_reduction_rate: float
    total_proposed_executions: int
    total_allowed_executions: int
    total_prevented_executions: int

    # 4. Cost per Successful Task (CPST): Gasto económico estimado por tarea resuelta exitosamente
    cost_per_successful_task_usd: float
    total_cost_usd: float
    avg_cost_per_task_usd: float


class EfficiencyCalculator:
    """Calculador formal de métricas de eficiencia comparativa."""

    @staticmethod
    def calculate_step_cost(tokens_in: int, tokens_out: int, model_name: str) -> float:
        """Calcula el coste económico en USD de un paso según el catálogo de precios."""
        pricing = MODEL_PRICING_CATALOG.get(model_name)
        if not pricing:
            pricing = MODEL_PRICING_CATALOG["deepseek-r1-7b"]
        
        cost_in = (tokens_in / 1_000_000.0) * pricing.price_per_million_input_tokens
        cost_out = (tokens_out / 1_000_000.0) * pricing.price_per_million_output_tokens
        return cost_in + cost_out + pricing.supervisor_cost_per_decision

    @classmethod
    def aggregate_session(
        cls,
        session_id: str,
        scale: str,
        model_name: str,
        successful_completion: bool,
        steps: List[StepEfficiencyRecord],
    ) -> SessionEfficiencySummary:
        """Agrega los registros de pasos en un resumen de sesión con métricas consolidadas."""
        total_steps = len(steps)
        total_tokens_in = sum(s.tokens_in for s in steps)
        total_tokens_out = sum(s.tokens_out for s in steps)
        total_tokens = total_tokens_in + total_tokens_out
        total_llm_calls = sum(s.llm_calls for s in steps)
        total_llm_latency_ms = sum(s.llm_latency_ms for s in steps)
        total_praxeon_overhead_ms = sum(s.praxeon_overhead_ms for s in steps)
        total_execution_time_ms = sum(s.execution_time_ms for s in steps)
        total_session_latency_ms = sum(s.total_step_latency_ms for s in steps)

        decision_overhead_pct = (
            (total_praxeon_overhead_ms / total_session_latency_ms * 100.0)
            if total_session_latency_ms > 0
            else 0.0
        )

        proposed_execs = sum(1 for s in steps if s.physical_execution_attempted)
        allowed_execs = sum(1 for s in steps if s.physical_execution_allowed)
        prevented_execs = proposed_execs - allowed_execs
        exec_reduction_pct = (
            (prevented_execs / proposed_execs * 100.0) if proposed_execs > 0 else 0.0
        )

        errors_count = sum(1 for s in steps if s.is_error)
        total_cost_usd = sum(s.cost_usd for s in steps)

        return SessionEfficiencySummary(
            session_id=session_id,
            scale=scale,
            model_name=model_name,
            total_steps=total_steps,
            successful_completion=successful_completion,
            total_tokens_in=total_tokens_in,
            total_tokens_out=total_tokens_out,
            total_tokens=total_tokens,
            total_llm_calls=total_llm_calls,
            total_llm_latency_ms=round(total_llm_latency_ms, 2),
            total_praxeon_overhead_ms=round(total_praxeon_overhead_ms, 2),
            total_execution_time_ms=round(total_execution_time_ms, 2),
            total_session_latency_ms=round(total_session_latency_ms, 2),
            decision_overhead_percentage=round(decision_overhead_pct, 2),
            physical_executions_proposed=proposed_execs,
            physical_executions_allowed=allowed_execs,
            physical_executions_prevented=prevented_execs,
            execution_reduction_percentage=round(exec_reduction_pct, 2),
            errors_count=errors_count,
            total_cost_usd=round(total_cost_usd, 6),
            steps=steps,
        )

    @classmethod
    def calculate_comparative_metrics(
        cls,
        praxeon_sessions: List[SessionEfficiencySummary],
        baseline_sessions: Optional[List[SessionEfficiencySummary]] = None,
    ) -> EfficiencyMetrics:
        """Calcula las 4 métricas formales de la Fase 1 comparando PRAXEON frente a Baseline."""
        if not praxeon_sessions:
            raise ValueError("Se requiere al menos una sesión de PRAXEON para calcular métricas.")

        total_tasks = len(praxeon_sessions)
        success_count = sum(1 for s in praxeon_sessions if s.successful_completion)
        success_rate = (success_count / total_tasks) if total_tasks > 0 else 0.0

        total_prax_tokens = sum(s.total_tokens for s in praxeon_sessions)
        avg_tokens_per_task = total_prax_tokens / total_tasks
        avg_tokens_in = sum(s.total_tokens_in for s in praxeon_sessions) / total_tasks
        avg_tokens_out = sum(s.total_tokens_out for s in praxeon_sessions) / total_tasks

        # 1. Context Reduction Ratio (CRR)
        if baseline_sessions and len(baseline_sessions) == total_tasks:
            total_baseline_tokens = sum(s.total_tokens for s in baseline_sessions)
            if total_baseline_tokens > 0:
                crr = max(0.0, (1.0 - (total_prax_tokens / total_baseline_tokens)) * 100.0)
            else:
                crr = 0.0
        else:
            total_saved_steps = sum(s.physical_executions_prevented for s in praxeon_sessions)
            crr = (total_saved_steps / max(1, sum(s.total_steps for s in praxeon_sessions))) * 100.0

        # 2. Decision Overhead (DO)
        total_overhead_ms = sum(s.total_praxeon_overhead_ms for s in praxeon_sessions)
        total_time_ms = sum(s.total_session_latency_ms for s in praxeon_sessions)
        do_ms = total_overhead_ms / max(1, sum(s.total_steps for s in praxeon_sessions))
        do_pct = (total_overhead_ms / total_time_ms * 100.0) if total_time_ms > 0 else 0.0

        total_llm_lat_ms = sum(s.total_llm_latency_ms for s in praxeon_sessions)
        avg_llm_step = total_llm_lat_ms / max(1, sum(s.total_steps for s in praxeon_sessions))
        avg_step_lat = total_time_ms / max(1, sum(s.total_steps for s in praxeon_sessions))

        # 3. Execution Reduction (ER)
        total_proposed = sum(s.physical_executions_proposed for s in praxeon_sessions)
        total_allowed = sum(s.physical_executions_allowed for s in praxeon_sessions)
        total_prevented = sum(s.physical_executions_prevented for s in praxeon_sessions)
        er_rate = (total_prevented / total_proposed * 100.0) if total_proposed > 0 else 0.0

        # 4. Cost per Successful Task (CPST)
        total_cost = sum(s.total_cost_usd for s in praxeon_sessions)
        avg_cost = total_cost / total_tasks
        cpst = (total_cost / success_count) if success_count > 0 else float("inf")

        return EfficiencyMetrics(
            total_tasks_evaluated=total_tasks,
            successful_tasks_count=success_count,
            task_success_rate=round(success_rate, 4),
            context_reduction_ratio=round(crr, 2),
            avg_tokens_per_task=round(avg_tokens_per_task, 1),
            avg_tokens_in_per_task=round(avg_tokens_in, 1),
            avg_tokens_out_per_task=round(avg_tokens_out, 1),
            decision_overhead_ms=round(do_ms, 2),
            decision_overhead_percentage=round(do_pct, 2),
            avg_llm_latency_per_step_ms=round(avg_llm_step, 2),
            avg_step_latency_ms=round(avg_step_lat, 2),
            execution_reduction_rate=round(er_rate, 2),
            total_proposed_executions=total_proposed,
            total_allowed_executions=total_allowed,
            total_prevented_executions=total_prevented,
            cost_per_successful_task_usd=round(cpst, 4),
            total_cost_usd=round(total_cost, 4),
            avg_cost_per_task_usd=round(avg_cost, 4),
        )
