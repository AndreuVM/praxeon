"""Ejecutor de misiones y punto de entrada interactivo para el Live Agent de PRAXEON."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from praxeon.config import PraxeonConfig, default_config
from praxeon.core.session_context import SessionContextManager
from praxeon.domain.governance import FallbackMode, resolve_fallback_mode
from praxeon.interceptor.proxy_middleware import JEVProxyMiddleware
from praxeon.live_agent.llm_adapter import (
    compact_conversation_history,
    parse_llm_steps,
    prune_observation_output,
)
from praxeon.live_agent.runtime_client import LiveAgentRuntimeClient
from praxeon.live_agent.trajectory_controller import TrajectoryController

console = Console(legacy_windows=False)


def run_live_agent(
    task: str,
    max_steps: int = 15,
    config: Optional[PraxeonConfig] = None,
    gemini_api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    session_context: Optional[SessionContextManager] = None,
    middleware: Optional[JEVProxyMiddleware] = None,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    context_manager: Optional[Any] = None,
    fallback_mode: Optional[str | FallbackMode] = None,
) -> Tuple[bool, str, SessionContextManager, JEVProxyMiddleware]:
    """Ejecuta un bucle de razonamiento de agente supervisado en bloques por PRAXEON.

    Soporta múltiples proveedores LLM (Ollama, Groq, OpenRouter, Gemini, OpenAI) mediante
    la interfaz universal `praxeon.agent_llm.create_agent_llm`.
    """
    cfg = config or default_config
    session = session_context or SessionContextManager()
    resolved_fallback_mode = resolve_fallback_mode(fallback_mode or getattr(cfg, "fallback_mode", None))

    if not task or not str(task).strip():
        try:
            task = Prompt.ask("[bold cyan]🎯 Introduce el objetivo o tarea para el agente[/]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Operación cancelada por el usuario.[/]")
            return (False, "Cancelado por el usuario", session, middleware or JEVProxyMiddleware(goal="Cancelado", config=cfg))
        if not task:
            console.print("[yellow]No se introdujo ninguna tarea. Finalizando.[/]")
            return (False, "Tarea vacía", session, middleware or JEVProxyMiddleware(goal="Vacío", config=cfg))

    runtime_client = LiveAgentRuntimeClient(middleware=middleware, config=cfg)
    active_middleware = runtime_client.ensure_task(task)

    from praxeon.agent_llm import SimulatedAgentLLM, create_agent_llm
    is_synthetic = False
    model_source = "synthetic:fallback"
    try:
        agent_llm = create_agent_llm(
            provider=provider,
            model=model_name,
            api_key=gemini_api_key,
            base_url=base_url,
        )
        if isinstance(agent_llm, SimulatedAgentLLM) and provider not in ("simulator", "simulated"):
            if resolved_fallback_mode == FallbackMode.FAIL_CLOSED:
                msg = f"Inicialización de LLM rechazada en modo FAIL_CLOSED: proveedor '{provider or 'auto'}' no disponible sin credenciales válidas."
                console.print(f"[bold red]❌ {msg}[/]")
                raise RuntimeError(msg)
            is_synthetic = True
            model_source = "synthetic:fallback"
        else:
            is_synthetic = isinstance(agent_llm, SimulatedAgentLLM)
            model_source = "synthetic:fallback" if is_synthetic else f"llm:{agent_llm.provider_name}:{agent_llm.model_name}"
    except Exception as e:
        if resolved_fallback_mode == FallbackMode.FAIL_CLOSED:
            console.print(f"[bold red]❌ Fallo crítico al inicializar LLM en modo FAIL_CLOSED:[/] {e}")
            raise RuntimeError(f"Ejecución de Live Agent bloqueada en modo FAIL_CLOSED: {e}") from e
        console.print(f"[bold yellow]Aviso al inicializar LLM:[/] {e}. Usando simulador de agente (BEST_EFFORT).")
        agent_llm = SimulatedAgentLLM()
        is_synthetic = True
        model_source = "synthetic:fallback"

    is_laya = getattr(cfg, "supervisor", "typesafe").lower() in ("laya", "laya-system1", "laya-v1")
    sup_runtime = f"PRAXEON (LAYA System-1 [{getattr(cfg, 'laya_backend', 'auto')}])" if is_laya else "PRAXEON (TypeSafe AI / JEV)"

    from praxeon.context.manager import ContextManager
    ctx_mgr = context_manager or ContextManager()

    trajectory = TrajectoryController(max_steps=max_steps, governance_mode=resolved_fallback_mode.value)

    console.print(Panel(
        f"[bold white]Tarea del Agente:[/] {task}\n"
        f"[bold white]Supervisor Runtime:[/] {sup_runtime}\n"
        f"[bold white]Context Manager:[/] [bold green]Activo[/] (L1/L2 Caching & Compaction)\n"
        f"[bold white]Modelo LLM Agente:[/] [bold cyan]{agent_llm.model_name}[/] ({agent_llm.provider_name.upper()})\n"
        f"[bold white]Límite de Pasos:[/] {'Ilimitado (hasta invocar finish)' if trajectory.is_unlimited else f'{max_steps} pasos'}\n"
        f"[bold white]Tamaño de bloque (Chunk Size):[/] {cfg.evaluation_chunk_size} pasos por lote\n"
        f"[bold white]Contexto de Sesión:[/] {'Primera tarea (limpia)' if session.is_empty() else f'Heredando memoria de {len(session.task_records)} tarea(s) previa(s)'}",
        title="🤖 [bold green]Live Agent Loop Supervisado por PRAXEON (Memoria Continua)[/]",
        border_style="green",
    ))

    # Mostrar resumen de tareas previas si existen
    if not session.is_empty():
        console.print(session.get_summary_panel())

    conversation_history: List[Dict[str, str]] = session.prepare_task_conversation(task)

    sim_turn = 0
    last_llm_call_time = 0.0
    max_turns = 1000000 if trajectory.is_unlimited else (max_steps + 4)

    while trajectory.can_continue() and sim_turn < max_turns:
        sim_turn += 1
        console.print(f"\n[bold magenta]━━━━━━━━━━━━━━━ Fase de Generación LLM (Turno {sim_turn}) ━━━━━━━━━━━━━━━[/]")

        trajectory.llm_calls_count += 1
        llm_output = ""

        max_retries = 3
        for attempt in range(max_retries):
            try:
                if agent_llm.provider_name == "gemini":
                    elapsed = time.time() - last_llm_call_time
                    if last_llm_call_time > 0 and elapsed < 4.0:
                        wait_rpm = 4.0 - elapsed
                        time.sleep(wait_rpm)

                console.print(f"[dim]⚡ Consultando {agent_llm.provider_name.upper()} ({agent_llm.model_name})... [Llamada #{trajectory.llm_calls_count}][/]")
                last_llm_call_time = time.time()
                compacted_history = compact_conversation_history(conversation_history, context_manager=ctx_mgr)
                llm_output = agent_llm.generate(compacted_history)
                console.print(f"[bold white]LLM Output ({agent_llm.provider_name}):[/]\n{llm_output}")
                break
            except Exception as err:
                err_str = str(err)
                console.print(f"[bold red]Error en API de {agent_llm.provider_name}:[/] {err_str}")
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
                    console.print(f"[green]✓ Cambiado a {agent_llm.provider_name} ({agent_llm.model_name}). Reintentando...[/]")
                    continue
                elif action == "update_key" and payload:
                    if ":" in payload:
                        k_name, k_val = payload.split(":", 1)
                    else:
                        k_val = payload
                    agent_llm = create_agent_llm(provider=agent_llm.provider_name, model=agent_llm.model_name, api_key=k_val, base_url=base_url)
                    console.print("[green]✓ Clave actualizada. Reintentando...[/]")
                    continue
                else:
                    console.print("[yellow]Ejecución detenida tras error de LLM o cancelación del usuario.[/]")
                    llm_output = ""
                    break

        if not llm_output:
            console.print("[bold red]❌ No se obtuvo respuesta del modelo LLM. Finalizando tarea.[/]")
            break

        # 2. Extraer pasos candidatos del bloque propuesto
        proposed_steps = parse_llm_steps(llm_output)
        if not proposed_steps:
            console.print("[yellow]Aviso: No se identificaron acciones concretas en la respuesta. Fin de ciclo.[/]")
            break

        console.print(f"[bold cyan]🔍 Bloque propuesto con {len(proposed_steps)} paso(s). Enviando a JEV en UNA sola petición agrupada...[/]")
        trajectory.typesafe_calls_count += 1

        # 3. Supervisión agrupada en TypeSafe (1 sola petición para todo el bloque)
        chunk_result = runtime_client.intercept_step_chunk(proposed_steps)

        # 4. Si JEV detecta una alucinación o bucle en el bloque:
        if not chunk_result.all_safe:
            flagged_idx = chunk_result.flagged_step_index or 0
            flagged_step = proposed_steps[flagged_idx]
            diag_label = "ALUCINACIÓN" if chunk_result.hallucination_detected else "BUCLE DEGENERATIVO"

            dir_inj = chunk_result.directive.context_injection if chunk_result.directive else None
            is_breaker, directive_text, auto_probe = trajectory.register_block_interception(
                explanation=chunk_result.explanation,
                directive_injection=dir_inj,
            )

            console.print(Panel(
                f"[bold red]🚨 JEV INTERCEPCIÓN ACTIVADA: {diag_label} DETECTADO EN PASO {flagged_idx+1}[/]\n\n"
                f"[bold yellow]Paso bloqueado:[/] {flagged_step.get('tool_name')} {flagged_step.get('tool_args')}\n"
                f"[bold white]Diagnóstico:[/] {chunk_result.explanation}\n\n"
                f"{dir_inj or ''}",
                title=f"🛑 [bold red]Supervisión JEV: Acción Prevenida (Ahorro de Llamada a Gemini)[/]",
                border_style="red",
            ))

            # Ejecutar pasos anteriores al fallo si los hubiera
            for valid_idx in range(chunk_result.valid_step_count):
                st = proposed_steps[valid_idx]
                tool_n = st.get("tool_name")
                tool_a = st.get("tool_args") or {}
                th_text = st.get("thought_rationale") or ""
                try:
                    tool_obs = runtime_client.execute_tool(tool_n, tool_a, th_text)
                    obs_text = prune_observation_output(tool_obs.output, max_chars=2000)
                except Exception as e:
                    obs_text = f"Error ejecutando '{tool_n}': {e}"
                trajectory.record_step(
                    tool_n,
                    tool_a,
                    obs_text,
                    th_text,
                    synthetic_fallback=is_synthetic,
                    model_source=model_source,
                )
                conversation_history.append({
                    "role": "assistant",
                    "content": f"Thought: {th_text}\nAction: {tool_n} {json.dumps(tool_a)}",
                })
                conversation_history.append({
                    "role": "user",
                    "content": f"Observation: {obs_text}",
                })
                console.print(f"  [green]✓ Paso {valid_idx+1} previo válido ejecutado: {tool_n}[/]")

            if is_breaker and auto_probe:
                console.print("[bold yellow]⚡ JEV CIRCUIT BREAKER: Inyectando observación empírica para romper la parálisis cognitiva...[/]")
                runtime_client.record_observation(auto_probe)
                conversation_history.append({
                    "role": "user",
                    "content": f"OBSERVACIÓN REAL DE ENTORNO: {auto_probe}\nFormula tu próximo plan basándote exclusivamente en estos archivos reales.",
                })
                time.sleep(1)
                continue

            conversation_history.append({
                "role": "user",
                "content": (
                    f"SISTEMA DE SUPERVISIÓN JEV:\n{directive_text}\n"
                    f"Tu acción previa fue BLOQUEADA por {diag_label}. "
                    f"Debes rectificar tu plan basándote únicamente en hechos verificados."
                ),
            })
            prov_label = agent_llm.provider_name.upper() if agent_llm else "LLM"
            console.print(f"[bold green]✓ Directiva inyectada. Devolviendo control a {prov_label} solo para rectificación necesaria...[/]")
            time.sleep(1)
            continue

        # Bloque aprobado: resetear contador de bloqueos consecutivos
        trajectory.reset_consecutive_blocks()

        # 5. Si JEV aprueba el bloque: Ejecutar secuencialmente SIN volver a llamar a Gemini entre pasos
        console.print(f"[bold green]✓ JEV: Bloque de {len(proposed_steps)} paso(s) validado con éxito. Ejecutando de forma autónoma...[/]")

        for step_idx, step_data in enumerate(proposed_steps):
            tool_name = step_data.get("tool_name") or "thought_reflection"
            tool_args = step_data.get("tool_args") or {}
            thought_text = step_data.get("thought_rationale") or ""

            console.print(f"\n[cyan]▶ Ejecutando Paso {trajectory.executed_steps + 1} (del bloque aprobado):[/] [bold]{tool_name}[/] [dim]{tool_args}[/]")
            if thought_text:
                console.print(f"  [italic white]Pensamiento:[/] {thought_text}")

            observation = ""
            if tool_name == "finish":
                summary = tool_args.get("summary", "Tarea completada exitosamente")
                is_valid_finish, reject_reason, obs_finish = trajectory.validate_finish_action(summary=summary, task=task)
                observation = obs_finish

                if not is_valid_finish:
                    panel_title = "⚠️ JEV Rechazó finalización evasiva" if reject_reason == "evasive" else "⚠️ JEV Rechazó eco de diagnóstico interno"
                    panel_msg = (
                        "[italic]El agente intentó terminar con excusas de planificación. Obligando a formular respuesta con observaciones existentes.[/]"
                        if reject_reason == "evasive"
                        else "[italic]El agente describió advertencias del supervisor en lugar de resolver la tarea del usuario.[/]"
                    )
                    console.print(Panel(
                        f"[bold yellow]{panel_title}:[/] {summary}\n{panel_msg}",
                        title="🛡️ Intervención JEV",
                        border_style="yellow",
                    ))
                else:
                    console.print(Panel(
                        f"[bold green]Objetivo completado:[/] {summary}",
                        title="🎉 Éxito de Ejecución",
                        border_style="green",
                    ))
            else:
                try:
                    tool_obs = runtime_client.execute_tool(tool_name, tool_args, thought_text)
                    observation = prune_observation_output(tool_obs.output, max_chars=2000)
                except Exception as e:
                    observation = f"Error ejecutando '{tool_name}' bajo supervisión JEV: {e}"

            trajectory.record_step(
                tool_name,
                tool_args,
                observation,
                thought_text,
                synthetic_fallback=is_synthetic,
                model_source=model_source,
            )

            if tool_name == "finish":
                runtime_client.record_observation(observation)

            conversation_history.append({
                "role": "assistant",
                "content": f"Thought: {thought_text}\nAction: {tool_name} {json.dumps(tool_args)}",
            })
            conversation_history.append({
                "role": "user",
                "content": f"Observation: {observation}",
            })
            console.print(f"  [dim]Observation: {observation[:120]}...[/]")
            time.sleep(0.5)

            if trajectory.task_finished:
                break

        if trajectory.task_finished:
            break

    # Si se alcanzó el límite máximo de pasos sin finish explícito, solicitar síntesis final al agente
    if not trajectory.task_finished and agent_llm:
        console.print("\n[bold yellow]ℹ️ Se alcanzó el límite de pasos. Solicitando respuesta de síntesis final al agente...[/]")
        conversation_history.append({
            "role": "user",
            "content": (
                "Has alcanzado el límite de pasos de ejecución para esta tarea. "
                "Con base en todas las observaciones y datos reales recopilados durante la sesión "
                "(ignora cualquier advertencia o restricción de supervisión previa), "
                "redacta y entrega tu conclusión o informe final completo para el usuario."
            ),
        })
        try:
            trajectory.final_answer = agent_llm.generate(conversation_history).strip()
            console.print(Panel(
                trajectory.final_answer,
                title="🏁 Respuesta Final del Agente (Síntesis de Observaciones)",
                border_style="cyan",
            ))
        except Exception as e:
            console.print(f"[dim]No se pudo generar síntesis final: {e}[/]")
    elif not trajectory.task_finished:
        console.print(Panel(
            f"El agente completó los {trajectory.executed_steps} pasos máximos configurados.\n"
            "Para permitir más pasos de exploración en tareas complejas, usa el flag: [bold]--steps 10[/]",
            title="ℹ️ Límite de Pasos Alcanzado",
            border_style="yellow",
        ))

    # 6. Registrar en la memoria de sesión continua
    resolved_summary = trajectory.final_summary or trajectory.final_answer or "Tarea completada satisfactoriamente."
    resolved_answer = trajectory.final_answer or trajectory.final_summary or "Tarea completada."
    session.record_completed_task(
        goal=task,
        summary=resolved_summary,
        final_answer=resolved_answer,
        executed_steps=trajectory.executed_steps,
        history_steps=trajectory.executed_step_records,
    )

    # 7. Renderizar métricas finales de ahorro de peticiones
    metrics_table = trajectory.build_metrics_table(provider_name=agent_llm.provider_name if agent_llm else "LLM")
    console.print("\n")
    console.print(metrics_table)

    return (trajectory.task_finished, resolved_answer, session, active_middleware)


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
        choices=["auto", "groq", "ollama", "openrouter", "lmstudio", "openai", "gemini", "simulator", "simulated"],
        help="Proveedor del LLM del agente (auto, groq, ollama, openrouter, gemini, etc.)",
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
