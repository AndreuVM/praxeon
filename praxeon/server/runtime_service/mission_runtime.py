"""Ciclo de vida de misiones autónomas, checkpoints, rollback y worker de misión."""

from __future__ import annotations

from datetime import datetime, UTC


import functools
import logging
import os
import threading
import time
from typing import Any, Concatenate, ParamSpec
from collections.abc import Callable
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
from praxeon.server.runtime_service.host import RuntimeHost
from praxeon.server.schemas.action import ProposeActionRequest
from praxeon.server.services import (
    generate_goal_tailored_steps,
)

# El bucle del agente vive en agent_loop; estos nombres se reexportan porque otros módulos y los tests los importan de aquí
from praxeon.server.runtime_service.agent_loop import (  # noqa: E402,F401
    COMPRESS_BATCH,
    MAX_CONSECUTIVE_FAILURES,
    MAX_FINISH_REJECTIONS,
    NODE_BUDGET_WARN_AT,
    AgentLoop,
    LoopContext,
    _llm_observation_limit,
    _made_progress,
    _missing_path_feedback,
    _missing_path_from_reasons,
    _targets_missing_path,
    build_loop_system_prompt,
)


# Proveedores que piden expresamente el planificador sintético (no un modelo)
SIMULATOR_PROVIDERS = ("simulator", "simulated", "mock", "sim")

DEFAULT_MAX_CONCURRENT_MISSIONS = 4

_P = ParamSpec("_P")


def prompt_goal(mission: dict[str, Any]) -> str:
    """Objetivo tal como lo ve el agente: el de la misión más su contexto (adjuntos). El objetivo registrado no lleva el contexto."""
    context = mission.get("prompt_context")
    return f"{mission['goal']}\n\n{context}" if context else str(mission["goal"])


class MissionLimitError(RuntimeError):
    """Hay ya tantas misiones en curso como permite `PRAXEON_MAX_CONCURRENT_MISSIONS` (FE-008)."""


def max_concurrent_missions() -> int:
    """Misiones que pueden correr a la vez: `PRAXEON_MAX_CONCURRENT_MISSIONS` (por defecto 4; un valor no válido o menor que 1 usa el defecto).

    Cada misión es un hilo con su bucle de agente y, a menudo, un modelo; sin tope, varios paneles pueden agotar la memoria.
    """
    try:
        value = int(os.getenv("PRAXEON_MAX_CONCURRENT_MISSIONS", "") or DEFAULT_MAX_CONCURRENT_MISSIONS)
    except ValueError:
        return DEFAULT_MAX_CONCURRENT_MISSIONS
    return value if value >= 1 else DEFAULT_MAX_CONCURRENT_MISSIONS


def _limited(fn: Callable[Concatenate[Any, _P], dict[str, Any]]) -> Callable[Concatenate[Any, _P], dict[str, Any]]:
    """Reserva un hueco de misión antes de crear nada (sin efectos si se rechaza) y lo suelta cuando la misión ya tiene hilo o falló."""

    @functools.wraps(fn)
    def wrapper(self: Any, /, *args: _P.args, **kwargs: _P.kwargs) -> dict[str, Any]:
        self._reserve_mission_slot()
        try:
            return fn(self, *args, **kwargs)
        finally:
            with self._lock:
                self._missions_starting -= 1

    return wrapper


