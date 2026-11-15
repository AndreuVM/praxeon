"""Visual Dashboard interactivo y enriquecido con Rich para supervisión con TypeSafe AI (System One)."""

import argparse
import io
import json
import os
import sys
import time
from typing import Any

# UTF-8 en terminales Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer") and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
from rich.prompt import Prompt

from praxeon.config import PraxeonConfig, default_config
from praxeon.core.session_context import SessionContextManager
from praxeon.domain.events import EventType, RuntimeEvent
from praxeon.domain.governance import FallbackMode
from praxeon.interceptor.proxy_middleware import JEVProxyMiddleware
from praxeon.live_agent.mission_runner import _run_with_recovery, ask_approval
from praxeon.live_agent.runtime_session import LiveTaskResult, TerminalMission

console = Console(legacy_windows=False)


def render_noul_bar(prob: float, width: int = 16) -> str:
    """Genera una barra visual de riesgo de alucinación/bucle basada en la probabilidad Noul de TypeSafe AI."""
    clamped = max(0.0, min(1.0, prob))
    filled = int(clamped * width)
    empty = width - filled

    if prob < 0.35:
        fill_char = "[bold green]█[/]"
        style = "bold green"
        tag = "ALLOW (BAJO RIESGO)"
    elif prob < 0.60:
        fill_char = "[bold yellow]█[/]"
        style = "bold yellow"
        tag = "RIESGO MODERADO"
    else:
        fill_char = "[bold red]█[/]"
        style = "bold red"
        tag = "ALUCINACIÓN"

    bar = (fill_char * filled) + ("[dim white]░[/]" * empty)
    return f"[{bar}] [{style}]{prob:.2f} ({tag})[/]"


