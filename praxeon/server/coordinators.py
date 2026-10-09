"""Coordinadores modulares de dominio para el RuntimeApplicationService (REF-02).

Descompone la arquitectura monolítica del Runtime en cuatro coordinadores especializados:
1. SessionLifecycleCoordinator: Creación, configuración, ciclo de vida y worker de misiones.
2. StepExecutionCoordinator: Pipeline de propuestas de acción, evaluación, confirmación y ejecución física.
3. CheckpointCoordinator: Gestión atómica de snapshots, checkpoints y reversiones (rollback).
4. DiagnosticsCoordinator: Inspección de decisiones (tabs), formateo de auditoría, eventos y catálogo de proveedores.
"""

from __future__ import annotations

import datetime
from datetime import datetime as dt, timezone, timedelta
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid
from praxeon.domain.decision import (
    CapabilityPayload,
    DecisionReceipt,
    DecisionStatus,
    ExecutionMode,
    PolicyDecision,
    sign_receipt,
    verify_capability_signature,
)
from praxeon.domain.events import EventType, RuntimeEvent
from praxeon.domain.models import (
    ActionCandidate,
    Goal,
    ProviderAssessment,
    RiskAssessment,
    RiskLevel,
    ToolCall,
)
from praxeon.domain.decision_provider import DecisionModelConfig
from praxeon.providers.registry import default_registry
from praxeon.runtime.decision_runtime import DecisionRuntime
from praxeon.runtime.session_runtime import SessionRuntime
from praxeon.runtime.state import SessionState
from praxeon.runtime.tree_reducer import reduce_events_to_tree
from praxeon.server.schemas.action import ProposeActionRequest
from praxeon.server.schemas.decision import (
    ConfirmDecisionResponse,
    DecisionDetailResponse,
    DecisionResponse,
    ExecuteDecisionResponse,
    PolicyDTO,
)
from praxeon.server.services.mission_service import generate_goal_tailored_steps
from praxeon.live_agent import parse_llm_steps

logger = logging.getLogger("praxeon.server.coordinators")


def get_default_workspace_root() -> str:
    """Calcula la raíz de trabajo por defecto para sesiones de agente."""
    return os.path.abspath(os.getcwd())