class MissionRuntimeMixin(RuntimeHost):
    """Ciclo de vida de misiones autónomas, checkpoints, rollback y worker de misión."""

    # =========================================================================
    # EJECUCIÓN INTERACTIVA EN TIEMPO REAL (Live Mission Runner)
    # =========================================================================

    def _await_human_decision(self, decision_id: str, mission: dict[str, Any]) -> str | None:
        """Espera la resolución humana de una decisión en REVIEW (hasta `approval_timeout_seconds`, 10 min por defecto).

        Devuelve "ALLOW", "BLOCKED" o None si sigue pendiente (tiempo agotado o misión detenida).
        """
        timeout = float(mission.get("approval_timeout_seconds") or 600.0)
        deadline = time.time() + timeout
        status: str | None = None
        while time.time() < deadline and not mission.get("stopped"):
            with self._lock:
                record = self._decisions.get(decision_id)
                status = record.get("status") if record else None
            if status in ("ALLOW", "BLOCKED"):
                return str(status)
            time.sleep(0.25)
        with self._lock:
            record = self._decisions.get(decision_id)
            status = record.get("status") if record else None
        return status if status in ("ALLOW", "BLOCKED") else None

    def active_mission_count(self) -> int:
        """Misiones en curso: las que tienen su hilo vivo más las que están arrancando."""
        with self._lock:
            return self._active_missions_locked() + self._missions_starting

    def _active_missions_locked(self) -> int:
        for sid in [s for s, t in self._mission_threads.items() if not t.is_alive()]:
            del self._mission_threads[sid]
        return len(self._mission_threads)

    def _reserve_mission_slot(self) -> None:
        limit = max_concurrent_missions()
        with self._lock:
            active = self._active_missions_locked() + self._missions_starting
            if active >= limit:
                raise MissionLimitError(
                    f"Hay {active} misiones en curso y el límite es {limit} (PRAXEON_MAX_CONCURRENT_MISSIONS). "
                    "Detén una o espera a que termine antes de iniciar otra."
                )
            self._missions_starting += 1

    @_limited
    def start_mission(
        self,
        goal: str,
        session_id: str | None = None,
        agent_name: str = "CodingAgent",
        execution_mode: str | None = None,
        workspace_root: str | None = None,
        llm_provider: str = "simulator",
        llm_model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        supervisor: str = "laya",
        decision_model: Any | None = None,
        max_steps: int = 25,
        step_delay_ms: int = 900,
        autonomous: bool = False,
        allow_unattended_execution: bool = False,
        llm_failure_policy: str = "synthetic_fallback",
        chat_history: list[dict[str, str]] | None = None,
        metadata: dict[str, Any] | None = None,
        workflow_id: str | None = None,
        prompt_context: str | None = None,
        image_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Inicia una misión interactiva supervisada en tiempo real.

        `prompt_context` (p. ej. los adjuntos del mensaje) se añade al objetivo SOLO en el prompt del agente; el objetivo registrado no cambia.

        Con `workflow_id` (un workflow activo) el grafo gobierna la tarea: ver `WorkflowMissionDriver`.
        """
        sid = session_id or f"s-{uuid.uuid4().hex[:8]}"
        # Sin modo (o «auto»): aislado si el sistema lo permite (AUD-19); create_session deja el aviso si no
        from praxeon.runtime import isolation

        execution_mode, _ = isolation.resolve_execution_mode(execution_mode)
        if workflow_id:
            wf = self.workflow_editor_service.get_workflow(workflow_id)
            if wf is None:
                raise ValueError(f"Workflow '{workflow_id}' no encontrado.")
            if not wf.metadata.get("active", False):
                raise ValueError(f"El workflow '{workflow_id}' no está activo. Actívalo antes de usarlo en una tarea.")

        # Validación anti-SSRF estricta para endpoint remoto o base_url (SEC-01)
        if base_url:
            from praxeon.policy.egress import validate_provider_endpoint
            prof = getattr(self, "security_profile", None) or "dev"
            allow_custom = getattr(self.config.security, "allow_custom_endpoints", False)
            allowed_hosts: set[str] = getattr(self.config.security, "allowed_custom_hosts", set())
            base_url = validate_provider_endpoint(
                url=base_url,
                allow_custom=allow_custom,
                profile=prof,
                allowed_hosts=allowed_hosts,
            )

        # Resolver DecisionModelConfig y DecisionRuntime aislado para esta misión
        cfg: DecisionModelConfig | None = None
        if isinstance(decision_model, DecisionModelConfig):
            cfg = decision_model
        elif isinstance(decision_model, dict):
            try:
                cfg = DecisionModelConfig(**decision_model)
            except (TypeError, ValueError) as e:  # decision_model inválido: se ignora y se usa el proveedor por defecto
                logger.warning(f"Error parseando decision_model dict en start_mission: {e}")
        if not cfg:
            cfg = DecisionModelConfig(provider=supervisor, model_id=supervisor)

        dec_runtime: DecisionRuntime | None = None
        try:
            dec_runtime = DecisionRuntime.from_config(cfg, registry=default_registry)
            sess_provider = dec_runtime.provider
        except Exception as e:  # noqa: BLE001 - frontera con el proveedor de la misión: cualquier fallo al resolverlo usa el proveedor por defecto
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
            "prompt_context": prompt_context,
            "image_paths": list(image_paths or []),      # rutas de las imágenes adjuntas; los bytes se leen y limpian al enviarlos, no se guardan aquí
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
            except Exception as ex:  # noqa: BLE001 - persistencia best-effort: un fallo al guardar la misión se registra y la misión sigue
                logger.warning("Fallo al persistir misión '%s' en SQLite: %s", sid, ex)

        worker_thread = threading.Thread(
            target=self._run_mission_worker,
            args=(mission_state,),
            daemon=True,
            name=f"mission-worker-{sid}",
        )
        with self._lock:
            self._mission_threads[sid] = worker_thread
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

    def create_checkpoint(self, session_id: str, label: str | None = None) -> dict[str, Any]:
        """Crea un checkpoint atómico persistente del estado actual de la sesión (delegado en CheckpointCoordinator)."""
        return self.checkpoint_coordinator.create_checkpoint(session_id=session_id, label=label)

    def rollback_session(
        self,
        session_id: str,
        checkpoint_id: str | None = None,
        culprit_tool: str | None = None,
        reason: str = "Rollback formal por degradación de trayectoria",
    ) -> dict[str, Any]:
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
        checkpoint_id: str | None = None,
        culprit_tool: str | None = None,
        reason: str = "Rollback formal por degradación de trayectoria",
    ) -> dict[str, Any]:
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

        now_iso = datetime.now(UTC).isoformat()
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


    def _run_mission_worker(self, mission: dict[str, Any]) -> None:
        """Worker asíncrono delegado en SessionLifecycleCoordinator."""
        return self.session_coordinator.run_mission_worker(mission)

    def _run_llm_loop(
        self,
        mission: dict[str, Any],
        agent_llm: Any,
        *,
        goal: str,
        max_steps: int,
        delay_sec: float,
        provider_name: str,
        session_mode: str,
        system_prompt: str | None = None,
        id_prefix: str = "",
        root_id: str | None = None,
        owns_session: bool = True,
        role_preamble: str = "",
        tool_filter: Callable[[str], str | None] | None = None,
        trace: dict[str, Any] | None = None,
        start_parent_id: str | None = None,
        finish_check: Callable[[str], str | None] | None = None,
    ) -> dict[str, Any]:
        """Bucle ReAct supervisado de un agente (Thought/Action -> política -> ejecución -> observación).

        Lo usa la misión completa (`owns_session=True`, comportamiento histórico) y cada nodo de un workflow
        (`owns_session=False`, con `id_prefix`/`root_id` propios para no colisionar en el árbol de decisiones).
        `role_preamble` antepone la identidad/misión del agente al prompt de sistema y `tool_filter(tool)` devuelve
        un motivo de rechazo si el agente no tiene permitida esa herramienta. `trace` (nodo/agente del workflow)
        viaja en cada acción para que el árbol muestre quién tiene el control, y `start_parent_id` engancha la
        primera acción a la última del nodo anterior (traspaso entre agentes). `finish_check(summary)` devuelve el
        motivo por el que un finish no se acepta (p. ej. falta el campo que el grafo necesita) o None si es válido.
        """
        sid = mission["session_id"]
        root_id = root_id or f"root_{sid}"
        if system_prompt is None:
            system_prompt = build_loop_system_prompt(self, mission, goal, trace, role_preamble, tool_filter)
        ctx = LoopContext(
            runtime=self,
            mission=mission,
            agent_llm=agent_llm,
            goal=goal,
            max_steps=max_steps,
            delay_sec=delay_sec,
            provider_name=provider_name,
            session_mode=session_mode,
            system_prompt=system_prompt,
            id_prefix=id_prefix,
            root_id=root_id,
            owns_session=owns_session,
            anchor_id=start_parent_id or root_id,
            tool_filter=tool_filter,
            trace=trace,
            finish_check=finish_check,
            llm_fail_policy=(mission.get("llm_failure_policy") or "synthetic_fallback").lower().strip(),
        )
        return AgentLoop(ctx).run()

    def _run_workflow_mission(self, mission: dict[str, Any], base_llm: Any, session_mode: str) -> bool:
        """Ejecuta la tarea siguiendo el workflow: el grafo manda y cada agente trabaja dentro de su nodo."""
        from praxeon import agent_llm as llm_mod
        from praxeon.agents.definition import ModelConfig
        from praxeon.workflows.mission_driver import WorkflowMissionDriver

        sid = mission["session_id"]
        workflow = self.workflow_editor_service.get_workflow(mission["workflow_id"])
        if workflow is None:
            self._report_workflow_failure(sid, f"Workflow '{mission['workflow_id']}' no encontrado.")
            return False

        own_llms: dict[Any, Any] = {}

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
                except Exception as err:  # noqa: BLE001 - frontera con el modelo propio del agente: si no está disponible se usa el modelo de la misión
                    logger.warning(
                        "Modelo propio del agente '%s' no disponible (%s): se usa el modelo de la misión.", agent.agent_id, err
                    )
                    own_llms[key] = base_llm
            return own_llms[key]

        driver = WorkflowMissionDriver(
            workflow=workflow,
            mission=mission,
            goal=prompt_goal(mission),
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

    def _run_mission_worker_impl(self, mission: dict[str, Any]) -> None:
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
        agent_llm: BaseAgentLLM | None = None
        use_real_llm = False

        if provider_name not in SIMULATOR_PROVIDERS:
            try:
                llm_timeout = float(os.getenv("PRAXEON_LLM_TIMEOUT", "180.0"))
                agent_llm = create_agent_llm(
                    provider=provider_name,
                    model=model_name,
                    api_key=api_key,
                    base_url=base_url,
                    timeout=llm_timeout,
                )
                if isinstance(agent_llm, SimulatedAgentLLM):
                    # El proveedor pedido no está disponible (p. ej. sin credenciales) y la fábrica devolvió el simulador:
                    # es un fallo de inicialización, que con fail_closed debe detener la misión en vez de simular
                    raise RuntimeError(f"proveedor '{provider_name}' no disponible (sin credenciales válidas o sin servidor)")
                use_real_llm = True
                logger.info("Misión inicializada con LLM real: %s (%s)", agent_llm.provider_name, agent_llm.model_name)
            except Exception as err:  # noqa: BLE001 - frontera con el proveedor LLM: la política de fallo decide si la misión se detiene o usa el planificador sintético
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
                end_session = getattr(agent_llm, "end_session", None)
                if callable(end_session):
                    end_session()
            step_records = [{"success": wf_ok}]
        elif use_real_llm and agent_llm is not None:
            try:
                loop_result = self._run_llm_loop(
                    mission,
                    agent_llm,
                    goal=prompt_goal(mission),
                    max_steps=max_steps,
                    delay_sec=delay_sec,
                    provider_name=provider_name,
                    session_mode=session_mode,
                )
            finally:
                end_session = getattr(agent_llm, "end_session", None)
                if callable(end_session):
                    end_session()
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
                        logger.warning('No se pudo persistir la misión %s', sid, exc_info=True)

                # Usar parent_id explícito del plan o el active_parent_id actual
                target_parent = step_data.get("parent_id") or active_parent_id
                action_node_id = f"act_{step_idx}"
                thought_str = step_data["thought"]
                prov_source = f"LLM ({provider_name.upper()})"
                if provider_name not in SIMULATOR_PROVIDERS:
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
                        # las acciones del planificador no las propone un modelo: la procedencia lo deja constancia
                        "synthetic_fallback": True,
                        "model_source": "synthetic:fallback",
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
                            except Exception as ex:  # noqa: BLE001 - frontera del auto-confirmado en Full Access: el fallo se registra y el paso queda sin ejecutar
                                logger.warning("Error auto-confirmando en Full Access: %s", ex)
                        else:
                            dec_status = self._await_human_decision(resp.decision_id, mission)
                            if dec_status == "ALLOW":
                                try:
                                    exec_res = self.execute_decision(decision_id=resp.decision_id)
                                    executed_successfully = exec_res.success and not exec_res.is_error
                                except Exception as ex:  # noqa: BLE001 - frontera de ejecución: el fallo se registra y el paso se da por no ejecutado
                                    logger.debug("Execution note: %s", ex)
                    elif resp.status == "ALLOW":
                        try:
                            exec_res = self.execute_decision(decision_id=resp.decision_id)
                            executed_successfully = exec_res.success and not exec_res.is_error
                        except Exception as ex:  # noqa: BLE001 - frontera de ejecución: el fallo se registra y el paso se da por no ejecutado
                            logger.debug("Execution note: %s", ex)
                except Exception as err:  # noqa: BLE001 - frontera del paso: el fallo se registra y el paso queda como no ejecutado
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
                logger.warning('No se pudo persistir la misión detenida %s', sid, exc_info=True)

        self.event_bus.emit(
            session_id=sid,
            event_type=EventType.SESSION_COMPLETED,
            node_id=f"root_{sid}",
            payload={"status": resolved_status.lower(), "summary": resolved_summary},
        )
