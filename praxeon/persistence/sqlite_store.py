"""Almacén de Persistencia Transaccional SQLite para PRAXEON (F4/F6).

Proporciona persistencia durable, ACID y versionada para:
1. Agentes y catálogo de definiciones (agents, agent_versions).
2. Trazabilidad inmutable de versiones con hash criptográfico SHA-256.
3. Gestión de cuarentena para definiciones corruptas o no deserializables.
4. Historial e instancias de ejecución de flujos de trabajo (workflow_executions).
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional
import uuid

from praxeon.agents.definition import AgentDefinition, AgentStatus
from praxeon.workflows.models import (
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowStatus,
)


class SqlitePersistenceStore:
    """Almacén relacional SQLite con soporte transaccional y cuarentena de entidades corruptas."""

    def __init__(self, db_path: Optional[str] = None):
        from praxeon.config import resolve_db_path
        if db_path == ":memory:":
            self.db_path = ":memory:"
        else:
            self.db_path = str(resolve_db_path("praxeon.db", db_path))
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        self._lock = threading.Lock()
        self._mem_conn: Optional[sqlite3.Connection] = None
        self._init_db()


    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._mem_conn.row_factory = sqlite3.Row
            return self._mem_conn

        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _close_conn(self, conn: sqlite3.Connection) -> None:
        if self.db_path != ":memory:":
            conn.close()

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    # 1. Catálogo actual de agentes
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS agents (
                            agent_id TEXT PRIMARY KEY,
                            name TEXT NOT NULL,
                            role TEXT NOT NULL,
                            version INTEGER NOT NULL,
                            status TEXT NOT NULL,
                            definition_hash TEXT NOT NULL,
                            definition_json TEXT NOT NULL,
                            is_quarantined INTEGER NOT NULL DEFAULT 0,
                            quarantine_reason TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        """
                    )
                    # 2. Historial inmutable de versiones de agentes
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS agent_versions (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            agent_id TEXT NOT NULL,
                            version INTEGER NOT NULL,
                            definition_hash TEXT NOT NULL,
                            definition_json TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            UNIQUE(agent_id, version)
                        );
                        """
                    )
                    # 3. Registro formal de ejecuciones de workflows
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS workflow_executions (
                            execution_id TEXT PRIMARY KEY,
                            workflow_id TEXT NOT NULL,
                            status TEXT NOT NULL,
                            execution_json TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            finished_at TEXT,
                            updated_at TEXT NOT NULL
                        );
                        """
                    )
                    # 4. Tabla de cuarentena para registros no deserializables o corruptos
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS quarantined_records (
                            record_id TEXT PRIMARY KEY,
                            entity_type TEXT NOT NULL,
                            entity_id TEXT,
                            raw_content TEXT NOT NULL,
                            error_message TEXT NOT NULL,
                            quarantined_at TEXT NOT NULL
                        );
                        """
                    )
                    # 5. Definiciones de workflows persistentes
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS workflows (
                            workflow_id TEXT PRIMARY KEY,
                            name TEXT NOT NULL,
                            description TEXT,
                            version INTEGER NOT NULL DEFAULT 1,
                            workflow_json TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        """
                    )
            finally:
                self._close_conn(conn)

    # =========================================================================
    # GESTIÓN TRANSACCIONAL DE AGENTES Y VERSIONADO
    # =========================================================================

    def save_agent(self, agent: AgentDefinition) -> None:
        """Persiste atómicamente un agente y registra su versión inmutable."""
        agent_dict = agent.to_dict()
        definition_json = json.dumps(agent_dict, ensure_ascii=False)
        now_str = datetime.now(timezone.utc).isoformat()
        created_at_str = agent.created_at.isoformat() if hasattr(agent.created_at, "isoformat") else str(agent.created_at)
        updated_at_str = agent.updated_at.isoformat() if hasattr(agent.updated_at, "isoformat") else str(agent.updated_at)

        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    # Upsert en agents
                    conn.execute(
                        """
                        INSERT INTO agents (
                            agent_id, name, role, version, status,
                            definition_hash, definition_json, is_quarantined,
                            quarantine_reason, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)
                        ON CONFLICT(agent_id) DO UPDATE SET
                            name = excluded.name,
                            role = excluded.role,
                            version = excluded.version,
                            status = excluded.status,
                            definition_hash = excluded.definition_hash,
                            definition_json = excluded.definition_json,
                            is_quarantined = 0,
                            quarantine_reason = NULL,
                            updated_at = excluded.updated_at;
                        """,
                        (
                            agent.agent_id,
                            agent.name,
                            agent.role,
                            agent.version,
                            agent.status.value if hasattr(agent.status, "value") else str(agent.status),
                            agent.definition_hash,
                            definition_json,
                            created_at_str,
                            updated_at_str,
                        ),
                    )

                    # Registrar versión inmutable en agent_versions si no existe
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO agent_versions (
                            agent_id, version, definition_hash, definition_json, created_at
                        ) VALUES (?, ?, ?, ?, ?);
                        """,
                        (
                            agent.agent_id,
                            agent.version,
                            agent.definition_hash,
                            definition_json,
                            now_str,
                        ),
                    )
            finally:
                self._close_conn(conn)

    def get_agent(self, agent_id: str) -> Optional[AgentDefinition]:
        """Recupera un agente. Si está corrupto, lo envía a cuarentena y retorna None."""
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute(
                    "SELECT * FROM agents WHERE agent_id = ?;", (agent_id,)
                ).fetchone()
                if not row:
                    return None

                if row["is_quarantined"]:
                    return None

                raw_json = row["definition_json"]
                try:
                    data = json.loads(raw_json)
                    if "status" in row.keys() and row["status"]:
                        data["status"] = row["status"]
                    return AgentDefinition.from_dict(data)
                except Exception as exc:
                    # Enviar a cuarentena inmediatamente
                    self._quarantine_locked(
                        conn=conn,
                        entity_type="agent",
                        entity_id=agent_id,
                        raw_content=raw_json,
                        error=f"Error al deserializar AgentDefinition: {exc}",
                    )
                    return None
            finally:
                self._close_conn(conn)

    def list_agents(
        self,
        status: Optional[AgentStatus] = None,
        role: Optional[str] = None,
        include_quarantined: bool = False,
    ) -> List[AgentDefinition]:
        """Lista agentes activos o filtrados con protección contra registros corruptos."""
        with self._lock:
            conn = self._get_connection()
            try:
                query = "SELECT * FROM agents WHERE 1=1"
                params: List[Any] = []
                if not include_quarantined:
                    query += " AND is_quarantined = 0"
                if status is not None:
                    query += " AND status = ?"
                    params.append(status.value if hasattr(status, "value") else str(status))
                if role is not None:
                    query += " AND LOWER(role) = LOWER(?)"
                    params.append(role)

                rows = conn.execute(query, params).fetchall()
                agents: List[AgentDefinition] = []
                for row in rows:
                    raw_json = row["definition_json"]
                    agent_id = row["agent_id"]
                    try:
                        data = json.loads(raw_json)
                        if "status" in row.keys() and row["status"]:
                            data["status"] = row["status"]
                        agents.append(AgentDefinition.from_dict(data))
                    except Exception as exc:
                        self._quarantine_locked(
                            conn=conn,
                            entity_type="agent",
                            entity_id=agent_id,
                            raw_content=raw_json,
                            error=f"Fallo de integridad/deserialización: {exc}",
                        )
                return agents
            finally:
                self._close_conn(conn)

    def delete_agent(self, agent_id: str, hard_delete: bool = False) -> bool:
        """Elimina un agente (soft-delete como TERMINATED o hard-delete de tabla agents)."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    row = conn.execute(
                        "SELECT * FROM agents WHERE agent_id = ?;", (agent_id,)
                    ).fetchone()
                    if not row:
                        return False

                    if hard_delete:
                        conn.execute("DELETE FROM agents WHERE agent_id = ?;", (agent_id,))
                    else:
                        now_str = datetime.now(timezone.utc).isoformat()
                        raw_json = row["definition_json"]
                        try:
                            data = json.loads(raw_json)
                            data["status"] = AgentStatus.TERMINATED.value
                            data["updated_at"] = now_str
                            new_json = json.dumps(data, ensure_ascii=False)
                        except Exception:
                            new_json = raw_json
                        conn.execute(
                            """
                            UPDATE agents
                            SET status = ?, definition_json = ?, updated_at = ?
                            WHERE agent_id = ?;
                            """,
                            (AgentStatus.TERMINATED.value, new_json, now_str, agent_id),
                        )
                    return True
            finally:
                self._close_conn(conn)

    def get_agent_versions(self, agent_id: str) -> List[Dict[str, Any]]:
        """Recupera el catálogo histórico inmutable de versiones de un agente."""
        with self._lock:
            conn = self._get_connection()
            try:
                rows = conn.execute(
                    """
                    SELECT id, agent_id, version, definition_hash, created_at
                    FROM agent_versions
                    WHERE agent_id = ?
                    ORDER BY version ASC;
                    """,
                    (agent_id,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                self._close_conn(conn)

    def get_agent_version(self, agent_id: str, version: int) -> Optional[AgentDefinition]:
        """Obtiene una versión histórica inmutable específica de un agente."""
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute(
                    """
                    SELECT definition_json FROM agent_versions
                    WHERE agent_id = ? AND version = ?;
                    """,
                    (agent_id, version),
                ).fetchone()
                if not row:
                    return None
                data = json.loads(row["definition_json"])
                return AgentDefinition.from_dict(data)
            finally:
                self._close_conn(conn)

    # =========================================================================
    # CUARENTENA Y DIAGNÓSTICO DE REGISTROS CORRUPTOS
    # =========================================================================

    def _quarantine_locked(
        self,
        conn: sqlite3.Connection,
        entity_type: str,
        entity_id: Optional[str],
        raw_content: str,
        error: str,
    ) -> str:
        record_id = f"quarantine_{uuid.uuid4().hex[:12]}"
        now_str = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """
                INSERT INTO quarantined_records (
                    record_id, entity_type, entity_id, raw_content, error_message, quarantined_at
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (record_id, entity_type, entity_id, raw_content, error, now_str),
            )
            if entity_type == "agent" and entity_id:
                conn.execute(
                    """
                    UPDATE agents
                    SET is_quarantined = 1,
                        status = ?,
                        quarantine_reason = ?,
                        updated_at = ?
                    WHERE agent_id = ?;
                    """,
                    (AgentStatus.QUARANTINED.value, error, now_str, entity_id),
                )
        return record_id

    def quarantine_record(
        self,
        entity_type: str,
        entity_id: Optional[str],
        raw_content: str,
        error: str,
    ) -> str:
        """Pone explícitamente un registro en cuarentena por fallo de integridad."""
        with self._lock:
            conn = self._get_connection()
            try:
                return self._quarantine_locked(conn, entity_type, entity_id, raw_content, error)
            finally:
                self._close_conn(conn)

    def list_quarantined(self, entity_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Obtiene la lista de registros en cuarentena."""
        with self._lock:
            conn = self._get_connection()
            try:
                if entity_type:
                    rows = conn.execute(
                        "SELECT * FROM quarantined_records WHERE entity_type = ? ORDER BY quarantined_at DESC;",
                        (entity_type,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM quarantined_records ORDER BY quarantined_at DESC;"
                    ).fetchall()
                return [dict(r) for r in rows]
            finally:
                self._close_conn(conn)

    def release_quarantine(self, record_id: str) -> bool:
        """Libera o descarta un registro de la cuarentena."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    row = conn.execute(
                        "SELECT * FROM quarantined_records WHERE record_id = ?;", (record_id,)
                    ).fetchone()
                    if not row:
                        return False
                    entity_type = row["entity_type"]
                    entity_id = row["entity_id"]
                    conn.execute("DELETE FROM quarantined_records WHERE record_id = ?;", (record_id,))
                    if entity_type == "agent" and entity_id:
                        now_str = datetime.now(timezone.utc).isoformat()
                        conn.execute(
                            """
                            UPDATE agents
                            SET is_quarantined = 0,
                                quarantine_reason = NULL,
                                status = ?,
                                updated_at = ?
                            WHERE agent_id = ?;
                            """,
                            (AgentStatus.INACTIVE.value, now_str, entity_id),
                        )
                    return True
            finally:
                self._close_conn(conn)

    # =========================================================================
    # PERSISTENCIA DE WORKFLOW EXECUTIONS
    # =========================================================================

    def save_workflow_execution(self, execution: WorkflowExecution) -> None:
        """Persiste transaccionalmente una instancia de WorkflowExecution."""
        exec_dict = execution.to_dict()
        exec_json = json.dumps(exec_dict, ensure_ascii=False)
        now_str = datetime.now(timezone.utc).isoformat()
        created_at_str = execution.created_at.isoformat() if hasattr(execution.created_at, "isoformat") else str(execution.created_at)
        finished_at_str = execution.finished_at.isoformat() if execution.finished_at else None

        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO workflow_executions (
                            execution_id, workflow_id, status, execution_json,
                            created_at, finished_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(execution_id) DO UPDATE SET
                            status = excluded.status,
                            execution_json = excluded.execution_json,
                            finished_at = excluded.finished_at,
                            updated_at = excluded.updated_at;
                        """,
                        (
                            execution.execution_id,
                            execution.workflow_id,
                            execution.status.value if hasattr(execution.status, "value") else str(execution.status),
                            exec_json,
                            created_at_str,
                            finished_at_str,
                            now_str,
                        ),
                    )
            finally:
                self._close_conn(conn)

    def get_workflow_execution(self, execution_id: str) -> Optional[WorkflowExecution]:
        """Obtiene una ejecución formal por su identificador único."""
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute(
                    "SELECT execution_json FROM workflow_executions WHERE execution_id = ?;",
                    (execution_id,),
                ).fetchone()
                if not row:
                    return None
                data = json.loads(row["execution_json"])
                return WorkflowExecution.from_dict(data)
            finally:
                self._close_conn(conn)

    def list_workflow_executions(
        self,
        workflow_id: Optional[str] = None,
        status: Optional[WorkflowStatus] = None,
    ) -> List[WorkflowExecution]:
        """Lista ejecuciones registradas aplicando filtros opcionales de workflow_id o estado."""
        with self._lock:
            conn = self._get_connection()
            try:
                query = "SELECT execution_json FROM workflow_executions WHERE 1=1"
                params: List[Any] = []
                if workflow_id:
                    query += " AND workflow_id = ?"
                    params.append(workflow_id)
                if status is not None:
                    query += " AND status = ?"
                    params.append(status.value if hasattr(status, "value") else str(status))
                query += " ORDER BY created_at DESC"

                rows = conn.execute(query, params).fetchall()
                results: List[WorkflowExecution] = []
                for row in rows:
                    data = json.loads(row["execution_json"])
                    results.append(WorkflowExecution.from_dict(data))
                return results
            finally:
                self._close_conn(conn)

    def delete_workflow_execution(self, execution_id: str) -> bool:
        """Elimina el registro de una ejecución."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    cur = conn.execute(
                        "DELETE FROM workflow_executions WHERE execution_id = ?;",
                        (execution_id,),
                    )
                    return cur.rowcount > 0
            finally:
                self._close_conn(conn)

    # =========================================================================
    # PERSISTENCIA DE WORKFLOW DEFINITIONS
    # =========================================================================

    def save_workflow(self, workflow: WorkflowDefinition) -> None:
        """Persiste transaccionalmente una definición de WorkflowDefinition."""
        wf_dict = workflow.to_dict()
        wf_json = json.dumps(wf_dict, ensure_ascii=False)
        now_str = datetime.now(timezone.utc).isoformat()
        created_at_str = (
            workflow.created_at.isoformat()
            if hasattr(workflow.created_at, "isoformat")
            else str(workflow.created_at)
        )
        updated_at_str = (
            workflow.updated_at.isoformat()
            if hasattr(workflow.updated_at, "isoformat")
            else str(workflow.updated_at)
        )

        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO workflows (
                            workflow_id, name, description, version, workflow_json,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(workflow_id) DO UPDATE SET
                            name = excluded.name,
                            description = excluded.description,
                            version = excluded.version,
                            workflow_json = excluded.workflow_json,
                            updated_at = excluded.updated_at;
                        """,
                        (
                            workflow.workflow_id,
                            workflow.name,
                            workflow.description,
                            workflow.version,
                            wf_json,
                            created_at_str,
                            updated_at_str or now_str,
                        ),
                    )
            finally:
                self._close_conn(conn)

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        """Obtiene una definición de workflow persistida."""
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute(
                    "SELECT workflow_json FROM workflows WHERE workflow_id = ?;",
                    (workflow_id,),
                ).fetchone()
                if not row:
                    return None
                data = json.loads(row["workflow_json"])
                return WorkflowDefinition.from_dict(data)
            finally:
                self._close_conn(conn)

    def list_workflows(self) -> List[WorkflowDefinition]:
        """Lista todas las definiciones de workflows persistidas."""
        with self._lock:
            conn = self._get_connection()
            try:
                rows = conn.execute(
                    "SELECT workflow_json FROM workflows ORDER BY updated_at DESC;"
                ).fetchall()
                results: List[WorkflowDefinition] = []
                for row in rows:
                    data = json.loads(row["workflow_json"])
                    results.append(WorkflowDefinition.from_dict(data))
                return results
            finally:
                self._close_conn(conn)

    def delete_workflow(self, workflow_id: str) -> bool:
        """Elimina una definición de workflow persistida."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    cur = conn.execute(
                        "DELETE FROM workflows WHERE workflow_id = ?;",
                        (workflow_id,),
                    )
                    return cur.rowcount > 0
            finally:
                self._close_conn(conn)