class PraxeonDashboard:
    """Gestor del visualizador CLI de supervisión cognitiva basado en TypeSafe AI y LAYA (REF-01)."""

    def __init__(self, goal: str, model_name: str = "Gemini / Antigravity", config: PraxeonConfig | None = None):

        self.goal = goal
        self.model_name = model_name
        self.config = config or default_config
        self.middleware = JEVProxyMiddleware(goal=goal, config=self.config)

        # Estado de telemetría
        self.turn = 0
        self.executed_steps = 0
        self.llm_calls = 0
        self.llm_calls_saved = 0
        self.typesafe_calls = 0
        self.interventions_count = 0
        self.hallucinations_blocked = 0

        # Estado visual del agente
        self.agent_status = "Iniciando sesión..."
        self.current_thought = "Analizando el objetivo y planificando primeros pasos..."
        self.current_action = "Iniciando análisis..."
        self.last_observation = "Esperando primera acción..."
        self.agent_state_color = "cyan"

        # Métricas directas del Supervisor Cognitivo (TypeSafe / LAYA)
        self.is_laya = getattr(self.config, "supervisor", "typesafe").lower() in ("laya", "laya-system1", "laya-v1")
        if self.is_laya:
            self.supervisor_status = f"🛡️ Centinela LAYA ({getattr(self.config, 'laya_backend', 'auto')}) Activo"
        else:
            self.supervisor_status = "🛡️ Centinela TypeSafe AI Activo"
        self.supervisor_color = "green"
        self.noul_prob = 0.12
        self.groundedness = "ALTA (Hechos observados en disco)"
        self.progress_score = "SIGNIFICANT_PROGRESS"
        self.diagnostic_type = "NONE"
        self.explanation = "Sistema en espera de candidatos..."
        self.directive_text: str | None = None
        self.directive_level: str | None = None

        # Historial de nodos para el DAG/Tree
        self.step_history: list[dict[str, Any]] = []

    def make_header(self) -> Panel:
        """Renderiza el banner superior de la consola."""
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=2)
        grid.add_column(justify="right", ratio=1)

        title_text = Text.from_markup(
            "⚡ [bold magenta]PRAXEON RUNTIME SUPERVISOR[/] ⚡ [bold white]|[/] "
            "[bold cyan]Centro de Control y Supervisión de Runtime[/]"
        )
        sup_label = f"PRAXEON ({'LAYA System-1 [' + getattr(self.config, 'laya_backend', 'auto') + ']' if self.is_laya else 'TypeSafe JEV'})"
        subtitle_text = Text.from_markup(
            f"🎯 [bold yellow]Meta:[/] [white]{self.goal[:70]}[/]  "
            f"[dim]•  Modelo Agente:[/] [bold green]{self.model_name}[/]  "
            f"[dim]•  Motor Supervisor:[/] [bold magenta]{sup_label}[/]"
        )

        badge_status = f"[{self.supervisor_color}]● ESTADO: {self.supervisor_status}[/]"
        grid.add_row(title_text, Text.from_markup(badge_status))
        grid.add_row(subtitle_text, Text.from_markup(f"[dim]Turno #{self.turn} | Pasos: {self.executed_steps}[/]"))

        return Panel(grid, border_style="cyan", padding=(0, 1))

    def make_agent_panel(self) -> Panel:
        """Renderiza el panel de actividad del agente."""
        content = Table.grid(expand=True)
        content.add_column(ratio=1)

        content.add_row(f"[bold {self.agent_state_color}]🤖 Estado del Agente:[/] {self.agent_status}\n")

        # Pensamiento
        thought_panel = Panel(
            f"[italic white]{self.current_thought}[/]",
            title="💭 [bold white]Razonamiento Interno (Thought)[/]",
            border_style="dim blue",
            padding=(0, 1),
        )
        content.add_row(thought_panel)
        content.add_row("")

        # Acción
        action_panel = Panel(
            f"[bold yellow]{self.current_action}[/]",
            title="⚡ [bold yellow]Acción Propuesta (Tool Call)[/]",
            border_style="yellow",
            padding=(0, 1),
        )
        content.add_row(action_panel)
        content.add_row("")

        # Observación
        obs_preview = self.last_observation
        if len(obs_preview) > 180:
            obs_preview = obs_preview[:180] + "..."
        obs_panel = Panel(
            f"[dim green]{obs_preview}[/]",
            title="👁️ [bold green]Observación Recibida (Entorno / Disco)[/]",
            border_style="dim green",
            padding=(0, 1),
        )
        content.add_row(obs_panel)

        return Panel(
            content,
            title="🤖 [bold cyan]Flujo de Razonamiento del Agente LLM[/]",
            border_style="blue",
            padding=(1, 1),
        )

    def make_supervisor_panel(self) -> Panel:
        """Renderiza el panel de supervisión cognitiva basado en TypeSafe AI."""
        table = Table.grid(expand=True)
        table.add_column(ratio=1)

        # 1. Métricas de TypeSafe AI System One
        score_table = Table(show_header=False, box=None, expand=True)
        score_table.add_column("Métrica", ratio=2)
        score_table.add_column("Indicador", ratio=3)

        noul_gauge = render_noul_bar(self.noul_prob)
        score_table.add_row("[bold white]Riesgo de Alucinación (Noul):[/]", noul_gauge)
        score_table.add_row("[dim]Fundamentación Empírica:[/]", f"[bold {'green' if 'ALTA' in self.groundedness else 'yellow' if 'VERIFIC' in self.groundedness else 'red'}]{self.groundedness}[/]")
        score_table.add_row("[dim]Contribución Semántica (Score):[/]", f"[cyan]{self.progress_score}[/]")
        diag_color = "green" if self.diagnostic_type in ("NONE", "none") else "bold red"
        score_table.add_row("[dim]Diagnóstico Semántico:[/]", f"[{diag_color}]{self.diagnostic_type}[/]")

        table.add_row(Panel(score_table, title="🛡️ [bold magenta]Evaluación TypeSafe AI (System One)[/]", border_style="magenta", padding=(0, 1)))
        table.add_row("")

        # 2. Veredicto del centinela
        status_box = Panel(
            f"[bold {self.supervisor_color}]{self.supervisor_status}[/]\n\n"
            f"[white]{self.explanation}[/]",
            title="🛡️ [bold white]Veredicto Semántico TypeSafe AI[/]",
            border_style=self.supervisor_color,
            padding=(0, 1),
        )
        table.add_row(status_box)

        # 3. Directiva de intervención si aplica
        if self.directive_text:
            table.add_row("")
            dir_panel = Panel(
                f"[bold red]Nivel:[/] {self.directive_level or 'INTERVENCIÓN'}\n"
                f"[bold yellow]Directiva:[/] {self.directive_text}",
                title="🚨 [bold red]Directiva de Intervención Activa[/]",
                border_style="red",
                padding=(0, 1),
            )
            table.add_row(dir_panel)

        sup_title = "🧠 [bold magenta]Supervisor Cognitivo LAYA System-1[/]" if self.is_laya else "🧠 [bold magenta]Supervisor Cognitivo TypeSafe AI[/]"
        return Panel(
            table,
            title=sup_title,
            border_style="magenta",
            padding=(1, 1),
        )

    def make_tree_panel(self) -> Panel:
        """Renderiza el árbol de razonamiento/DAG acumulado."""
        tree = Tree(f"🎯 [bold yellow]{self.goal[:50]}[/]")
        for item in self.step_history:
            badge = item.get("badge", "✅")
            color = item.get("color", "green")
            step_desc = item.get("desc", "")
            score_txt = item.get("score_txt", "")
            tree.add(f"[{color}]{badge} {step_desc}[/] [dim]({score_txt})[/]")

        if not self.step_history:
            tree.add("[dim italic]Esperando registro de pasos...[/]")

        return Panel(tree, title="🌳 [bold green]Trayectoria y Ramas de Razonamiento[/]", border_style="green", padding=(0, 1))

    def make_footer(self) -> Panel:
        """Renderiza la barra inferior de telemetría y eficiencia."""
        table = Table(show_header=True, header_style="bold cyan", expand=True, box=None)
        table.add_column("Pasos Ejecutados", justify="center")
        table.add_column("Llamadas LLM", justify="center")
        table.add_column("Llamadas Ahorradas (JEV)", justify="center", style="bold green")
        table.add_column("Evaluaciones TypeSafe", justify="center")
        table.add_column("Alucinaciones Bloqueadas", justify="center", style="bold red")
        table.add_column("Salud de Trayectoria", justify="center")

        health_badge = "[bold green]🟢 CONVERGENTE[/]" if self.noul_prob < 0.50 else "[bold red]🔴 RIESGO DE BUCLE / ALUCINACIÓN[/]"
        savings_ratio = (
            f"{self.llm_calls_saved} [green]({(self.llm_calls_saved / max(1, self.llm_calls + self.llm_calls_saved)) * 100:.0f}% ahorro)[/]"
            if (self.llm_calls + self.llm_calls_saved) > 0
            else "0 (0%)"
        )

        table.add_row(
            str(self.executed_steps),
            str(self.llm_calls),
            savings_ratio,
            str(self.typesafe_calls),
            str(self.hallucinations_blocked),
            health_badge,
        )

        return Panel(table, title="📊 [bold cyan]Telemetría y Eficiencia de Cuotas[/]", border_style="cyan", padding=(0, 1))

    def generate_layout(self) -> Layout:
        """Construye el layout visual modular de Rich."""
        layout = Layout(name="root")
        layout.split_column(
            Layout(name="header", size=4),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=5),
        )

        layout["main"].split_row(
            Layout(name="agent_col", ratio=3),
            Layout(name="supervisor_col", ratio=3),
            Layout(name="tree_col", ratio=2),
        )

        layout["header"].update(self.make_header())
        layout["agent_col"].update(self.make_agent_panel())
        layout["supervisor_col"].update(self.make_supervisor_panel())
        layout["tree_col"].update(self.make_tree_panel())
        layout["footer"].update(self.make_footer())

        return layout

    def update_agent_step(self, thought: str, action: str, observation: str = "", status: str = "Ejecutando acción..."):
        self.current_thought = thought
        self.current_action = action
        if observation:
            self.last_observation = observation
        self.agent_status = status
        self.agent_state_color = "yellow" if "Bloqueado" in status or "Alucinación" in status else "cyan"

    def record_step_result(self, step_name: str, score_text: str, is_safe: bool, explanation: str):
        self.executed_steps += 1
        badge = "✅" if is_safe else "🛑"
        color = "green" if is_safe else "red"
        self.step_history.append({
            "desc": step_name,
            "badge": badge,
            "color": color,
            "score_txt": score_text,
            "explanation": explanation,
        })


