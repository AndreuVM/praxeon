"""Ciclo de vida de misiones autónomas, checkpoints, rollback y worker de misión."""

from __future__ import annotations

from datetime import datetime, timezone


import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import uuid

logger = logging.getLogger("praxeon.server.dependencies")

from praxeon.domain.events import EventType
from praxeon.runtime.sandbox import (
    get_default_workspace_root,
)
from praxeon.domain.decision_provider import DecisionModelConfig
from praxeon.providers.registry import default_registry
from praxeon.runtime.decision_runtime import DecisionRuntime
from praxeon.runtime.session_runtime import SessionRuntime
from praxeon.server.schemas.action import ProposeActionRequest
from praxeon.server.services import (
    generate_goal_tailored_steps,
)



_MISSING_PREFIX = "UNGROUNDED_FILE_NOT_FOUND ("

# El resumen de pasos antiguos solo se rehace cada tantos pasos: entre medias la conversación solo crece por el final
COMPRESS_BATCH = 6

# Fallos seguidos (vetos o errores) tras los que el supervisor corta la misión. Con 4 cortaba a modelos pequeños que aún
# podían rectificar (en el banco, un tercio de las misiones fallidas); el aviso de «cambia de enfoque» sale a los 2.
MAX_CONSECUTIVE_FAILURES = max(3, int(os.getenv("PRAXEON_MAX_CONSECUTIVE_FAILURES", "6") or 6))


def _llm_observation_limit() -> int:
    """Caracteres de una observación que se entregan íntegros al LLM (`PRAXEON_LLM_OBSERVATION_CHARS`, 150.000 por defecto)."""
    try:
        return max(5_000, int(os.getenv("PRAXEON_LLM_OBSERVATION_CHARS", "") or 150_000))
    except ValueError:
        return 150_000


def _missing_path_from_reasons(reason_codes: Optional[List[str]]) -> Optional[str]:
    for code in reason_codes or []:
        if str(code).startswith(_MISSING_PREFIX) and str(code).endswith(")"):
            return str(code)[len(_MISSING_PREFIX):-1]
    return None


from praxeon.server.runtime_service.mission_prompt import build_mission_system_prompt  # noqa: E402
from praxeon.server.runtime_service.veto_feedback import explain_veto  # noqa: E402
from praxeon.server.runtime_service.node_prompt import (  # noqa: E402
    NODE_FORMAT_FEEDBACK,
    NODE_INTENTION_FEEDBACK,
    build_node_system_prompt,
    looks_like_intention,
)

def _targets_missing_path(tool: str, args: Dict[str, Any], workspace_root: Optional[str]) -> bool:
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


def _missing_path_feedback(path: str, workspace_root: Optional[str]) -> str:
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


