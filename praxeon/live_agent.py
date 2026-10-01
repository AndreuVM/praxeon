"""Agente autónomo en vivo supervisado en tiempo real por JEV-Reasoning-Navigator con supervisión agrupada (chunking) y llamadas condicionales a Gemini."""

import argparse
import io
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Asegurar codificación UTF-8 en Windows para evitar errores con charmap cp1252
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer") and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from praxeon.config import PraxeonConfig, default_config
from praxeon.core.session_context import SessionContextManager
from praxeon.interceptor.proxy_middleware import JEVProxyMiddleware
from praxeon.models.schema import InterventionLevel

console = Console(legacy_windows=False)


def decode_process_bytes(raw_bytes: bytes) -> str:
    """Decodifica de manera inteligente la salida binaria de procesos en Windows y Linux evitando caracteres corruptos."""
    if not raw_bytes:
        return ""
    try:
        text = raw_bytes.decode("utf-8")
        if "\ufffd" not in text:
            return text
    except UnicodeDecodeError:
        pass
    for enc in ("cp1252", "cp850", "latin-1"):
        try:
            return raw_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def prune_observation_output(output: str, max_chars: int = 2000) -> str:
    """Trunca salidas extensas de comandos preservando el inicio y el final relevante para optimizar tokens."""
    cleaned = (output or "Comando ejecutado sin salida").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    keep_head = int(max_chars * 0.65)
    keep_tail = int(max_chars * 0.35)
    omitted = len(cleaned) - (keep_head + keep_tail)
    return (
        f"{cleaned[:keep_head]}\n\n"
        f"[... {omitted} caracteres intermedios omitidos por JEV Token Pruner para optimizar contexto ...]\n\n"
        f"{cleaned[-keep_tail:]}"
    )


def build_optimized_prompt(conversation_history: List[Dict[str, str]], max_recent_turns: int = 8) -> str:
    """Construye el prompt optimizado para el LLM aplicando compresión a turnos antiguos si la conversación es larga."""
    if len(conversation_history) <= (max_recent_turns * 2 + 2):
        return "\n\n".join(f"[{m['role'].upper()}]: {m['content']}" for m in conversation_history) + "\n\n[ASSISTANT]:\n"
    
    header = conversation_history[:2]
    recent = conversation_history[-(max_recent_turns * 2):]
    middle_count = len(conversation_history) - len(header) - len(recent)
    summary_msg = {
        "role": "user",
        "content": f"[... Historial intermedio: {middle_count} mensajes anteriores comprimidos por JEV para preservar ventana de contexto ...]",
    }
    compacted = header + [summary_msg] + recent
    return "\n\n".join(f"[{m['role'].upper()}]: {m['content']}" for m in compacted) + "\n\n[ASSISTANT]:\n"