# Alias de retrocompatibilidad temporal (REF-01)
JEVDashboard = PraxeonDashboard


def _execute_single_demo_task(goal: str) -> PraxeonDashboard:
    """Ejecuta una corrida individual de demostración en el visualizador TUI."""
    dash = PraxeonDashboard(goal=goal, model_name="Antigravity / Gemini 3.8")
    console.clear()

    demo_sequence = [
        {
            "turn": 1,
            "agent_status": "🔍 Planificando bloque de inspección inicial...",
            "thought": "Inspeccionaré el código fuente y las variables de configuración del proyecto para contextualizar el entorno.",
            "action": "run_command {\"command\": \"dir\"}",
            "observation": "Directorio de trabajo: auth_service.py, pyproject.toml, .env",
            "is_chunk": True,
            "chunk_steps": [
                {"tool_name": "run_command", "tool_args": {"command": "dir"}, "thought": "Listar archivos"},
                {"tool_name": "read_file", "tool_args": {"path": "auth_service.py"}, "thought": "Leer archivo para conocer funciones"},
            ],
            "noul_prob": 0.12,
            "groundedness": "ALTA (Hechos observados en disco)",
            "progress_score": "SIGNIFICANT_PROGRESS",
            "diagnostic_type": "NONE",
            "safe": True,
            "sup_status": "🛡️ BLOQUE APROBADO: CONVERGENTE",
            "sup_color": "green",
            "explanation": "Petición agrupada evaluada en 1 sola llamada a TypeSafe AI. Acciones constructivas y fundamentadas.",
            "directive": None,
            "sleep_time": 1.2,
        },
        {
            "turn": 2,
            "agent_status": "⚠️ Alucinación / Suposición no comprobada detectada...",
            "thought": "Asumo sin verificar que la variable JWT_SECRET ya existe y modificaré el archivo con un valor ficticio.",
            "action": "edit_file {\"path\": \"auth_service.py\", \"diff\": \"JWT_SECRET = 'token_inventado'\"}",
            "observation": "🛑 [ACCIÓN PREVENIDA] Intercepción TypeSafe activada. No se modificó ningún archivo en disco.",
            "is_chunk": True,
            "chunk_steps": [
                {"tool_name": "edit_file", "tool_args": {"path": "auth_service.py"}, "thought": "Asumo sin verificar la clave secreta"},
            ],
            "noul_prob": 0.88,
            "groundedness": "BAJA (Suposición no comprobada)",
            "progress_score": "COUNTERPRODUCTIVE",
            "diagnostic_type": "UNVERIFIED_ASSUMPTION",
            "safe": False,
            "sup_status": "🚨 INTERCEPCIÓN ACTIVADA: ALUCINACIÓN DETECTADA",
            "sup_color": "red",
            "explanation": "El paso formula hipótesis sobre credenciales sin verificación previa en disco.",
            "directive": "Prohibido modificar auth_service.py sin leer antes .env o verificar las claves reales con grep.",
            "sleep_time": 1.5,
        },
        {
            "turn": 3,
            "agent_status": "🔄 Rectificando tras recibir la Directiva de TypeSafe...",
            "thought": "El supervisor me ordenó verificar primero las credenciales reales. Leo el archivo .env para obtener la variable legítima.",
            "action": "read_file {\"path\": \".env\"}",
            "observation": "JWT_SECRET=prod_encrypted_vault_key_9921",
            "is_chunk": True,
            "chunk_steps": [
                {"tool_name": "read_file", "tool_args": {"path": ".env"}, "thought": "Verificar variable real"},
            ],
            "noul_prob": 0.15,
            "groundedness": "ALTA (Hechos observados en disco)",
            "progress_score": "SIGNIFICANT_PROGRESS",
            "diagnostic_type": "NONE",
            "safe": True,
            "sup_status": "🛡️ PODA DE RAMA EXITOSA: RECTIFICACIÓN APROBADA",
            "sup_color": "green",
            "explanation": "El agente obedeció la directiva de supervisión y fundamentó su conocimiento en datos reales observados.",
            "directive": None,
            "sleep_time": 1.2,
        },
        {
            "turn": 4,
            "agent_status": "🎉 Solución completada y verificada.",
            "thought": "Con las claves reales comprobadas empíricamente, aplico el parche seguro y concluyo.",
            "action": "finish {\"summary\": \"Autenticación refactorizada usando clave verificada. Tests pasando al 100%.\"}",
            "observation": "Tarea completada exitosamente con cero errores y supervisión continua.",
            "is_chunk": True,
            "chunk_steps": [
                {"tool_name": "edit_file", "tool_args": {"path": "auth_service.py"}, "thought": "Aplicar parche comprobado"},
                {"tool_name": "finish", "tool_args": {"summary": "Éxito total"}, "thought": "Concluir"},
            ],
            "noul_prob": 0.05,
            "groundedness": "ALTA (Solución comprobada)",
            "progress_score": "DIRECT_SOLUTION",
            "diagnostic_type": "NONE",
            "safe": True,
            "sup_status": "🌟 TRAYECTORIA CONVERGENTE COMPLETADA",
            "sup_color": "green",
            "explanation": "Meta alcanzada con éxito. Llamadas ahorradas mediante ejecución autónoma de bloques aprobados.",
            "directive": None,
            "sleep_time": 1.2,
        },
    ]

    with Live(dash.generate_layout(), console=console, refresh_per_second=4, screen=False) as live:
        for item in demo_sequence:
            dash.turn = item["turn"]
            dash.llm_calls += 1
            dash.typesafe_calls += 1

            if item["safe"]:
                dash.llm_calls_saved += max(0, len(item["chunk_steps"]) - 1)
            else:
                dash.hallucinations_blocked += 1
                dash.interventions_count += 1

            dash.update_agent_step(
                thought=item["thought"],
                action=item["action"],
                observation=item["observation"],
                status=item["agent_status"],
            )

            dash.noul_prob = item["noul_prob"]
            dash.groundedness = item["groundedness"]
            dash.progress_score = item["progress_score"]
            dash.diagnostic_type = item["diagnostic_type"]
            dash.supervisor_status = item["sup_status"]
            dash.supervisor_color = item["sup_color"]
            dash.explanation = item["explanation"]
            dash.directive_text = item["directive"]
            dash.directive_level = "LEVEL_2_FORCED_BACKTRACKING" if not item["safe"] else None

            dash.record_step_result(
                step_name=f"Paso {item['turn']}: {item['action'].split()[0]}",
                score_text=f"Noul {item['noul_prob']:.2f}",
                is_safe=item["safe"],
                explanation=item["explanation"],
            )

            live.update(dash.generate_layout())
            time.sleep(item["sleep_time"])

    return dash


