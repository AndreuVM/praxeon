"""Ejecutor de misiones y punto de entrada interactivo para el Live Agent de PRAXEON.

Cada tarea es una misión del runtime con el bucle canónico (``praxeon.live_agent.runtime_session``): misma política,
sandbox y recibos que la app y la web. Este módulo pinta su progreso en la terminal, pide aquí las confirmaciones
humanas y mantiene la memoria entre tareas encadenadas de la sesión.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from praxeon.config import PraxeonConfig, default_config
from praxeon.core.session_context import SessionContextManager
from praxeon.domain.events import EventType, RuntimeEvent
from praxeon.domain.governance import FallbackMode, resolve_fallback_mode
from praxeon.live_agent.runtime_session import LiveTaskResult, TerminalMission

console = Console(legacy_windows=False)

MAX_RECOVERY_ATTEMPTS = 3
_VERDICT_STYLE = {"ALLOW": "green", "REVIEW": "yellow", "REPLAN": "yellow", "BLOCK": "red"}


def render_event(event: RuntimeEvent) -> None:
    """Pinta en la terminal un evento de la misión."""
    payload = event.payload or {}
    if event.type == EventType.ACTION_PROPOSED:
        args = json.dumps(payload.get("arguments") or {}, ensure_ascii=False)[:200]
        console.print(f"\n[cyan]▶ Paso {payload.get('step', '?')}:[/] [bold]{payload.get('tool')}[/] [dim]{args}[/]")
        if payload.get("thought_rationale"):
            console.print(f"  [italic white]Pensamiento:[/] {str(payload['thought_rationale'])[:400]}")
    elif event.type == EventType.POLICY_DECIDED:
        status = str(payload.get("status", "")).upper()
        style = _VERDICT_STYLE.get(status, "white")
        console.print(f"  [{style}]Supervisor: {status}[/] [dim]{payload.get('reason_code', '')}[/]")
    elif event.type == EventType.OBSERVATION_RECORDED:
        console.print(f"  [dim]Observación: {str(payload.get('output', ''))[:300]}[/]")
    elif event.type == EventType.INTERVENTION_APPLIED:
        text = payload.get("message") or payload.get("warning") or payload.get("error")
        if text:
            console.print(f"  [bold yellow]⚡ {text}[/]")
    elif event.type == EventType.APPROVAL_REQUESTED:
        reasons = ", ".join(map(str, payload.get("reason_code") or []))
        console.print(Panel(
            f"[bold]Riesgo:[/] {payload.get('risk_level')}\n[bold]Motivos:[/] {reasons}",
            title="🛡️ La acción necesita tu confirmación",
            border_style="yellow",
        ))


def ask_approval(event: RuntimeEvent) -> bool:
    """Pregunta en la terminal si se aprueba la acción. Sin terminal interactiva, no se aprueba."""
    if not sys.stdin.isatty():
        console.print("[yellow]Entrada no interactiva: la acción que pide confirmación no se ejecuta.[/]")
        return False
    try:
        return Prompt.ask("[bold yellow]¿Aprobar esta acción?[/]", choices=["s", "n"], default="n") == "s"
    except (KeyboardInterrupt, EOFError):
        return False


def _chat_history(session: SessionContextManager) -> List[Dict[str, str]]:
    """Memoria entre tareas encadenadas: objetivo y respuesta de cada tarea previa de la sesión."""
    history: List[Dict[str, str]] = []
    for record in session.task_records:
        history.append({"role": "user", "content": record.goal})
        history.append({"role": "assistant", "content": record.final_answer or record.summary})
    return history


def run_live_agent(
    task: str,
    max_steps: int = 15,
    config: Optional[PraxeonConfig] = None,
    gemini_api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    session_context: Optional[SessionContextManager] = None,
    middleware: Optional[Any] = None,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    context_manager: Optional[Any] = None,
    fallback_mode: Optional[str | FallbackMode] = None,
    mission: Optional[TerminalMission] = None,
) -> Tuple[bool, str, SessionContextManager, Optional[Any]]:
    """Ejecuta una tarea del agente en la terminal como misión supervisada del runtime.

    Devuelve ``(terminada, respuesta, sesión, middleware)``. ``middleware`` y ``context_manager`` se aceptan por
    compatibilidad y ya no intervienen: la supervisión es la del runtime. Con FAIL_CLOSED, si el modelo del agente no
    está disponible se lanza ``RuntimeError`` en vez de simular.
    """
    cfg = config or default_config
    session = session_context or SessionContextManager()
    mode = resolve_fallback_mode(fallback_mode or getattr(cfg, "fallback_mode", None))

    if not task or not str(task).strip():
        try:
            task = Prompt.ask("[bold cyan]🎯 Introduce el objetivo o tarea para el agente[/]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Operación cancelada por el usuario.[/]")
            return (False, "Cancelado por el usuario", session, middleware)
        if not task:
            console.print("[yellow]No se introdujo ninguna tarea. Finalizando.[/]")
            return (False, "Tarea vacía", session, middleware)

    runner = mission or TerminalMission(on_event=render_event, approver=ask_approval)
    supervisor = getattr(cfg, "supervisor", "laya")
    limit = "sin límite (hasta finish)" if max_steps <= 0 else str(max_steps)
    memory = "primera tarea" if session.is_empty() else f"{len(session.task_records)} tarea(s) previa(s)"
    console.print(Panel(
        f"[bold white]Tarea del Agente:[/] {task}\n"
        f"[bold white]Supervisor:[/] PRAXEON ({supervisor})\n"
        f"[bold white]Modelo del agente:[/] {model_name or 'por defecto'} ({(provider or 'auto').upper()})\n"
        f"[bold white]Límite de pasos:[/] {limit}\n"
        f"[bold white]Contexto de sesión:[/] {memory}",
        title="🤖 [bold green]Live Agent supervisado por PRAXEON[/]",
        border_style="green",
    ))

    result = _run_with_recovery(runner, task, mode, provider, model_name, gemini_api_key, base_url, max_steps, supervisor, session)
    answer = result.final_answer or "Tarea sin respuesta final."
    if result.finished:
        console.print(Panel(answer, title="🎉 Respuesta del agente", border_style="green"))
    else:
        console.print(Panel(answer, title="ℹ️ La tarea terminó sin finish aceptado", border_style="yellow"))

    session.record_completed_task(
        goal=task,
        summary=answer[:200],
        final_answer=answer,
        executed_steps=len(result.steps),
        history_steps=[
            {
                "tool": step.get("tool"),
                "arguments": step.get("arguments"),
                "observation": step.get("observation", ""),
                "thought": step.get("thought", ""),
                "verdict": step.get("verdict"),
                "synthetic_fallback": step.get("synthetic_fallback", False),
                "model_source": step.get("model_source"),
                "governance_mode": mode.value,
            }
            for step in result.steps
        ],
    )
    return (result.finished, answer, session, middleware)


def _run_with_recovery(
    runner: TerminalMission,
    task: str,
    mode: FallbackMode,
    provider: Optional[str],
    model_name: Optional[str],
    api_key: Optional[str],
    base_url: Optional[str],
    max_steps: int,
    supervisor: str,
    session: SessionContextManager,
) -> LiveTaskResult:
    """Ejecuta la tarea; si el modelo del agente falla, ofrece cambiar de modelo o de clave y reintenta.

    Con FAIL_CLOSED no se ofrece nada: un modelo no disponible detiene la tarea con ``RuntimeError``.
    """
    for attempt in range(MAX_RECOVERY_ATTEMPTS):
        result = runner.run(
            task,
            provider=provider,
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            max_steps=max_steps,
            supervisor=supervisor,
            llm_failure_policy="fail_closed" if mode == FallbackMode.FAIL_CLOSED else "synthetic_fallback",
            chat_history=_chat_history(session),
        )
        failure = result.fatal_error or result.llm_error
        if failure is None:
            return result
        if mode == FallbackMode.FAIL_CLOSED:
            console.print(f"[bold red]❌ {failure}[/]")
            raise RuntimeError(f"Ejecución de Live Agent bloqueada en modo FAIL_CLOSED: {failure}")
        if attempt == MAX_RECOVERY_ATTEMPTS - 1 or not sys.stdin.isatty():
            return result
        from praxeon.model_recovery import prompt_model_recovery_menu
        action, payload = prompt_model_recovery_menu(
            error_message=failure, current_model=f"{provider or 'auto'}:{model_name or ''}", console=console
        )
        if action == "change_model" and payload:
            if ":" in payload:
                provider, model_name = payload.split(":", 1)
            else:
                model_name = payload
        elif action == "update_key" and payload:
            api_key = payload.split(":", 1)[1] if ":" in payload else payload
        else:
            return result
        console.print(f"[green]✓ Reintentando con {provider} ({model_name or 'por defecto'})...[/]")
    return result


def run_live_gemini_agent(*args, **kwargs):
    """Alias retrocompatible para run_live_agent."""
    return run_live_agent(*args, **kwargs)


def run_live_session(
    initial_task: Optional[str] = None,
    max_steps: int = 15,
    config: Optional[PraxeonConfig] = None,
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    once: bool = False,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    fallback_mode: Optional[str | FallbackMode] = None,
) -> None:
    """Ejecuta una sesión interactiva continua con el agente LLM y el supervisor PRAXEON."""
    cfg = config or default_config

    console.print(Panel(
        "[bold cyan]🎯 PRAXEON LIVE AGENT — MODO INTERACTIVO ITERATIVO[/]\n\n"
        "Supervisión continua con evaluación por bloques (chunking) y memoria acumulada entre tareas concatenadas.\n"
        "Introduce objetivos sucesivamente. Escribe [bold yellow]'reset'[/] para nueva sesión limpia, o [bold red]'salir'[/] para finalizar.",
        title="🧭 [bold white]PRAXEON Live Agent Session[/]",
        border_style="cyan",
    ))

    is_first_iteration = True
    session_context = SessionContextManager()
    middleware = None

    while True:
        current_task: Optional[str] = None

        if is_first_iteration and initial_task and initial_task.strip():
            current_task = initial_task.strip()
            is_first_iteration = False
        else:
            is_first_iteration = False
            if not sys.stdin.isatty():
                # Modo no interactivo / datos enviados por tubería stdin
                piped_line = sys.stdin.readline()
                if not piped_line:
                    break
                candidate = piped_line.strip()
                if not candidate or candidate.lower() in ("salir", "exit", "quit", "q"):
                    break
                current_task = candidate
            else:
                # Entrada interactiva por consola
                while True:
                    try:
                        console.print()
                        user_input = Prompt.ask(
                            "[bold cyan]🎯 Introduce el objetivo o tarea para el agente[/] [dim](o 'reset' para nueva sesión, 'salir' para terminar)[/]"
                        ).strip()
                    except (KeyboardInterrupt, EOFError):
                        console.print("\n[bold yellow]Sesión interactiva finalizada por el usuario.[/]")
                        return

                    if not user_input:
                        console.print("[yellow]⚠️ Por favor, introduce un objetivo válido o escribe 'salir' para terminar.[/]")
                        continue
                    if user_input.lower() in ("salir", "exit", "quit", "q"):
                        console.print("[bold green]👋 Sesión interactiva de PRAXEON finalizada. ¡Hasta pronto![/]")
                        return
                    current_task = user_input
                    break

        if not current_task:
            break

        if current_task.lower() in ("reset", "clear", "nueva", "nuevo", "/reset", "/clear"):
            session_context.reset()
            middleware = None
            console.print("[bold green]🧹 Memoria de sesión reiniciada. Puedes introducir un nuevo objetivo limpio.[/]")
            continue

        # Resolver dinámicamente run_live_gemini_agent para soportar monkeypatching a nivel de paquete
        import praxeon.live_agent as live_agent_pkg
        runner_fn = getattr(live_agent_pkg, "run_live_gemini_agent", run_live_gemini_agent)

        res = runner_fn(
            task=current_task,
            max_steps=max_steps,
            config=cfg,
            gemini_api_key=api_key,
            model_name=model_name,
            session_context=session_context,
            middleware=middleware,
            provider=provider,
            base_url=base_url,
            fallback_mode=fallback_mode,
        )
        if isinstance(res, tuple) and len(res) >= 4:
            _, _, session_context, middleware = res

        if once:
            break

        if sys.stdin.isatty():
            console.print("\n" + "━" * 70)
            console.print(f"[bold green]✓ Tarea finalizada bajo supervisión PRAXEON. Memoria de sesión ({len(session_context.task_records)} tareas) disponible para el siguiente objetivo.[/]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Agent Loop supervisado por PRAXEON")
    parser.add_argument("task", type=str, nargs="?", default=None, help="Objetivo o tarea del agente")
    parser.add_argument("--goal", "-g", type=str, default=None, help="Objetivo o tarea del agente (alias de task)")
    parser.add_argument("--once", action="store_true", help="Ejecutar solo el objetivo especificado y salir sin modo interactivo continuo")
    parser.add_argument("--steps", type=int, default=15, help="Máximo número de pasos por tarea (usa 0 para modo ilimitado)")
    parser.add_argument("--chunk-size", type=int, default=3, help="Tamaño de bloque para evaluación agrupada")
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
    parser.add_argument("--typesafe", action="store_true", help="Utilizar TypeSafe AI como evaluador")
    parser.add_argument(
        "--fallback-mode",
        type=str,
        default=os.getenv("PRAXEON_FALLBACK_MODE", "BEST_EFFORT"),
        choices=["fail-closed", "best-effort", "FAIL_CLOSED", "BEST_EFFORT"],
        help="Modo de gobernanza ante fallo de LLM: FAIL_CLOSED (bloqueo estricto) o BEST_EFFORT (fallback sintético)",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="auto",
        choices=["auto", "groq", "ollama", "openrouter", "lmstudio", "openai", "anthropic", "claude_cli", "gemini", "simulator", "simulated"],
        help="Proveedor del LLM del agente (auto, groq, ollama, openrouter, gemini, anthropic, claude_cli, etc.)",
    )
    parser.add_argument("--base-url", type=str, default=None, help="URL base para servidor local (Ollama/LM Studio) o endpoint OpenAI-compatible")
    parser.add_argument("--api-key", "--gemini-key", dest="api_key", type=str, default=None, help="Clave de API del LLM")
    parser.add_argument("--model", type=str, default=None, help="Modelo LLM a utilizar")
    args = parser.parse_args()

    cfg = default_config.model_copy()
    if args.supervisor:
        cfg.supervisor = args.supervisor
    if args.supervisor_model:
        cfg.provider.model = args.supervisor_model
    if args.laya_backend:
        cfg.laya_backend = args.laya_backend
    if args.typesafe:
        cfg.use_typesafe_api = True
    if args.chunk_size:
        cfg.evaluation_chunk_size = args.chunk_size
    if args.fallback_mode:
        cfg.fallback_mode = args.fallback_mode

    run_live_session(
        initial_task=args.goal or args.task,
        max_steps=args.steps,
        config=cfg,
        api_key=args.api_key,
        model_name=args.model,
        once=args.once,
        provider=args.provider,
        base_url=args.base_url,
        fallback_mode=args.fallback_mode,
    )


if __name__ == "__main__":
    main()
