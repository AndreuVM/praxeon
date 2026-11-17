"""Bucle ReAct supervisado de un agente como pipeline de pasos (auditoría A-01).

Cada turno del bucle (Thought/Action -> política -> ejecución -> observación) es una secuencia fija de pasos con un
contrato común, ``Step.run(ctx) -> Outcome``:

- ``Outcome.CONTINUE``: el paso no cierra el turno; sigue el siguiente paso.
- ``Outcome.END``: el turno termina aquí (ya registrado); tras la pausa entre pasos empieza el siguiente.
- ``Outcome.STOP``: el bucle termina (finish aceptado, cortacircuitos técnico, terminación forzada...).

``TURN_STEPS`` fija el orden. ``AgentLoop`` envuelve los turnos: pausa/parada del operador, límite de pasos y el resumen
final de un nodo de workflow que agota su presupuesto.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable, Iterable

from praxeon import attachments
from praxeon.core.pipeline import Outcome, Step, run_steps
from praxeon.domain.events import EventType
from praxeon.reasoning.observations import reports_missing_path
from praxeon.runtime.sandbox import get_default_workspace_root
from praxeon.server.runtime_service.mission_prompt import build_mission_system_prompt
from praxeon.server.runtime_service.node_prompt import (
    NODE_FORMAT_FEEDBACK,
    NODE_INTENTION_FEEDBACK,
    build_node_system_prompt,
    looks_like_intention,
)
from praxeon.server.runtime_service.veto_feedback import explain_veto
from praxeon.server.schemas.action import ProposeActionRequest
from praxeon.server.services import generate_goal_tailored_steps

logger = logging.getLogger("praxeon.server.dependencies")

_MISSING_PREFIX = "UNGROUNDED_FILE_NOT_FOUND ("

# El resumen de pasos antiguos solo se rehace cada tantos pasos: entre medias la conversación solo crece por el final
COMPRESS_BATCH = 6

# Fallos seguidos (vetos o errores) tras los que el supervisor corta la misión. Con 4 cortaba a modelos pequeños que aún
# podían rectificar (en el banco, un tercio de las misiones fallidas); el aviso de «cambia de enfoque» sale a los 2.
MAX_CONSECUTIVE_FAILURES = max(3, int(os.getenv("PRAXEON_MAX_CONSECUTIVE_FAILURES", "6") or 6))
NODE_BUDGET_WARN_AT = (4, 1)   # pasos que le quedan a un nodo de workflow cuando se le avisa de que concluya
MAX_FINISH_REJECTIONS = 4   # veces que un nodo de workflow puede ver rechazado su finish por faltar campos antes de aceptarlo
MAX_TECHNICAL_FAILURES = 3   # fallos seguidos del proveedor LLM que abren el cortacircuitos técnico

FINISH_TOOLS = ("finish", "complete_task", "done", "complete", "task_completed")
PLACEHOLDER_TOOLS = ("action", "undefined", "none", "null", "step", "")
SAFE_INSPECTION_COMMAND = "python -c \"print('Paso de inspección segura')\""

_MUTATING_TOOLS = ("edit_file", "write_file", "create_file", "apply_patch", "delete_file")
_MCP_WRITE_HINTS = ("create", "update", "write", "delete", "report", "add", "set", "move", "release", "acquire")
_TECHNICAL_GOAL_HINTS = (
    "test", "pytest", "bug", "error", "edit", "modific", "implement", "crea",
    "analiz", "archivo", "archivos", "codigo", "código", "como funciona",
    "cómo funciona", "audita", "inspeccion", "revisa",
)
_CONCLUSION_HINTS = ("finish", "complet", "conclu", "finaliz", "resuelt", "respuesta", "opini")
_FINISH_CALL = re.compile(
    r'finish\s*\(\s*(?:summary\s*=\s*)?(?:"""(.*?)"""|\'\'\'(.*?)\'\'\'|"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|(.*?))\s*\)',
    re.DOTALL,
)


# ------------------------------------------------------------------------------------------------ utilidades


def _made_progress(record: dict[str, Any]) -> bool:
    """Un paso correcto que cambió algo: escribió/ejecutó, o llamó a una herramienta MCP de escritura."""
    if not record.get("success"):
        return False
    tool = str(record.get("tool") or "")
    return tool in _MUTATING_TOOLS or (tool.startswith("mcp__") and any(h in tool.lower() for h in _MCP_WRITE_HINTS))


def _llm_observation_limit() -> int:
    """Caracteres de una observación que se entregan íntegros al LLM (`PRAXEON_LLM_OBSERVATION_CHARS`, 150.000 por defecto)."""
    try:
        return max(5_000, int(os.getenv("PRAXEON_LLM_OBSERVATION_CHARS", "") or 150_000))
    except ValueError:
        return 150_000


def _missing_path_from_reasons(reason_codes: list[str] | None) -> str | None:
    for code in reason_codes or []:
        if str(code).startswith(_MISSING_PREFIX) and str(code).endswith(")"):
            return str(code)[len(_MISSING_PREFIX):-1]
    return None


def _targets_missing_path(tool: str, args: dict[str, Any], workspace_root: str | None) -> bool:
    """True si la acción lee o lista una ruta que de verdad no existe en el workspace (comprobación directa)."""
    if tool not in ("read_file", "view_file", "list_dir"):
        return False
    raw = str((args or {}).get("path") or (args or {}).get("file") or "").strip()
    if not raw:
        return False
    path = os.path.expanduser(os.path.expandvars(raw))
    if not os.path.isabs(path):
        path = os.path.join(workspace_root or os.getcwd(), path)
    return not os.path.exists(path)


def _missing_path_feedback(path: str, workspace_root: str | None) -> str:
    """Mensaje para el agente cuando referencia una ruta inexistente (evita que la reintente o la alucine)."""
    base = workspace_root or os.getcwd()
    candidate = path if os.path.isabs(path) else os.path.join(base, path)
    anchor = os.path.dirname(candidate)
    while anchor and not os.path.isdir(anchor) and os.path.dirname(anchor) != anchor:
        anchor = os.path.dirname(anchor)
    listing = ""
    try:
        entries = sorted(os.listdir(anchor))[:30]
        shown = ", ".join(entries) if entries else "(vacío)"
        listing = f"\nContenido real de '{anchor}': {shown}"
    except OSError:
        pass
    return (
        f"❌ La ruta '{path}' NO EXISTE en el sistema de archivos (workspace: {base}). "
        "No ha sido leída ni modificada nada. No reintentes esta ruta ni inventes otras: "
        "usa 'list_dir' o 'run_command' (p. ej. dir/ls) para descubrir los archivos reales y trabaja solo con los que existan."
        + listing
    )


def _truncate_for_llm(observation: str) -> str:
    """Recorta una observación demasiado larga conservando el principio (70 %) y el final."""
    limit = _llm_observation_limit()
    if len(observation) <= limit:
        return observation
    keep_head = limit * 7 // 10
    keep_tail = limit - keep_head
    omitted = len(observation) - limit
    return (
        f"{observation[:keep_head]}\n\n[... Truncado: {omitted} caracteres intermedios omitidos por JEV para optimizar "
        f"contexto del LLM ...]\n\n{observation[-keep_tail:]}"
    )


def _answer_after_last_thought(llm_output: str) -> str:
    """El texto tras el último «Thought:» (o todo, si no lo hay): la respuesta directa del agente."""
    if "thought:" in llm_output.lower():
        return re.split(r"(?i)thought\s*:", llm_output)[-1].strip()
    return llm_output


def build_loop_system_prompt(
    runtime: Any,
    mission: dict[str, Any],
    goal: str,
    trace: dict[str, Any] | None,
    role_preamble: str,
    tool_filter: Callable[[str], str | None] | None,
) -> str:
    """Prompt de sistema del bucle: el de misión o el de nodo de workflow, más las secciones de MCP, web y skills.

    Una sección que falla se omite con un aviso en el log: un servidor MCP, el acceso web o una skill rotos nunca deben
    impedir la misión.
    """
    ws_root = mission.get("workspace_root") or get_default_workspace_root()
    if trace is not None:
        from praxeon.core.session_context import SessionContextManager
        env_info = SessionContextManager().get_environment_info(root_dir=ws_root)
        system_prompt = role_preamble + build_node_system_prompt(ws_root, env_info, goal)
    else:
        system_prompt = role_preamble + build_mission_system_prompt(ws_root)

    def mcp_section(mcp: Any) -> str:
        mcp.ensure_started()
        return str(mcp.prompt_section(tool_filter) or "")

    sections = (
        ("mcp", mcp_section, "No se pudieron preparar las herramientas MCP: %s"),
        ("web", lambda web: str(web.prompt_section(tool_filter) or ""), "No se pudieron preparar las herramientas web: %s"),
        ("skills", lambda skills: str(skills.prompt_section(ws_root) or ""), "No se pudieron preparar las skills: %s"),
    )
    for attr, render, warning in sections:
        provider = getattr(runtime, attr, None)
        if provider is None:
            continue
        try:
            section = render(provider)
        except Exception as err:  # noqa: BLE001 - frontera con un generador de sección del prompt: el fallo se registra y se omite la sección
            logger.warning(warning, err)
            section = ""
        if section:
            system_prompt = system_prompt + "\n\n" + section
    return system_prompt


# ------------------------------------------------------------------------------------------------ contrato de pasos


@dataclass
class LoopContext:
    """Configuración del bucle, su estado entre turnos y el estado del turno en curso."""

    # configuración (no cambia durante el bucle)
    runtime: Any
    mission: dict[str, Any]
    agent_llm: Any
    goal: str
    max_steps: int
    delay_sec: float
    provider_name: str
    session_mode: str
    system_prompt: str
    id_prefix: str
    root_id: str
    owns_session: bool
    anchor_id: str
    tool_filter: Callable[[str], str | None] | None = None
    trace: dict[str, Any] | None = None
    finish_check: Callable[[str], str | None] | None = None
    llm_fail_policy: str = "synthetic_fallback"
    images: list[dict[str, Any]] | None = None     # imágenes adjuntas (FE-006), cargadas y limpiadas una vez al primer turno
    images_loaded: bool = False

    # estado entre turnos
    step_idx: int = 0
    step_records: list[dict[str, Any]] = field(default_factory=list)
    active_parent_id: str = ""
    consecutive_failures: int = 0
    technical_failures: int = 0
    semantic_fixations: int = 0
    finish_rejections: int = 0   # finish rechazados por faltar campos del grafo: tras MAX_FINISH_REJECTIONS se acepta
    recent_action_signatures: list[str] = field(default_factory=list)
    circuit_breaker_triggered: bool = False
    final_answer: str | None = None
    forced_termination: bool = False

    # estado del turno en curso (begin_turn lo reinicia)
    llm_output: str = ""
    llm_ms: float = 0.0
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    thought: str = ""
    action_node_id: str = ""
    resp: Any = None
    obs_output: str = ""
    executed_successfully: bool = False
    pending_approval: bool = False

    def __post_init__(self) -> None:
        if not self.active_parent_id:
            self.active_parent_id = self.anchor_id

    @property
    def sid(self) -> str:
        return str(self.mission["session_id"])

    @property
    def is_node(self) -> bool:
        """El bucle trabaja dentro de un nodo de workflow (no es la misión completa)."""
        return self.trace is not None

    @property
    def is_finish(self) -> bool:
        return self.tool in FINISH_TOOLS

    @property
    def workspace_root(self) -> str | None:
        return self.mission.get("workspace_root")

    @property
    def step_verdict(self) -> Any:
        return getattr(self.resp, "status", "ERROR") if self.resp else "ERROR"

    @property
    def step_reasons(self) -> str:
        if not self.resp:
            return ""
        return ", ".join(getattr(getattr(self.resp, "policy", None), "reason_codes", []) or [])

    def begin_turn(self) -> None:
        self.step_idx += 1
        self.mission["current_step"] = self.step_idx
        self.llm_output, self.llm_ms = "", 0.0
        self.tool, self.args, self.thought, self.action_node_id = "", {}, "", ""
        self.resp, self.obs_output = None, ""
        self.executed_successfully = self.pending_approval = False

    def record(self, verdict: str, reason_code: str, observation: str, success: bool = False, **extra: Any) -> None:
        """Añade el registro del turno en curso al historial que verá el agente."""
        self.step_records.append({
            "step": self.step_idx,
            "thought": self.thought,
            "tool": self.tool,
            "arguments": self.args,
            "verdict": verdict,
            "reason_code": reason_code,
            "observation": observation,
            "success": success,
            **extra,
        })

    def emit(self, node_id: str, payload: dict[str, Any], event_type: EventType = EventType.INTERVENTION_APPLIED, **kw: Any) -> None:
        self.runtime.event_bus.emit(session_id=self.sid, event_type=event_type, node_id=node_id, payload=payload, **kw)


def run_turn(steps: Iterable[Step[LoopContext]], ctx: LoopContext) -> Outcome:
    """Ejecuta los pasos de un turno en orden hasta que uno cierra el turno o detiene el bucle."""
    return run_steps(steps, ctx)


# ------------------------------------------------------------------------------------------------ pasos del turno


class PersistMissionStep(Step[LoopContext]):
    """Guarda el estado de la misión en SQLite (reconciliación periódica). Un fallo no detiene la misión."""

    name = "persist_mission"

    def run(self, ctx: LoopContext) -> Outcome:
        store = ctx.runtime.state_store
        if hasattr(store, "save_mission"):
            try:
                store.save_mission(dict(ctx.mission))
            except Exception as save_err:  # noqa: BLE001 - persistencia best-effort: un fallo al guardar la misión se registra y la misión sigue
                logger.debug("Error persistiendo misión en SQLite: %s", save_err)
        return Outcome.CONTINUE


class NodeBudgetWarningStep(Step[LoopContext]):
    """Avisa a un nodo de workflow de que le quedan pocos pasos, para que concluya con lo hecho en vez de agotarlos."""

    name = "node_budget_warning"

    def run(self, ctx: LoopContext) -> Outcome:
        if ctx.is_node and ctx.max_steps > 0 and ctx.step_records and (ctx.max_steps - ctx.step_idx) in NODE_BUDGET_WARN_AT:
            left = ctx.max_steps - ctx.step_idx + 1
            last = ctx.step_records[-1]
            last["observation"] = (
                f"{last.get('observation') or ''}\n\n⏳ [PRAXEON]: te quedan {left} pasos en este nodo. Si no puedes terminar todo, "
                "llama ya a finish con lo hecho y lo que falta; el flujo te devolverá el trabajo pendiente en la siguiente pasada."
            )
        return Outcome.CONTINUE


class GenerateStep(Step[LoopContext]):
    """Pide al LLM el siguiente Thought/Action.

    Ante un fallo del proveedor: tras MAX_TECHNICAL_FAILURES seguidos abre el cortacircuitos técnico (STOP); con la
    política ``fail_closed`` se detiene al primero; si no, sigue en modo degradado con un paso del planificador sintético.
    """

    name = "generate"

    @staticmethod
    def _load_images(ctx: LoopContext) -> None:
        """Lee y limpia (EXIF, tamaño) las imágenes del mensaje una sola vez. Si una ya no está, el agente lo sabe en vez de ver nada."""
        if ctx.images_loaded:
            return
        ctx.images_loaded = True
        refs = ctx.mission.get("image_paths") or []
        if not refs:
            return
        try:
            ctx.images = attachments.load_images(refs)
        except Exception as exc:  # noqa: BLE001 - un archivo borrado a mitad de misión no debe tumbar la misión
            logger.warning("No se pudieron cargar las imágenes adjuntas de la misión %s: %s", ctx.mission.get("session_id"), exc)
            ctx.goal += "\n\n(Las imágenes adjuntas ya no están disponibles: no puedes verlas.)"

    def run(self, ctx: LoopContext) -> Outcome:
        llm = ctx.agent_llm
        self._load_images(ctx)
        # ContextManager como única autoridad de ensamblado de prompt y compresión semántica
        conversation = ctx.runtime.context_manager.assemble_mission_prompt(
            images=ctx.images,
            goal=ctx.goal,
            system_prompt=ctx.system_prompt,
            steps_history=ctx.step_records,
            environment_info=None,
            chat_history=None if ctx.is_node else ctx.mission.get("chat_history"),
            node_mode=ctx.is_node,
            # compresión por lotes: el prefijo se mantiene estable entre turnos (caché de prompt del proveedor); un
            # proveedor con sesión propia (CLI) guarda la conversación entera y no necesita comprimir
            compress_batch=COMPRESS_BATCH,
            compress=not getattr(llm, "stateful_session", False),
        )
        try:
            started = time.perf_counter()
            if hasattr(llm, "last_usage"):
                llm.last_usage = None
            ctx.llm_output = llm.generate(conversation, system_prompt=ctx.system_prompt)
            ctx.llm_ms = (time.perf_counter() - started) * 1000.0
            logger.info(
                "LLM %s/%s: %.1fs, prompt ~%d caracteres, respuesta %d caracteres (%s paso %d)",
                ctx.provider_name, getattr(llm, "model_name", "?"), ctx.llm_ms / 1000.0,
                len(ctx.system_prompt or "") + sum(len(m.get("content", "")) for m in conversation),
                len(ctx.llm_output), (ctx.trace or {}).get("node_id", "misión"), ctx.step_idx,
            )
            ctx.technical_failures = 0
            self._record_usage(ctx)
            self._publish_agy_activity(ctx)
        except Exception as gen_err:  # noqa: BLE001 - frontera con el LLM del agente: cualquier fallo lo gestiona _on_failure (reintento o corte)
            return self._on_failure(ctx, gen_err)
        return Outcome.CONTINUE

    @staticmethod
    def _publish_agy_activity(ctx: LoopContext) -> None:
        """Con herramientas de agy activadas: anota en el log de eventos lo que usó y guarda en el workspace las imágenes que generó."""
        take = getattr(ctx.agent_llm, "take_activity", None)
        if not callable(take):
            return
        try:
            activity = take()
        except Exception as exc:  # noqa: BLE001 - recoger lo generado nunca debe tumbar el turno
            logger.warning("No se pudo leer la actividad de agy: %s", exc)
            return
        if activity.get("tools") or activity.get("denied"):
            ctx.emit(ctx.root_id, {
                "tools": [{"name": t.get("name"), "detail": t.get("detail", "")} for t in activity.get("tools", [])],
                "denied": int(activity.get("denied") or 0),
                "step": ctx.step_idx,
            }, EventType.AGY_TOOL_USED)
        for image in activity.get("images", []):
            try:
                saved = attachments.save_generated_image(ctx.sid, image.get("name"), image["data"], image.get("mime", ""))
            except Exception as exc:  # noqa: BLE001 - una imagen que no se puede guardar se descarta con aviso, no rompe la misión
                logger.warning("Se descarta una imagen generada por agy: %s", exc)
                ctx.emit(ctx.root_id, {"message": f"Se descartó una imagen generada: {exc}"}, EventType.INTERVENTION_APPLIED)
                continue
            ctx.emit(ctx.root_id, {
                "rel_path": saved["rel_path"], "name": saved["name"], "width": saved["width"], "height": saved["height"], "size": saved["size"],
                "step": ctx.step_idx,
            }, EventType.AGY_IMAGE_GENERATED)

    @staticmethod
    def _record_usage(ctx: LoopContext) -> None:
        usage = getattr(ctx.agent_llm, "last_usage", None)
        if not isinstance(usage, dict):
            return
        cached = usage.get("cached_tokens", 0)
        ctx.runtime.context_manager.record_actual_tokens(
            prompt_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cached_tokens=cached,
            cache_hit_status="hit" if cached else "miss",
        )
        logger.info(
            "Tokens reales %s: prompt %d (caché %d), salida %d",
            ctx.provider_name, usage.get("prompt_tokens", 0), cached, usage.get("output_tokens", 0),
        )

    @staticmethod
    def _on_failure(ctx: LoopContext, gen_err: Exception) -> Outcome:
        ctx.technical_failures += 1
        provider = ctx.provider_name
        logger.warning("Error durante generación con LLM '%s': %s", provider, gen_err)

        if ctx.technical_failures >= MAX_TECHNICAL_FAILURES:
            ctx.emit(ctx.root_id, {
                "intervention": "TECHNICAL_CIRCUIT_BREAKER",
                "message": (
                    f"⚡ PRAXEON TECHNICAL CIRCUIT BREAKER: Fallos técnicos consecutivos ({ctx.technical_failures}) con el "
                    f"proveedor LLM '{provider}'. Circuito técnico ABIERTO para prevenir saturación."
                ),
                "consecutive_technical_failures": ctx.technical_failures,
            })
            ctx.mission["stopped"] = True
            ctx.mission["technical_failure"] = f"El proveedor LLM '{provider}' falló {ctx.technical_failures} veces seguidas. Último error: {gen_err}"[:600]
            return Outcome.STOP

        if ctx.llm_fail_policy == "fail_closed":
            ctx.emit(ctx.root_id, {
                "error": f"Fallo de generación en LLM '{provider}' bajo política fail_closed: {gen_err}",
                "fatal": True,
                "llm_failure_policy": "fail_closed",
            })
            ctx.mission["stopped"] = True
            ctx.mission["technical_failure"] = f"El proveedor LLM '{provider}' falló y la política es fail_closed. Último error: {gen_err}"[:600]
            return Outcome.STOP

        ctx.emit(ctx.root_id, {
            "warning": (
                f"[DEGRADED_MODE: SYNTHETIC_PLANNER] Fallo temporal de LLM '{provider}' ({gen_err}). "
                "Activando contingencia contextual."
            ),
            "degraded_mode": True,
            "planner": "synthetic",
        })
        backup = generate_goal_tailored_steps(goal=ctx.goal, max_steps=ctx.max_steps if ctx.max_steps > 0 else max(ctx.step_idx, 12))
        backup_idx = min(ctx.step_idx - 1, len(backup) - 1)
        step = backup[backup_idx] if (backup and backup_idx >= 0) else {
            "tool_name": "run_command",
            "tool_args": {"command": SAFE_INSPECTION_COMMAND},
            "thought_rationale": "Paso de contingencia tras fallo del proveedor LLM.",
        }
        ctx.llm_output = (
            f"Thought: [DEGRADED_MODE: SYNTHETIC_PLANNER] [Contingencia por fallo temporal en {provider}]: "
            f"{step.get('thought_rationale', '')}\nAction: {step.get('tool_name', 'run_command')}({json.dumps(step.get('tool_args', {}))})"
        )
        return Outcome.CONTINUE


class ParseActionStep(Step[LoopContext]):
    """Extrae del texto del LLM la herramienta, sus argumentos y el razonamiento.

    Sin acción reconocible: un nodo de workflow recibe un aviso de formato (END); la misión interpreta un finish
    explícito o una respuesta directa, salvo en el primer paso de una tarea técnica, que fuerza una inspección.
    """

    name = "parse_action"

    def run(self, ctx: LoopContext) -> Outcome:
        from praxeon.live_agent import parse_llm_steps

        parsed = parse_llm_steps(ctx.llm_output)
        first_tool = str(parsed[0].get("tool_name") or "").strip().lower() if parsed else ""
        if ctx.is_node and (not parsed or first_tool in PLACEHOLDER_TOOLS):
            ctx.consecutive_failures += 1
            ctx.step_records.append({
                "step": ctx.step_idx,
                "thought": (ctx.llm_output or "")[:300],
                "tool": "(sin acción válida)",
                "arguments": {},
                "verdict": "REPLAN",
                "reason_code": "INVALID_ACTION_FORMAT",
                "observation": NODE_FORMAT_FEEDBACK,
                "success": False,
            })
            return Outcome.END
        if parsed:
            self._from_parsed_step(ctx, parsed[0])
        else:
            self._from_free_text(ctx)
        return Outcome.CONTINUE

    @staticmethod
    def _from_parsed_step(ctx: LoopContext, step: dict[str, Any]) -> None:
        raw_tool = (step.get("tool_name") or "").strip().lower()
        if raw_tool in PLACEHOLDER_TOOLS:
            # Degradación sintáctica del modelo
            ctx.tool = "run_command"
            ctx.args = {"command": SAFE_INSPECTION_COMMAND}
            ctx.thought = "El modelo emitió una herramienta no especificada ('undefined'). El supervisor redirige a inspección segura."
        else:
            ctx.tool = raw_tool
            ctx.args = step.get("tool_args") or {}
            ctx.thought = step.get("thought_rationale") or f"Paso {ctx.step_idx} generado por {ctx.agent_llm.model_name}."

    @staticmethod
    def _from_free_text(ctx: LoopContext) -> None:
        output = ctx.llm_output
        finish_match = _FINISH_CALL.search(output)
        if finish_match:
            raw_summary = next((g for g in finish_match.groups() if g), "")
            ctx.tool, ctx.args, ctx.thought = "finish", {"summary": raw_summary.strip()}, "Conclusión directa emitida por el agente."
            return
        if any(w in output.lower() for w in _CONCLUSION_HINTS):
            ctx.tool = "finish"
            ctx.args = {"summary": _answer_after_last_thought(output).strip()}
            ctx.thought = "Conclusión directa emitida por el agente."
            return
        is_technical_goal = any(k in ctx.goal.lower() for k in _TECHNICAL_GOAL_HINTS)
        if not is_technical_goal or ctx.step_idx > 1:
            ctx.tool = "finish"
            ctx.args = {"summary": _answer_after_last_thought(output).strip()}
            ctx.thought = "Respuesta directa emitida por el agente."
            return
        # En el paso inicial de una tarea técnica o de análisis de código, forzar inspección empírica
        ctx.tool = "run_command"
        ctx.args = {"command": "python -c \"import os; print('Archivos del proyecto:', [f for f in os.listdir('.') if not f.startswith('.')][:12])\""}
        ctx.thought = output[:250].strip() or "Paso inicial de inspección de archivos."


class SemanticFixationStep(Step[LoopContext]):
    """Cortacircuitos semántico: la misma acción con los mismos argumentos tres veces seguidas poda la rama (REPLAN)."""

    name = "semantic_fixation"

    def run(self, ctx: LoopContext) -> Outcome:
        signature = f"{ctx.tool}:{json.dumps(ctx.args, sort_keys=True)}"
        recent = ctx.recent_action_signatures
        ctx.semantic_fixations = ctx.semantic_fixations + 1 if recent and recent[-1] == signature else 0
        recent.append(signature)
        if len(recent) > 10:
            recent.pop(0)
        if ctx.semantic_fixations < 2:
            return Outcome.CONTINUE

        ctx.emit(
            f"{ctx.id_prefix}act_{ctx.step_idx}",
            {
                "intervention": "SEMANTIC_CIRCUIT_BREAKER_LOOP_PRUNED",
                "message": (
                    f"⚡ PRAXEON SEMANTIC CIRCUIT BREAKER: Fijación semántica / bucle del agente detectado en '{ctx.tool}'. "
                    "El supervisor poda la rama y fuerza REPLAN."
                ),
                "action_signature": signature,
                "backtrack_to": ctx.active_parent_id,
            },
            parent_id=ctx.active_parent_id,
        )
        ctx.record("REPLAN", "SEMANTIC_CIRCUIT_BREAKER_LOOP", (
            "🚨 [SUPERVISOR PRAXEON - SEMANTIC CIRCUIT BREAKER]: Bucle de fijación semántica detectado.\n"
            f"Has propuesto '{ctx.tool}' con argumentos idénticos repetidamente sin avance comprobable. Esta rama queda PODADA.\n"
            "DEBES formular una alternativa de razonamiento diferente, inspeccionar otros archivos o cambiar tu estrategia."
        ))
        ctx.semantic_fixations = 0
        return Outcome.END


def _finish_summary(args: dict[str, Any]) -> str:
    return str(args.get("summary") or args.get("final_answer") or "")


class PrematureNodeFinishStep(Step[LoopContext]):
    """Un nodo de workflow que «termina» anunciando lo que va a hacer, en vez de entregarlo, debe seguir (REPLAN)."""

    name = "premature_node_finish"

    def run(self, ctx: LoopContext) -> Outcome:
        if ctx.is_node and ctx.is_finish and looks_like_intention(_finish_summary(ctx.args)):
            ctx.record("REPLAN", "PREMATURE_NODE_FINISH", NODE_INTENTION_FEEDBACK)
            return Outcome.END
        return Outcome.CONTINUE


class FinishFieldsStep(Step[LoopContext]):
    """El finish de un nodo debe traer los campos que el grafo necesita; tras MAX_FINISH_REJECTIONS se acepta igual."""

    name = "finish_fields"

    def run(self, ctx: LoopContext) -> Outcome:
        if ctx.finish_check is None or not ctx.is_finish:
            return Outcome.CONTINUE
        rejection = ctx.finish_check(_finish_summary(ctx.args))
        if rejection and ctx.finish_rejections >= MAX_FINISH_REJECTIONS:
            rejection = None    # el agente no consigue dar el formato: se acepta lo entregado y el flujo sigue (sin el campo, la condición no se cumple)
        if not rejection:
            return Outcome.CONTINUE
        ctx.finish_rejections += 1
        ctx.record("REPLAN", "FINISH_MISSING_REQUIRED_FIELDS", rejection)
        return Outcome.END


class ToolPermissionStep(Step[LoopContext]):
    """El agente del nodo solo puede usar sus herramientas permitidas (BLOCK, cuenta como fallo)."""

    name = "tool_permission"

    def run(self, ctx: LoopContext) -> Outcome:
        if ctx.tool_filter is None or ctx.is_finish:
            return Outcome.CONTINUE
        denied = ctx.tool_filter(ctx.tool)
        if not denied:
            return Outcome.CONTINUE
        ctx.consecutive_failures += 1
        ctx.record("BLOCK", "AGENT_TOOL_NOT_ALLOWED", denied)
        return Outcome.END


class SuperviseAndExecuteStep(Step[LoopContext]):
    """Propone la acción a la política y, según el veredicto, la ejecuta, espera a una persona o explica el veto."""

    name = "supervise_and_execute"

    def run(self, ctx: LoopContext) -> Outcome:
        ctx.action_node_id = f"{ctx.id_prefix}act_{ctx.step_idx}"
        req = ProposeActionRequest(
            action_id=ctx.action_node_id,
            parent_id=ctx.active_parent_id,
            tool=ctx.tool,
            operation=f"{ctx.step_idx}. {ctx.tool}",
            arguments=ctx.args,
            thought_rationale=ctx.thought,
            provenance={
                "source": f"LLM ({ctx.agent_llm.provider_name.upper()} - {ctx.agent_llm.model_name})",
                "step": ctx.step_idx,
                "llm_ms": round(ctx.llm_ms),
                **({"workflow": ctx.trace} if ctx.trace else {}),
            },
            context={"goal": ctx.goal},
        )
        try:
            ctx.resp = resp = ctx.runtime.propose_action(session_id=ctx.sid, proposal=req)
            if resp.status == "BLOCK":
                ctx.obs_output = explain_veto("BLOCK", resp.policy.reason_codes, ctx.tool, ctx.args)
            elif resp.status == "REPLAN":
                missing = _missing_path_from_reasons(resp.policy.reason_codes)
                ctx.obs_output = (
                    _missing_path_feedback(missing, ctx.workspace_root)
                    if missing
                    else explain_veto("REPLAN", resp.policy.reason_codes, ctx.tool, ctx.args)
                )
            elif resp.status == "REVIEW" or resp.policy.requires_confirmation:
                self._review(ctx, resp)
            elif resp.status == "ALLOW":
                self._execute(ctx, resp.decision_id)
        except Exception as err:  # noqa: BLE001 - frontera del paso: el fallo se registra y el paso queda como no ejecutado
            logger.error(f"Error proponiendo paso {ctx.step_idx} en sesión {ctx.sid}: {err}")
            ctx.obs_output = str(err)
            ctx.executed_successfully = False
        return Outcome.CONTINUE

    @staticmethod
    def _execute(ctx: LoopContext, decision_id: str) -> None:
        try:
            result = ctx.runtime.execute_decision(decision_id=decision_id)
            ctx.obs_output = result.output or ""
            ctx.executed_successfully = result.success and not result.is_error
        except Exception as ex:  # noqa: BLE001 - frontera de ejecución: el fallo se registra y queda como observación del paso
            logger.debug("Execution note: %s", ex)
            ctx.obs_output = str(ex)

    def _review(self, ctx: LoopContext, resp: Any) -> None:
        """Acción que requiere confirmación humana (p. ej. REVIEW por git push)."""
        mission, runtime = ctx.mission, ctx.runtime
        full_access_authorized = bool(mission.get("full_access_authorized_by_operator") or mission.get("operator_authorized"))
        if (
            ctx.session_mode == "full_access"
            and full_access_authorized
            and not mission.get("require_step_confirmation")
            and runtime._may_auto_confirm(ctx.tool)
        ):
            # Auto-confirmación sólo con consentimiento explícito de la sesión en modo Full Access
            logger.warning("[SECURITY AUDIT] Auto-confirmando decisión %s en modo FULL_ACCESS_AUTONOMOUS...", resp.decision_id)
            try:
                confirmation = runtime.confirm_decision(
                    decision_id=resp.decision_id,
                    approved=True,
                    reason="Auto-autorizado por consentimiento explícito de sesión en modo Full Access Autónomo",
                    operator_id="operator_full_access_auto",
                    role="operator",
                )
                if confirmation.status == "ALLOW":
                    result = runtime.execute_decision(decision_id=resp.decision_id)
                    ctx.obs_output = result.output or ""
                    ctx.executed_successfully = result.success and not result.is_error
            except Exception as ex:  # noqa: BLE001 - frontera de ejecución en Full Access: el fallo queda como observación del paso
                ctx.obs_output = f"Error en ejecución Full Access: {ex}"
                ctx.executed_successfully = False
            return

        decision = runtime._await_human_decision(resp.decision_id, mission)
        if decision == "ALLOW":
            self._execute(ctx, resp.decision_id)
        elif decision != "BLOCKED" and not mission.get("stopped"):
            # Sigue esperando al operador: no ha sido rechazada ni ejecutada (no es un veto)
            ctx.pending_approval = True
            ctx.obs_output = (
                "⏳ Acción pendiente de aprobación humana: NO ha sido rechazada ni ejecutada. "
                "No la repitas; continúa con otra acción de lectura o espera la decisión del operador."
            )


class MissingPathFeedbackStep(Step[LoopContext]):
    """Si la acción falló sobre una ruta que no existe, el agente ve lo que sí hay en el ancestro más cercano.

    La primera petición de una ruta inexistente se ejecuta (no se veta): así rectifica en lugar de insistir.
    """

    name = "missing_path_feedback"

    def run(self, ctx: LoopContext) -> Outcome:
        if not ctx.executed_successfully and _targets_missing_path(ctx.tool, ctx.args, ctx.workspace_root):
            missing_arg = str(ctx.args.get("path") or ctx.args.get("file") or "").strip()
            if missing_arg:
                ctx.obs_output = _missing_path_feedback(missing_arg, ctx.workspace_root)
        return Outcome.CONTINUE


class PendingApprovalStep(Step[LoopContext]):
    """Aprobación humana en curso: sin poda, sin retroceso y sin contar como fallo consecutivo."""

    name = "pending_approval"

    def run(self, ctx: LoopContext) -> Outcome:
        if not ctx.pending_approval:
            return Outcome.CONTINUE
        ctx.record("REVIEW", ctx.step_reasons or "APPROVAL_PENDING", ctx.obs_output, pending_approval=True)
        return Outcome.END


class RecordResultStep(Step[LoopContext]):
    """Registra el resultado. Un éxito avanza el cursor del árbol; un fallo retrocede y, si se repite, activa los
    cortacircuitos: a los 2 fallos seguidos poda la rama y orienta al agente (END), a MAX_CONSECUTIVE_FAILURES
    termina la misión (STOP).
    """

    name = "record_result"

    def run(self, ctx: LoopContext) -> Outcome:
        if ctx.executed_successfully:
            ctx.consecutive_failures = 0
            ctx.active_parent_id = ctx.action_node_id
            ctx.record(
                ctx.step_verdict, ctx.step_reasons, _truncate_for_llm(ctx.obs_output or "Acción ejecutada correctamente."),
                success=True,
            )
            return Outcome.CONTINUE

        ctx.consecutive_failures += 1
        self._emit_backtrack(ctx)
        if ctx.consecutive_failures >= 2 and not ctx.circuit_breaker_triggered:
            return self._circuit_breaker(ctx)
        if ctx.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            return self._forced_termination(ctx)
        ctx.record(
            self._failure_verdict(ctx), ctx.step_reasons or "EXECUTION_FAILURE",
            ctx.obs_output or "Acción no autorizada o fallida.",
        )
        return Outcome.CONTINUE

    @staticmethod
    def _failure_verdict(ctx: LoopContext) -> str:
        return ctx.step_verdict if ctx.step_verdict != "ALLOW" else "BLOCK"

    @staticmethod
    def _missing_paths(ctx: LoopContext, observation: str) -> bool:
        """Los fallos se deben a rutas inexistentes (solo entonces se habla de archivos inexistentes)."""
        return (
            "UNGROUNDED_FILE_NOT_FOUND" in (ctx.step_reasons or "")
            or reports_missing_path(observation)      # no basta con que el CONTENIDO leído diga «no existe»/«no existen»
            or _targets_missing_path(ctx.tool, ctx.args, ctx.workspace_root)
        )

    @staticmethod
    def _emit_backtrack(ctx: LoopContext) -> None:
        """Retroceso y bifurcación contextual tras un fallo o veto."""
        reasons, node = ctx.step_reasons, ctx.action_node_id
        if ctx.active_parent_id == ctx.anchor_id or ctx.step_idx == 1:
            name = "INITIAL_HYPOTHESIS_VETOED"
            message = (
                f"Acción inicial '{node}' vetada por el supervisor ({reasons or 'falta de evidencias empíricas'}). "
                + (
                    "El agente debe corregir la acción y volver a intentarlo."
                    if ctx.mission.get("workflow_id")
                    else "Se exige recopilar evidencias inspeccionando archivos antes de emitir conclusiones."
                )
            )
        else:
            name = "BACKTRACK_AND_BRANCH"
            message = (
                f"Fallo o veto en '{node}'. El supervisor realiza un retroceso a '{ctx.active_parent_id}' "
                f"para bifurcar una hipótesis alternativa."
            )
        ctx.emit(
            node,
            {"intervention": name, "message": message, "backtrack_to": ctx.active_parent_id, "failed_node": node},
            parent_id=ctx.active_parent_id,
        )

    def _circuit_breaker(self, ctx: LoopContext) -> Outcome:
        """Segundo fallo seguido: poda la rama, vuelve al ancla y orienta al agente (una vez por bucle)."""
        ctx.circuit_breaker_triggered = True
        observation = ctx.obs_output or "Acción no autorizada o fallida."
        missing_paths = self._missing_paths(ctx, observation)
        real_files: list[str] = []
        if missing_paths:
            ws_dir = ctx.workspace_root or get_default_workspace_root()
            try:
                real_files = [f for f in sorted(os.listdir(ws_dir)) if not f.startswith(".")][:12]
            except OSError:  # workspace no listable: se omite la lista de ficheros
                real_files = []
        real_files_str = ", ".join(real_files) if real_files else "(carpeta vacía)"
        cause = (ctx.step_reasons or "").split(",")[0].strip() or "acción fallida o vetada"
        failures = ctx.consecutive_failures
        if missing_paths:
            message = (
                f"⚡ JEV CIRCUIT BREAKER: {failures} fallos consecutivos intentando acceder a archivos o rutas "
                "que no existen. El supervisor poda la rama, retrocede e inyecta la estructura real del workspace."
            )
            observation += (
                f"\n🚨 [CIRCUIT BREAKER GROUNDING]: Archivos reales del workspace: [{real_files_str}]. "
                "Trabaja exclusivamente sobre rutas reales o usa 'finish'."
            )
        else:
            message = (
                f"⚡ JEV CIRCUIT BREAKER: {failures} fallos consecutivos (último motivo: {cause}). "
                "El supervisor poda la rama y pide cambiar de enfoque."
            )
            observation += (
                f"\n🚨 [CIRCUIT BREAKER]: {failures} fallos seguidos (motivo: {cause}). "
                "Lee el motivo, cambia de enfoque y no repitas la misma acción."
            )
        ctx.emit(
            ctx.action_node_id,
            {
                "intervention": "CIRCUIT_BREAKER_GROUNDING_INJECTION",
                "message": message,
                "real_files": real_files,
                "backtrack_to": ctx.root_id,
            },
            parent_id=ctx.root_id,
        )
        ctx.record(self._failure_verdict(ctx), ctx.step_reasons or "CIRCUIT_BREAKER_GROUNDING", observation)
        ctx.active_parent_id = ctx.anchor_id
        return Outcome.END

    def _forced_termination(self, ctx: LoopContext) -> Outcome:
        """Demasiados fallos seguidos sin avance: el supervisor termina la misión."""
        logger.warning(
            "Terminación preventiva por supervisor JEV en sesión %s tras %d fallos continuos.", ctx.sid, ctx.consecutive_failures
        )
        cause = (ctx.step_reasons or "").split(",")[0].strip() or "acciones fallidas o vetadas"
        summary = (
            f"Misión concluida preventivamente por el supervisor JEV tras {ctx.step_idx} turnos: el agente insistió en rutas o archivos que no existen."
            if self._missing_paths(ctx, ctx.obs_output or "")
            else f"Misión concluida preventivamente por el supervisor JEV tras {ctx.step_idx} turnos: {ctx.consecutive_failures} fallos consecutivos sin avance (último motivo: {cause})."
        )
        ctx.emit(
            ctx.action_node_id,
            {
                "intervention": "SUPERVISOR_FORCED_TERMINATION",
                "message": "Supervisión JEV: se cortó la ejecución para evitar un bucle de fallos sin avance. " + summary,
            },
            parent_id=ctx.root_id,
        )
        ctx.final_answer = summary
        ctx.forced_termination = True
        if ctx.owns_session:
            ctx.emit(ctx.root_id, {"status": "completed", "summary": summary}, event_type=EventType.SESSION_COMPLETED)
            with ctx.runtime._lock:
                if ctx.sid in ctx.runtime._sessions_meta:
                    ctx.runtime._sessions_meta[ctx.sid]["status"] = "Completed"
                    ctx.runtime._sessions_meta[ctx.sid]["final_answer"] = summary
        ctx.record("BLOCK", "SUPERVISOR_FORCED_TERMINATION", summary)
        return Outcome.STOP


class FinishStep(Step[LoopContext]):
    """Un finish ejecutado cierra el bucle con la respuesta final; uno vetado se devuelve al agente para que siga."""

    name = "finish"

    def run(self, ctx: LoopContext) -> Outcome:
        if not ctx.is_finish:
            return Outcome.CONTINUE
        if ctx.executed_successfully:
            answer = ctx.args.get("summary") or ctx.args.get("final_answer") or ctx.obs_output.replace("Tarea concluida: ", "")
            ctx.final_answer = answer
            if ctx.owns_session:
                with ctx.runtime._lock:
                    if ctx.sid in ctx.runtime._sessions_meta:
                        ctx.runtime._sessions_meta[ctx.sid]["final_answer"] = answer
            return Outcome.STOP

        # Veto del supervisor a la conclusión prematura: alimentar al LLM para corregir rumbo
        reason = ctx.step_reasons or "FALTA_DE_EVIDENCIAS_EMPÍRICAS"
        logger.warning(
            "Intento de finalización ('%s') vetado en paso %d (motivo: %s). Se retroalimenta al agente para continuar.",
            ctx.tool, ctx.step_idx, reason,
        )
        if ctx.mission.get("workflow_id"):
            # En un nodo de workflow, finish entrega el resultado del NODO: no se exige leer el proyecto.
            feedback = (
                f"🚨 [SUPERVISOR PRAXEON - {reason}]: tu finish no se ha podido aceptar.\n"
                "Si el motivo es de formato, repítelo con finish(summary=<texto>) o con un objeto JSON válido. "
                "Si tu misión necesita datos del proyecto, usa read_file o run_command; si no los necesita, "
                "entrega directamente el resultado de tu misión."
            )
        else:
            feedback = (
                f"🚨 [VETO DEL SUPERVISOR PRAXEON - {reason}]:\n"
                f"Tu intento de concluir la misión mediante '{ctx.tool}' ha sido RECHAZADO por falta de evidencia empírica fundamentada.\n"
                "NO se permite emitir conclusiones sin antes haber inspeccionado los archivos reales del proyecto.\n"
                "EN EL SIGUIENTE TURNO DEBES ejecutar 'read_file(path)' o 'run_command' para examinar los archivos relevantes."
            )
        if ctx.step_records and ctx.step_records[-1]["step"] == ctx.step_idx:
            last = ctx.step_records[-1]
            last["observation"], last["verdict"], last["reason_code"] = feedback, "REPLAN", reason
        return Outcome.END


# Orden explícito de los pasos de un turno
TURN_STEPS: tuple[Step[LoopContext], ...] = (
    PersistMissionStep(),
    NodeBudgetWarningStep(),
    GenerateStep(),
    ParseActionStep(),
    SemanticFixationStep(),
    PrematureNodeFinishStep(),
    FinishFieldsStep(),
    ToolPermissionStep(),
    SuperviseAndExecuteStep(),
    MissingPathFeedbackStep(),
    PendingApprovalStep(),
    RecordResultStep(),
    FinishStep(),
)


# ------------------------------------------------------------------------------------------------ el bucle


class AgentLoop:
    """Ejecuta turnos hasta un finish, una parada o el límite de pasos (``max_steps <= 0``: sin límite)."""

    def __init__(self, ctx: LoopContext, steps: Iterable[Step[LoopContext]] = TURN_STEPS):
        self.ctx = ctx
        self.steps = tuple(steps)

    def run(self) -> dict[str, Any]:
        ctx, mission = self.ctx, self.ctx.mission
        # max_steps <= 0 significa sin límite: lo frenan el finish, los cortacircuitos del supervisor o el operador
        while ctx.max_steps <= 0 or ctx.step_idx < ctx.max_steps:
            if mission.get("stopped"):
                break
            while mission.get("paused") and not mission.get("stopped"):
                time.sleep(0.2)
            ctx.begin_turn()
            if run_turn(self.steps, ctx) is Outcome.STOP:
                break
            time.sleep(ctx.delay_sec)

        if ctx.final_answer is None and self._node_ran_out_of_steps():
            ctx.final_answer = self._interrupted_node_summary()
        return {
            "step_records": ctx.step_records,
            "final_answer": ctx.final_answer,
            "steps_used": ctx.step_idx,
            "last_action_id": ctx.active_parent_id if ctx.active_parent_id != ctx.anchor_id else None,
            "forced_termination": ctx.forced_termination,
        }

    def _node_ran_out_of_steps(self) -> bool:
        ctx = self.ctx
        return (
            ctx.is_node and ctx.max_steps > 0 and ctx.step_idx >= ctx.max_steps
            and not ctx.mission.get("stopped") and not ctx.forced_termination
        )

    def _interrupted_node_summary(self) -> str | None:
        """Presupuesto agotado trabajando (no atascado): el nodo entrega lo hecho y el flujo decide, en vez de fallar y
        repetirlo todo. Sin trabajo hecho, no hay resumen."""
        done = [r for r in self.ctx.step_records if _made_progress(r)]
        if not done:
            return None
        touched: list[str] = []
        for r in done:
            target = (r.get("arguments") or {}).get("path") or (r.get("arguments") or {}).get("command")
            if target and str(target)[:120] not in touched:
                touched.append(str(target)[:120])
        return (
            f"[Nodo interrumpido por límite de {self.ctx.max_steps} pasos, con trabajo hecho: {len(done)} acciones de escritura.]\n"
            "Acciones realizadas: " + "; ".join(touched[:25]) + ("; …" if len(touched) > 25 else "") + ".\n"
            "El trabajo NO está terminado: continúa desde lo ya hecho (usa list_dir/read_file para verlo) sin repetir lo existente."
        )
