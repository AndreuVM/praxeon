"""Consola interactiva y visualizador CLI enriquecido con Rich para JEV-Reasoning-Navigator."""

import argparse
import io
import json
import os
from pathlib import Path
import sys
from typing import List, Optional

# Asegurar codificación UTF-8 en Windows para evitar errores con charmap cp1252
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer") and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from praxeon import __version__
from praxeon.config import JEVConfig, default_config
from praxeon.core.intervention_policy import InterventionPolicy
from praxeon.core.jev_engine import JEVEngine
from praxeon.core.state_graph import StateGraph
from praxeon.models.schema import (
    ActionCandidate,
    InterventionDirective,
    InterventionLevel,
    JEVScore,
    LoopReport,
    LoopType,
    Step,
    StepType,
    Trajectory,
)
from praxeon.models.trace import TraceParser

console = Console(legacy_windows=False)


def analyze_trace_file(file_path: str, config: Optional[JEVConfig] = None) -> None:
    """Carga y analiza detalladamente una traza de razonamiento mediante TypeSafe AI."""
    path = Path(file_path)
    if not path.exists():
        console.print(f"[bold red]Error:[/] El archivo '{file_path}' no existe.")
        return

    # Cargar contenido
    text_content = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text_content)
        if isinstance(data, dict):
            trajectory = TraceParser.from_dict(data)
        elif isinstance(data, list):
            trajectory = TraceParser.from_dict({"steps": data})
        else:
            trajectory = TraceParser.from_scratchpad(text_content, goal=path.stem)
    except Exception:
        trajectory = TraceParser.from_scratchpad(text_content, goal=path.stem)

    cfg = config or default_config
    graph = StateGraph(cfg)
    graph.load_trajectory(trajectory)

    engine = JEVEngine(graph, cfg)
    policy = InterventionPolicy(graph, cfg)

    # 1. Ejecutar evaluaciones en lote con TypeSafe AI
    loop_report = engine.diagnose_trajectory(trajectory)
    scores: List[JEVScore] = []
    for step in trajectory.steps:
        score = engine.evaluate_step(step)
        scores.append(score)
        graph.set_step_jev(step.id, score.total_jev)

    directive = policy.evaluate_and_intervene(
        current_step=trajectory.steps[-1] if trajectory.steps else None,
        jev_score=scores[-1] if scores else None,
        loop_report=loop_report,
    )

    # 2. Renderizar Header
    status_text = (
        "[bold red]ALERTA DE BUCLE O ALUCINACIÓN DETECTADA[/]"
        if loop_report.loop_detected
        else "[bold green]TRAYECTORIA CONVERGENTE SALUDABLE[/]"
    )
    engine_name = "🚀 [bold magenta]TypeSafe AI (Modelo Jev - System One)[/]"
    header_panel = Panel(
        f"[bold white]Sesión:[/] {trajectory.session_id}\n"
        f"[bold white]Objetivo:[/] {trajectory.goal}\n"
        f"[bold white]Motor evaluador:[/] {engine_name}\n"
        f"[bold white]Pasos analizados:[/] {len(trajectory.steps)}\n"
        f"[bold white]Estado global:[/] {status_text}",
        title="🧠 [bold cyan]JEV-Reasoning-Navigator: Diagnóstico de Traza[/]",
        border_style="cyan" if not loop_report.loop_detected else "red",
    )
    console.print(header_panel)

    # 3. Renderizar Árbol de Razonamiento
    tree = Tree(f"🎯 [bold yellow]Meta:[/] {trajectory.goal[:80]}")
    cycle_nodes_set = set(loop_report.cycle_nodes)

    for idx, (step, sc) in enumerate(zip(trajectory.steps, scores)):
        is_cycle = step.id in cycle_nodes_set
        score_val = sc.total_jev

        if is_cycle or score_val < 0.0:
            color = "red"
            badge = "❌ [bold red]LOOP / BAJO JEV[/]"
        elif score_val >= 0.20:
            color = "green"
            badge = "✅ [bold green]ALTO JEV[/]"
        else:
            color = "yellow"
            badge = "⚠️  [bold yellow]MEDIO JEV[/]"

        preview = (step.content or f"{step.tool_name} {step.tool_args or ''}").strip()
        if len(preview) > 75:
            preview = preview[:75] + "..."

        step_node = tree.add(
            f"[{color}][{step.id} ({step.step_type.value})] {preview}[/]  "
            f"[dim](JEV: {score_val:+.2f})[/] {badge}"
        )

    console.print("\n[bold cyan]Árbol Cronológico de Razonamiento:[/]")
    console.print(tree)

    # 4. Tabla analítica de evaluación semántica TypeSafe AI (System One)
    table = Table(
        title="Evaluación Semántica de Trayectoria: TypeSafe AI (System One)",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Paso", style="dim", width=8)
    table.add_column("Tipo", width=11)
    table.add_column("Herramienta / Contenido", width=36)
    table.add_column("Noul (Riesgo)", justify="right", width=13)
    table.add_column("Avance Meta", justify="center", width=15)
    table.add_column("Diagnóstico Semántico", justify="left", width=24)
    table.add_column("Estado", justify="center", width=10)

    for step, sc in zip(trajectory.steps, scores):
        is_cycle = step.id in cycle_nodes_set
        content_repr = step.tool_name if step.tool_name else step.content
        if len(content_repr) > 33:
            content_repr = content_repr[:33] + "..."

        ts_chunk = sc.details.get("chunk_eval") or {}
        noul_val = float(ts_chunk.get("noul_prob", 0.15 if sc.total_jev >= 0 else 0.85))
        noul_style = "bold red" if noul_val >= 0.6 else ("bold yellow" if noul_val >= 0.35 else "bold green")
        diag_label = ts_chunk.get("hallucination_type") or ("LOOP" if is_cycle else "NONE")

        progress_label = "DIRECT_SOLUTION" if sc.p_progress >= 0.9 else ("SIGNIFICANT" if sc.p_progress >= 0.7 else "MINOR")
        status_tag = "[red]BLOCK[/]" if (is_cycle or sc.total_jev < 0) else "[green]ALLOW[/]"

        table.add_row(
            step.id,
            step.step_type.value,
            content_repr,
            f"[{noul_style}]{noul_val:.2f}[/]",
            f"[cyan]{progress_label}[/]",
            f"[dim]{diag_label}[/]",
            status_tag,
        )

    console.print("\n")
    console.print(table)

    # 5. Panel de Intervención si se requiere
    if directive:
        border = "red" if directive.level >= InterventionLevel.LEVEL_2_FORCED_BACKTRACKING else "yellow"
        directive_panel = Panel(
            f"[bold]{directive.message}[/]\n\n"
            f"[bold yellow]Acciones Prohibidas:[/] {directive.forbidden_actions or 'Ninguna'}\n"
            f"[bold green]Acción Sugerida:[/] {directive.suggested_action or 'Continuar'}\n\n"
            f"[bold cyan]Prompt Inyectable al Agente:[/]\n[dim]{directive.context_injection}[/]",
            title=f"🚨 [bold red]Directiva de Intervención Activada ({directive.level.name})[/]",
            border_style=border,
        )
        console.print("\n")
        console.print(directive_panel)
    else:
        console.print("\n[bold green]✓ No se requiere ninguna intervención correctiva.[/]")


def simulate_trace_execution(file_path: str, config: Optional[JEVConfig] = None) -> None:
    """Simula la ejecución paso a paso de una traza interceptando bucles en tiempo real."""
    from rich.markup import escape

    path = Path(file_path)
    if not path.exists():
        console.print(f"[bold red]Error:[/] Archivo no encontrado '{file_path}'.")
        return

    text_content = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text_content)
        trajectory = TraceParser.from_dict(data)
    except Exception:
        trajectory = TraceParser.from_scratchpad(text_content)

    cfg = config or default_config
    graph = StateGraph(cfg)
    engine = JEVEngine(graph, cfg)
    policy = InterventionPolicy(graph, cfg)

    engine_name = "🚀 [bold magenta]TypeSafe AI (Modelo Jev - System One)[/]"

    console.print(
        Panel(
            f"Objetivo: [bold]{escape(trajectory.goal)}[/]\n"
            f"Motor evaluador: {engine_name}",
            title="🕹️ [bold blue]Modo Simulación JEV[/]"
        )
    )

    graph.goal = trajectory.goal

    for i, step in enumerate(trajectory.steps):
        desc = escape((step.tool_name or step.content or "").strip())
        if len(desc) > 60:
            desc = desc[:60] + "..."
        console.print(f"\n[bold cyan]--> Procesando paso {i+1}/{len(trajectory.steps)}:[/] [bold yellow]{escape(step.id)}[/] ({escape(step.step_type.value)}: [dim]{desc}[/])")
        graph.add_step(step)
        score = engine.evaluate_step(step)
        graph.set_step_jev(step.id, score.total_jev)

        ts_eval = score.details.get("typesafe_eval") or {}
        is_loop = bool(ts_eval.get("is_loop", False) or score.total_jev < cfg.critical_jev_threshold)
        loop_type = ts_eval.get("loop_type", LoopType.ONE_HOP_TOOL_REPEAT if is_loop else LoopType.NONE)

        loop_rep = LoopReport(
            loop_detected=is_loop,
            loop_type=loop_type,
            severity=3 if is_loop else 0,
            explanation=f"Evaluación TypeSafe: {'bucle o estancamiento detectado' if is_loop else 'acción constructiva'}",
            culprit_tool=step.tool_name,
        )

        directive = policy.evaluate_and_intervene(current_step=step, jev_score=score, loop_report=loop_rep)

        console.print(f"    JEV: [bold {'green' if score.total_jev >= 0 else 'red'}]{score.total_jev:+.2f}[/] (P={score.p_progress:.2f}, ΔU={score.delta_u:.2f}, Penalty={score.loop_penalty:.2f})")

        if directive and (is_loop or score.total_jev < cfg.critical_jev_threshold):
            console.print(f"    [bold red]🛑 ¡INTERCEPCIÓN ACTIVADA![/] Nivel: [bold yellow]{directive.level.name}[/]")
            console.print(f"    Directiva inyectada: {directive.suggested_action}")
            if directive.level >= InterventionLevel.LEVEL_2_FORCED_BACKTRACKING:
                console.print(f"    [bold magenta]PODA DE RAMA:[/] Retroceder a nodo '[bold white]{directive.target_step_id}[/]'. Prohibido: [red]{directive.forbidden_actions}[/]")
                break


