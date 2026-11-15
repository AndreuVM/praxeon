"""Repositorio durable de decisiones para PRAXEON 1.0 (praxeon/runtime/decision_store.py).

Especificación PRAXEON 1.0 (Sección 4).

Garantiza:
1. Persistencia transaccional de decisiones en SQLite WAL.
2. Supervivencia a reinicios: el Decision Inspector puede recuperar decisiones sin depender de RAM.
3. Reconstrucción determinista desde el log de eventos (EventStore) ante cache miss.
"""

from datetime import datetime, UTC
import json

import logging
from typing import Any

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.decision import (
    DecisionReceipt,
    ExecutionMode,
)
from praxeon.domain.events import EventType, RuntimeEvent

from praxeon.sqlite_base import SqliteStore

logger = logging.getLogger("praxeon.runtime.decision_store")


def _serialize_decision_record(record: dict[str, Any]) -> str:
    """Serializa un registro de decisión a JSON preservando tipos Pydantic y datetimes."""
    data = {}
    for k, v in record.items():
        if hasattr(v, "model_dump") and callable(v.model_dump):
            data[k] = v.model_dump(mode="json")
        elif isinstance(v, (datetime,)):
            data[k] = v.isoformat()
        elif isinstance(v, list):
            serialized_list = []
            for item in v:
                if hasattr(item, "model_dump") and callable(item.model_dump):
                    serialized_list.append(item.model_dump(mode="json"))
                elif isinstance(item, (datetime,)):
                    serialized_list.append(item.isoformat())
                else:
                    serialized_list.append(item)
            data[k] = serialized_list
        else:
            data[k] = v
    return json.dumps(data, default=str)


def _deserialize_decision_record(raw_json: str) -> dict[str, Any]:
    """Reconstruye un registro estructurado a partir del JSON persistido."""
    from praxeon.server.schemas.decision import PolicyDTO, ProviderEvaluationDTO, RiskDTO

    d = json.loads(raw_json)

    # Reconstruir ActionCandidate si está presente
    if "action" in d and isinstance(d["action"], dict):
        d["action"] = ActionCandidate.model_validate(d["action"])

    # Reconstruir DecisionReceipt
    if "receipt" in d and isinstance(d["receipt"], dict):
        d["receipt"] = DecisionReceipt.model_validate(d["receipt"])

    # Reconstruir RiskDTO
    if "risk" in d and isinstance(d["risk"], dict):
        d["risk"] = RiskDTO.model_validate(d["risk"])

    # Reconstruir PolicyDTO
    if "policy" in d and isinstance(d["policy"], dict):
        d["policy"] = PolicyDTO.model_validate(d["policy"])

    # Reconstruir Providers
    if "providers" in d and isinstance(d["providers"], list):
        d["providers"] = [
            ProviderEvaluationDTO.model_validate(p) if isinstance(p, dict) else p
            for p in d["providers"]
        ]

    # Reconstruir Datetimes
    for dt_key in ("created_at", "expires_at"):
        if d.get(dt_key) and isinstance(d[dt_key], str):
            try:
                d[dt_key] = datetime.fromisoformat(d[dt_key])
            except Exception:
                logger.debug('Fecha ilegible en una decisión guardada', exc_info=True)

    return d