class MissionRuntimeMixin:
    """Ciclo de vida de misiones autónomas, checkpoints, rollback y worker de misión."""

    # =========================================================================
    # EJECUCIÓN INTERACTIVA EN TIEMPO REAL (Live Mission Runner)
    # =========================================================================

    def _await_human_decision(self, decision_id: str, mission: Dict[str, Any]) -> Optional[str]:
        """Espera la resolución humana de una decisión en REVIEW (hasta `approval_timeout_seconds`, 10 min por defecto).

        Devuelve "ALLOW", "BLOCKED" o None si sigue pendiente (tiempo agotado o misión detenida).
        """
        timeout = float(mission.get("approval_timeout_seconds") or 600.0)
        deadline = time.time() + timeout
        status: Optional[str] = None
        while time.time() < deadline and not mission.get("stopped"):
            with self._lock:
                record = self._decisions.get(decision_id)
                status = record.get("status") if record else None
            if status in ("ALLOW", "BLOCKED"):
                return status
            time.sleep(0.25)
        with self._lock:
            record = self._decisions.get(decision_id)
            status = record.get("status") if record else None
        return status if status in ("ALLOW", "BLOCKED") else None

    def start_mission(
        self,
        goal: str,
        session_id: Optional[str] = None,
        agent_name: str = "CodingAgent",
        execution_mode: str = "local_restricted",
        workspace_root: Optional[str] = None,
        llm_provider: str = "simulator",
        llm_model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        supervisor: str = "laya",
        decision_model: Optional[Any] = None,
        max_steps: int = 25,
        step_delay_ms: int = 900,
        autonomous: bool = False,
        allow_unattended_execution: bool = False,
        llm_failure_policy: str = "synthetic_fallback",
        chat_history: Optional[List[Dict[str, str]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inicia una misión interactiva supervisada en tiempo real.

        Con `workflow_id` (un workflow activo) el grafo gobierna la tarea: ver `WorkflowMissionDriver`.
        """
        sid = session_id or f"s-{uuid.uuid4().hex[:8]}"
        if workflow_id:
            wf = self.workflow_editor_service.get_workflow(workflow_id)
            if wf is None:
                raise ValueError(f"Workflow '{workflow_id}' no encontrado.")
            if not wf.metadata.get("active", False):
                raise ValueError(f"El workflow '{workflow_id}' no está activo. Actívalo antes de usarlo en una tarea.")

        # Validación anti-SSRF estricta para endpoint remoto o base_url (SEC-01)
        if base_url:
            from praxeon.policy.egress import validate_provider_endpoint
            prof = (os.environ.get("PRAXEON_PROFILE") or os.environ.get("PRAXEON_ENV") or "dev").lower().strip()
            allow_custom = getattr(self.config.security, "allow_custom_endpoints", False)
            allowed_hosts = getattr(self.config.security, "allowed_custom_hosts", set())
            base_url = validate_provider_endpoint(
                url=base_url,
                allow_custom=allow_custom,
                profile=prof,
                allowed_hosts=allowed_hosts,
            )

        # Resolver DecisionModelConfig y DecisionRuntime aislado para esta misión
        cfg: Optional[DecisionModelConfig] = None
        if isinstance(decision_model, DecisionModelConfig):
            cfg = decision_model
        elif isinstance(decision_model, dict):
            try:
                cfg = DecisionModelConfig(**decision_model)
            except Exception as e:
                logger.warning(f"Error parseando decision_model dict en start_mission: {e}")
        if not cfg:
            cfg = DecisionModelConfig(provider=supervisor, model_id=supervisor)

        dec_runtime: Optional[DecisionRuntime] = None
        try:
            dec_runtime = DecisionRuntime.from_config(cfg, registry=default_registry)
            sess_provider = dec_runtime.provider
        except Exception as e:
            logger.warning(f"Error instanciando DecisionRuntime para misión: {e}. Usando fallback.")
            sess_provider = self._default_provider

        with self._lock:
            self._session_runtimes[sid] = SessionRuntime(
                session_id=sid,
                decision_provider=sess_provider,
                decision_runtime=dec_runtime,
                policy_profile="default",
                execution_profile=execution_mode,
                metadata={"supervisor": supervisor, "decision_model": cfg.model_dump() if cfg else None},
            )

        effective_ws = workspace_root or get_default_workspace_root()
        unattended = bool(autonomous or allow_unattended_execution)

        sess_metadata = {
            "llm_provider": llm_provider,
            "llm_model": llm_model or "default",
            "supervisor": supervisor,
            "max_steps": max_steps,
            "execution_mode": execution_mode,
            "workspace_root": effective_ws,
            "working_directory": effective_ws,
            "autonomous": unattended,
            "allow_unattended_execution": unattended,
            "llm_failure_policy": llm_failure_policy,
        }
        if workflow_id:
            sess_metadata["workflow_id"] = workflow_id
            sess_metadata["workflow_execution_id"] = f"wfx-{sid}"
        if metadata:
            sess_metadata.update(metadata)

        meta = self.create_session(
            goal=goal,
            session_id=sid,
            agent_name=agent_name,
            execution_mode=execution_mode,
            workspace_root=effective_ws,
            metadata=sess_metadata,
        )

        full_access_auth = bool(
            (meta.get("metadata") or {}).get("full_access_authorized_by_operator", False)
        )

        mission_state = {
            "session_id": sid,
            "goal": goal,
            "agent_name": agent_name,
            "execution_mode": execution_mode,
            "workspace_root": effective_ws,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "api_key": api_key,
            "base_url": base_url,
            "supervisor": supervisor,
            "max_steps": max_steps,
            "step_delay_ms": step_delay_ms,
            "paused": False,
            "stopped": False,
            "current_step": 0,
            "autonomous": unattended,
            "allow_unattended_execution": unattended,
            "full_access_authorized_by_operator": full_access_auth,
            "llm_failure_policy": llm_failure_policy,
            "chat_history": chat_history,
            "workflow_id": workflow_id,
            # Tiempo máximo (s) que la misión espera una aprobación humana antes de seguir sin vetar la acción
            "approval_timeout_seconds": (metadata or {}).get("approval_timeout_seconds"),
            "require_step_confirmation": bool((metadata or {}).get("require_step_confirmation", False)),
        }

        with self._lock:
            self._running_missions[sid] = mission_state
        if hasattr(self.state_store, "save_mission"):
            try:
                self.state_store.save_mission(mission_state)
            except Exception as ex:
                logger.warning("Fallo al persistir misión '%s' en SQLite: %s", sid, ex)

        worker_thread = threading.Thread(
            target=self._run_mission_worker,
            args=(mission_state,),
            daemon=True,
            name=f"mission-worker-{sid}",
        )
        worker_thread.start()

        return meta

    def pause_mission(self, session_id: str) -> bool:
        """Pausa temporalmente la ejecución interactiva de una misión."""
        with self._lock:
            if session_id in self._running_missions:
                self._running_missions[session_id]["paused"] = True
                if hasattr(self.state_store, "save_mission"):
                    self.state_store.save_mission(self._running_missions[session_id])
                return True
        return False

    def resume_mission(self, session_id: str) -> bool:
        """Reanuda la ejecución interactiva de una misión en pausa."""
        with self._lock:
            if session_id in self._running_missions:
                self._running_missions[session_id]["paused"] = False
                if hasattr(self.state_store, "save_mission"):
                    self.state_store.save_mission(self._running_missions[session_id])
                return True
        return False

    def stop_mission(self, session_id: str) -> bool:
        """Detiene de forma definitiva la ejecución interactiva de una misión."""
        with self._lock:
            if session_id in self._running_missions:
                self._running_missions[session_id]["stopped"] = True
                if hasattr(self.state_store, "save_mission"):
                    self.state_store.save_mission(self._running_missions[session_id])
                return True
        return False

    def create_checkpoint(self, session_id: str, label: Optional[str] = None) -> Dict[str, Any]:
        """Crea un checkpoint atómico persistente del estado actual de la sesión (delegado en CheckpointCoordinator)."""
        return self.checkpoint_coordinator.create_checkpoint(session_id=session_id, label=label)

    def rollback_session(
        self,
        session_id: str,
        checkpoint_id: Optional[str] = None,
        culprit_tool: Optional[str] = None,
        reason: str = "Rollback formal por degradación de trayectoria",
    ) -> Dict[str, Any]:
        """Revierte físicamente el estado de una sesión al checkpoint especificado o al más reciente (delegado en CheckpointCoordinator)."""
        return self.checkpoint_coordinator.rollback_session(
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            culprit_tool=culprit_tool,
            reason=reason,
        )

    def _rollback_session_impl(
        self,
        session_id: str,
        checkpoint_id: Optional[str] = None,
        culprit_tool: Optional[str] = None,
        reason: str = "Rollback formal por degradación de trayectoria",
    ) -> Dict[str, Any]:
        """Implementación interna de reversión de sesión."""
        state = self.state_store.load_state(session_id)
        if not state:
            raise KeyError(f"Sesión '{session_id}' no encontrada para reversión.")

        if checkpoint_id:
            target_chk_id = checkpoint_id
        else:
            latest_chk = self.state_store.get_latest_checkpoint(session_id)
            if not latest_chk:
                latest_chk = self.state_store.create_checkpoint(
                    session_id=session_id, label="Genesis checkpoint", state=state
                )
            target_chk_id = latest_chk.id

        chk = self.state_store.get_checkpoint(target_chk_id)
        if not chk:
            raise KeyError(f"Checkpoint '{target_chk_id}' no encontrado en el almacén de checkpoints.")

        # Restauración física real del estado
        restored_state = self.state_store.restore_checkpoint(
            session_id=session_id,
            checkpoint_id=target_chk_id,
            culprit_tool=culprit_tool,
            reason=reason,
        )
        if not restored_state:
            raise RuntimeError(f"Fallo al restaurar el estado desde el checkpoint '{target_chk_id}'.")

        # Invalidar decisiones posteriores en memoria
        target_step_idx = chk.step_index
        with self._lock:
            discarded_action_ids = {
                st.id for st in state.steps[target_step_idx:]
            }
            to_remove = [
                d_id for d_id, d_rec in self._decisions.items()
                if d_rec.get("session_id") == session_id
                and d_rec.get("action_id") in discarded_action_ids
            ]
            for d_id in to_remove:
                self._decisions.pop(d_id, None)

        now_iso = datetime.now(timezone.utc).isoformat()
        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.INTERVENTION_APPLIED,
            node_id=f"rollback_{session_id}_{target_chk_id}",
            payload={
                "action": "rollback",
                "checkpoint_id": target_chk_id,
                "step_index": target_step_idx,
                "culprit_tool": culprit_tool,
                "reason": reason,
                "restored_at": now_iso,
            },
        )
        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.SESSION_ROLLBACK,
            node_id=f"rollback_{target_chk_id}",
            payload={
                "checkpoint_id": target_chk_id,
                "step_index": target_step_idx,
                "culprit_tool": culprit_tool,
                "reason": reason,
                "restored_at": now_iso,
            },
        )

        return {
            "session_id": session_id,
            "checkpoint_id": target_chk_id,
            "step_index": target_step_idx,
            "status": "RolledBack",
            "steps_count": len(restored_state.steps),
            "forbidden_tools": list(restored_state.forbidden_tools),
        }


    def _run_mission_worker(self, mission: Dict[str, Any]) -> None:
        """Worker asíncrono delegado en SessionLifecycleCoordinator."""
        return self.session_coordinator.run_mission_worker(mission)

    def _run_llm_loop(
        self,
        mission: Dict[str, Any],
        agent_llm: Any,
        *,
        goal: str,
        max_steps: int,
        delay_sec: float,
        provider_name: str,
        session_mode: str,
        system_prompt: Optional[str] = None,
        id_prefix: str = "",
        root_id: Optional[str] = None,
        owns_session: bool = True,
        role_preamble: str = "",
        tool_filter: Optional[Callable[[str], Optional[str]]] = None,
        trace: Optional[Dict[str, Any]] = None,
        start_parent_id: Optional[str] = None,
        finish_check: Optional[Callable[[str], Optional[str]]] = None,
    ) -> Dict[str, Any]:
        """Bucle ReAct supervisado de un agente (Thought/Action -> política -> ejecución -> observación).

        Lo usa la misión completa (`owns_session=True`, comportamiento histórico) y cada nodo de un workflow
        (`owns_session=False`, con `id_prefix`/`root_id` propios para no colisionar en el árbol de decisiones).
        `role_preamble` antepone la identidad/misión del agente al prompt de sistema y `tool_filter(tool)` devuelve
        un motivo de rechazo si el agente no tiene permitida esa herramienta. `trace` (nodo/agente del workflow)
        viaja en cada acción para que el árbol muestre quién tiene el control, y `start_parent_id` engancha la
        primera acción a la última del nodo anterior (traspaso entre agentes). `finish_check(summary)` devuelve el
        motivo por el que un finish no se acepta (p. ej. falta el campo que el grafo necesita) o None si es válido.
        """
        from praxeon.live_agent import parse_llm_steps

        sid = mission["session_id"]
        root_id = root_id or f"root_{sid}"
        step_idx = 0
        if system_prompt is None:
            ws_root = mission.get("workspace_root") or get_default_workspace_root()
            if trace is not None:
                from praxeon.core.session_context import SessionContextManager
                env_info = SessionContextManager().get_environment_info(root_dir=ws_root)
                system_prompt = role_preamble + build_node_system_prompt(ws_root, env_info, goal)
            else:
                system_prompt = role_preamble + build_mission_system_prompt(ws_root)
            mcp = getattr(self, "mcp", None)
            if mcp is not None:
                try:
                    mcp.ensure_started()
                    mcp_section = mcp.prompt_section(tool_filter)
                except Exception as mcp_err:   # un servidor MCP roto nunca debe impedir la misión
                    logger.warning("No se pudieron preparar las herramientas MCP: %s", mcp_err)
                    mcp_section = ""
                if mcp_section:
                    system_prompt = system_prompt + "\n\n" + mcp_section

        step_records: List[Dict[str, Any]] = []
        forced_termination = False
        anchor_id = start_parent_id or root_id
        active_parent_id = anchor_id
        consecutive_failures = 0
        final_answer: Optional[str] = None
        technical_failures = 0
        semantic_fixations = 0
        recent_action_signatures: List[str] = []
        circuit_breaker_triggered = False
        llm_fail_policy = (mission.get("llm_failure_policy") or "synthetic_fallback").lower().strip()

        # max_steps <= 0 significa sin límite: lo frenan el finish, los cortacircuitos del supervisor o el operador
        while max_steps <= 0 or step_idx < max_steps:
            if mission.get("stopped"):
                break

            while mission.get("paused") and not mission.get("stopped"):
                time.sleep(0.2)

            step_idx += 1
            mission["current_step"] = step_idx

            # Reconciliación / persistencia periódica en SQLite
            if hasattr(self.state_store, "save_mission"):
                try:
                    self.state_store.save_mission(dict(mission))
                except Exception as save_err:
                    logger.debug("Error persistiendo misión en SQLite: %s", save_err)

            # ContextManager como única autoridad de ensamblado de prompt y compresión semántica
            conversation = self.context_manager.assemble_mission_prompt(
                goal=goal,
                system_prompt=system_prompt,
                steps_history=step_records,
                environment_info=None,
                chat_history=None if trace is not None else mission.get("chat_history"),
                node_mode=trace is not None,
                # compresión por lotes: el prefijo se mantiene estable entre turnos (caché de prompt del proveedor); un
                # proveedor con sesión propia (CLI) guarda la conversación entera y no necesita comprimir
                compress_batch=COMPRESS_BATCH,
                compress=not getattr(agent_llm, "stateful_session", False),
            )

            # Invocar LLM real
            llm_output = ""
            llm_ms = 0.0
            try:
                t_llm = time.perf_counter()
                if hasattr(agent_llm, "last_usage"):
                    agent_llm.last_usage = None
                llm_output = agent_llm.generate(conversation, system_prompt=system_prompt)
                llm_ms = (time.perf_counter() - t_llm) * 1000.0
                logger.info(
                    "LLM %s/%s: %.1fs, prompt ~%d caracteres, respuesta %d caracteres (%s paso %d)",
                    provider_name, getattr(agent_llm, "model_name", "?"), llm_ms / 1000.0,
                    len(system_prompt or "") + sum(len(m.get("content", "")) for m in conversation),
                    len(llm_output), (trace or {}).get("node_id", "misión"), step_idx,
                )
                technical_failures = 0  # Éxito técnico: resetear contador técnico
                usage = getattr(agent_llm, "last_usage", None)
                if isinstance(usage, dict):
                    cached = usage.get("cached_tokens", 0)
                    self.context_manager.record_actual_tokens(
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        cached_tokens=cached,
                        cache_hit_status="hit" if cached else "miss",
                    )
                    logger.info(
                        "Tokens reales %s: prompt %d (caché %d), salida %d",
                        provider_name, usage.get("prompt_tokens", 0), cached, usage.get("output_tokens", 0),
                    )
            except Exception as gen_err:
                technical_failures += 1
                logger.warning("Error durante generación con LLM '%s': %s", provider_name, gen_err)

                if technical_failures >= 3:
                    self.event_bus.emit(
                        session_id=sid,
                        event_type=EventType.INTERVENTION_APPLIED,
                        node_id=root_id,
                        payload={
                            "intervention": "TECHNICAL_CIRCUIT_BREAKER",
                            "message": f"⚡ PRAXEON TECHNICAL CIRCUIT BREAKER: Fallos técnicos consecutivos ({technical_failures}) con el proveedor LLM '{provider_name}'. Circuito técnico ABIERTO para prevenir saturación.",
                            "consecutive_technical_failures": technical_failures,
                        },
                    )
                    mission["stopped"] = True
                    break

                if llm_fail_policy == "fail_closed":
                    self.event_bus.emit(
                        session_id=sid,
                        event_type=EventType.INTERVENTION_APPLIED,
                        node_id=root_id,
                        payload={
                            "error": f"Fallo de generación en LLM '{provider_name}' bajo política fail_closed: {gen_err}",
                            "fatal": True,
                            "llm_failure_policy": "fail_closed",
                        },
                    )
                    mission["stopped"] = True
                    break

                self.event_bus.emit(
                    session_id=sid,
                    event_type=EventType.INTERVENTION_APPLIED,
                    node_id=root_id,
                    payload={
                        "warning": f"[DEGRADED_MODE: SYNTHETIC_PLANNER] Fallo temporal de LLM '{provider_name}' ({gen_err}). Activando contingencia contextual.",
                        "degraded_mode": True,
                        "planner": "synthetic",
                    },
                )
                steps_backup = generate_goal_tailored_steps(goal=goal, max_steps=max_steps if max_steps > 0 else max(step_idx, 12))
                backup_idx = min(step_idx - 1, len(steps_backup) - 1)
                b_step = steps_backup[backup_idx] if (steps_backup and backup_idx >= 0) else {
                    "tool_name": "run_command",
                    "tool_args": {"command": "python -c \"print('Paso de inspección segura')\""},
                    "thought_rationale": "Paso de contingencia tras fallo del proveedor LLM.",
                }
                llm_output = f"Thought: [DEGRADED_MODE: SYNTHETIC_PLANNER] [Contingencia por fallo temporal en {provider_name}]: {b_step.get('thought_rationale', '')}\nAction: {b_step.get('tool_name', 'run_command')}({json.dumps(b_step.get('tool_args', {}))})"

            # Parsear Thought + Action
            parsed_steps = parse_llm_steps(llm_output)
            if trace is not None and (
                not parsed_steps
                or str(parsed_steps[0].get("tool_name") or "").strip().lower() in ("action", "undefined", "none", "null", "step", "")
            ):
                consecutive_failures += 1
                step_records.append({
                    "step": step_idx,
                    "thought": (llm_output or "")[:300],
                    "tool": "(sin acción válida)",
                    "arguments": {},
                    "verdict": "REPLAN",
                    "reason_code": "INVALID_ACTION_FORMAT",
                    "observation": NODE_FORMAT_FEEDBACK,
                    "success": False,
                })
                time.sleep(delay_sec)
                continue
            if parsed_steps:
                st = parsed_steps[0]
                raw_tool = (st.get("tool_name") or "").strip().lower()
                if raw_tool in ("action", "undefined", "none", "null", "step", ""):
                    # Degradación sintáctica del modelo
                    tool = "run_command"
                    args = {"command": "python -c \"print('Paso de inspección segura')\""}
                    thought = "El modelo emitió una herramienta no especificada ('undefined'). El supervisor redirige a inspección segura."
                else:
                    tool = raw_tool
                    args = st.get("tool_args") or {}
                    thought = st.get("thought_rationale") or f"Paso {step_idx} generado por {agent_llm.model_name}."
            else:
                finish_match = re.search(
                    r'finish\s*\(\s*(?:summary\s*=\s*)?(?:"""(.*?)"""|\'\'\'(.*?)\'\'\'|"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|(.*?))\s*\)',
                    llm_output,
                    re.DOTALL,
                )
                if finish_match:
                    raw_summary = (
                        finish_match.group(1)
                        or finish_match.group(2)
                        or finish_match.group(3)
                        or finish_match.group(4)
                        or finish_match.group(5)
                        or ""
                    )
                    tool = "finish"
                    args = {"summary": raw_summary.strip()}
                    thought = "Conclusión directa emitida por el agente."
                elif any(w in llm_output.lower() for w in ("finish", "complet", "conclu", "finaliz", "resuelt", "respuesta", "opini")):
                    clean_ans = llm_output
                    if "thought:" in llm_output.lower():
                        parts = re.split(r"(?i)thought\s*:", llm_output)
                        clean_ans = parts[-1].strip()
                    tool = "finish"
                    args = {"summary": clean_ans.strip()}
                    thought = "Conclusión directa emitida por el agente."
                else:
                    is_technical_action = any(
                        k in goal.lower()
                        for k in (
                            "test", "pytest", "bug", "error", "edit", "modific", "implement", "crea",
                            "analiz", "archivo", "archivos", "codigo", "código", "como funciona",
                            "cómo funciona", "audita", "inspeccion", "revisa"
                        )
                    )
                    if not is_technical_action or step_idx > 1:
                        clean_ans = llm_output
                        if "thought:" in llm_output.lower():
                            parts = re.split(r"(?i)thought\s*:", llm_output)
                            clean_ans = parts[-1].strip()
                        tool = "finish"
                        args = {"summary": clean_ans.strip()}
                        thought = "Respuesta directa emitida por el agente."
                    else:
                        # En el paso inicial de una tarea técnica o de análisis de código, forzar inspección empírica
                        tool = "run_command"
                        args = {"command": "python -c \"import os; print('Archivos del proyecto:', [f for f in os.listdir('.') if not f.startswith('.')][:12])\""}
                        thought = llm_output[:250].strip() or "Paso inicial de inspección de archivos."

            # CIRCUITO DE FIJACIÓN SEMÁNTICA (SEMANTIC CIRCUIT BREAKER): Detección de bucles repetitivos de herramientas
            action_sig = f"{tool}:{json.dumps(args, sort_keys=True)}"
            if recent_action_signatures and recent_action_signatures[-1] == action_sig:
                semantic_fixations += 1
            else:
                semantic_fixations = 0
            recent_action_signatures.append(action_sig)
            if len(recent_action_signatures) > 10:
                recent_action_signatures.pop(0)

            if semantic_fixations >= 2:
                self.event_bus.emit(
                    session_id=sid,
                    event_type=EventType.INTERVENTION_APPLIED,
                    node_id=f"{id_prefix}act_{step_idx}",
                    parent_id=active_parent_id,
                    payload={
                        "intervention": "SEMANTIC_CIRCUIT_BREAKER_LOOP_PRUNED",
                        "message": f"⚡ PRAXEON SEMANTIC CIRCUIT BREAKER: Fijación semántica / bucle del agente detectado en '{tool}'. El supervisor poda la rama y fuerza REPLAN.",
                        "action_signature": action_sig,
                        "backtrack_to": active_parent_id,
                    },
                )
                step_records.append({
                    "step": step_idx,
                    "thought": thought,
                    "tool": tool,
                    "arguments": args,
                    "verdict": "REPLAN",
                    "reason_code": "SEMANTIC_CIRCUIT_BREAKER_LOOP",
                    "observation": (
                        f"🚨 [SUPERVISOR PRAXEON - SEMANTIC CIRCUIT BREAKER]: Bucle de fijación semántica detectado.\n"
                        f"Has propuesto '{tool}' con argumentos idénticos repetidamente sin avance comprobable. Esta rama queda PODADA.\n"
                        "DEBES formular una alternativa de razonamiento diferente, inspeccionar otros archivos o cambiar tu estrategia."
                    ),
                    "success": False,
                })
                semantic_fixations = 0
                time.sleep(delay_sec)
                continue

            if (
                trace is not None
                and tool in ("finish", "complete_task", "done", "complete", "task_completed")
                and looks_like_intention(str(args.get("summary") or args.get("final_answer") or ""))
            ):
                step_records.append({
                    "step": step_idx,
                    "thought": thought,
                    "tool": tool,
                    "arguments": args,
                    "verdict": "REPLAN",
                    "reason_code": "PREMATURE_NODE_FINISH",
                    "observation": NODE_INTENTION_FEEDBACK,
                    "success": False,
                })
                time.sleep(delay_sec)
                continue

            if finish_check is not None and tool in ("finish", "complete_task", "done", "complete", "task_completed"):
                rejection = finish_check(str(args.get("summary") or args.get("final_answer") or ""))
                if rejection:
                    step_records.append({
                        "step": step_idx,
                        "thought": thought,
                        "tool": tool,
                        "arguments": args,
                        "verdict": "REPLAN",
                        "reason_code": "FINISH_MISSING_REQUIRED_FIELDS",
                        "observation": rejection,
                        "success": False,
                    })
                    time.sleep(delay_sec)
                    continue

            if tool_filter is not None and tool not in ("finish", "complete_task", "done", "complete", "task_completed"):
                denied = tool_filter(tool)
                if denied:
                    consecutive_failures += 1
                    step_records.append({
                        "step": step_idx,
                        "thought": thought,
                        "tool": tool,
                        "arguments": args,
                        "verdict": "BLOCK",
                        "reason_code": "AGENT_TOOL_NOT_ALLOWED",
                        "observation": denied,
                        "success": False,
                    })
                    time.sleep(delay_sec)
                    continue

            operation = f"{step_idx}. {tool}"
            action_node_id = f"{id_prefix}act_{step_idx}"

            req = ProposeActionRequest(
                action_id=action_node_id,
                parent_id=active_parent_id,
                tool=tool,
                operation=operation,
                arguments=args,
                thought_rationale=thought,
                provenance={
                    "source": f"LLM ({agent_llm.provider_name.upper()} - {agent_llm.model_name})",
                    "step": step_idx,
                    "llm_ms": round(llm_ms),
                    **({"workflow": trace} if trace else {}),
                },
                context={"goal": goal},
            )

            obs_output = ""
            executed_successfully = False
            pending_approval = False
            try:
                resp = self.propose_action(session_id=sid, proposal=req)

                if resp.status == "BLOCK":
                    executed_successfully = False
                    obs_output = explain_veto("BLOCK", resp.policy.reason_codes, tool, args)
                elif resp.status == "REPLAN":
                    executed_successfully = False
                    missing = _missing_path_from_reasons(resp.policy.reason_codes)
                    if missing:
                        obs_output = _missing_path_feedback(missing, mission.get("workspace_root"))
                    else:
                        obs_output = explain_veto("REPLAN", resp.policy.reason_codes, tool, args)
                # Si requiere confirmación humana (ej. REVIEW por git push)
                elif resp.status == "REVIEW" or resp.policy.requires_confirmation:
                    allow_unattended = bool(
                        mission.get("allow_unattended_execution")
                        or mission.get("autonomous")
                        or False
                    )
                    full_access_authorized = bool(
                        mission.get("full_access_authorized_by_operator")
                        or mission.get("operator_authorized")
                    )
                    if session_mode == "full_access" and full_access_authorized and not mission.get("require_step_confirmation") and self._may_auto_confirm(tool):
                        # Auto-confirmación sólo si explícitamente se configuró allow_unattended_execution / autonomous
                        logger.warning(
                            "[SECURITY AUDIT] Auto-confirmando decisión %s en modo FULL_ACCESS_AUTONOMOUS...",
                            resp.decision_id,
                        )
                        try:
                            conf_res = self.confirm_decision(
                                decision_id=resp.decision_id,
                                approved=True,
                                reason="Auto-autorizado por consentimiento explícito de sesión en modo Full Access Autónomo",
                                operator_id="operator_full_access_auto",
                                role="operator",
                            )
                            if conf_res.status == "ALLOW":
                                exec_res = self.execute_decision(decision_id=resp.decision_id)
                                obs_output = exec_res.output or ""
                                executed_successfully = exec_res.success and not exec_res.is_error
                        except Exception as ex:
                            obs_output = f"Error en ejecución Full Access: {ex}"
                            executed_successfully = False
                    else:
                        dec_status = self._await_human_decision(resp.decision_id, mission)
                        if dec_status == "ALLOW":
                            try:
                                exec_res = self.execute_decision(decision_id=resp.decision_id)
                                obs_output = exec_res.output or ""
                                executed_successfully = exec_res.success and not exec_res.is_error
                            except Exception as ex:
                                logger.debug("Execution note: %s", ex)
                                obs_output = str(ex)
                        elif dec_status != "BLOCKED" and not mission.get("stopped"):
                            # Sigue esperando al operador: no ha sido rechazada ni ejecutada (no es un veto)
                            pending_approval = True
                            obs_output = (
                                "⏳ Acción pendiente de aprobación humana: NO ha sido rechazada ni ejecutada. "
                                "No la repitas; continúa con otra acción de lectura o espera la decisión del operador."
                            )
                elif resp.status == "ALLOW":
                    try:
                        exec_res = self.execute_decision(decision_id=resp.decision_id)
                        obs_output = exec_res.output or ""
                        executed_successfully = exec_res.success and not exec_res.is_error
                    except Exception as ex:
                        logger.debug("Execution note: %s", ex)
                        obs_output = str(ex)
            except Exception as err:
                logger.error(f"Error proponiendo paso {step_idx} en sesión {sid}: {err}")
                obs_output = str(err)
                executed_successfully = False

            if not executed_successfully and _targets_missing_path(tool, args, mission.get("workspace_root")):
                # La primera petición de una ruta inexistente se ejecuta (no se veta): el error que recibe el agente incluye
                # lo que sí existe en el ancestro más cercano, para que rectifique en lugar de insistir
                missing_arg = str(args.get("path") or args.get("file") or "").strip()
                if missing_arg:
                    obs_output = _missing_path_feedback(missing_arg, mission.get("workspace_root"))

            step_verdict = getattr(resp, "status", "ERROR") if 'resp' in locals() and resp else "ERROR"
            step_reasons = ", ".join(getattr(getattr(resp, "policy", None), "reason_codes", []) or []) if 'resp' in locals() and resp else ""

            if pending_approval:
                # Aprobación humana en curso: sin poda, sin retroceso y sin contar como fallo consecutivo
                step_records.append({
                    "step": step_idx,
                    "thought": thought,
                    "tool": tool,
                    "arguments": args,
                    "verdict": "REVIEW",
                    "reason_code": step_reasons or "APPROVAL_PENDING",
                    "observation": obs_output,
                    "success": False,
                    "pending_approval": True,
                })
                time.sleep(delay_sec)
                continue

            if executed_successfully:
                # Acción exitosa: el cursor activo del árbol avanza
                consecutive_failures = 0
                active_parent_id = action_node_id
                raw_obs = obs_output or "Acción ejecutada correctamente."
                obs_limit = _llm_observation_limit()
                if len(raw_obs) > obs_limit:
                    keep_h = obs_limit * 7 // 10
                    keep_t = obs_limit - keep_h
                    omitted = len(raw_obs) - obs_limit
                    llm_obs = f"{raw_obs[:keep_h]}\n\n[... Truncado: {omitted} caracteres intermedios omitidos por JEV para optimizar contexto del LLM ...]\n\n{raw_obs[-keep_t:]}"
                else:
                    llm_obs = raw_obs

                step_records.append({
                    "step": step_idx,
                    "thought": thought,
                    "tool": tool,
                    "arguments": args,
                    "verdict": step_verdict,
                    "reason_code": step_reasons,
                    "observation": llm_obs,
                    "success": True,
                })
            else:
                consecutive_failures += 1

                # RETROCESO (BACKTRACK) Y BIFURCACIÓN CONTEXTUAL:
                is_initial_step = active_parent_id == anchor_id or step_idx == 1
                if is_initial_step:
                    interv_name = "INITIAL_HYPOTHESIS_VETOED"
                    interv_msg = (
                        f"Acción inicial '{action_node_id}' vetada por el supervisor ({step_reasons or 'falta de evidencias empíricas'}). "
                        + (
                            "El agente debe corregir la acción y volver a intentarlo."
                            if mission.get("workflow_id")
                            else "Se exige recopilar evidencias inspeccionando archivos antes de emitir conclusiones."
                        )
                    )
                else:
                    interv_name = "BACKTRACK_AND_BRANCH"
                    interv_msg = (
                        f"Fallo o veto en '{action_node_id}'. El supervisor realiza un retroceso a '{active_parent_id}' "
                        f"para bifurcar una hipótesis alternativa."
                    )

                self.event_bus.emit(
                    session_id=sid,
                    event_type=EventType.INTERVENTION_APPLIED,
                    node_id=action_node_id,
                    parent_id=active_parent_id,
                    payload={
                        "intervention": interv_name,
                        "message": interv_msg,
                        "backtrack_to": active_parent_id,
                        "failed_node": action_node_id,
                    },
                )

                obs_fail = obs_output or "Acción no autorizada o fallida."

                # CIRCUITO DE BLOQUEO Y DETECCIÓN DE BUCLE PERSISTENTE (JEV CIRCUIT BREAKER)
                if consecutive_failures >= 2 and not circuit_breaker_triggered:
                    circuit_breaker_triggered = True
                    # El motivo real de los fallos: solo si son rutas inexistentes se habla de archivos inexistentes
                    missing_paths = (
                        "UNGROUNDED_FILE_NOT_FOUND" in (step_reasons or "")
                        or "no existe" in obs_fail.lower()
                        or _targets_missing_path(tool, args, mission.get("workspace_root"))
                    )
                    real_files = []
                    if missing_paths:
                        ws_dir = mission.get("workspace_root") or get_default_workspace_root()
                        try:
                            real_files = [f for f in sorted(os.listdir(ws_dir)) if not f.startswith(".")][:12]
                        except Exception:
                            real_files = []
                    real_files_str = ", ".join(real_files) if real_files else "(carpeta vacía)"
                    cause = (step_reasons or "").split(",")[0].strip() or "acción fallida o vetada"
                    if missing_paths:
                        cb_message = (
                            f"⚡ JEV CIRCUIT BREAKER: {consecutive_failures} fallos consecutivos intentando acceder a archivos o rutas "
                            "que no existen. El supervisor poda la rama, retrocede e inyecta la estructura real del workspace."
                        )
                    else:
                        cb_message = (
                            f"⚡ JEV CIRCUIT BREAKER: {consecutive_failures} fallos consecutivos (último motivo: {cause}). "
                            "El supervisor poda la rama y pide cambiar de enfoque."
                        )

                    self.event_bus.emit(
                        session_id=sid,
                        event_type=EventType.INTERVENTION_APPLIED,
                        node_id=action_node_id,
                        parent_id=root_id,
                        payload={
                            "intervention": "CIRCUIT_BREAKER_GROUNDING_INJECTION",
                            "message": cb_message,
                            "real_files": real_files,
                            "backtrack_to": root_id,
                        },
                    )

                    if missing_paths:
                        obs_fail += f"\n🚨 [CIRCUIT BREAKER GROUNDING]: Archivos reales del workspace: [{real_files_str}]. Trabaja exclusivamente sobre rutas reales o usa 'finish'."
                    else:
                        obs_fail += f"\n🚨 [CIRCUIT BREAKER]: {consecutive_failures} fallos seguidos (motivo: {cause}). Lee el motivo, cambia de enfoque y no repitas la misma acción."
                    step_records.append({
                        "step": step_idx,
                        "thought": thought,
                        "tool": tool,
                        "arguments": args,
                        "verdict": step_verdict if step_verdict != "ALLOW" else "BLOCK",
                        "reason_code": step_reasons or "CIRCUIT_BREAKER_GROUNDING",
                        "observation": obs_fail,
                        "success": False,
                    })
                    active_parent_id = anchor_id
                    time.sleep(delay_sec)
                    continue

                elif consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.warning("Terminación preventiva por supervisor JEV en sesión %s tras %d fallos continuos.", sid, consecutive_failures)
                    stop_missing = (
                        "UNGROUNDED_FILE_NOT_FOUND" in (step_reasons or "")
                        or "no existe" in (obs_output or "").lower()
                        or _targets_missing_path(tool, args, mission.get("workspace_root"))
                    )
                    stop_cause = (step_reasons or "").split(",")[0].strip() or "acciones fallidas o vetadas"
                    summary_final = (
                        f"Misión concluida preventivamente por el supervisor JEV tras {step_idx} turnos: el agente insistió en rutas o archivos que no existen."
                        if stop_missing
                        else f"Misión concluida preventivamente por el supervisor JEV tras {step_idx} turnos: {consecutive_failures} fallos consecutivos sin avance (último motivo: {stop_cause})."
                    )
                    self.event_bus.emit(
                        session_id=sid,
                        event_type=EventType.INTERVENTION_APPLIED,
                        node_id=action_node_id,
                        parent_id=root_id,
                        payload={
                            "intervention": "SUPERVISOR_FORCED_TERMINATION",
                            "message": "Supervisión JEV: se cortó la ejecución para evitar un bucle de fallos sin avance. " + summary_final,
                        },
                    )
                    final_answer = summary_final
                    forced_termination = True
                    if owns_session:
                        self.event_bus.emit(
                            session_id=sid,
                            event_type=EventType.SESSION_COMPLETED,
                            node_id=root_id,
                            payload={"status": "completed", "summary": summary_final},
                        )
                        with self._lock:
                            if sid in self._sessions_meta:
                                self._sessions_meta[sid]["status"] = "Completed"
                                self._sessions_meta[sid]["final_answer"] = summary_final
                    step_records.append({
                        "step": step_idx,
                        "thought": thought,
                        "tool": tool,
                        "arguments": args,
                        "verdict": "BLOCK",
                        "reason_code": "SUPERVISOR_FORCED_TERMINATION",
                        "observation": summary_final,
                        "success": False,
                    })
                    break
                else:
                    step_records.append({
                        "step": step_idx,
                        "thought": thought,
                        "tool": tool,
                        "arguments": args,
                        "verdict": step_verdict if step_verdict != "ALLOW" else "BLOCK",
                        "reason_code": step_reasons or "EXECUTION_FAILURE",
                        "observation": obs_fail,
                        "success": False,
                    })

            if tool in ("finish", "complete_task", "done", "complete", "task_completed"):
                if executed_successfully:
                    final_ans = (
                        args.get("summary")
                        or args.get("final_answer")
                        or obs_output.replace("Tarea concluida: ", "")
                    )
                    final_answer = final_ans
                    if owns_session:
                        with self._lock:
                            if sid in self._sessions_meta:
                                self._sessions_meta[sid]["final_answer"] = final_ans
                    break
                else:
                    # Veto del supervisor a la conclusión prematura: alimentar al LLM para corregir rumbo
                    v_reason = step_reasons or "FALTA_DE_EVIDENCIAS_EMPÍRICAS"
                    logger.warning(
                        "Intento de finalización ('%s') vetado en paso %d (motivo: %s). Se retroalimenta al agente para continuar.",
                        tool,
                        step_idx,
                        v_reason,
                    )
                    if mission.get("workflow_id"):
                        # En un nodo de workflow, finish entrega el resultado del NODO: no se exige leer el proyecto.
                        supervisor_feedback = (
                            f"🚨 [SUPERVISOR PRAXEON - {v_reason}]: tu finish no se ha podido aceptar.\n"
                            "Si el motivo es de formato, repítelo con finish(summary=<texto>) o con un objeto JSON válido. "
                            "Si tu misión necesita datos del proyecto, usa read_file o run_command; si no los necesita, "
                            "entrega directamente el resultado de tu misión."
                        )
                    else:
                        supervisor_feedback = (
                            f"🚨 [VETO DEL SUPERVISOR PRAXEON - {v_reason}]:\n"
                            f"Tu intento de concluir la misión mediante '{tool}' ha sido RECHAZADO por falta de evidencia empírica fundamentada.\n"
                            "NO se permite emitir conclusiones sin antes haber inspeccionado los archivos reales del proyecto.\n"
                            "EN EL SIGUIENTE TURNO DEBES ejecutar 'read_file(path)' o 'run_command' para examinar los archivos relevantes."
                        )
                    if step_records and step_records[-1]["step"] == step_idx:
                        step_records[-1]["observation"] = supervisor_feedback
                        step_records[-1]["verdict"] = "REPLAN"
                        step_records[-1]["reason_code"] = v_reason

                    time.sleep(delay_sec)
                    continue

            time.sleep(delay_sec)

        return {
            "step_records": step_records,
            "final_answer": final_answer,
            "steps_used": step_idx,
            "last_action_id": active_parent_id if active_parent_id != anchor_id else None,
            "forced_termination": forced_termination,
        }

    def _run_workflow_mission(self, mission: Dict[str, Any], base_llm: Any, session_mode: str) -> bool:
        """Ejecuta la tarea siguiendo el workflow: el grafo manda y cada agente trabaja dentro de su nodo."""
        from praxeon import agent_llm as llm_mod
        from praxeon.agents.definition import ModelConfig
        from praxeon.workflows.mission_driver import WorkflowMissionDriver

        sid = mission["session_id"]
        workflow = self.workflow_editor_service.get_workflow(mission["workflow_id"])
        if workflow is None:
            self._report_workflow_failure(sid, f"Workflow '{mission['workflow_id']}' no encontrado.")
            return False

        own_llms: Dict[Any, Any] = {}

        def llm_for_agent(agent: Any) -> Any:
            """El agente usa su propio proveedor/modelo si lo personalizó; si no, el que eligió el usuario al lanzar."""
            if base_llm is None or agent is None or agent.model == ModelConfig():
                return base_llm
            key = (agent.model.provider, agent.model.model_name)
            if key not in own_llms:
                try:
                    own_llms[key] = llm_mod.create_agent_llm(
                        provider=agent.model.provider,
                        model=agent.model.model_name,
                        timeout=float(os.getenv("PRAXEON_LLM_TIMEOUT", "180.0")),
                    )
                except Exception as err:
                    logger.warning(
                        "Modelo propio del agente '%s' no disponible (%s): se usa el modelo de la misión.", agent.agent_id, err
                    )
                    own_llms[key] = base_llm
            return own_llms[key]

        driver = WorkflowMissionDriver(
            workflow=workflow,
            mission=mission,
            goal=mission["goal"],
            agent_registry=self.agent_registry,
            workflow_service=self.workflow_service,
            run_agent_loop=self._run_llm_loop,
            llm_for_agent=llm_for_agent,
            supervised_runner=self.workflow_editor_service.supervised_runner,
            on_node_started=lambda node_id, payload: self.event_bus.emit(
                session_id=sid,
                event_type=EventType.WORKFLOW_NODE_STARTED,
                node_id=node_id,
                payload=payload,
            ),
            on_node_completed=lambda node_id, payload: self.event_bus.emit(
                session_id=sid,
                event_type=EventType.WORKFLOW_NODE_COMPLETED,
                node_id=node_id,
                payload=payload,
            ),
        )
        ok = False
        try:
            ok = driver.run()
        except Exception as err:
            logger.error("Error ejecutando la misión con workflow %s: %s", workflow.workflow_id, err, exc_info=True)
            self._report_workflow_failure(sid, f"Error ejecutando el workflow: {err}")
            return False
        if ok:
            answer = driver.final_answer()
            with self._lock:
                if sid in self._sessions_meta:
                    self._sessions_meta[sid]["final_answer"] = answer
        else:
            self._report_workflow_failure(sid, driver.final_answer())
        return ok

    def _report_workflow_failure(self, sid: str, message: str) -> None:
        self.event_bus.emit(
            session_id=sid,
            event_type=EventType.INTERVENTION_APPLIED,
            node_id=f"root_{sid}",
            payload={"intervention": "WORKFLOW_FAILED", "message": message, "fatal": True},
        )

    def _run_mission_worker_impl(self, mission: Dict[str, Any]) -> None:
        """Implementación del worker asíncrono de misiones interactivas."""
        from praxeon.agent_llm import BaseAgentLLM, SimulatedAgentLLM, create_agent_llm

        sid = mission["session_id"]
        goal = mission["goal"]
        provider_name = (mission.get("llm_provider") or "simulator").lower().strip()
        model_name = mission.get("llm_model")
        api_key = mission.get("api_key")
        base_url = mission.get("base_url")
        max_steps = mission.get("max_steps", 25)
        delay_sec = max(0.2, mission.get("step_delay_ms", 900) / 1000.0)

        # Resolver modo de ejecución de la sesión (Full Access vs Container vs Local Restricted)
        session_mode = mission.get("execution_mode") or "local_restricted"
        with self._lock:
            if sid in self._sessions_meta:
                session_mode = self._sessions_meta[sid].get("execution_mode") or session_mode
        if not session_mode or session_mode == "local_restricted":
            st = self.state_store.load_state(sid)
            if st and st.metadata:
                session_mode = st.metadata.get("execution_mode", session_mode)

        # Retardo inicial para dar tiempo al WebSocket a suscribirse
        time.sleep(0.4)

        # 1. Intentar inicializar cliente LLM real si se solicitó un proveedor online/local
        agent_llm: Optional[BaseAgentLLM] = None
        use_real_llm = False

        if provider_name not in ("simulator", "mock", "sim"):
            try:
                llm_timeout = float(os.getenv("PRAXEON_LLM_TIMEOUT", "180.0"))
                agent_llm = create_agent_llm(
                    provider=provider_name,
                    model=model_name,
                    api_key=api_key,
                    base_url=base_url,
                    timeout=llm_timeout,
                )
                if not isinstance(agent_llm, SimulatedAgentLLM):
                    use_real_llm = True
                    logger.info("Misión inicializada con LLM real: %s (%s)", agent_llm.provider_name, agent_llm.model_name)
            except Exception as err:
                llm_fail_policy = (mission.get("llm_failure_policy") or "synthetic_fallback").lower().strip()
                if llm_fail_policy == "fail_closed":
                    logger.error(
                        "No se pudo inicializar proveedor LLM '%s': %s bajo política fail_closed.",
                        provider_name,
                        err,
                    )
                    self.event_bus.emit(
                        session_id=sid,
                        event_type=EventType.INTERVENTION_APPLIED,
                        node_id=f"root_{sid}",
                        payload={
                            "error": f"Fallo al inicializar proveedor LLM '{provider_name}' bajo política fail_closed: {err}",
                            "fatal": True,
                            "llm_failure_policy": "fail_closed",
                        },
                    )
                    mission["stopped"] = True
                    return

                logger.warning(
                    "No se pudo inicializar proveedor LLM '%s': %s. Se activará el planificador contextual dinámico en modo degradado.",
                    provider_name,
                    err,
                )
                self.event_bus.emit(
                    session_id=sid,
                    event_type=EventType.INTERVENTION_APPLIED,
                    node_id=f"root_{sid}",
                    payload={
                        "warning": f"[DEGRADED_MODE: SYNTHETIC_PLANNER] LLM '{provider_name}' no disponible ({err}). Activando razonamiento contextual dinámico adaptado a: '{goal}'.",
                        "degraded_mode": True,
                        "planner": "synthetic",
                    },
                )

        step_idx = 0

        # =========================================================================
        # MODO A: LLM REAL (Ollama, Groq, OpenRouter, OpenAI, Gemini) -> _run_llm_loop
        # =========================================================================
        if mission.get("workflow_id"):
            try:
                wf_ok = self._run_workflow_mission(mission, agent_llm if use_real_llm else None, session_mode)
            finally:
                if callable(getattr(agent_llm, "end_session", None)):
                    agent_llm.end_session()
            step_records = [{"success": wf_ok}]
        elif use_real_llm and agent_llm is not None:
            try:
                loop_result = self._run_llm_loop(
                    mission,
                    agent_llm,
                    goal=goal,
                    max_steps=max_steps,
                    delay_sec=delay_sec,
                    provider_name=provider_name,
                    session_mode=session_mode,
                )
            finally:
                if callable(getattr(agent_llm, "end_session", None)):
                    agent_llm.end_session()
            step_records = loop_result["step_records"]
            step_idx = loop_result["steps_used"]

        # =========================================================================
        # MODO B: PLANIFICADOR CONTEXTUAL DINÁMICO (Simulator o Fallback de Provider)
        # =========================================================================
        else:
            steps_to_run = generate_goal_tailored_steps(goal=goal, max_steps=max_steps if max_steps > 0 else 25)
            active_parent_id = f"root_{sid}"

            while step_idx < len(steps_to_run):
                if mission.get("stopped"):
                    break

                while mission.get("paused") and not mission.get("stopped"):
                    time.sleep(0.2)

                step_data = steps_to_run[step_idx]
                step_idx += 1
                mission["current_step"] = step_idx

                # Reconciliación / persistencia periódica en SQLite
                if hasattr(self.state_store, "save_mission"):
                    try:
                        self.state_store.save_mission(dict(mission))
                    except Exception:
                        pass

                # Usar parent_id explícito del plan o el active_parent_id actual
                target_parent = step_data.get("parent_id") or active_parent_id
                action_node_id = f"act_{step_idx}"
                thought_str = step_data["thought"]
                prov_source = f"LLM ({provider_name.upper()})"
                if provider_name not in ("simulator", "mock", "sim"):
                    thought_str = f"[DEGRADED_MODE: SYNTHETIC_PLANNER] {thought_str}"
                    prov_source = "SYNTHETIC_FALLBACK"

                req = ProposeActionRequest(
                    action_id=action_node_id,
                    parent_id=target_parent,
                    tool=step_data["tool"],
                    operation=step_data["operation"],
                    arguments=step_data["arguments"],
                    thought_rationale=thought_str,
                    provenance={
                        "source": prov_source,
                        "step": step_idx,
                    },
                    context={"goal": goal},
                )

                executed_successfully = False
                try:
                    resp = self.propose_action(session_id=sid, proposal=req)

                    # Si requiere confirmación humana (ej. REVIEW por git push), esperar a que sea autorizada
                    if resp.status == "REVIEW" or resp.policy.requires_confirmation:
                        allow_unattended = bool(
                            mission.get("allow_unattended_execution")
                            or mission.get("autonomous")
                            or False
                        )
                        full_access_authorized = bool(
                            mission.get("full_access_authorized_by_operator")
                            or mission.get("operator_authorized")
                        )
                        if session_mode == "full_access" and full_access_authorized and not mission.get("require_step_confirmation"):
                            logger.warning(
                                "[SECURITY AUDIT] Auto-confirmando decisión %s en modo FULL_ACCESS_AUTONOMOUS...",
                                resp.decision_id,
                            )
                            try:
                                conf_res = self.confirm_decision(
                                    decision_id=resp.decision_id,
                                    approved=True,
                                    reason="Auto-aprobado por sesión Full Access Autónomo",
                                    operator_id="operator_full_access_auto",
                                    role="operator",
                                    )
                                if conf_res.status == "ALLOW":
                                    exec_res = self.execute_decision(decision_id=resp.decision_id)
                                    executed_successfully = exec_res.success and not exec_res.is_error
                            except Exception as ex:
                                logger.warning("Error auto-confirmando en Full Access: %s", ex)
                        else:
                            dec_status = self._await_human_decision(resp.decision_id, mission)
                            if dec_status == "ALLOW":
                                try:
                                    exec_res = self.execute_decision(decision_id=resp.decision_id)
                                    executed_successfully = exec_res.success and not exec_res.is_error
                                except Exception as ex:
                                    logger.debug("Execution note: %s", ex)
                    elif resp.status == "ALLOW":
                        try:
                            exec_res = self.execute_decision(decision_id=resp.decision_id)
                            executed_successfully = exec_res.success and not exec_res.is_error
                        except Exception as ex:
                            logger.debug("Execution note: %s", ex)
                except Exception as err:
                    logger.error(f"Error proponiendo paso {step_idx} en sesión {sid}: {err}")
                    executed_successfully = False

                if step_data.get("simulate_failure"):
                    # Si el paso simulaba un fallo/veto para ilustrar poda y retroceso
                    executed_successfully = False
                    self.event_bus.emit(
                        session_id=sid,
                        event_type=EventType.DECISION_PRUNED,
                        node_id=action_node_id,
                        parent_id=target_parent,
                        payload={"reason": "Poda del supervisor: rama heurística no óptima."},
                    )
                    self.event_bus.emit(
                        session_id=sid,
                        event_type=EventType.INTERVENTION_APPLIED,
                        node_id=action_node_id,
                        parent_id=target_parent,
                        payload={
                            "intervention": "BACKTRACK_AND_BRANCH",
                            "message": f"Rama exploratoria '{action_node_id}' podada. Retrocediendo a '{target_parent}' para bifurcar hipótesis alternativa.",
                            "backtrack_to": target_parent,
                            "failed_node": action_node_id,
                        },
                    )

                if executed_successfully:
                    active_parent_id = action_node_id
                else:
                    # Retroceder al padre objetivo
                    active_parent_id = target_parent

                if step_data["tool"] == "finish":
                    if executed_successfully:
                        break
                    else:
                        logger.warning("Paso finish en Modo B no tuvo éxito; la misión continúa.")

                time.sleep(delay_sec)

        has_any_success = any(r.get("success") for r in step_records) if 'step_records' in locals() else True
        final_summary = None
        with self._lock:
            if sid in self._sessions_meta:
                final_summary = self._sessions_meta[sid].get("final_answer")

        if not final_summary and not has_any_success:
            resolved_status = "Intervened"
            resolved_summary = f"Misión '{goal}' intervenida por el supervisor PRAXEON tras vetos por falta de evidencia empírica."
        else:
            resolved_status = "Completed"
            resolved_summary = final_summary or f"Misión '{goal}' finalizada exitosamente."

        with self._lock:
            if sid in self._sessions_meta:
                self._sessions_meta[sid]["status"] = resolved_status
                if final_summary:
                    self._sessions_meta[sid]["final_answer"] = final_summary

        mission["status"] = resolved_status
        mission["stopped"] = True
        if hasattr(self.state_store, "save_mission"):
            try:
                self.state_store.save_mission(dict(mission))
            except Exception:
                pass

        self.event_bus.emit(
            session_id=sid,
            event_type=EventType.SESSION_COMPLETED,
            node_id=f"root_{sid}",
            payload={"status": resolved_status.lower(), "summary": resolved_summary},
        )