def parse_llm_steps(llm_output: str) -> List[Dict[str, Any]]:
    """Extrae uno o múltiples pasos (Thought + Action) de la salida del LLM."""
    steps: List[Dict[str, Any]] = []

    # 1. Normalizar etiquetas markdown (**Thought:**, **Action:**, etc.)
    text = re.sub(r"[\*_]{1,2}(Step\s+\d+|Paso\s+\d+|Thought|Action)[\*_]{0,2}\s*:", r"\1:", llm_output, flags=re.IGNORECASE)
    # 2. Insertar saltos de línea antes de palabras clave si vienen en línea continua o sin salto
    text = re.sub(r"(?i)(?<!\n)\s*(action\s*:)", r"\n\1", text)
    text = re.sub(r"(?i)(?<!\n)\s*(thought\s*:)", r"\n\1", text)
    text = re.sub(r"(?i)(?<!\n)\s*(step\s+\d+\s*:)", r"\n\1", text)
    text = re.sub(r"(?i)(?<!\n)\s*(paso\s+\d+\s*:)", r"\n\1", text)

    current_thought = ""
    current_tool = None
    current_args: Dict[str, Any] = {}

    lines = text.strip().splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        lower_line = stripped.lower()
        if lower_line.startswith("thought:"):
            if current_tool:
                steps.append({
                    "thought_rationale": current_thought,
                    "tool_name": current_tool,
                    "tool_args": current_args,
                })
                current_thought = ""
                current_tool = None
                current_args = {}
            current_thought = stripped[len("thought:"):].strip()
        elif lower_line.startswith("action:"):
            action_content = stripped[len("action:"):].strip()
            # 1. Comprobar sintaxis estilo llamada de función: tool_name(...)
            m = re.match(r"^([a-zA-Z0-9_]+)\s*\((.*)\)$", action_content, re.DOTALL)
            if m:
                current_tool = m.group(1).strip()
                raw_arg = m.group(2).strip()
                try:
                    parsed = json.loads(raw_arg)
                    if isinstance(parsed, dict):
                        current_args = parsed
                    elif isinstance(parsed, str):
                        clean_str = parsed.strip()
                        if current_tool == "run_command":
                            current_args = {"command": clean_str}
                        elif current_tool in ("read_file", "view_file"):
                            current_args = {"path": clean_str}
                        elif current_tool == "finish":
                            current_args = {"summary": clean_str}
                        else:
                            current_args = {"raw": clean_str}
                    else:
                        current_args = {"raw": str(parsed)}
                except Exception:
                    # Detectar si viene con sintaxis Python kwargs: tool(param1="val1", param2="val2")
                    kw_pairs = re.findall(r'([a-zA-Z0-9_]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s,)]+))', raw_arg)
                    if kw_pairs:
                        current_args = {}
                        for k, v1, v2, v3 in kw_pairs:
                            current_args[k] = v1 or v2 or v3
                    else:
                        clean_arg = raw_arg.strip('"').strip("'")
                        for prefix in ("path=", "command=", "summary=", "diff="):
                            if clean_arg.startswith(prefix):
                                clean_arg = clean_arg[len(prefix):].strip('"').strip("'")
                                break
                        if current_tool == "run_command":
                            current_args = {"command": clean_arg}
                        elif current_tool in ("read_file", "view_file"):
                            current_args = {"path": clean_arg}
                        elif current_tool == "finish":
                            current_args = {"summary": clean_arg}
                        else:
                            current_args = {"raw": clean_arg, "command": clean_arg}
            else:
                # 2. Sintaxis estándar: tool_name <json_o_string>
                parts = action_content.split(maxsplit=1)
                if parts:
                    current_tool = parts[0].strip()
                    if len(parts) > 1:
                        raw_arg = parts[1].strip()
                        try:
                            parsed = json.loads(raw_arg)
                            if isinstance(parsed, dict):
                                current_args = parsed
                            else:
                                current_args = {"raw": str(parsed)}
                        except Exception:
                            kw_pairs = re.findall(r'([a-zA-Z0-9_]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s,)]+))', raw_arg)
                            if kw_pairs:
                                current_args = {}
                                for k, v1, v2, v3 in kw_pairs:
                                    current_args[k] = v1 or v2 or v3
                            else:
                                clean_arg = raw_arg.strip('"').strip("'")
                                for prefix in ("path=", "command=", "summary=", "diff="):
                                    if clean_arg.startswith(prefix):
                                        clean_arg = clean_arg[len(prefix):].strip('"').strip("'")
                                        break
                                if current_tool == "run_command":
                                    current_args = {"command": clean_arg}
                                elif current_tool in ("read_file", "view_file"):
                                    current_args = {"path": clean_arg}
                                elif current_tool == "finish":
                                    current_args = {"summary": clean_arg}
                                else:
                                    current_args = {"raw": clean_arg, "command": clean_arg}
                    else:
                        current_args = {}
        elif (lower_line.startswith("step ") or lower_line.startswith("paso ")) and ":" in stripped:
            if current_tool:
                steps.append({
                    "thought_rationale": current_thought,
                    "tool_name": current_tool,
                    "tool_args": current_args,
                })
                current_thought = ""
                current_tool = None
                current_args = {}

    if current_tool:
        steps.append({
            "thought_rationale": current_thought,
            "tool_name": current_tool,
            "tool_args": current_args,
        })
    elif current_thought:
        # Solo interpretar como 'finish' si el pensamiento es explícitamente concluyente
        lower_th = current_thought.lower()
        is_conclusion = any(w in lower_th for w in ("conclu", "finaliz", "resuelt", "solución", "solucion", "terminad", "completad", "finish"))
        if is_conclusion:
            steps.append({
                "thought_rationale": current_thought,
                "tool_name": "finish",
                "tool_args": {"summary": current_thought},
            })
    elif not steps and llm_output.strip():
        lower_out = llm_output.lower()
        if any(w in lower_out for w in ("conclu", "finaliz", "resuelt", "solución", "solucion", "terminad", "completad")):
            steps.append({
                "thought_rationale": "Conclusión directa emitida por el agente LLM",
                "tool_name": "finish",
                "tool_args": {"summary": llm_output.strip()},
            })

    return steps


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
) -> Tuple[bool, str, SessionContextManager, JEVProxyMiddleware]:
    """Ejecuta un bucle de razonamiento de agente supervisado en bloques por PRAXEON.
    
    Soporta múltiples proveedores LLM (Ollama, Groq, OpenRouter, Gemini, OpenAI) mediante
    la interfaz universal `praxeon.agent_llm.create_agent_llm`.
    """
    cfg = config or default_config
    is_unlimited = (max_steps <= 0)

    session = session_context or SessionContextManager()

    if not task or not str(task).strip():
        try:
            task = Prompt.ask("[bold cyan]🎯 Introduce el objetivo o tarea para el agente[/]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Operación cancelada por el usuario.[/]")
            return (False, "Cancelado por el usuario", session, middleware or JEVProxyMiddleware(goal="Cancelado", config=cfg))
        if not task:
            console.print("[yellow]No se introdujo ninguna tarea. Finalizando.[/]")
            return (False, "Tarea vacía", session, middleware or JEVProxyMiddleware(goal="Vacío", config=cfg))

    if middleware is None:
        middleware = JEVProxyMiddleware(goal=task, config=cfg)
    else:
        middleware.start_subtask(task)

    from praxeon.agent_llm import create_agent_llm, SimulatedAgentLLM
    try:
        agent_llm = create_agent_llm(
            provider=provider,
            model=model_name,
            api_key=gemini_api_key,
            base_url=base_url,
        )
    except Exception as e:
        console.print(f"[bold yellow]Aviso al inicializar LLM:[/] {e}. Usando simulador de agente.")
        agent_llm = SimulatedAgentLLM()

    is_laya = getattr(cfg, "supervisor", "typesafe").lower() in ("laya", "laya-system1", "laya-v1")
    sup_runtime = f"PRAXEON (LAYA System-1 [{getattr(cfg, 'laya_backend', 'auto')}])" if is_laya else "PRAXEON (TypeSafe AI / JEV)"

    console.print(Panel(
        f"[bold white]Tarea del Agente:[/] {task}\n"
        f"[bold white]Supervisor Runtime:[/] {sup_runtime}\n"
        f"[bold white]Modelo LLM Agente:[/] [bold cyan]{agent_llm.model_name}[/] ({agent_llm.provider_name.upper()})\n"
        f"[bold white]Límite de Pasos:[/] {'Ilimitado (hasta invocar finish)' if is_unlimited else f'{max_steps} pasos'}\n"
        f"[bold white]Tamaño de bloque (Chunk Size):[/] {cfg.evaluation_chunk_size} pasos por lote\n"
        f"[bold white]Contexto de Sesión:[/] {'Primera tarea (limpia)' if session.is_empty() else f'Heredando memoria de {len(session.task_records)} tarea(s) previa(s)'}",
        title="🤖 [bold green]Live Agent Loop Supervisado por PRAXEON (Memoria Continua)[/]",
        border_style="green",
    ))

    # Mostrar resumen de tareas previas si existen
    if not session.is_empty():
        console.print(session.get_summary_panel())

    chunk_size = cfg.evaluation_chunk_size
    conversation_history: List[Dict[str, str]] = session.prepare_task_conversation(task)

    executed_steps = 0
    executed_step_records: List[Dict[str, Any]] = []
    final_summary: str = ""
    final_answer: str = ""
    llm_calls_count = 0
    typesafe_calls_count = 0
    interventions_count = 0
    sim_turn = 0
    task_finished = False

    last_llm_call_time = 0.0

    consecutive_blocks = 0
    max_turns = 1000000 if is_unlimited else (max_steps + 4)
    while (is_unlimited or executed_steps < max_steps) and sim_turn < max_turns:
        sim_turn += 1
        console.print(f"\n[bold magenta]━━━━━━━━━━━━━━━ Fase de Generación LLM (Turno {sim_turn}) ━━━━━━━━━━━━━━━[/]")

        llm_calls_count += 1
        llm_output = ""

        max_retries = 3
        for attempt in range(max_retries):
            try:
                if agent_llm.provider_name == "gemini":
                    elapsed = time.time() - last_llm_call_time
                    if last_llm_call_time > 0 and elapsed < 4.0:
                        wait_rpm = 4.0 - elapsed
                        time.sleep(wait_rpm)

                console.print(f"[dim]⚡ Consultando {agent_llm.provider_name.upper()} ({agent_llm.model_name})... [Llamada #{llm_calls_count}][/]")
                last_llm_call_time = time.time()
                llm_output = agent_llm.generate(conversation_history)
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
        typesafe_calls_count += 1

        # 3. Supervisión agrupada en TypeSafe (1 sola petición para todo el bloque)
        chunk_result = middleware.intercept_step_chunk(proposed_steps)

        # 4. Si JEV detecta una alucinación o bucle en el bloque:
        if not chunk_result.all_safe:
            interventions_count += 1
            consecutive_blocks += 1
            flagged_idx = chunk_result.flagged_step_index or 0
            flagged_step = proposed_steps[flagged_idx]
            diag_label = "ALUCINACIÓN" if chunk_result.hallucination_detected else "BUCLE DEGENERATIVO"

            console.print(Panel(
                f"[bold red]🚨 JEV INTERCEPCIÓN ACTIVADA: {diag_label} DETECTADO EN PASO {flagged_idx+1}[/]\n\n"
                f"[bold yellow]Paso bloqueado:[/] {flagged_step.get('tool_name')} {flagged_step.get('tool_args')}\n"
                f"[bold white]Diagnóstico:[/] {chunk_result.explanation}\n\n"
                f"{chunk_result.directive.context_injection if chunk_result.directive else ''}",
                title=f"🛑 [bold red]Supervisión JEV: Acción Prevenida (Ahorro de Llamada a Gemini)[/]",
                border_style="red",
            ))

            # Ejecutar pasos anteriores al fallo si los hubiera
            for valid_idx in range(chunk_result.valid_step_count):
                st = proposed_steps[valid_idx]
                executed_steps += 1
                tool_n = st.get("tool_name")
                tool_a = st.get("tool_args") or {}
                th_text = st.get("thought_rationale") or ""
                try:
                    tool_obs = middleware.execute_tool(tool_n, tool_a, th_text)
                    obs_text = prune_observation_output(tool_obs.output, max_chars=2000)
                except Exception as e:
                    obs_text = f"Error ejecutando '{tool_n}': {e}"
                executed_step_records.append({
                    "tool_name": tool_n,
                    "tool_args": tool_a,
                    "observation": obs_text,
                })
                conversation_history.append({
                    "role": "assistant",
                    "content": f"Thought: {th_text}\nAction: {tool_n} {json.dumps(tool_a)}",
                })
                conversation_history.append({
                    "role": "user",
                    "content": f"Observation: {obs_text}",
                })
                console.print(f"  [green]✓ Paso {valid_idx+1} previo válido ejecutado: {tool_n}[/]")

            # Inyectar la directiva en el contexto de Gemini para que rectifique en la siguiente llamada
            directive_text = chunk_result.directive.context_injection if chunk_result.directive else chunk_result.explanation

            # Mecanismo Circuit Breaker ante bloqueos repetitivos
            if consecutive_blocks == 2:
                if executed_steps >= 2:
                    directive_text += (
                        "\n\n💡 [ORIENTACIÓN DE CIERRE]: Ya has obtenido observaciones empíricas durante la sesión. "
                        "Si dispones de suficiente contexto para responder a la tarea del usuario, sintetiza tu informe o conclusión "
                        "y entrega el resultado invocando obligatoriamente:\n"
                        'Action: finish {"summary": "informe o conclusión fundamentada basada en lo observado"}'
                    )
                else:
                    directive_text += (
                        "\n\n🚨 [ALERTA DE DESBLOQUEO]: Has acumulado bloqueos seguidos. "
                        "Obtén primero evidencias del entorno con comandos o lecturas de inspección "
                        "(ej. read_file o run_command con 'dir' o 'git status') antes de cualquier otra acción."
                    )
            elif consecutive_blocks >= 3:
                console.print("[bold yellow]⚡ JEV CIRCUIT BREAKER: Inyectando observación empírica para romper la parálisis cognitiva...[/]")
                auto_probe = "Observación automática de archivos reales en disco: "
                try:
                    auto_probe += ", ".join([f for f in os.listdir(".") if not f.startswith(".")][:6])
                except Exception:
                    auto_probe += "pyproject.toml, README.md"
                middleware.record_observation(auto_probe)
                conversation_history.append({
                    "role": "user",
                    "content": f"OBSERVACIÓN REAL DE ENTORNO: {auto_probe}\nFormula tu próximo plan basándote exclusivamente en estos archivos reales.",
                })
                consecutive_blocks = 0
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
        consecutive_blocks = 0

        # 5. Si JEV aprueba el bloque: Ejecutar secuencialmente SIN volver a llamar a Gemini entre pasos
        console.print(f"[bold green]✓ JEV: Bloque de {len(proposed_steps)} paso(s) validado con éxito. Ejecutando de forma autónoma...[/]")

        task_finished = False
        for step_idx, step_data in enumerate(proposed_steps):
            executed_steps += 1
            tool_name = step_data.get("tool_name") or "thought_reflection"
            tool_args = step_data.get("tool_args") or {}
            thought_text = step_data.get("thought_rationale") or ""

            console.print(f"\n[cyan]▶ Ejecutando Paso {executed_steps} (del bloque aprobado):[/] [bold]{tool_name}[/] [dim]{tool_args}[/]")
            if thought_text:
                console.print(f"  [italic white]Pensamiento:[/] {thought_text}")

            observation = ""
            if tool_name == "finish":
                summary = tool_args.get("summary", "Tarea completada exitosamente")
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
                    console.print(Panel(
                        f"[bold yellow]⚠️ JEV Rechazó finalización evasiva:[/] {summary}\n"
                        "[italic]El agente intentó terminar con excusas de planificación. Obligando a formular respuesta con observaciones existentes.[/]",
                        title="🛡️ Intervención JEV",
                        border_style="yellow",
                    ))
                    task_finished = False
                    observation = (
                        "OBSERVACIÓN DEL SUPERVISOR (JEV): Tu llamada a 'finish' ha sido RECHAZADA porque contiene un texto de planificación o evasión ('pendiente de lectura'). "
                        "NO puedes finalizar sin dar una respuesta concreta. Analiza las observaciones y el contenido ya obtenido y responde directamente con los hallazgos en tu siguiente turno."
                    )
                elif any(m in sum_lower for m in meta_leakage_markers):
                    console.print(Panel(
                        f"[bold yellow]⚠️ JEV Rechazó eco de diagnóstico interno:[/] {summary}\n"
                        "[italic]El agente describió advertencias del supervisor en lugar de resolver la tarea del usuario.[/]",
                        title="🛡️ Intervención JEV",
                        border_style="yellow",
                    ))
                    task_finished = False
                    observation = (
                        f"OBSERVACIÓN DEL SUPERVISOR (JEV): Tu llamada a 'finish' ha sido RECHAZADA porque estás describiendo "
                        f"mensajes de diagnóstico interno del supervisor en lugar de responder a la tarea del usuario: '{task}'. "
                        f"Prohibido mencionar 'herramienta vetada', 'bucle detectado' o 'policy_engine'. Responde directamente a: '{task}' "
                        f"con tus conclusiones y opinión fundamentada sobre el proyecto que has inspeccionado."
                    )
                else:
                    console.print(Panel(
                        f"[bold green]Objetivo completado:[/] {summary}",
                        title="🎉 Éxito de Ejecución",
                        border_style="green",
                    ))
                    task_finished = True
                    final_summary = summary
                    final_answer = summary
                    observation = f"Tarea finalizada: {summary}"
            else:
                try:
                    tool_obs = middleware.execute_tool(tool_name, tool_args, thought_text)
                    observation = prune_observation_output(tool_obs.output, max_chars=2000)
                except Exception as e:
                    observation = f"Error ejecutando '{tool_name}' bajo supervisión JEV: {e}"

            executed_step_records.append({
                "tool_name": tool_name,
                "tool_args": tool_args,
                "observation": observation,
            })
            # Nota: middleware.record_observation ya se invoca internamente en execute_tool si aplica
            if tool_name == "finish":
                middleware.record_observation(observation)
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

        if task_finished:
            break

    # Si se alcanzó el límite máximo de pasos sin finish explícito, solicitar síntesis final al agente
    if not task_finished and agent_llm:
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
            final_answer = agent_llm.generate(conversation_history).strip()
            console.print(Panel(
                final_answer,
                title="🏁 Respuesta Final del Agente (Síntesis de Observaciones)",
                border_style="cyan",
            ))
        except Exception as e:
            console.print(f"[dim]No se pudo generar síntesis final: {e}[/]")
    elif not task_finished:
        console.print(Panel(
            f"El agente completó los {executed_steps} pasos máximos configurados.\n"
            "Para permitir más pasos de exploración en tareas complejas, usa el flag: [bold]--steps 10[/]",
            title="ℹ️ Límite de Pasos Alcanzado",
            border_style="yellow",
        ))

    # 6. Registrar en la memoria de sesión continua
    resolved_summary = final_summary or final_answer or "Tarea completada satisfactoriamente."
    resolved_answer = final_answer or final_summary or "Tarea completada."
    session.record_completed_task(
        goal=task,
        summary=resolved_summary,
        final_answer=resolved_answer,
        executed_steps=executed_steps,
        history_steps=executed_step_records,
    )

    # 7. Renderizar métricas finales de ahorro de peticiones
    savings_pct = max(0, int(((executed_steps - llm_calls_count) / max(1, executed_steps)) * 100))
    metrics_table = Table(title="📊 Métricas de Eficiencia JEV y Ahorro de Cuota API", show_header=True)
    metrics_table.add_column("Métrica", style="bold white")
    metrics_table.add_column("Valor", justify="right", style="bold cyan")
    metrics_table.add_column("Impacto", style="green")

    metrics_table.add_row("Pasos cognitivos ejecutados", str(executed_steps), "Progreso real")
    metrics_table.add_row(f"Llamadas a LLM ({agent_llm.provider_name.upper()})", str(llm_calls_count), f"Ahorro de ~{savings_pct}% en llamadas LLM")
    metrics_table.add_row("Llamadas a TypeSafe AI", str(typesafe_calls_count), "Evaluaciones en lote (Chunking)")
    metrics_table.add_row("Intervenciones JEV / Alucinaciones evitadas", str(interventions_count), "Prevención de desvíos cognitivos")

    console.print("\n")
    console.print(metrics_table)

    return (task_finished, resolved_answer, session, middleware)