class SqliteDecisionRepository(SqliteStore):
    """Almacén durable de decisiones con persistencia SQLite WAL y reconstrucción determinista."""

    DEFAULT_DB_NAME = 'decisions.db'
    MIGRATIONS = (
        (   # versión 1: esquema histórico (IF NOT EXISTS: lo aceptan las bases creadas antes de las migraciones)
            """
            CREATE TABLE IF NOT EXISTS durable_decisions (
                decision_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                status TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """,
            'CREATE INDEX IF NOT EXISTS idx_decisions_session ON durable_decisions (session_id, created_at);',
        ),
    )


    def save(self, record: dict[str, Any]) -> None:
        """Guarda o actualiza un registro de decisión completo."""
        decision_id = record["decision_id"]
        session_id = record["session_id"]
        action_id = record.get("action_id", "")
        status = record.get("status", "ALLOW")
        execution_mode = (
            record.get("execution_mode")
            or getattr(record.get("receipt"), "execution_mode", ExecutionMode.LOCAL_RESTRICTED.value)
        )
        created_at_dt = record.get("created_at") or datetime.now(UTC)
        created_at_str = created_at_dt.isoformat() if isinstance(created_at_dt, datetime) else str(created_at_dt)

        data_json = _serialize_decision_record(record)

        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO durable_decisions (
                            decision_id, session_id, action_id, status, execution_mode, data_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(decision_id) DO UPDATE SET
                            status = excluded.status,
                            execution_mode = excluded.execution_mode,
                            data_json = excluded.data_json;
                        """,
                        (
                            decision_id,
                            session_id,
                            action_id,
                            status,
                            execution_mode,
                            data_json,
                            created_at_str,
                        ),
                    )
            finally:
                self._close_conn(conn)

    def get(self, decision_id: str) -> dict[str, Any] | None:
        """Recupera un registro de decisión por su ID."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT data_json FROM durable_decisions WHERE decision_id = ?",
                    (decision_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return _deserialize_decision_record(row[0])
            finally:
                self._close_conn(conn)

    def list_by_session(self, session_id: str) -> list[dict[str, Any]]:
        """Recupera la lista de decisiones asociadas a una sesión."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT data_json FROM durable_decisions WHERE session_id = ? ORDER BY created_at ASC",
                    (session_id,),
                )
                rows = cur.fetchall()
                return [_deserialize_decision_record(r[0]) for r in rows]
            finally:
                self._close_conn(conn)

    def list_all(self, limit: int = 200) -> list[dict[str, Any]]:
        """Recupera la lista de decisiones más recientes de todas las sesiones ordenadas cronológicamente."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT data_json FROM durable_decisions ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
                rows = cur.fetchall()
                return [_deserialize_decision_record(r[0]) for r in rows]
            finally:
                self._close_conn(conn)

    def reconstruct_from_events(
        self,
        session_id: str,
        events: list[RuntimeEvent],
    ) -> dict[str, dict[str, Any]]:
        """Reconstruye el registro observable de decisiones a partir del stream inmutable de eventos."""
        from praxeon.server.schemas.decision import PolicyDTO, ProviderEvaluationDTO, RiskDTO

        decisions: dict[str, dict[str, Any]] = {}

        for ev in events:
            if ev.session_id != session_id:
                continue

            dec_id = ev.decision_id
            if not dec_id:
                continue

            if dec_id not in decisions:
                decisions[dec_id] = {
                    "decision_id": dec_id,
                    "session_id": session_id,
                    "action_id": ev.node_id or f"act_{dec_id}",
                    "action": ActionCandidate(
                        id=ev.node_id or f"act_{dec_id}",
                        description="Acción reconstruida",
                    ),
                    "status": "ALLOW",
                    "execution_mode": ExecutionMode.LOCAL_RESTRICTED.value,
                    "action_hash": "reconstructed",
                    "risk": RiskDTO(level="LOW", score=0.1, reasons=[]),
                    "providers": [ProviderEvaluationDTO(name="EventLogReconstructed", score=0.9, verdict="ALLOW")],
                    "policy": PolicyDTO(decision="ALLOW", reason_codes=["RECONSTRUCTED_FROM_EVENT_LOG"]),
                    "capability": None,
                    "expires_at": None,
                    "receipt": DecisionReceipt(
                        decision_id=dec_id,
                        session_id=session_id,
                        action_id=ev.node_id or f"act_{dec_id}",
                        state_hash="0" * 64,
                        action_hash="0" * 64,
                        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
                    ),
                    "evidence": [],
                    "grounding_score": 1.0,
                    "created_at": ev.timestamp,
                }

            rec = decisions[dec_id]
            p = ev.payload or {}

            # Enriquecer según tipo de evento
            if ev.type == EventType.ACTION_PROPOSED:      # ACTION_CANDIDATE no existe: lanzaba AttributeError
                tool = p.get("tool") or p.get("tool_name")
                args = p.get("arguments") or {}
                rationale = p.get("rationale") or p.get("description") or ""
                rec["action"] = ActionCandidate(
                    id=ev.node_id or rec["action_id"],
                    tool_call=ToolCall(tool_name=tool, arguments=args) if tool else None,
                    rationale=rationale,
                    description=rationale,
                )
            elif ev.type == EventType.PROVIDER_EVALUATED:
                name = p.get("provider_name", "LAYA")
                raw_score = p.get("score")
                score = float(raw_score) if (raw_score is not None and str(raw_score).strip() != "") else None
                verdict = p.get("verdict", "ALLOW")
                avail = bool(p.get("available", True))
                rec["providers"] = [ProviderEvaluationDTO(name=name, score=score, verdict=verdict, available=avail)]
            elif ev.type == EventType.RISK_ASSESSED:
                level = p.get("risk_level", "LOW")
                score = float(p.get("risk_score", 0.1))
                reasons = p.get("reasons", [])
                rec["risk"] = RiskDTO(level=level, score=score, reasons=reasons)
            elif ev.type == EventType.POLICY_DECIDED:
                rec["status"] = p.get("status", "ALLOW")
                rec["policy"] = PolicyDTO(
                    decision=p.get("status", "ALLOW"),
                    reason_codes=[p.get("reason_code", "POLICY_EVALUATED")],
                    requires_confirmation=p.get("requires_confirmation", False),
                )
            elif ev.type == EventType.CAPABILITY_ISSUED:
                rec["capability"] = p
                rec["status"] = "ALLOW"
                if "execution_mode" in p:
                    rec["execution_mode"] = p["execution_mode"]
                if "expires_at" in p and p["expires_at"]:
                    try:
                        rec["expires_at"] = datetime.fromisoformat(p["expires_at"])
                    except Exception:
                        logger.debug('Fecha de caducidad ilegible en una decisión guardada', exc_info=True)
            elif ev.type == EventType.APPROVAL_REQUESTED:
                rec["status"] = "REVIEW"
            elif ev.type == EventType.APPROVAL_COMPLETED:
                approved = p.get("approved", True)
                rec["status"] = "ALLOW" if approved else "BLOCKED"
            elif ev.type == EventType.DECISION_PRUNED:
                rec["status"] = "BLOCKED"
            elif ev.type == EventType.EXECUTION_COMPLETED:
                # Actualizar estado de ejecución en recibo
                rec["receipt"] = rec["receipt"].model_copy(
                    update={"is_executed": True, "execution_timestamp": ev.timestamp}
                )

        # Persistir las decisiones reconstruidas para acelerar futuros accesos
        for rec in decisions.values():
            self.save(rec)

        return decisions