def run_visual_demo(task: str | None = None, once: bool = False):
    """Ejecuta una demostración visual interactiva continua del trabajo conjunto Agente ⇄ TypeSafe AI."""
    console.print(Panel(
        "[bold cyan]🚀 JEV Reasoning Navigator ⇄ TypeSafe AI (System One)[/]\n"
        "[dim]Demostración visual interactiva de supervisión cognitiva en tiempo real.[/]\n"
        "[dim]Escribe un objetivo a simular (o pulsa Enter para demo estándar). Escribe [bold red]'salir'[/] para terminar.[/]",
        title="🌟 [bold magenta]Modo Demostración Interactivo[/]",
        border_style="magenta",
        padding=(0, 2),
    ))

    current_task = task

    while True:
        if not current_task:
            try:
                console.print()
                user_input = Prompt.ask(
                    "[bold cyan]🎯 Objetivo a simular[/] [dim](Enter=demo estándar, 'salir'=terminar)[/]",
                    default="Auditar arquitectura y refactorizar módulo crítico previniendo alucinaciones",
                )
            except (KeyboardInterrupt, EOFError):
                console.print("\n[yellow]👋 Demostración finalizada por el usuario.[/]")
                break

            user_input = (user_input or "").strip()
            if user_input.lower() in ("salir", "exit", "quit", "q"):
                console.print("[green]👋 ¡Hasta luego! Demostración finalizada.[/]")
                break
            current_task = user_input

        dash = _execute_single_demo_task(current_task)

        console.print("\n")
        console.print(Panel(
            "[bold green]✓ Demostración completada con éxito.[/]\n"
            f"• Objetivo: [bold white]{current_task}[/]\n"
            f"• Se ejecutaron [bold white]{dash.executed_steps}[/] pasos.\n"
            f"• Se ahorraron [bold green]{dash.llm_calls_saved}[/] llamadas al LLM gracias a la aprobación por bloques.\n"
            f"• Se bloqueó [bold red]{dash.hallucinations_blocked}[/] intento de alucinación antes de tocar el sistema.\n"
            "• El supervisor TypeSafe AI (System One) mantuvo la convergencia de la trayectoria en todo momento.",
            title="🏁 [bold green]Resumen de la Sesión Visual TypeSafe AI[/]",
            border_style="green",
        ))

        if once:
            break

        current_task = None


