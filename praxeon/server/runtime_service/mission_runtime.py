"""Ciclo de vida de misiones autónomas, checkpoints, rollback y worker de misión."""

from __future__ import annotations

from datetime import datetime, timezone


import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional
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
    ) -> Dict[str, Any]:
        """Inicia una misión interactiva supervisada en tiempo real."""
        sid = session_id or f"s-{uuid.uuid4().hex[:8]}"

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
            # Tiempo máximo (s) que la misión espera una aprobación humana antes de seguir sin vetar la acción
            "approval_timeout_seconds": (metadata or {}).get("approval_timeout_seconds"),
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

    def _run_mission_worker_impl(self, mission: Dict[str, Any]) -> None:
        """Implementación del worker asíncrono de misiones interactivas."""
        from praxeon.agent_llm import BaseAgentLLM, SimulatedAgentLLM, create_agent_llm
        from praxeon.live_agent import parse_llm_steps

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
        # MODO A: LLM REAL (Ollama, Groq, OpenRouter, OpenAI, Gemini)
        # =========================================================================
        if use_real_llm and agent_llm is not None:
            # Recolectar información detallada del sistema operativo y espacio de trabajo real
            ws_root = mission.get("workspace_root") or get_default_workspace_root()
            from praxeon.core.session_context import SessionContextManager
            ctx_mgr = SessionContextManager()
            env_info = ctx_mgr.get_environment_info(root_dir=ws_root)

            system_prompt = (
                "Eres un asistente y agente de software autónomo supervisado cognitivamente en tiempo real por PRAXEON (JEV Reasoning Navigator).\n"
                f"OBJETIVO O PREGUNTA DEL USUARIO: {goal}\n"
                f"DIRECTORIO DE TRABAJO BASE PARA ESTA SESIÓN: '{ws_root}'\n\n"
                f"{env_info}\n\n"
                "FORMATO DE RESPUESTA EN CADA TURNO (Estricto ReAct):\n"
                "Thought: <análisis concreto de lo que vas a hacer y por qué>\n"
                "Action: <herramienta>(<argumentos_en_json_o_string>)\n\n"
                "Herramientas disponibles:\n"
                "- read_file(path: str) -> Lee el contenido de un archivo del espacio de trabajo local si la tarea requiere auditar código o configuración existente.\n"
                "- edit_file(path: str, diff: str) -> Aplica modificaciones a un archivo en el espacio de trabajo local cuando la tarea pide editar código existente.\n"
                "- run_command(command: str) -> Ejecuta un comando en la consola del SO (PowerShell en Windows, bash en Unix).\n"
                "- git(command: str) -> Ejecuta comandos git en el repositorio (status, diff, log, etc.).\n"
                "- finish(summary: str) -> Concluye entregando la respuesta directa a la pregunta, especificación, diseño, informe o solución solicitada por el usuario.\n\n"
                "PRINCIPIOS OPERATIVOS Y RAZONAMIENTO COGNITIVO:\n"
                "1. COMPRENSIÓN DEL ÁMBITO DEL PROYECTO (ANTI-SESGO DETERMINISTA): Atiende estrictamente a lo que el usuario solicita. Si el usuario te pide un informe, especificación, arquitectura, estructura de carpetas, requisitos funcionales/no funcionales o user stories para OTRO proyecto externo, una nueva aplicación o una propuesta puramente conceptual, NO asumas que se refiere al proyecto local ni leas archivos locales innecesariamente. Elabora y entrega el diseño de dicho proyecto directamente mediante finish(summary=\"...\").\n"
                "2. AUDITORÍA Y ANÁLISIS DE ESTE PROYECTO / REPOSITORIO LOCAL: Si el usuario te pide explícitamente analizar, inspeccionar, explicar o auditar archivos, módulos o flujos del proyecto local ('analiza los archivos', 'cómo funciona el workflow', 'inspecciona el código', 'explica la arquitectura', etc.), DEBES obligatoriamente usar 'read_file' o 'run_command' para examinar las evidencias reales. El supervisor cognitivo PRAXEON VETARÁ (con REPLAN por falta de evidencia empírica / LOW_GROUNDED_PROBABILITY) cualquier llamada prematura a 'finish' que no haya consultado previamente los archivos requeridos.\n"
                "3. PREGUNTAS Y VALORACIONES CONCEPTUALES: Si te consultan tu opinión general, teoría o valoraciones abstractas sin referencia a archivos de este entorno, responde de forma crítica y fundamentada directamente con finish(summary=\"...\").\n"
                "4. TAREAS DE CÓDIGO TÉCNICO SOBRE EL REPOSITORIO LOCAL: Si la tarea solicita implementar código, corregir un bug o ejecutar pruebas sobre archivos existentes de este entorno, utiliza read_file, edit_file o run_command antes de dar por resuelta la tarea.\n"
                "5. COMPATIBILIDAD DE SO: En Windows, NO uses comandos Unix/Linux como 'ls', 'cat', 'grep'. Utiliza 'read_file(path)' o comandos de PowerShell en run_command.\n"
                "6. RETROCESO Y VETOS DEL SUPERVISOR: Si una acción (incluido un intento de 'finish') es vetada por el supervisor o rechazada por falta de evidencia, reflexiona en 'Thought:' sobre la observación de veto recibida y ejecuta la herramienta de inspección correspondiente (ej. read_file) para fundamentar empíricamente tu razonamiento antes de intentar finalizar de nuevo."
            )

            step_records: List[Dict[str, Any]] = []
            active_parent_id = f"root_{sid}"
            consecutive_failures = 0
            technical_failures = 0
            semantic_fixations = 0
            recent_action_signatures: List[str] = []
            circuit_breaker_triggered = False
            llm_fail_policy = (mission.get("llm_failure_policy") or "synthetic_fallback").lower().strip()

            while step_idx < max_steps:
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
                    chat_history=mission.get("chat_history"),
                )

                # Invocar LLM real
                llm_output = ""
                try:
                    llm_output = agent_llm.generate(conversation, system_prompt=system_prompt)
                    technical_failures = 0  # Éxito técnico: resetear contador técnico
                except Exception as gen_err:
                    technical_failures += 1
                    logger.warning("Error durante generación con LLM '%s': %s", provider_name, gen_err)

                    if technical_failures >= 3:
                        self.event_bus.emit(
                            session_id=sid,
                            event_type=EventType.INTERVENTION_APPLIED,
                            node_id=f"root_{sid}",
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
                            node_id=f"root_{sid}",
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
                        node_id=f"root_{sid}",
                        payload={
                            "warning": f"[DEGRADED_MODE: SYNTHETIC_PLANNER] Fallo temporal de LLM '{provider_name}' ({gen_err}). Activando contingencia contextual.",
                            "degraded_mode": True,
                            "planner": "synthetic",
                        },
                    )
                    steps_backup = generate_goal_tailored_steps(goal=goal, max_steps=max_steps)
                    backup_idx = min(step_idx - 1, len(steps_backup) - 1)
                    b_step = steps_backup[backup_idx] if (steps_backup and backup_idx >= 0) else {
                        "tool_name": "run_command",
                        "tool_args": {"command": "python -c \"print('Paso de inspección segura')\""},
                        "thought_rationale": "Paso de contingencia tras fallo del proveedor LLM.",
                    }
                    llm_output = f"Thought: [DEGRADED_MODE: SYNTHETIC_PLANNER] [Contingencia por fallo temporal en {provider_name}]: {b_step.get('thought_rationale', '')}\nAction: {b_step.get('tool_name', 'run_command')}({json.dumps(b_step.get('tool_args', {}))})"

                # Parsear Thought + Action
                parsed_steps = parse_llm_steps(llm_output)
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
                        thought = st.get("thought_rationale") or f"Paso {step_idx} generado por {agent_llm.model_name} para '{goal}'."
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
                            thought = llm_output[:250].strip() or f"Paso inicial de inspección de archivos requerido para fundamentar: '{goal}'."

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
                        node_id=f"act_{step_idx}",
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

                operation = f"{step_idx}. {tool}"
                action_node_id = f"act_{step_idx}"

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
                        obs_output = f"Acción clasificada como PELIGROSA o destructiva por seguridad: {', '.join(resp.policy.reason_codes or ['Veto operacional'])}"
                    elif resp.status == "REPLAN":
                        executed_successfully = False
                        obs_output = f"Acción clasificada como INNECESARIA, desvío o bucle por el supervisor JEV-LAYA: {', '.join(resp.policy.reason_codes or ['Poda cognitiva'])}"
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
                        if session_mode == "full_access" and allow_unattended and full_access_authorized:
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
                    if len(raw_obs) > 50_000:
                        keep_h = 35_000
                        keep_t = 15_000
                        omitted = len(raw_obs) - 50_000
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
                    is_initial_step = active_parent_id.startswith("root_") or step_idx == 1
                    if is_initial_step:
                        interv_name = "INITIAL_HYPOTHESIS_VETOED"
                        interv_msg = (
                            f"Acción inicial '{action_node_id}' vetada por el supervisor ({step_reasons or 'falta de evidencias empíricas'}). "
                            f"Se exige recopilar evidencias inspeccionando archivos antes de emitir conclusiones."
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
                        cwd = os.getcwd()
                        real_files = []
                        try:
                            real_files = [f for f in os.listdir(cwd) if not f.startswith(".")][:12]
                        except Exception:
                            real_files = ["praxeon", "web", "tests"]
                        real_files_str = ", ".join(real_files)

                        self.event_bus.emit(
                            session_id=sid,
                            event_type=EventType.INTERVENTION_APPLIED,
                            node_id=action_node_id,
                            parent_id=f"root_{sid}",
                            payload={
                                "intervention": "CIRCUIT_BREAKER_GROUNDING_INJECTION",
                                "message": f"⚡ JEV CIRCUIT BREAKER: Bucle de alucinación/fallos consecutivos ({consecutive_failures}) intentando acceder a archivos/scripts inexistentes. El supervisor poda la rama, fuerza retroceso a la raíz e inyecta la estructura real del proyecto.",
                                "real_files": real_files,
                                "backtrack_to": f"root_{sid}",
                            },
                        )

                        obs_fail += f"\n🚨 [CIRCUIT BREAKER GROUNDING]: Archivos reales: [{real_files_str}]. Trabaja exclusivamente sobre rutas reales o usa 'finish'."
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
                        active_parent_id = f"root_{sid}"
                        time.sleep(delay_sec)
                        continue

                    elif consecutive_failures >= 4:
                        logger.warning("Terminación preventiva por supervisor JEV en sesión %s tras 4 fallos continuos.", sid)
                        summary_final = f"Misión concluida preventivamente por el supervisor JEV para detener bucle de alucinación tras {step_idx} turnos. El agente insistió repetidamente en recursos no fundamentados."
                        self.event_bus.emit(
                            session_id=sid,
                            event_type=EventType.INTERVENTION_APPLIED,
                            node_id=action_node_id,
                            parent_id=f"root_{sid}",
                            payload={
                                "intervention": "SUPERVISOR_FORCED_TERMINATION",
                                "message": "Supervisión JEV: Se cortó la ejecución para evitar un bucle de alucinación infinito. Misión concluida de forma segura.",
                            },
                        )
                        self.event_bus.emit(
                            session_id=sid,
                            event_type=EventType.SESSION_COMPLETED,
                            node_id=f"root_{sid}",
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

        # =========================================================================
        # MODO B: PLANIFICADOR CONTEXTUAL DINÁMICO (Simulator o Fallback de Provider)
        # =========================================================================
        else:
            steps_to_run = generate_goal_tailored_steps(goal=goal, max_steps=max_steps)
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
                        if session_mode == "full_access" and allow_unattended and full_access_authorized:
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