def run_benchmark_cli(
    ablation: bool = False,
    compare_v1: bool = False,
    compare_providers: bool = False,
    provider_name: str = "replay",
    count: Optional[int] = None,
    output_file: Optional[str] = None,
) -> None:
    """Ejecuta y formatea en consola el benchmark formal de supervisión (PRAXEON 1.0)."""
    from praxeon.evaluation import BenchmarkRunner, ScenarioCatalog
    from praxeon.evaluation.metrics import compute_navigator_economic_value
    from praxeon.runtime import Navigator, SecureExecutor
    from praxeon.providers.replay import ReplayProvider
    from praxeon.providers.laya import LayaProvider
    from praxeon.providers.typesafe import TypeSafeAdapter

    if provider_name == "laya":
        prov = LayaProvider(backend="simulated")
        nav = Navigator(provider=prov, executor=SecureExecutor(dry_run=True))
        runner = BenchmarkRunner(navigator=nav)
    elif provider_name == "typesafe":
        prov = TypeSafeAdapter(api_key=None)
        nav = Navigator(provider=prov, executor=SecureExecutor(dry_run=True))
        runner = BenchmarkRunner(navigator=nav)
    else:
        runner = BenchmarkRunner()

    if count and count > 0:
        scenarios = ScenarioCatalog.generate_large_scale_dataset(count=count)
    else:
        scenarios = ScenarioCatalog.get_extended_scenarios()

    console.print(f"\n🔬 [bold cyan]Iniciando Suite Formal de Benchmark PRAXEON ({len(scenarios)} escenarios)[/]...\n")

    if compare_providers:
        comp = runner.run_provider_comparison(scenarios=scenarios)
        table = Table(title=f"⚖️ [bold white]Comparativa de Proveedores: {comp.provider_a_name} vs {comp.provider_b_name}[/]", border_style="green")
        table.add_column("Métrica de Concordancia y Eficiencia", style="bold yellow")
        table.add_column("Valor Evaluado", style="cyan")

        table.add_row("Total de Escenarios Evaluados", str(comp.total_scenarios))
        table.add_row("Tasa de Concordancia (Agreement Rate)", f"[bold green]{comp.agreement_rate * 100:.1f}%[/] ({comp.agreement_count}/{comp.total_scenarios})")
        table.add_row("Tasa de Discrepancia Decisional", f"[bold {'red' if comp.disagreement_count > 0 else 'green'}]{comp.disagreement_rate * 100:.1f}%[/] ({comp.disagreement_count})")
        table.add_row(f"Latencia Media ({comp.provider_a_name})", f"{comp.provider_a_avg_latency_ms:.2f} ms (p95: {comp.provider_a_p95_latency_ms:.2f} ms)")
        table.add_row(f"Latencia Media ({comp.provider_b_name})", f"{comp.provider_b_avg_latency_ms:.2f} ms (p95: {comp.provider_b_p95_latency_ms:.2f} ms)")
        console.print(table)
        if comp.disagreements:
            console.print(f"\n[bold yellow]Detalle de Discrepancias ({len(comp.disagreements)}):[/]")
            for d in comp.disagreements[:5]:
                console.print(f" - [bold cyan]{d['scenario_id']}:[/] JEV loop={d['provider_a']['loop']} vs LAYA loop={d['provider_b']['loop']}")
        return

    if compare_v1:
        comp = runner.compare_v01_vs_v02(scenarios)
        table = Table(title="🛡️ [bold white]Comparativa de Seguridad y Calidad: Baseline Sin Supervisor vs PRAXEON 1.0[/]", border_style="cyan")
        table.add_column("Métrica", style="bold yellow")
        table.add_column("Baseline (Sin Supervisor / Permisivo)", style="red")
        table.add_column("PRAXEON 1.0 (Enterprise GA)", style="green")


        v1_data = comp["v0.1"]
        v2_data = comp["v0.2"]
        for k in v1_data:
            table.add_row(k, str(v1_data[k]), str(v2_data[k]))

        console.print(table)
        console.print(f"\n[bold green]Resultado clave:[/] Reducción de falso permitido: [bold]{comp['false_allow_reduction']}[/]")
        if comp["critical_safety_gap_closed"]:
            console.print("[bold green]✅ Brecha de seguridad crítica cerrada:[/] 0 acciones destructivas indebidamente autorizadas.")
        return

    if ablation:
        ablations = runner.run_ablation_study(scenarios)
        table = Table(title="🧪 [bold white]Estudio Formal de Ablaciones (5 Capas Arquitectónicas)[/]", border_style="magenta")
        table.add_column("Configuración", style="bold cyan")
        table.add_column("Accuracy", justify="center")
        table.add_column("F1-Score", justify="center")
        table.add_column("False Allow Rate (Crítico)", justify="center")
        table.add_column("Destructive False Allows", justify="center")
        table.add_column("Latencia Media", justify="center")

        for name, m in ablations.items():
            color = "green" if m.destructive_false_allow_count == 0 else "red"
            table.add_row(
                name,
                f"{m.accuracy * 100:.1f}%",
                f"{m.f1_score:.3f}",
                f"[{color}]{m.false_allow_rate * 100:.1f}%[/]",
                f"[{color}]{m.destructive_false_allow_count}[/]",
                f"{m.latency_mean_ms:.2f} ms",
            )
        console.print(table)
        return

    # Ejecución estándar de benchmark
    report = runner.run_benchmark(scenarios=scenarios)

    # Si hay demasiados escenarios (ej. 1000), mostrar solo los primeros 20 en la tabla detallada
    display_results = report.results[:20] if len(report.results) > 20 else report.results
    table = Table(
        title=f"📋 [bold white]Resultados de Escenarios: {report.suite_name} (Mostrando {len(display_results)}/{len(report.results)})[/]",
        border_style="cyan",
    )
    table.add_column("ID Escenario", style="bold white")
    table.add_column("Categoría", style="cyan")
    table.add_column("Esperado", justify="center")
    table.add_column("Emitido", justify="center")
    table.add_column("Estado", justify="center")
    table.add_column("Latencia", justify="right")

    for r in display_results:
        status_sym = "[bold green]✅ PASS[/]" if r.is_match else "[bold red]❌ FAIL[/]"
        table.add_row(
            r.scenario_id,
            r.category,
            f"[yellow]{r.expected_status.value.upper()}[/]",
            f"[bold {('green' if r.is_match else 'red')}]{r.actual_status.value.upper()}[/]",
            status_sym,
            f"{r.latency_ms:.2f} ms",
        )

    console.print(table)

    econ = compute_navigator_economic_value(report.metrics)

    summary_panel = Panel(
        f"[bold white]Total Escenarios:[/] {report.metrics.total_scenarios} [dim](Dataset de Validación Procedural)[/]\n"
        f"[bold white]Exactitud Decisional:[/] [bold green]{report.metrics.accuracy * 100:.1f}%[/]\n"
        f"[bold white]F1-Score Decisional:[/] [bold green]{report.metrics.f1_score:.3f}[/]\n"
        f"[bold white]False Allow Rate (Métrica Crítica):[/] [bold green]{report.metrics.false_allow_rate * 100:.1f}%[/]\n"
        f"[bold white]Acciones Destructivas Falsamente Permitidas:[/] [bold green]{report.metrics.destructive_false_allow_count}[/]\n"
        f"[bold cyan]─ Seguridad de Ejecución Física y Capabilities ─[/]\n"
        f"[bold white]Prevención de Ejecución No Autorizada:[/] [bold green]{report.metrics.execution_prevention_rate * 100:.1f}%[/]\n"
        f"[bold white]Ejecuciones Físicas No Autorizadas:[/] [bold green]{report.metrics.unauthorized_physical_executions}[/]\n"
        f"[bold white]Verificación de Capabilities/Receipt:[/] [bold green]{report.metrics.capability_verification_rate * 100:.1f}%[/]\n"
        f"[bold white]Latencia p50 / p95:[/] {report.metrics.latency_p50_ms:.2f} ms / {report.metrics.latency_p95_ms:.2f} ms\n"
        f"[bold yellow]─ Valor Económico Estimado (Sección 20) ─[/]\n"
        f"[bold white]Valor Neto del Supervisor:[/] [bold green]${econ['net_navigator_value']:,.2f}[/]\n"
        f"[bold white]Coste de Fallos Evitados:[/] [green]${econ['gross_avoided_cost']:,.2f}[/] | [dim]Coste Supervisor: ${econ['navigator_cost']:.4f} | Penalización Rechazos: ${econ['false_block_cost']:.2f}[/]",
        title="📊 [bold green]Métricas Consolidadas de Gobernanza, Seguridad Física y Eficiencia[/]",
        border_style="green",
    )
    console.print(summary_panel)

    if output_file:
        Path(output_file).write_text(json.dumps(report.model_dump(), indent=2), encoding="utf-8")
        console.print(f"[bold green]Reporte exportado exitosamente a:[/] {output_file}")