class _DashboardMission(TerminalMission):
    """Misión del runtime cuyos eventos alimentan los paneles del dashboard dentro de una vista Live."""

    def __init__(self, dash: "PraxeonDashboard"):
        super().__init__(on_event=self._on_event, approver=self._ask)
        self.dash = dash
        self.live: Live | None = None

    def run(self, goal: str, **kwargs: Any) -> LiveTaskResult:
        with Live(self.dash.generate_layout(), console=console, refresh_per_second=4, screen=False) as live:
            self.live = live
            try:
                return super().run(goal, **kwargs)
            finally:
                self.live = None

    def _refresh(self) -> None:
        if self.live is not None:
            self.live.update(self.dash.generate_layout())

    def _ask(self, event: RuntimeEvent) -> bool:
        if self.live is not None:
            self.live.stop()
        try:
            return ask_approval(event)
        finally:
            if self.live is not None:
                self.live.start()

    def _on_event(self, event: RuntimeEvent) -> None:
        dash, payload = self.dash, event.payload or {}
        if event.type == EventType.ACTION_PROPOSED:
            dash.turn += 1
            dash.llm_calls += 1
            dash.update_agent_step(
                thought=str(payload.get("thought_rationale") or ""),
                action=f"{payload.get('tool')} {json.dumps(payload.get('arguments') or {}, ensure_ascii=False)}",
                status="🔍 Evaluando la acción con el supervisor...",
            )
            dash.supervisor_status, dash.supervisor_color = "⏳ Evaluando...", "yellow"
        elif event.type == EventType.POLICY_DECIDED:
            status = str(payload.get("status", "")).upper()
            reason = str(payload.get("reason_code", ""))
            vetoed = status in ("BLOCK", "REPLAN")
            if vetoed:
                dash.interventions_count += 1
            dash.supervisor_status = f"{'🛑' if vetoed else '✅'} {status}"
            dash.supervisor_color = "red" if status == "BLOCK" else ("yellow" if vetoed or status == "REVIEW" else "green")
            dash.explanation = reason or dash.explanation
            dash.record_step_result(
                step_name=f"Paso {dash.turn}: {dash.current_action.split(' ', 1)[0]}",
                score_text=status,
                is_safe=not vetoed,
                explanation=reason,
            )
        elif event.type == EventType.OBSERVATION_RECORDED:
            dash.last_observation = str(payload.get("output", ""))[:600]
            dash.agent_status = "✅ Acción ejecutada"
        elif event.type == EventType.INTERVENTION_APPLIED:
            text = payload.get("message") or payload.get("warning") or payload.get("error")
            if text:
                dash.directive_text, dash.directive_level = str(text), "WARNING"
        elif event.type == EventType.SESSION_COMPLETED:
            dash.agent_status = "🎉 Misión terminada."
            dash.supervisor_status, dash.supervisor_color = "🌟 TRAYECTORIA COMPLETADA", "green"
        self._refresh()