def run_live_gemini_agent(*args, **kwargs):
    """Alias retrocompatible para run_live_agent."""
    return run_live_agent(*args, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Agent Loop supervisado por PRAXEON")
    parser.add_argument("task", type=str, nargs="?", default=None, help="Objetivo o tarea del agente")
    parser.add_argument("--goal", "-g", type=str, default=None, help="Objetivo o tarea del agente (alias de task)")
    parser.add_argument("--once", action="store_true", help="Ejecutar solo el objetivo especificado y salir sin modo interactivo continuo")
    parser.add_argument("--steps", type=int, default=15, help="Máximo número de pasos por tarea (usa 0 para modo ilimitado)")
    parser.add_argument("--chunk-size", type=int, default=3, help="Tamaño de bloque para evaluación agrupada")
    parser.add_argument("--typesafe", action="store_true", help="Utilizar TypeSafe AI como evaluador")
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
    if args.typesafe:
        cfg.use_typesafe_api = True
    if args.chunk_size:
        cfg.evaluation_chunk_size = args.chunk_size

def run_live_session(
    initial_task: Optional[str] = None,
    max_steps: int = 15,
    config: Optional[PraxeonConfig] = None,
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    once: bool = False,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
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

        res = run_live_gemini_agent(
            task=current_task,
            max_steps=max_steps,
            config=cfg,
            gemini_api_key=api_key,
            model_name=model_name,
            session_context=session_context,
            middleware=middleware,
            provider=provider,
            base_url=base_url,
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

    run_live_session(
        initial_task=args.goal or args.task,
        max_steps=args.steps,
        config=cfg,
        api_key=args.api_key,
        model_name=args.model,
        once=args.once,
        provider=args.provider,
        base_url=args.base_url,
    )


if __name__ == "__main__":
    main()