class SessionLifecycleCoordinator:
    """Coordinador responsable del ciclo de vida de sesiones y misiones interactivas."""

    def __init__(self, service: Any):
        self.service = service

    def create_session(
        self,
        goal: str,
        session_id: Optional[str] = None,
        agent_name: str = "CodingAgent",
        metadata: Optional[Dict[str, Any]] = None,
        execution_mode: str = "local_restricted",
        workspace_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Crea formalmente una nueva sesión de supervisión y persiste su estado génesis."""
        sid = session_id or f"s-{uuid.uuid4().hex[:8]}"
        now = dt.now(timezone.utc)

        meta = dict(metadata or {})
        mode_val = meta.get("execution_mode") or execution_mode
        meta["execution_mode"] = mode_val
        is_autonomous = bool(meta.get("allow_unattended_execution") or meta.get("autonomous") or False)
        meta["autonomous"] = is_autonomous
        meta["allow_unattended_execution"] = is_autonomous
        effective_ws = workspace_root or meta.get("workspace_root") or meta.get("working_directory") or get_default_workspace_root()
        meta["workspace_root"] = effective_ws
        meta["working_directory"] = effective_ws
        meta["network_mode"] = "host" if mode_val == "full_access" else "isolated"
        meta["created_by"] = meta.get("created_by", "system")
        meta["full_access_authorized_by_operator"] = bool(meta.get("full_access_authorized_by_operator", False))

        state = SessionState(session_id=sid, goal=Goal(objective=goal), metadata=meta)
        self.service.state_store.save_state(state)
        self.service.state_store.create_checkpoint(session_id=sid, label="Genesis checkpoint", state=state)

        if meta.get("supervisor") or meta.get("decision_provider") or meta.get("decision_model"):
            self.get_session_provider(sid)

        with self.service._lock:
            self.service._sessions_meta[sid] = {
                "session_id": sid,
                "goal": goal,
                "agent_name": agent_name,
                "status": "Active",
                "execution_mode": mode_val,
                "created_at": now,
                "updated_at": now,
                "metadata": meta,
            }

        root_node_id = f"root_{sid}"
        self.service.event_bus.emit(
            session_id=sid,
            event_type=EventType.SESSION_STARTED,
            node_id=root_node_id,
            payload={
                "agent_name": agent_name,
                "status": "Active",
                "label": "Start",
                "execution_mode": mode_val,
                "created_at": now.isoformat(),
            },
        )
        self.service.event_bus.emit(
            session_id=sid,
            event_type=EventType.GOAL_CREATED,
            node_id=f"goal_{sid}",
            parent_id=root_node_id,
            payload={"goal": goal},
        )

        return self.service._sessions_meta[sid]

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene el resumen y metadatos de una sesión."""
        with self.service._lock:
            if session_id in self.service._sessions_meta:
                return dict(self.service._sessions_meta[session_id])

        state = self.service.state_store.load_state(session_id)
        if not state:
            return None

        mode_val = state.metadata.get("execution_mode", "local_restricted")
        meta = {
            "session_id": session_id,
            "goal": state.goal.objective,
            "agent_name": "CodingAgent",
            "status": "Active",
            "execution_mode": mode_val,
            "created_at": dt.now(timezone.utc),
            "updated_at": dt.now(timezone.utc),
            "metadata": state.metadata,
        }
        with self.service._lock:
            self.service._sessions_meta[session_id] = meta
        return meta

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Devuelve todas las sesiones registradas con sus contadores de decisiones."""
        session_ids = self.service.state_store.list_sessions()
        with self.service._lock:
            for s in self.service._sessions_meta.keys():
                if s not in session_ids:
                    session_ids.append(s)

        results = []
        for sid in session_ids:
            summary = self.get_session_summary(sid)
            if summary:
                results.append(summary)
        return results

    def get_session_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Calcula el resumen agregado de una sesión."""
        sess = self.get_session(session_id)
        if not sess:
            return None

        events = self.service.event_bus.get_all_events(session_id)
        total_decisions = sum(1 for e in events if e.type == EventType.POLICY_DECIDED)
        allowed = sum(1 for e in events if e.type == EventType.POLICY_DECIDED and "ALLOW" in str(e.payload.get("status", "")).upper())
        blocked = sum(1 for e in events if e.type == EventType.POLICY_DECIDED and "BLOCK" in str(e.payload.get("status", "")).upper())
        review = sum(1 for e in events if e.type == EventType.POLICY_DECIDED and "REVIEW" in str(e.payload.get("status", "")).upper())
        waiting = sum(1 for e in events if e.type == EventType.APPROVAL_REQUESTED)

        tree = reduce_events_to_tree(events, session_id=session_id)
        meta = sess.get("metadata", {})
        execution_mode = sess.get("execution_mode") or meta.get("execution_mode", "local_restricted")
        ws_root = meta.get("workspace_root") or meta.get("working_directory") or get_default_workspace_root()

        dec_effective = None
        with self.service._lock:
            s_runtime = self.service._session_runtimes.get(session_id)
            if s_runtime and s_runtime.decision_runtime:
                dec_effective = s_runtime.decision_runtime.config.model_dump()
            elif meta.get("decision_model"):
                d_m = meta.get("decision_model")
                dec_effective = d_m.model_dump() if hasattr(d_m, "model_dump") else d_m

        return {
            "session_id": session_id,
            "goal": sess.get("goal", ""),
            "status": sess.get("status", "Active"),
            "agent_name": sess.get("agent_name", "CodingAgent"),
            "execution_mode": execution_mode,
            "workspace_root": ws_root,
            "operator_approval_status": "Review Required" if waiting > 0 else "Normal",
            "created_at": sess.get("created_at"),
            "updated_at": events[-1].timestamp if events else sess.get("updated_at"),
            "total_decisions": total_decisions,
            "allowed_count": allowed,
            "blocked_count": blocked,
            "review_count": review,
            "waiting_count": waiting,
            "event_count": len(events),
            "node_count": tree.node_count,
            "decision_model_effective": dec_effective,
        }

    def get_session_snapshot(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Snapshot completo con el árbol de decisiones derivado deterministamente."""
        summary = self.get_session_summary(session_id)
        if not summary:
            return None

        events = self.service.event_bus.get_all_events(session_id)
        tree = reduce_events_to_tree(events, session_id=session_id)

        return {
            "session_id": session_id,
            "goal": summary.get("goal", ""),
            "status": summary.get("status", "Active"),
            "agent_name": summary.get("agent_name", "CodingAgent"),
            "execution_mode": summary.get("execution_mode", "local_restricted"),
            "created_at": summary.get("created_at"),
            "tree": tree.model_dump(mode="json"),
            "summary": summary,
        }

    def delete_session(self, session_id: str) -> bool:
        """Elimina una sesión y purga todos sus estados, eventos y checkpoints."""
        with self.service._lock:
            if session_id in self.service._running_missions:
                self.service._running_missions[session_id]["stopped"] = True
                self.service._running_missions.pop(session_id, None)

            existed_in_meta = session_id in self.service._sessions_meta
            self.service._sessions_meta.pop(session_id, None)

        existed_in_state = self.service.state_store.delete_session(session_id)
        self.service.event_bus.delete_session(session_id)
        return existed_in_meta or existed_in_state

    def clear_old_sessions(
        self,
        only_completed: bool = True,
        exclude_session_id: Optional[str] = None,
    ) -> int:
        """Purga sesiones antiguas para liberar espacio."""
        all_sessions = self.list_sessions()
        deleted_count = 0
        for sess in all_sessions:
            sid = sess.get("session_id")
            if not sid or sid == exclude_session_id:
                continue
            status_val = (sess.get("status") or "").lower()
            should_delete = False
            if only_completed:
                if status_val in ("completed", "stopped", "finished", "finalizada"):
                    should_delete = True
            else:
                should_delete = True
            if should_delete and self.delete_session(sid):
                deleted_count += 1
        return deleted_count

    def register_session_runtime(
        self,
        session_id: str,
        provider: Any,
        llm_runtime: Optional[Any] = None,
        context_manager: Optional[Any] = None,
        policy_profile: str = "default",
        execution_profile: str = "local_restricted",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionRuntime:
        """Registra un runtime de ejecución y supervisión aislado para una sesión."""
        with self.service._lock:
            runtime = SessionRuntime(
                session_id=session_id,
                decision_provider=provider,
                llm_runtime=llm_runtime,
                context_manager=context_manager,
                policy_profile=policy_profile,
                execution_profile=execution_profile,
                metadata=metadata or {},
            )
            self.service._session_runtimes[session_id] = runtime
            return runtime

    def get_session_provider(self, session_id: str) -> Any:
        """Obtiene el proveedor de decisiones exclusivo para la sesión solicitada."""
        with self.service._lock:
            if session_id in self.service._session_runtimes:
                return self.service._session_runtimes[session_id].decision_provider

        meta: Dict[str, Any] = {}
        with self.service._lock:
            if session_id in self.service._sessions_meta:
                meta = dict(self.service._sessions_meta[session_id].get("metadata") or {})
        if not meta:
            st = self.service.state_store.load_state(session_id)
            if st and st.metadata:
                meta = dict(st.metadata)

        supervisor = meta.get("supervisor") or meta.get("decision_provider")
        decision_model = meta.get("decision_model")

        cfg: Optional[DecisionModelConfig] = None
        if isinstance(decision_model, DecisionModelConfig):
            cfg = decision_model
        elif isinstance(decision_model, dict):
            try:
                cfg = DecisionModelConfig(**decision_model)
            except Exception as e:
                logger.warning(f"Error parseando decision_model dict: {e}")
        elif supervisor:
            cfg = DecisionModelConfig(provider=str(supervisor), model_id=str(supervisor))

        dec_runtime: Optional[DecisionRuntime] = None
        if cfg:
            try:
                dec_runtime = DecisionRuntime.from_config(cfg, registry=default_registry)
                provider_instance = dec_runtime.provider
            except Exception as e:
                logger.warning(f"Error resolviendo DecisionRuntime para sesión '{session_id}': {e}. Usando fallback.")
                provider_instance = self.service._default_provider
        else:
            provider_instance = self.service._default_provider

        with self.service._lock:
            if session_id not in self.service._session_runtimes:
                self.service._session_runtimes[session_id] = SessionRuntime(
                    session_id=session_id,
                    decision_provider=provider_instance,
                    decision_runtime=dec_runtime,
                    metadata=meta,
                )
            return self.service._session_runtimes[session_id].decision_provider

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

        if base_url:
            from praxeon.policy.egress import validate_provider_endpoint
            prof = (os.environ.get("PRAXEON_PROFILE") or os.environ.get("PRAXEON_ENV") or "dev").lower().strip()
            allow_custom = getattr(self.service.config.security, "allow_custom_endpoints", False)
            allowed_hosts = getattr(self.service.config.security, "allowed_custom_hosts", set())
            base_url = validate_provider_endpoint(
                url=base_url,
                allow_custom=allow_custom,
                profile=prof,
                allowed_hosts=allowed_hosts,
            )

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
            sess_provider = self.service._default_provider

        with self.service._lock:
            self.service._session_runtimes[sid] = SessionRuntime(
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

        full_access_auth = bool((meta.get("metadata") or {}).get("full_access_authorized_by_operator", False))

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
        }

        with self.service._lock:
            self.service._running_missions[sid] = mission_state
        if hasattr(self.service.state_store, "save_mission"):
            try:
                self.service.state_store.save_mission(mission_state)
            except Exception as ex:
                logger.warning("Fallo al persistir misión '%s' en SQLite: %s", sid, ex)

        worker_thread = threading.Thread(
            target=self.run_mission_worker,
            args=(mission_state,),
            daemon=True,
            name=f"mission-worker-{sid}",
        )
        worker_thread.start()
        return meta

    def pause_mission(self, session_id: str) -> bool:
        """Pausa temporalmente la ejecución interactiva de una misión."""
        with self.service._lock:
            if session_id in self.service._running_missions:
                self.service._running_missions[session_id]["paused"] = True
                if hasattr(self.service.state_store, "save_mission"):
                    self.service.state_store.save_mission(self.service._running_missions[session_id])
                return True
        return False

    def resume_mission(self, session_id: str) -> bool:
        """Reanuda la ejecución interactiva de una misión pausada."""
        with self.service._lock:
            if session_id in self.service._running_missions:
                self.service._running_missions[session_id]["paused"] = False
                if hasattr(self.service.state_store, "save_mission"):
                    self.service.state_store.save_mission(self.service._running_missions[session_id])
                return True
        return False

    def stop_mission(self, session_id: str) -> bool:
        """Detiene permanentemente una misión en ejecución."""
        with self.service._lock:
            if session_id in self.service._running_missions:
                self.service._running_missions[session_id]["stopped"] = True
                self.service._running_missions.pop(session_id, None)
                if hasattr(self.service.state_store, "delete_mission"):
                    try:
                        self.service.state_store.delete_mission(session_id)
                    except Exception:
                        pass
                return True
        return False

    def run_mission_worker(self, mission: Dict[str, Any]) -> None:
        """Worker asíncrono que genera y propone pasos interactivos para la sesión delegando en la implementación de servicio."""
        return self.service._run_mission_worker_impl(mission)


class StepExecutionCoordinator:
    """Coordinador responsable de propuestas de acción, evaluación semántica, confirmación y ejecución física."""

    def __init__(self, service: Any):
        self.service = service

    def propose_action(
        self,
        session_id: str,
        proposal: ProposeActionRequest,
    ) -> DecisionResponse:
        """Punto de entrada de una propuesta externa pasando por todo el pipeline formal."""
        return self.service._propose_action_impl(session_id, proposal)

    def confirm_decision(
        self,
        decision_id: str,
        approved: bool,
        reason: Optional[str] = None,
        actor: str = "human_operator",
        operator_id: Optional[str] = None,
        role: str = "operator",
        operator_token: Optional[str] = None,
        caller_is_verified_operator: bool = False,
        security_profile: Optional[str] = None,
    ) -> ConfirmDecisionResponse:
        """Confirma o veta formalmente una decisión en espera de aprobación humana (REVIEW)."""
        return self.service._confirm_decision_impl(
            decision_id=decision_id,
            approved=approved,
            reason=reason,
            actor=actor,
            operator_id=operator_id,
            role=role,
            operator_token=operator_token,
            caller_is_verified_operator=caller_is_verified_operator,
            security_profile=security_profile,
        )

    def reject_decision(
        self,
        decision_id: str,
        reason: str,
        actor: str = "human_operator",
        operator_id: Optional[str] = None,
        role: str = "operator",
        operator_token: Optional[str] = None,
        caller_is_verified_operator: bool = False,
        security_profile: Optional[str] = None,
    ) -> ConfirmDecisionResponse:
        """Rechaza formalmente una decisión en espera de aprobación humana (REVIEW)."""
        return self.confirm_decision(
            decision_id=decision_id,
            approved=False,
            reason=reason,
            actor=actor,
            operator_id=operator_id,
            role=role,
            operator_token=operator_token,
            caller_is_verified_operator=caller_is_verified_operator,
            security_profile=security_profile,
        )

    def execute_decision(
        self,
        decision_id: str,
        capability_token: Optional[Dict[str, Any]] = None,
        operator_id: Optional[str] = None,
        role: str = "operator",
    ) -> ExecuteDecisionResponse:
        """Ejecuta físicamente la herramienta autorizada en el sandbox o host."""
        return self.service._execute_decision_impl(
            decision_id=decision_id,
            capability_token=capability_token,
            operator_id=operator_id,
            role=role,
        )


class CheckpointCoordinator:
    """Coordinador responsable de snapshots atómicos, checkpoints persistentes y reversiones (rollback)."""

    def __init__(self, service: Any):
        self.service = service

    def create_checkpoint(self, session_id: str, label: Optional[str] = None) -> Dict[str, Any]:
        """Crea un checkpoint atómico persistente del estado actual de la sesión."""
        ckpt = self.service.state_store.create_checkpoint(session_id=session_id, label=label)
        data = ckpt.model_dump(mode="json") if hasattr(ckpt, "model_dump") else dict(ckpt)
        data["label"] = label or data.get("reason", "")
        return data

    def rollback_session(
        self,
        session_id: str,
        checkpoint_id: Optional[str] = None,
        culprit_tool: Optional[str] = None,
        reason: str = "Rollback formal por degradación de trayectoria",
    ) -> Dict[str, Any]:
        """Revierte físicamente el estado de una sesión al checkpoint especificado o al más reciente."""
        state = self.service.state_store.load_state(session_id)
        if not state:
            raise KeyError(f"Sesión '{session_id}' no encontrada para reversión.")

        if checkpoint_id:
            target_chk_id = checkpoint_id
        else:
            latest_chk = self.service.state_store.get_latest_checkpoint(session_id)
            if not latest_chk:
                latest_chk = self.service.state_store.create_checkpoint(
                    session_id=session_id, label="Genesis checkpoint", state=state
                )
            target_chk_id = latest_chk.id

        chk = self.service.state_store.get_checkpoint(target_chk_id)
        if not chk:
            raise KeyError(f"Checkpoint '{target_chk_id}' no encontrado en el almacén de checkpoints.")

        restored_state = self.service.state_store.restore_checkpoint(
            session_id=session_id,
            checkpoint_id=target_chk_id,
            culprit_tool=culprit_tool,
            reason=reason,
        )
        if not restored_state:
            raise RuntimeError(f"Fallo al restaurar el estado desde el checkpoint '{target_chk_id}'.")

        target_step_idx = chk.step_index
        with self.service._lock:
            discarded_action_ids = {st.id for st in state.steps[target_step_idx:]}
            to_remove = [
                d_id for d_id, d_rec in self.service._decisions.items()
                if d_rec.get("session_id") == session_id
                and d_rec.get("action_id") in discarded_action_ids
            ]
            for d_id in to_remove:
                self.service._decisions.pop(d_id, None)

        now_iso = dt.now(timezone.utc).isoformat()
        self.service.event_bus.emit(
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
        self.service.event_bus.emit(
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


class DiagnosticsCoordinator:
    """Coordinador responsable de inspección de decisiones, auditoría, eventos y catálogo de proveedores."""

    def __init__(self, service: Any):
        self.service = service

    def get_decision_detail(self, decision_id: str) -> Optional[DecisionDetailResponse]:
        """Recupera los datos estructurados en las 4 pestañas requeridas por la Sección 6.3."""
        return self.service._get_decision_detail_impl(decision_id)

    def list_decisions_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        """Lista cronológicamente las decisiones asociadas a una sesión recuperando de SQLite."""
        records = self.service.decision_repository.list_by_session(session_id)
        if not records:
            events = self.service.event_bus.get_all_events(session_id)
            reconstructed = self.service.decision_repository.reconstruct_from_events(session_id, events)
            records = list(reconstructed.values())

        with self.service._lock:
            for d in self.service._decisions.values():
                if d.get("session_id") == session_id:
                    if not any(r.get("decision_id") == d.get("decision_id") for r in records):
                        records.append(d)

        return self.service._format_decision_records(records)

    def list_all_decisions(self, session_id: Optional[str] = None, limit: int = 150) -> List[Dict[str, Any]]:
        """Lista cronológicamente o por recencia las decisiones del sistema."""
        if session_id:
            return self.list_decisions_for_session(session_id)

        records = self.service.decision_repository.list_all(limit=limit)
        with self.service._lock:
            for d in self.service._decisions.values():
                if not any(r.get("decision_id") == d.get("decision_id") for r in records):
                    records.append(d)

        return self.service._format_decision_records(records)

    def get_events(self, session_id: str) -> List[RuntimeEvent]:
        """Recupera la secuencia ordenada de eventos de una sesión."""
        return self.service.event_bus.store.get_events(session_id)

    def get_available_providers(self) -> Dict[str, Any]:
        """Genera el catálogo dinámico de proveedores disponibles."""
        return self.service._get_available_providers_impl()


__all__ = [
    "SessionLifecycleCoordinator",
    "StepExecutionCoordinator",
    "CheckpointCoordinator",
    "DiagnosticsCoordinator",
]
