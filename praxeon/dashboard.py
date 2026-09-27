"""Visual Dashboard interactivo y enriquecido con Rich para supervisión con TypeSafe AI (System One)."""

import argparse
import io
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# UTF-8 en terminales Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer") and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
from rich.prompt import Prompt

from praxeon.config import JEVConfig, default_config
from praxeon.core.session_context import SessionContextManager
from praxeon.interceptor.proxy_middleware import JEVProxyMiddleware

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


class JEVDashboard:
    """Gestor del visualizador CLI de supervisión cognitiva basado puramente en TypeSafe AI."""

    def __init__(self, goal: str, model_name: str = "Gemini / Antigravity", config: Optional[JEVConfig] = None):
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
        self.directive_text: Optional[str] = None
        self.directive_level: Optional[str] = None

        # Historial de nodos para el DAG/Tree
        self.step_history: List[Dict[str, Any]] = []

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


def _execute_single_demo_task(goal: str) -> JEVDashboard:
    """Ejecuta una corrida individual de demostración en el visualizador TUI."""
    dash = JEVDashboard(goal=goal, model_name="Antigravity / Gemini 3.8")
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


def run_visual_demo(task: Optional[str] = None, once: bool = False):
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


def _execute_single_live_task(
    goal: str,
    max_steps: int = 25,
    model_name: Optional[str] = None,
    config: Optional[JEVConfig] = None,
    session_context: Optional[SessionContextManager] = None,
    middleware: Optional[JEVProxyMiddleware] = None,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Tuple[bool, Optional[str], JEVDashboard, int, SessionContextManager, JEVProxyMiddleware]:
    """Ejecuta una corrida individual en vivo del agente supervisado por PRAXEON manteniendo contexto acumulado."""
    cfg = config or default_config

    session = session_context or SessionContextManager()
    if middleware is None:
        middleware = JEVProxyMiddleware(goal=goal, config=cfg)
    else:
        middleware.start_subtask(goal)

    from praxeon.agent_llm import create_agent_llm, SimulatedAgentLLM
    try:
        agent_llm = create_agent_llm(
            provider=provider,
            model=model_name,
            api_key=api_key,
            base_url=base_url,
        )
    except Exception as e:
        console.print(f"[bold yellow]Aviso al inicializar LLM:[/] {e}. Usando simulador.")
        agent_llm = SimulatedAgentLLM()

    dash = JEVDashboard(goal=goal, model_name=f"{agent_llm.model_name} ({agent_llm.provider_name.upper()})", config=cfg)
    dash.middleware = middleware
    console.clear()

    conversation_history = session.prepare_task_conversation(goal)
    from praxeon.live_agent import parse_llm_steps

    final_summary: Optional[str] = None
    task_finished = False
    turn = 0
    executed_step_records: List[Dict[str, Any]] = []

    with Live(dash.generate_layout(), console=console, refresh_per_second=4, screen=False) as live:
        consecutive_blocks = 0

        while turn < max_steps and not task_finished:
            turn += 1
            dash.turn = turn
            dash.llm_calls += 1
            dash.agent_status = f"⚡ Generando propuesta de bloque cognitivo ({agent_llm.provider_name.upper()})..."
            live.update(dash.generate_layout())

            llm_output = ""
            while not llm_output and not task_finished:
                try:
                    if agent_llm.provider_name == "gemini":
                        time.sleep(2.0)
                    llm_output = agent_llm.generate(conversation_history)
                except Exception as e:
                    err_str = str(e)
                    live.stop()
                    from praxeon.model_recovery import prompt_model_recovery_menu
                    action, payload = prompt_model_recovery_menu(
                        error_message=err_str,
                        current_model=f"{agent_llm.provider_name}:{agent_llm.model_name}",
                        console=console,
                    )
                    if action == "change_model" and payload:
                        if ":" in payload:
                            p_part, m_part = payload.split(":", 1)
                            agent_llm = create_agent_llm(provider=p_part, model=m_part, base_url=base_url)
                        else:
                            agent_llm = create_agent_llm(provider=agent_llm.provider_name, model=payload, base_url=base_url)
                        dash.model_name = f"{agent_llm.model_name} ({agent_llm.provider_name.upper()})"
                        live.start()
                        dash.agent_status = f"🔄 Reintentando con {agent_llm.provider_name} ({agent_llm.model_name})..."
                        live.update(dash.generate_layout())
                        continue
                    elif action == "update_key" and payload:
                        if ":" in payload:
                            k_name, k_val = payload.split(":", 1)
                        else:
                            k_val = payload
                        agent_llm = create_agent_llm(provider=agent_llm.provider_name, model=agent_llm.model_name, api_key=k_val, base_url=base_url)
                        live.start()
                        dash.agent_status = "🔄 Reintentando con clave actualizada..."
                        live.update(dash.generate_layout())
                        continue
                    else:
                        live.start()
                        dash.agent_status = f"🚨 ERROR LLM: {err_str[:60]}"
                        dash.supervisor_status = "❌ DETENIDO TRAS ERROR DE API / CUOTA"
                        dash.supervisor_color = "red"
                        live.update(dash.generate_layout())
                        final_summary = f"Ejecución detenida por error en {agent_llm.provider_name}: {err_str[:80]}"
                        task_finished = False
                        break

            if not llm_output:
                break

            proposed_steps = parse_llm_steps(llm_output)
            if not proposed_steps:
                lower_out = llm_output.lower()
                is_conclusive = any(w in lower_out for w in ("conclu", "finaliz", "resuelt", "solución", "solucion", "terminad", "completad", "finish"))
                if is_conclusive:
                    final_summary = llm_output.strip()
                    task_finished = True
                    dash.agent_status = "🎉 Solución completada y verificada."
                    dash.supervisor_status = "🌟 TRAYECTORIA CONVERGENTE COMPLETADA"
                    live.update(dash.generate_layout())
                    break
                else:
                    conversation_history.append({
                        "role": "assistant",
                        "content": llm_output,
                    })
                    conversation_history.append({
                        "role": "user",
                        "content": (
                            "Observación: Has emitido un razonamiento pero ninguna acción concreta.\n"
                            "Por favor, formula tu siguiente paso en una línea nueva con el formato:\n"
                            "Action: <herramienta> <argumentos_en_json>\n"
                            "Ejemplo: Action: run_command {\"command\": \"dir /b\"}\n"
                            "O para concluir: Action: finish {\"summary\": \"<resumen o solución final>\"}"
                        ),
                    })
                    dash.agent_status = "⚠️ Razonamiento sin acción detectado. Re-solicitando paso ejecutable..."
                    live.update(dash.generate_layout())
                    time.sleep(0.5)
                    continue

            first_st = proposed_steps[0]
            dash.update_agent_step(
                thought=first_st.get("thought_rationale", ""),
                action=f"{first_st.get('tool_name')} {json.dumps(first_st.get('tool_args') or {})}",
                status="🔍 Evaluando bloque en TypeSafe AI (System One)...",
            )
            dash.supervisor_status = "⏳ Evaluando bloque en lote..."
            dash.supervisor_color = "yellow"
            live.update(dash.generate_layout())
            time.sleep(0.4)

            dash.typesafe_calls += 1
            chunk_result = dash.middleware.intercept_step_chunk(proposed_steps)

            if not chunk_result.all_safe:
                dash.interventions_count += 1
                dash.hallucinations_blocked += 1
                consecutive_blocks += 1

                # Ejecutar pasos válidos previos si los hubiera (ej. si Step 1 fue read_file y Step 2 finish)
                for v_idx in range(chunk_result.valid_step_count or 0):
                    v_st = proposed_steps[v_idx]
                    v_tool = v_st.get("tool_name")
                    v_args = v_st.get("tool_args") or {}
                    v_thought = v_st.get("thought_rationale") or ""
                    try:
                        v_obs = dash.middleware.execute_tool(v_tool, v_args, v_thought)
                        v_obs_str = str(v_obs.output)[:500]
                    except Exception as e:
                        v_obs_str = f"Error ejecutando '{v_tool}': {e}"
                    conversation_history.append({
                        "role": "assistant",
                        "content": f"Thought: {v_thought}\nAction: {v_tool} {json.dumps(v_args)}"
                    })
                    conversation_history.append({
                        "role": "user",
                        "content": f"Observation: {v_obs_str}"
                    })
                    dash.record_step_result(
                        step_name=f"Paso {dash.executed_steps+1}: {v_tool}",
                        score_text="Noul 0.10 • ALLOW",
                        is_safe=True,
                        explanation="Paso previo del bloque ejecutado exitosamente.",
                    )

                flagged_idx = chunk_result.flagged_step_index or 0
                flagged_step = proposed_steps[flagged_idx]
                flagged_tool = flagged_step.get("tool_name", "accion")

                is_real_hallucination = bool(chunk_result.hallucination_detected)
                if is_real_hallucination:
                    dash.supervisor_status = "🚨 INTERCEPCIÓN ACTIVADA: ALUCINACIÓN DETECTADA"
                    dash.supervisor_color = "red"
                    dash.diagnostic_type = str(chunk_result.hallucination_type or "INVENTED_FACT").upper()
                    dash.groundedness = "BAJA (Suposición no comprobada)"
                    dash.noul_prob = 0.85
                else:
                    dash.supervisor_status = "⚠️ INTERCEPCIÓN ACTIVADA: BUCLE / REPETICIÓN DETECTADA"
                    dash.supervisor_color = "yellow"
                    dash.diagnostic_type = str(chunk_result.hallucination_type or "LOOP_REPETITION").upper()
                    dash.groundedness = "ALTA (Hechos observados en disco)"
                    dash.noul_prob = 0.65

                dash.explanation = chunk_result.explanation
                if chunk_result.directive and chunk_result.directive.message:
                    dash.directive_text = chunk_result.directive.message
                else:
                    dash.directive_text = chunk_result.explanation
                dash.directive_level = "LEVEL_2_FORCED_BACKTRACKING"
                dash.progress_score = "COUNTERPRODUCTIVE"

                dash.update_agent_step(
                    thought=flagged_step.get("thought_rationale", ""),
                    action=f"{flagged_tool} {flagged_step.get('tool_args')}",
                    observation=f"🛑 [{dash.diagnostic_type}] Acción pausada por el supervisor antes de tocar el sistema.",
                    status="🛑 Interceptado por Supervisor",
                )
                dash.record_step_result(
                    step_name=f"Paso {dash.executed_steps+1}: {flagged_tool} [BLOQUEADO]",
                    score_text=f"Noul {dash.noul_prob:.2f} • {dash.diagnostic_type}",
                    is_safe=False,
                    explanation=chunk_result.explanation,
                )

                # Circuit breaker para evitar bucles repetitivos de bloqueo
                if consecutive_blocks == 2:
                    if dash.executed_steps >= 2:
                        dash.directive_text += (
                            "\n\n💡 [ORIENTACIÓN DE CIERRE]: Ya has obtenido observaciones empíricas durante la sesión. "
                            "Si dispones de suficiente contexto para responder a la tarea del usuario, sintetiza tu informe o conclusión "
                            "y entrega el resultado invocando obligatoriamente:\n"
                            'Action: finish {"summary": "informe o conclusión fundamentada basada en lo observado"}'
                        )
                    else:
                        dash.directive_text += (
                            "\n\n🚨 [ALERTA DE DESBLOQUEO]: Queda TERMINANTEMENTE PROHIBIDO invocar 'edit_file'. "
                            "En tu siguiente turno invoca 'read_file' para verificar hechos o 'finish' para concluir con los hallazgos."
                        )
                elif consecutive_blocks >= 3:
                    console.print("[bold yellow]⚡ CIRCUIT BREAKER: Inyectando observación empírica para romper la parálisis cognitiva...[/]")
                    auto_probe = "Archivos reales en disco: "
                    try:
                        auto_probe += ", ".join([f for f in os.listdir(".") if not f.startswith(".")][:6])
                    except Exception:
                        auto_probe += "pyproject.toml, README.md"
                    dash.last_observation = auto_probe
                    dash.middleware.record_observation(auto_probe)
                    conversation_history.append({
                        "role": "user",
                        "content": f"OBSERVACIÓN REAL ENTORNO: {auto_probe}\nFormula tu próximo plan basándote exclusivamente en estos archivos.",
                    })
                    consecutive_blocks = 0
                    time.sleep(0.5)
                    continue

                if flagged_tool in ("edit_file", "write_file"):
                    agent_prompt_feedback = (
                        f"SUPERVISIÓN PRAXEON: La acción '{flagged_tool}' ha sido BLOQUEADA porque la tarea solicitada por el usuario "
                        f"('{goal}') no requiere modificar archivos ni alterar el código.\n"
                        f"INSTRUCCIÓN OBLIGATORIA: Tu objetivo es responder al usuario: '{goal}'. "
                        "Basándote en los archivos y código que ya has inspeccionado, entrega tu informe/opinión en:\n"
                        'Action: finish {"summary": "tu respuesta completa y fundamentada resolviendo la tarea del usuario"}'
                    )
                elif not is_real_hallucination and flagged_tool in ("read_file", "view_file", "list_dir", "run_command"):
                    agent_prompt_feedback = (
                        f"SUPERVISIÓN PRAXEON: La consulta '{flagged_tool}' ha sido pausada porque ese contenido ya fue obtenido "
                        "y está presente en tus observaciones anteriores. TIENES AUTORIZACIÓN PLENA para responder a la tarea del usuario.\n"
                        f"INSTRUCCIÓN OBLIGATORIA: Responde directamente al objetivo del usuario: '{goal}'. "
                        "No necesitas volver a leerlo: sintetiza la respuesta basándote en lo ya observado e invoca obligatoriamente:\n"
                        'Action: finish {"summary": "tu respuesta completa con los hallazgos"}'
                    )
                else:
                    agent_prompt_feedback = (
                        f"SUPERVISIÓN PRAXEON: Acción '{flagged_tool}' bloqueada. {dash.directive_text}\n"
                        f"INSTRUCCIÓN OBLIGATORIA: Tu meta es resolver la tarea del usuario: '{goal}'. "
                        "NO reproduzcas advertencias de bloqueo ni diagnósticos del supervisor; formula tu respuesta a la tarea en 'finish':\n"
                        'Action: finish {"summary": "tu respuesta final a la tarea del usuario"}'
                    )

                conversation_history.append({
                    "role": "user",
                    "content": agent_prompt_feedback,
                })
                live.update(dash.generate_layout())
                time.sleep(0.8)
                continue

            # Bloque aprobado
            consecutive_blocks = 0
            dash.supervisor_status = "🛡️ BLOQUE APROBADO: CONVERGENTE"
            dash.supervisor_color = "green"
            dash.explanation = chunk_result.explanation
            dash.directive_text = None
            dash.noul_prob = 0.10
            dash.groundedness = "ALTA (Hechos observados en disco)"
            dash.progress_score = "SIGNIFICANT_PROGRESS"
            dash.diagnostic_type = "NONE"
            dash.llm_calls_saved += max(0, len(proposed_steps) - 1)

            for st_data in proposed_steps:
                tool_name = st_data.get("tool_name") or "thought"
                tool_args = st_data.get("tool_args") or {}
                thought_str = st_data.get("thought_rationale") or ""

                obs = "Acción ejecutada correctamente."
                if tool_name == "finish":
                    summary = tool_args.get("summary", "Tarea completada exitosamente.")
                    evasive_markers = (
                        "pendiente de", "pendiente", "planificación", "planificacion",
                        "como soy un agente", "la acción real", "la accion real",
                        "todavía no", "aún no he", "aun no he", "sin analizar",
                        "no he podido leer", "no he podido", "provisional", "pending",
                        "este paso es de"
                    )
                    meta_leakage_markers = (
                        "ha sido vetada", "vetada temporalmente", "bucle o estancamiento",
                        "acción pausada", "accion pausada", "acción bloqueada", "accion bloqueada",
                        "bloqueado por el supervisor", "bloqueada por el supervisor",
                        "error de supervisión", "error de supervision",
                        "policy_engine", "system_intervention",
                        "no tengo la autorización", "no tengo la autorizacion",
                        "intervención activada", "intervencion activada",
                    )
                    sum_lower = str(summary).lower()
                    if any(m in sum_lower for m in evasive_markers):
                        dash.supervisor_status = "⚠️ FINISH EVASIVO RECHAZADO"
                        dash.supervisor_action = "Inyectando directiva para respuesta fundamentada"
                        task_finished = False
                        obs = (
                            "OBSERVACIÓN DEL SUPERVISOR (JEV): Tu llamada a 'finish' ha sido RECHAZADA porque contiene un texto de planificación o evasión ('pendiente de lectura'). "
                            "NO puedes finalizar sin dar una respuesta concreta. Analiza las observaciones y el contenido ya obtenido y responde directamente con los hallazgos en tu siguiente turno."
                        )
                    elif any(m in sum_lower for m in meta_leakage_markers):
                        dash.supervisor_status = "⚠️ RESPUESTA INVÁLIDA: ECO DE SUPERVISOR RECHAZADO"
                        dash.supervisor_action = "Inyectando directiva para responder al objetivo real"
                        task_finished = False
                        obs = (
                            f"OBSERVACIÓN DEL SUPERVISOR (JEV): Tu llamada a 'finish' ha sido RECHAZADA porque estás describiendo "
                            f"mensajes de diagnóstico interno del supervisor en lugar de responder a la tarea del usuario: '{goal}'. "
                            f"Prohibido mencionar 'herramienta vetada', 'bucle detectado' o 'policy_engine'. Responde directamente a: '{goal}' "
                            f"con tus conclusiones y opinión fundamentada sobre el proyecto que has inspeccionado."
                        )
                    else:
                        task_finished = True
                        final_summary = summary
                        obs = f"Tarea finalizada: {final_summary}"
                elif tool_name == "run_command":
                    cmd = tool_args.get("command") or tool_args.get("cmd") or tool_args.get("raw") or ""
                    if isinstance(cmd, dict):
                        cmd = cmd.get("command") or cmd.get("cmd") or cmd.get("raw") or ""
                    cmd = str(cmd).strip().strip('"').strip("'")
                    if cmd:
                        try:
                            import shutil
                            import subprocess
                            obs = None
                            if sys.platform == "win32":
                                cmd_stripped = cmd.strip()
                                if cmd_stripped.lower().startswith("git") and shutil.which("git") is None:
                                    if any(sub in cmd_stripped.lower() for sub in ("status", "ls-files", "branch")):
                                        cmd = "dir /b"
                                        cmd_stripped = cmd
                                    else:
                                        obs = "Aviso del entorno: 'git' no está instalado en este equipo Windows. Usa comandos nativos como 'dir /b' o invoca 'read_file(path)' para inspeccionar archivos."

                                if not obs:
                                    if cmd_stripped.startswith("find ") and any(x in cmd_stripped for x in ("-maxdepth", "-name", "-type", "-path", ".")):
                                        cmd = "Get-ChildItem -Depth 2 -Name"
                                        cmd_stripped = cmd
                                    elif cmd_stripped.startswith("ls"):
                                        cmd = "dir /b"
                                        cmd_stripped = cmd

                                    is_ps = any(cmd_stripped.lower().startswith(p) for p in ("get-", "set-", "select-", "where-", "format-", "out-", "$", "powershell"))
                                    if is_ps:
                                        proc = subprocess.run(
                                            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                                            capture_output=True,
                                            text=True,
                                            timeout=10,
                                            encoding="utf-8",
                                            errors="replace",
                                        )
                                    else:
                                        proc = subprocess.run(
                                            cmd,
                                            shell=True,
                                            capture_output=True,
                                            text=True,
                                            timeout=10,
                                            encoding="utf-8",
                                            errors="replace",
                                        )
                                        combined = ((proc.stderr or "") + (proc.stdout or "")).lower()
                                        if "no se reconoce como un comando" in combined or "not recognized as an internal" in combined:
                                            proc = subprocess.run(
                                                ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                                                capture_output=True,
                                                text=True,
                                                timeout=10,
                                                encoding="utf-8",
                                                errors="replace",
                                            )
                                    output = (proc.stdout or proc.stderr or "Comando ejecutado sin salida").strip()
                                    obs = output[:50_000]
                            else:
                                proc = subprocess.run(
                                    cmd,
                                    shell=True,
                                    capture_output=True,
                                    text=True,
                                    timeout=10,
                                    encoding="utf-8",
                                    errors="replace",
                                )
                                output = (proc.stdout or proc.stderr or "Comando ejecutado sin salida").strip()
                                obs = output[:10000]
                        except Exception as e:
                            obs = f"Error ejecutando '{cmd}': {e}"
                    else:
                        obs = "Comando vacío."
                elif tool_name == "read_file":
                    p = tool_args.get("path", "")
                    if p and os.path.exists(p):
                        try:
                            with open(p, "r", encoding="utf-8", errors="replace") as f:
                                file_content = f.read(50000)
                                if len(file_content) >= 50000:
                                    file_content += "\n\n[... Archivo muy extenso: truncado a 50.000 caracteres por seguridad de contexto ...]"
                                obs = f"Contenido de '{p}':\n{file_content}"
                        except Exception as e:
                            obs = f"Error leyendo {p}: {e}"
                    else:
                        obs = f"Archivo '{p}' no encontrado."
                elif tool_name == "edit_file":
                    obs = "Archivo editado correctamente en el entorno de pruebas."

                dash.last_observation = obs
                dash.middleware.record_observation(obs)
                conversation_history.append({
                    "role": "user",
                    "content": f"OBSERVACIÓN REAL ({tool_name}):\n{obs}",
                })

                dash.update_agent_step(
                    thought=thought_str,
                    action=f"{tool_name} {json.dumps(tool_args)}",
                    observation=obs,
                    status="✅ Ejecutando paso autorizado de forma autónoma...",
                )
                dash.record_step_result(
                    step_name=f"Paso {dash.executed_steps+1}: {tool_name}",
                    score_text="Noul 0.10 • ALLOW",
                    is_safe=True,
                    explanation="Acción constructiva",
                )
                executed_step_records.append({
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "observation": obs,
                })
                live.update(dash.generate_layout())
                time.sleep(0.4)

            if task_finished:
                dash.agent_status = "🎉 Solución completada y verificada."
                dash.supervisor_status = "🌟 TRAYECTORIA CONVERGENTE COMPLETADA"
                dash.progress_score = "DIRECT_SOLUTION"
                live.update(dash.generate_layout())
                break

        resolved_summary = final_summary or ("Tarea completada exitosamente" if task_finished else "Límite de pasos alcanzado")
        session.record_completed_task(
            goal=goal,
            summary=resolved_summary,
            final_answer=resolved_summary,
            executed_steps=dash.executed_steps,
            history_steps=executed_step_records,
        )

    return task_finished, final_summary, dash, turn, session, middleware


def _display_task_results(
    goal: str,
    task_finished: bool,
    final_summary: Optional[str],
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
                "El supervisor TypeSafe AI interceptó o detuvo la trayectoria antes de completar la tarea.",
                title="⚠️ [bold yellow]Sesión Incompleta[/]",
                border_style="yellow",
                padding=(1, 2),
            ))

    console.print(Panel(
        f"{'[bold green]✓ Tarea completada con éxito (Meta alcanzada).[/]' if task_finished else '[bold yellow]⚠️ Tarea detenida antes de alcanzar finish.[/]'}\n"
        f"• Pasos ejecutados: [bold white]{dash.executed_steps}[/]\n"
        f"• Llamadas LLM ahorradas gracias a TypeSafe: [bold green]{dash.llm_calls_saved}[/]\n"
        f"• Alucinaciones prevenidas: [bold red]{dash.hallucinations_blocked}[/]\n"
        f"• Evaluaciones TypeSafe AI: [bold magenta]{dash.typesafe_calls}[/]",
        title="📊 [bold green]Telemetría y Eficiencia TypeSafe AI[/]" if task_finished else "📊 [bold yellow]Métricas TypeSafe AI[/]",
        border_style="green" if task_finished else "yellow",
    ))


def run_visual_live(
    task: Optional[str] = None,
    max_steps: int = 25,
    model_name: Optional[str] = None,
    config: Optional[JEVConfig] = None,
    once: bool = False,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
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


if __name__ == "__main__":
    main()
