"""Servicio Modular de Gestión de Sesiones y Checkpoints (F1/F2).

Centraliza el ciclo de vida de sesiones:
- Creación, consulta, listado y cálculo de resúmenes agregados.
- Reconstrucción de árboles de decisión (DecisionTree) a partir de eventos de auditoría.
- Captura, consulta y restauración de puntos de control (checkpoints/backtracks).
"""

from datetime import datetime, timezone
import os
import threading
from typing import Any, Dict, List, Optional
import uuid

from praxeon.domain.events import EventType
from praxeon.domain.models import Goal
from praxeon.runtime.event_bus import EventBus
from praxeon.runtime.sandbox import get_default_workspace_root
from praxeon.runtime.state import SessionState
from praxeon.runtime.state_store import SqliteStateStore
from praxeon.runtime.tree_reducer import reduce_events_to_tree


class SessionService:
    """Gestiona el estado persistente y en memoria de sesiones de supervisión."""

    def __init__(
        self,
        event_bus: EventBus,
        state_store: SqliteStateStore,
    ):
        self.event_bus = event_bus
        self.state_store = state_store
        self._lock = threading.Lock()
        self._sessions_meta: Dict[str, Dict[str, Any]] = {}

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
        now = datetime.now(timezone.utc)

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
        self.state_store.save_state(state)
        self.state_store.create_checkpoint(session_id=sid, label="Genesis checkpoint", state=state)

        with self._lock:
            self._sessions_meta[sid] = {
                "session_id": sid,
                "goal": goal,
                "agent_name": agent_name,
                "status": "Active",
                "execution_mode": mode_val,
                "created_at": now,
                "updated_at": now,
                "metadata": meta,
            }

        # Emitir eventos canónicos iniciales
        root_node_id = f"root_{sid}"
        self.event_bus.emit(
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
        self.event_bus.emit(
            session_id=sid,
            event_type=EventType.GOAL_CREATED,
            node_id=f"goal_{sid}",
            parent_id=root_node_id,
            payload={"goal": goal},
        )

        return self._sessions_meta[sid]

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene el resumen y metadatos de una sesión."""
        with self._lock:
            if session_id in self._sessions_meta:
                return dict(self._sessions_meta[session_id])

        # Cargar de SQLite si no está en memoria
        state = self.state_store.load_state(session_id)
        if not state:
            return None

        mode_val = state.metadata.get("execution_mode", "local_restricted")
        meta = {
            "session_id": session_id,
            "goal": state.goal.objective,
            "agent_name": "CodingAgent",
            "status": "Active",
            "execution_mode": mode_val,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "metadata": state.metadata,
        }
        with self._lock:
            self._sessions_meta[session_id] = meta
        return meta

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Devuelve todas las sesiones registradas con sus resúmenes."""
        session_ids = self.state_store.list_sessions()
        with self._lock:
            for s in self._sessions_meta.keys():
                if s not in session_ids:
                    session_ids.append(s)

        results = []
        for sid in session_ids:
            summary = self.get_session_summary(sid)
            if summary:
                results.append(summary)
        return results

    def get_session_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Calcula el resumen agregado (contadores, eventos, nodos) de una sesión."""
        sess = self.get_session(session_id)
        if not sess:
            return None

        events = self.event_bus.get_all_events(session_id)
        total_decisions = sum(1 for e in events if e.type == EventType.POLICY_DECIDED)
        allowed = sum(1 for e in events if e.type == EventType.POLICY_DECIDED and "ALLOW" in str(e.payload.get("status", "")).upper())
        blocked = sum(1 for e in events if e.type == EventType.POLICY_DECIDED and "BLOCK" in str(e.payload.get("status", "")).upper())
        review = sum(1 for e in events if e.type == EventType.POLICY_DECIDED and "REVIEW" in str(e.payload.get("status", "")).upper())
        waiting = sum(1 for e in events if e.type == EventType.APPROVAL_REQUESTED)

        tree = reduce_events_to_tree(events, session_id=session_id)
        meta = sess.get("metadata", {})
        execution_mode = sess.get("execution_mode") or meta.get("execution_mode", "local_restricted")
        ws_root = meta.get("workspace_root") or meta.get("working_directory") or get_default_workspace_root()

        return {
            "session_id": session_id,
            "goal": sess.get("goal", ""),
            "agent_name": sess.get("agent_name", "CodingAgent"),
            "status": sess.get("status", "Active"),
            "execution_mode": execution_mode,
            "workspace_root": ws_root,
            "created_at": sess.get("created_at"),
            "updated_at": sess.get("updated_at"),
            "total_decisions": total_decisions,
            "allowed_count": allowed,
            "blocked_count": blocked,
            "review_count": review,
            "waiting_approval_count": waiting,
            "event_count": len(events),
            "tree": tree,
        }

    def get_session_snapshot(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene una instantánea completa de la sesión incluyendo su árbol de decisiones."""
        summary = self.get_session_summary(session_id)
        if not summary:
            return None
        events = self.event_bus.get_all_events(session_id)
        return {
            **summary,
            "events": [e.model_dump(mode="json") if hasattr(e, "model_dump") else e for e in events],
        }

    def create_checkpoint(self, session_id: str, label: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Genera un punto de control inmutable para la sesión."""
        state = self.state_store.load_state(session_id)
        if not state:
            return None
        ckpt = self.state_store.create_checkpoint(session_id, label=label)
        return ckpt.model_dump(mode="json") if hasattr(ckpt, "model_dump") else dict(ckpt)

    def list_checkpoints(self, session_id: str) -> List[Dict[str, Any]]:
        """Lista los puntos de control disponibles para una sesión."""
        ckpts = self.state_store.list_checkpoints(session_id)
        return [c.model_dump(mode="json") if hasattr(c, "model_dump") else dict(c) for c in ckpts]

    def rollback_to_checkpoint(self, session_id: str, checkpoint_id: str) -> bool:
        """Restaura el estado de la sesión al punto de control especificado."""
        ckpt = self.state_store.restore_checkpoint(session_id, checkpoint_id)
        if not ckpt:
            return False
        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.SESSION_ROLLBACK,
            node_id=f"rollback_{checkpoint_id}",
            payload={"checkpoint_id": checkpoint_id, "restored_at": datetime.now(timezone.utc).isoformat()},
        )
        return True