def main() -> None:
    """Punto de entrada principal para CLI de PRAXEON."""
    parser = argparse.ArgumentParser(
        prog="praxeon",
        description="PRAXEON: Runtime supervision for autonomous AI agents",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", help="Comando a ejecutar")

    # Subcomando analyze
    analyze_parser = subparsers.add_parser("analyze", help="Analiza y visualiza una traza con árbol y tabla Rich")
    analyze_parser.add_argument("trace_path", type=str, help="Ruta al archivo de traza (.json o .txt)")
    analyze_parser.add_argument("--typesafe", action="store_true", help="Habilitar evaluación remota mediante el modelo Jev de TypeSafe AI")
    analyze_parser.add_argument("--api-key", type=str, default=None, help="Clave API de TypeSafe AI (o configurar TYPESAFE_API_KEY en .env)")

    # Subcomando simulate
    sim_parser = subparsers.add_parser("simulate", help="Simula paso a paso la ejecución de una traza interceptando bucles")
    sim_parser.add_argument("trace_path", type=str, help="Ruta al archivo de traza (.json o .txt)")
    sim_parser.add_argument("--typesafe", action="store_true", help="Habilitar evaluación remota mediante el modelo Jev de TypeSafe AI")
    sim_parser.add_argument("--api-key", type=str, default=None, help="Clave API de TypeSafe AI (o configurar TYPESAFE_API_KEY en .env)")

    # Subcomando dashboard / visual
    dash_parser = subparsers.add_parser("dashboard", aliases=["visual"], help="Panel visual interactivo TUI del trabajo conjunto Agente ⇄ PRAXEON")
    dash_parser.add_argument("--task", type=str, default=None, help="Objetivo o descripción de la tarea a visualizar")
    dash_parser.add_argument("--live", action="store_true", help="Ejecutar en modo vivo con agente LLM")
    dash_parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="Proveedor del Agent LLM (groq, ollama, openrouter, gemini, lmstudio, openai, simulated)",
    )
    dash_parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="URL base para OpenAI-compatible (ej. http://localhost:11434/v1 para Ollama)",
    )
    dash_parser.add_argument(
        "--agent-key",
        "--api-key",
        dest="agent_key",
        type=str,
        default=None,
        help="Clave API del proveedor LLM para el agente",
    )
    dash_parser.add_argument("--model", type=str, default=None, help="Modelo LLM para modo vivo")
    dash_parser.add_argument(
        "--supervisor",
        type=str,
        default=os.getenv("PRAXEON_SUPERVISOR", "jev"),
        choices=["jev", "laya", "typesafe"],
        help="Motor supervisor cognitivo: 'jev' (TypeSafe AI Cloud) o 'laya' (LAYA System-1 Local/Hosted)",
    )
    dash_parser.add_argument(
        "--supervisor-model",
        type=str,
        default=None,
        help="Modelo específico para el supervisor (ej. 'laya-v1-calibrated' o 'jev-latest')",
    )
    dash_parser.add_argument(
        "--laya-backend",
        type=str,
        default=os.getenv("LAYA_BACKEND", "auto"),
        choices=["auto", "local", "simulated", "hosted"],
        help="Backend para el supervisor LAYA ('auto', 'local', 'simulated', 'hosted')",
    )
    dash_parser.add_argument("--max-steps", type=int, default=25, help="Número máximo de turnos permitidos")
    dash_parser.add_argument("--once", action="store_true", help="Ejecutar una única tarea y salir inmediatamente sin modo interactivo continuo")

    # Subcomando live
    live_parser = subparsers.add_parser("live", help="Ejecuta el agente autónomo con supervisión interactiva PRAXEON")
    live_parser.add_argument("--task", type=str, default=None, help="Objetivo o descripción de la tarea a resolver")
    live_parser.add_argument(
        "--supervisor",
        type=str,
        default=os.getenv("PRAXEON_SUPERVISOR", "jev"),
        choices=["jev", "laya", "typesafe"],
        help="Motor supervisor cognitivo: 'jev' (TypeSafe AI Cloud) o 'laya' (LAYA System-1 Local/Hosted)",
    )
    live_parser.add_argument(
        "--supervisor-model",
        type=str,
        default=None,
        help="Modelo específico para el supervisor (ej. 'laya-v1-calibrated' o 'jev-latest')",
    )
    live_parser.add_argument(
        "--laya-backend",
        type=str,
        default=os.getenv("LAYA_BACKEND", "auto"),
        choices=["auto", "local", "simulated", "hosted"],
        help="Backend para el supervisor LAYA ('auto', 'local', 'simulated', 'hosted')",
    )
    live_parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="Proveedor del Agent LLM (groq, ollama, openrouter, gemini, lmstudio, openai, simulated)",
    )
    live_parser.add_argument("--model", type=str, default=None, help="Modelo LLM a utilizar")
    live_parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="URL base para OpenAI-compatible (ej. http://localhost:11434/v1 para Ollama)",
    )
    live_parser.add_argument(
        "--api-key",
        "--agent-key",
        dest="agent_key",
        type=str,
        default=None,
        help="Clave API para el Agent LLM",
    )
    live_parser.add_argument("--max-steps", "--steps", dest="max_steps", type=int, default=25, help="Número máximo de turnos")
    live_parser.add_argument("--once", action="store_true", help="Ejecutar una única tarea y salir")

    # Subcomando benchmark
    bench_parser = subparsers.add_parser("benchmark", help="Ejecuta la suite formal de benchmarks y ablaciones")
    bench_parser.add_argument("--ablation", action="store_true", help="Ejecutar el estudio formal de ablaciones de las 5 capas")
    bench_parser.add_argument("--compare-v1", "--compare-baseline", dest="compare_v1", action="store_true", help="Comparar métricas de seguridad frente a baseline sin supervisor")
    bench_parser.add_argument("--compare-providers", action="store_true", help="Comparar concordancia y latencia entre proveedores (JEV vs LAYA)")
    bench_parser.add_argument("--provider", type=str, default="replay", choices=["replay", "laya", "typesafe"], help="Proveedor de razonamiento a evaluar")
    bench_parser.add_argument("--count", type=int, default=None, help="Número de escenarios sintéticos a evaluar (ej. 1000 para dataset masivo)")
    bench_parser.add_argument("--output", type=str, default=None, help="Ruta para exportar el reporte en JSON")

    args = parser.parse_args()

    cfg = default_config.model_copy()
    if getattr(args, "supervisor", None):
        cfg.supervisor = args.supervisor
    if getattr(args, "supervisor_model", None):
        cfg.provider.model = args.supervisor_model
    if getattr(args, "laya_backend", None):
        cfg.laya_backend = args.laya_backend

    if args.command in ("analyze", "simulate") and getattr(args, "api_key", None):
        cfg.typesafe_api_key = args.api_key
        cfg.use_typesafe_api = True
    elif getattr(args, "typesafe", False):
        cfg.use_typesafe_api = True

    if args.command == "analyze":
        analyze_trace_file(args.trace_path, config=cfg)
    elif args.command == "simulate":
        simulate_trace_execution(args.trace_path, config=cfg)
    elif args.command == "live":
        from praxeon.live_agent import run_live_session
        run_live_session(
            initial_task=getattr(args, "task", None),
            max_steps=getattr(args, "max_steps", 25),
            config=cfg,
            api_key=getattr(args, "agent_key", None),
            model_name=getattr(args, "model", None),
            once=getattr(args, "once", False),
            provider=getattr(args, "provider", None),
            base_url=getattr(args, "base_url", None),
        )
    elif args.command in ("dashboard", "visual"):
        from praxeon.dashboard import run_visual_demo, run_visual_live
        if getattr(args, "live", False):
            run_visual_live(
                task=getattr(args, "task", None),
                model_name=getattr(args, "model", None),
                max_steps=getattr(args, "max_steps", 25),
                config=cfg,
                once=getattr(args, "once", False),
                provider=getattr(args, "provider", None),
                base_url=getattr(args, "base_url", None),
                api_key=getattr(args, "agent_key", None),
            )
        else:
            run_visual_demo(
                task=getattr(args, "task", None),
                once=getattr(args, "once", False),
            )
    elif args.command == "benchmark":
        run_benchmark_cli(
            ablation=getattr(args, "ablation", False),
            compare_v1=getattr(args, "compare_v1", False),
            compare_providers=getattr(args, "compare_providers", False),
            provider_name=getattr(args, "provider", "replay"),
            count=getattr(args, "count", None),
            output_file=getattr(args, "output", None),
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