def _execute_single_live_task(
    goal: str,
    max_steps: int = 25,
    model_name: str | None = None,
    config: PraxeonConfig | None = None,
    session_context: SessionContextManager | None = None,
    middleware: Any | None = None,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> tuple[bool, str | None, JEVDashboard, int, SessionContextManager, Any | None]:
    """Ejecuta una tarea en vivo como misión del runtime (bucle canónico) y la pinta en el dashboard.

    ``middleware`` se acepta por compatibilidad y ya no interviene: la supervisión es la del runtime.
    """
    cfg = config or default_config
    session = session_context or SessionContextManager()
    dash = JEVDashboard(goal=goal, model_name=f"{model_name or 'por defecto'} ({(provider or 'auto').upper()})", config=cfg)
    console.clear()

    result = _run_with_recovery(
        _DashboardMission(dash), goal, FallbackMode.BEST_EFFORT, provider, model_name, api_key, base_url, max_steps,
        getattr(cfg, "supervisor", "laya"), session,
    )
    summary = result.final_answer or ("Tarea completada" if result.finished else "La tarea terminó sin finish aceptado")
    session.record_completed_task(
        goal=goal,
        summary=summary[:200],
        final_answer=summary,
        executed_steps=len(result.steps),
        history_steps=[
            {"tool_name": s.get("tool"), "tool_args": s.get("arguments"), "observation": s.get("observation", "")}
            for s in result.steps
        ],
    )
    return result.finished, summary, dash, len(result.steps), session, middleware


def _display_task_results(
    goal: str,
    task_finished: bool,
    final_summary: str | None,
    dash: JEVDashboard,
    max_steps: int,
    turn: int,
):
    """Muestra de forma prominente la respuesta generada por el agente y el resumen de telemetría."""
    console.print("\n")
    if task_finished and final_summary:
        console.print(Panel(
            f"[bold cyan]🎯 Objetivo resuelto:[/] [white]{goal}[/]\n\n"
            f"[bold green]📝 Respuesta y Conclusiones del Agente:[/]\n"
            f"[bold white]{final_summary}[/]",
            title="✨ [bold cyan]Respuesta Final del Agente (Completada y Verificada)[/]",
            border_style="cyan",
            padding=(1, 2),
        ))
    elif not task_finished:
        is_api_err = final_summary and any(k in final_summary.lower() for k in ("error", "cuota", "agotada", "gemini", "api"))
        if is_api_err:
            reason_msg = final_summary
            guidance_msg = (
                "[bold yellow]💡 ¿Cómo solucionarlo?[/]\n"
                "• [bold cyan]Opción 1:[/] Si dispones de otra clave con cuota activa, actualiza [bold white]GEMINI_API_KEY[/] en tu archivo [bold white].env[/].\n"
                "• [bold cyan]Opción 2:[/] Puedes supervisar agentes directamente en este chat de Antigravity IDE con el MCP [bold green]jev-navigator[/] (sin gastar cuota de Gemini)."
            )
            console.print(Panel(
                f"[bold red]❌ {reason_msg}[/]\n\n{guidance_msg}",
                title="🚨 [bold red]Sesión Detenida por Error de LLM / Cuota[/]",
                border_style="red",
                padding=(1, 2),
            ))
        else:
            reason_msg = (
                f"La sesión se detuvo tras alcanzar el límite máximo de {max_steps} turnos sin ejecutar finish."
                if turn >= max_steps
                else f"La sesión se detuvo anticipadamente en el turno {turn} porque el agente finalizó su salida sin emitir nuevas acciones."
            )
            console.print(Panel(
                f"[bold yellow]⚠️ {reason_msg}[/]\n"
                "El supervisor de PRAXEON vetó o detuvo la trayectoria antes de completar la tarea.",
                title="⚠️ [bold yellow]Sesión Incompleta[/]",
                border_style="yellow",
                padding=(1, 2),
            ))

    console.print(Panel(
        f"{'[bold green]✓ Tarea completada con éxito (Meta alcanzada).[/]' if task_finished else '[bold yellow]⚠️ Tarea detenida antes de alcanzar finish.[/]'}\n"
        f"• Pasos evaluados por el supervisor: [bold white]{dash.executed_steps}[/]\n"
        f"• Vetos del supervisor (BLOCK / REPLAN): [bold red]{dash.interventions_count}[/]",
        title="📊 [bold green]Supervisión PRAXEON[/]" if task_finished else "📊 [bold yellow]Supervisión PRAXEON[/]",
        border_style="green" if task_finished else "yellow",
    ))


def run_visual_live(
    task: str | None = None,
    max_steps: int = 25,
    model_name: str | None = None,
    config: PraxeonConfig | None = None,
    once: bool = False,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
):
    """Ejecuta una sesión interactiva continua con el agente LLM y el supervisor TypeSafe AI / PRAXEON."""
    console.print(Panel(
        "[bold cyan]🚀 PRAXEON Runtime Supervision ⇄ Agent LLM (System One)[/]\n"
        "[dim]Sesión interactiva en vivo con supervisión cognitiva, prevención de bucles y capabilities.[/]\n"
        "[dim]Introduce tus objetivos o tareas. Para salir en cualquier momento escribe [bold red]'salir'[/] o [bold red]'exit'[/].[/]",
        title="🌟 [bold magenta]Modo Interactivo Continuo (En Vivo)[/]",
        border_style="magenta",
        padding=(0, 2),
    ))

    current_task = task
    session_context = SessionContextManager()
    middleware = None

    while True:
        if not current_task:
            try:
                console.print()
                user_input = Prompt.ask("[bold cyan]🎯 Introduce un objetivo o tarea a resolver[/] [dim](o 'reset' para nueva sesión, 'salir')[/]")
            except (KeyboardInterrupt, EOFError):
                console.print("\n[yellow]👋 Sesión interactiva finalizada por el usuario.[/]")
                break

            user_input = (user_input or "").strip()
            if not user_input or user_input.lower() in ("salir", "exit", "quit", "q"):
                console.print("[green]👋 ¡Hasta luego! Sesión interactiva finalizada.[/]")
                break
            if user_input.lower() in ("reset", "clear", "nueva", "nuevo", "/reset", "/clear"):
                session_context.reset()
                middleware = None
                console.print("[bold green]🧹 Memoria de sesión reiniciada. Puedes introducir un nuevo objetivo limpio.[/]")
                continue
            current_task = user_input

        # Ejecución autónoma de la tarea
        task_finished, summary, dash, turn, session_context, middleware = _execute_single_live_task(
            goal=current_task,
            max_steps=max_steps,
            model_name=model_name,
            config=config,
            session_context=session_context,
            middleware=middleware,
            provider=provider,
            base_url=base_url,
            api_key=api_key,
        )

        # Mostrar métricas y respuesta final
        _display_task_results(current_task, task_finished, summary, dash, max_steps, turn)

        if once:
            break

        # Limpiar para la siguiente interacción
        current_task = None


def main():
    """Punto de entrada CLI para el visualizador interactivo."""
    parser = argparse.ArgumentParser(
        prog="praxeon-dash",
        description="Visualizador CLI interactivo de trabajo conjunto Agente ⇄ PRAXEON Runtime Supervisor",
    )
    parser.add_argument("--task", type=str, default=None, help="Objetivo o tarea a visualizar")
    parser.add_argument("--live", action="store_true", help="Ejecutar en modo vivo con agente LLM")
    parser.add_argument(
        "--supervisor",
        type=str,
        default=os.getenv("PRAXEON_SUPERVISOR", "jev"),
        choices=["jev", "laya", "typesafe"],
        help="Motor supervisor cognitivo: 'jev' (TypeSafe AI Cloud) o 'laya' (LAYA System-1 Local/Hosted)",
    )
    parser.add_argument(
        "--supervisor-model",
        type=str,
        default=None,
        help="Modelo específico para el supervisor (ej. 'laya-v1-calibrated' o 'jev-latest')",
    )
    parser.add_argument(
        "--laya-backend",
        type=str,
        default=os.getenv("LAYA_BACKEND", "auto"),
        choices=["auto", "local", "simulated", "hosted"],
        help="Backend para el supervisor LAYA ('auto', 'local', 'simulated', 'hosted')",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="Proveedor del Agent LLM (groq, ollama, openrouter, gemini, lmstudio, openai, simulated)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="URL base para OpenAI-compatible (ej. http://localhost:11434/v1 para Ollama)",
    )
    parser.add_argument(
        "--api-key",
        "--gemini-key",
        dest="api_key",
        type=str,
        default=None,
        help="Clave API del proveedor LLM seleccionado",
    )
    parser.add_argument("--model", type=str, default=None, help="Modelo LLM para modo vivo")
    parser.add_argument("--max-steps", type=int, default=25, help="Número máximo de turnos permitidos")
    parser.add_argument("--once", action="store_true", help="Ejecutar una única tarea y salir")
    args = parser.parse_args()

    cfg = default_config.model_copy()
    if args.supervisor:
        cfg.supervisor = args.supervisor
    if args.supervisor_model:
        cfg.provider.model = args.supervisor_model
    if args.laya_backend:
        cfg.laya_backend = args.laya_backend

    if args.live:
        run_visual_live(
            task=args.task,
            model_name=args.model,
            max_steps=args.max_steps,
            config=cfg,
            once=args.once,
            provider=args.provider,
            base_url=args.base_url,
            api_key=args.api_key,
        )
    else:
        run_visual_demo(task=args.task, once=args.once)


# Nomenclatura canónica PRAXEON 1.0 (JEVDashboard conservado para retrocompatibilidad)
JEVDashboard = PraxeonDashboard


if __name__ == "__main__":
    main()


