"""Controlador de trayectoria cognitiva para el Live Agent de PRAXEON.

Supervisa el progreso secuencial de pasos, detecta bucles degenerativos y parálisis,
gestiona el circuit-breaker de bloqueos y valida la admisibilidad semántica de finalización.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple
from rich.table import Table


class TrajectoryController:
    """Supervisa y controla la evolución de la trayectoria de ejecución de un agente."""

    EVASIVE_MARKERS = (
        "pendiente de",
        "pendiente",
        "planificación",
        "planificacion",
        "como soy un agente",
        "la acción real",
        "la accion real",
        "todavía no",
        "aún no he",
        "aun no he",
        "sin analizar",
        "no he podido leer",
        "no he podido",
        "provisional",
        "pending",
        "este paso es de",
    )

    META_LEAKAGE_MARKERS = (
        "ha sido vetada",
        "vetada temporalmente",
        "bucle o estancamiento",
        "acción pausada",
        "accion pausada",
        "acción bloqueada",
        "accion bloqueada",
        "bloqueado por el supervisor",
        "bloqueada por el supervisor",
        "error de supervisión",
        "error de supervision",
        "policy_engine",
        "system_intervention",
        "no tengo la autorización",
        "no tengo la autorizacion",
        "intervención activada",
        "intervencion activada",
    )

    def __init__(self, max_steps: int = 15, governance_mode: str = "BEST_EFFORT"):
        self.max_steps = max_steps
        self.governance_mode = governance_mode
        self.is_unlimited = (max_steps <= 0)
        self.executed_steps: int = 0
        self.executed_step_records: List[Dict[str, Any]] = []
        self.consecutive_blocks: int = 0
        self.interventions_count: int = 0
        self.llm_calls_count: int = 0
        self.typesafe_calls_count: int = 0
        self.synthetic_fallback_count: int = 0
        self.final_summary: str = ""
        self.final_answer: str = ""
        self.task_finished: bool = False

    def can_continue(self) -> bool:
        """Indica si el agente puede continuar ejecutando pasos."""
        if self.task_finished:
            return False
        if self.is_unlimited:
            return True
        return self.executed_steps < self.max_steps

    def record_step(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        observation: str,
        thought_text: str = "",
        synthetic_fallback: bool = False,
        model_source: str = "llm",
    ) -> None:
        """Registra un paso completado en la trayectoria con trazabilidad de procedencia."""
        self.executed_steps += 1
        if synthetic_fallback:
            self.synthetic_fallback_count += 1
        self.executed_step_records.append({
            "tool_name": tool_name,
            "tool_args": tool_args,
            "observation": observation,
            "thought_text": thought_text,
            "synthetic_fallback": synthetic_fallback,
            "model_source": model_source,
            "governance_mode": self.governance_mode,
        })

    def reset_consecutive_blocks(self) -> None:
        """Reinicia el contador de bloqueos consecutivos cuando un bloque resulta aprobado."""
        self.consecutive_blocks = 0

    def register_block_interception(
        self,
        explanation: str,
        directive_injection: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """Registra una intercepción de bloque y evalúa el disparador de Circuit Breaker.

        Returns:
            Tuple[is_circuit_breaker_triggered, directive_to_inject, optional_auto_probe_observation]
        """
        self.interventions_count += 1
        self.consecutive_blocks += 1

        directive_text = directive_injection if directive_injection else explanation

        # Circuit breaker ante bloqueos repetitivos
        if self.consecutive_blocks == 2:
            if self.executed_steps >= 2:
                directive_text += (
                    "\n\n💡 [ORIENTACIÓN DE CIERRE]: Ya has obtenido observaciones empíricas durante la sesión. "
                    "Si dispones de suficiente contexto para responder a la tarea del usuario, sintetiza tu informe o conclusión "
                    "y entrega el resultado invocando obligatoriamente:\n"
                    'Action: finish {"summary": "informe o conclusión fundamentada basada en lo observado"}'
                )
            else:
                directive_text += (
                    "\n\n🚨 [ALERTA DE DESBLOQUEO]: Has acumulado bloqueos seguidos. "
                    "Si la tarea requiere interactuar con el código, formula una hipótesis válida usando rutas reales o comandos del SO. "
                    "Si la tarea es conceptual o de diseño, sintetiza directamente en 'finish'."
                )
            return (False, directive_text, None)

        if self.consecutive_blocks >= 3:
            auto_probe = "Observación automática de archivos reales en disco: "
            try:
                auto_probe += ", ".join([f for f in os.listdir(".") if not f.startswith(".")][:6])
            except Exception:
                auto_probe += "(espacio de trabajo local no listable)"
            self.consecutive_blocks = 0
            return (True, directive_text, auto_probe)

        return (False, directive_text, None)

    def validate_finish_action(self, summary: str, task: str) -> Tuple[bool, str, str]:
        """Evalúa si la llamada a 'finish' es válida o evasiva/con fuga de meta-diagnóstico.

        Returns:
            Tuple[is_valid, rejection_reason_or_empty, observation_text]
        """
        sum_lower = str(summary).lower()
        if any(m in sum_lower for m in self.EVASIVE_MARKERS):
            obs = (
                "OBSERVACIÓN DEL SUPERVISOR (JEV): Tu llamada a 'finish' ha sido RECHAZADA porque contiene un texto de planificación o evasión ('pendiente de lectura'). "
                "NO puedes finalizar sin dar una respuesta concreta. Analiza las observaciones y el contenido ya obtenido y responde directamente con los hallazgos en tu siguiente turno."
            )
            return (False, "evasive", obs)

        if any(m in sum_lower for m in self.META_LEAKAGE_MARKERS):
            obs = (
                f"OBSERVACIÓN DEL SUPERVISOR (JEV): Tu llamada a 'finish' ha sido RECHAZADA porque estás describiendo "
                f"mensajes de diagnóstico interno del supervisor en lugar de responder a la tarea del usuario: '{task}'. "
                f"Prohibido mencionar 'herramienta vetada', 'bucle detectado' o 'policy_engine'. Responde directamente a: '{task}' "
                f"con tus conclusiones y opinión fundamentada sobre el proyecto que has inspeccionado."
            )
            return (False, "meta_leakage", obs)

        self.task_finished = True
        self.final_summary = summary
        self.final_answer = summary
        return (True, "", f"Tarea finalizada: {summary}")

    def build_metrics_table(self, provider_name: str = "LLM") -> Table:
        """Construye una tabla Rich con las métricas finales de ejecución y ahorro."""
        savings_pct = max(0, int(((self.executed_steps - self.llm_calls_count) / max(1, self.executed_steps)) * 100))
        table = Table(title="📊 Métricas de Eficiencia JEV y Ahorro de Cuota API", show_header=True)
        table.add_column("Métrica", style="bold white")
        table.add_column("Valor", justify="right", style="bold cyan")
        table.add_column("Impacto", style="green")

        table.add_row("Pasos cognitivos ejecutados", str(self.executed_steps), "Progreso real")
        table.add_row(f"Llamadas a LLM ({provider_name.upper()})", str(self.llm_calls_count), f"Ahorro de ~{savings_pct}% en llamadas LLM")
        table.add_row("Llamadas a TypeSafe AI", str(self.typesafe_calls_count), "Evaluaciones en lote (Chunking)")
        table.add_row("Intervenciones JEV / Alucinaciones evitadas", str(self.interventions_count), "Prevención de desvíos cognitivos")
        table.add_row("Modo de gobernanza LLM", str(self.governance_mode), "Seguridad / Fallback")
        table.add_row(
            "Pasos en Fallback Sintético",
            str(self.synthetic_fallback_count),
            "Degradación controlada" if self.synthetic_fallback_count else "100% LLM real",
        )
        return table

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Retorna un diccionario estructurado de métricas de ejecución."""
        return {
            "executed_steps": self.executed_steps,
            "llm_calls_count": self.llm_calls_count,
            "typesafe_calls_count": self.typesafe_calls_count,
            "interventions_count": self.interventions_count,
            "consecutive_blocks": self.consecutive_blocks,
            "task_finished": self.task_finished,
            "governance_mode": self.governance_mode,
            "synthetic_fallback_count": self.synthetic_fallback_count,
        }
