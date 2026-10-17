"""Benchmark de agente: misma tarea y mismo LLM con y sin el arnés de PRAXEON (tokens, tiempo, éxito, bloqueos, coste)."""

from praxeon.evaluation.agentbench.metering import MeteredLLM, PriceTable
from praxeon.evaluation.agentbench.report import aggregate, paired, render_markdown
from praxeon.evaluation.agentbench.runner import run_matrix, run_one
from praxeon.evaluation.agentbench.tasks import Task, build_suite

__all__ = ["MeteredLLM", "PriceTable", "Task", "aggregate", "build_suite", "paired", "render_markdown", "run_matrix", "run_one"]
