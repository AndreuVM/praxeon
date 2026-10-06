"""Test suite para [P1-PERSISTENCIA] Persistencia transaccional de AgentRegistry y WorkflowExecution en SQLite.

Verifica:
1. Operaciones atómicas de guardado, consulta, listado y borrado de agentes en SqlitePersistenceStore.
2. Versionado inmutable de AgentDefinition con hashes criptográficos SHA-256 y recuperación histórica.
3. Detección y gestión de cuarentena (QUARANTINED) ante corrupción de datos sin silenciar fallos.
4. Integración completa de AgentRegistry con SqlitePersistenceStore y captura de cuarentena en disco.
5. Persistencia transaccional y recuperación fidedigna de WorkflowExecution (incluyendo temporal tracking).
"""

from datetime import datetime, timezone
import json
import sqlite3
import pytest

from praxeon.agents.definition import (
    AgentContextPolicy,
    AgentDefinition,
    AgentStatus,
    ModelConfig,
    RiskProfile,
)
from praxeon.agents.registry import AgentRegistry
from praxeon.domain.models import RiskLevel
from praxeon.persistence.sqlite_store import SqlitePersistenceStore
from praxeon.workflows.models import (
    NodeStatus,
    WorkflowExecution,
    WorkflowStatus,
)


def sample_agent(agent_id: str = "ag_test_01", name: str = "Test Agent", version: int = 1) -> AgentDefinition:
    return AgentDefinition(
        agent_id=agent_id,
        name=name,
        role="Developer",
        system_prompt="You are a reliable testing agent.",
        version=version,
        status=AgentStatus.ACTIVE,
        model=ModelConfig(provider="mock", model_name="mock-model", temperature=0.0),
        risk_profile=RiskProfile(max_risk_level=RiskLevel.LOW),
        context_policy=AgentContextPolicy(max_input_tokens=2048),
        capabilities=["code_generation", "unit_testing"],
        skills=["python"],
    )


def test_sqlite_agent_atomic_crud(tmp_path):
    """Valida guardado atómico, consulta, listado filtrado y soft/hard delete en SQLite."""
    db_file = tmp_path / "praxeon_test.db"
    store = SqlitePersistenceStore(db_path=str(db_file))

    agent1 = sample_agent("ag_01", "Agent Alpha")
    agent2 = sample_agent("ag_02", "Agent Beta")

    store.save_agent(agent1)
    store.save_agent(agent2)

    # Consulta por ID
    loaded = store.get_agent("ag_01")
    assert loaded is not None
    assert loaded.agent_id == "ag_01"
    assert loaded.name == "Agent Alpha"
    assert loaded.definition_hash == agent1.definition_hash

    # Listado con filtros
    all_agents = store.list_agents()
    assert len(all_agents) == 2

    # Soft-delete
    ok_soft = store.delete_agent("ag_01", hard_delete=False)
    assert ok_soft is True
    updated = store.get_agent("ag_01")
    assert updated is not None
    assert updated.status == AgentStatus.TERMINATED

    # Hard-delete
    ok_hard = store.delete_agent("ag_02", hard_delete=True)
    assert ok_hard is True
    assert store.get_agent("ag_02") is None


def test_sqlite_agent_versioning_and_immutability(tmp_path):
    """Valida el historial inmutable de versiones y la recuperación histórica exacta."""
    store = SqlitePersistenceStore(":memory:")

    v1 = sample_agent("ag_versioned", "Versioned Agent", version=1)
    store.save_agent(v1)
    hash_v1 = v1.definition_hash

    # Crear versión 2 con cambios en prompt y capacidades
    v2_dict = v1.model_dump()
    v2_dict["version"] = 2
    v2_dict["system_prompt"] = "Updated prompt for v2"
    v2_dict["capabilities"] = ["code_generation", "unit_testing", "security_audit"]
    v2 = AgentDefinition(**v2_dict)
    store.save_agent(v2)
    hash_v2 = v2.definition_hash

    assert hash_v1 != hash_v2

    # El agente activo debe ser v2
    active = store.get_agent("ag_versioned")
    assert active.version == 2
    assert active.definition_hash == hash_v2

    # Verificar historial de versiones
    history = store.get_agent_versions("ag_versioned")
    assert len(history) == 2
    assert history[0]["version"] == 1
    assert history[0]["definition_hash"] == hash_v1
    assert history[1]["version"] == 2
    assert history[1]["definition_hash"] == hash_v2

    # Recuperar versiones históricas inmutables
    recovered_v1 = store.get_agent_version("ag_versioned", version=1)
    assert recovered_v1 is not None
    assert recovered_v1.version == 1
    assert recovered_v1.system_prompt == "You are a reliable testing agent."

    recovered_v2 = store.get_agent_version("ag_versioned", version=2)
    assert recovered_v2 is not None
    assert recovered_v2.version == 2
    assert recovered_v2.system_prompt == "Updated prompt for v2"


def test_quarantine_mechanism_for_corrupted_agents(tmp_path):
    """Verifica que un registro corrupto en SQLite no se silencie y sea aislado en cuarentena."""
    db_file = tmp_path / "quarantine_test.db"
    store = SqlitePersistenceStore(db_path=str(db_file))

    agent = sample_agent("ag_corrupt", "Corrupted Candidate")
    store.save_agent(agent)

    # Inyectar corrupción intencional en la columna definition_json
    conn = sqlite3.connect(str(db_file))
    conn.execute("UPDATE agents SET definition_json = '{INVALID_JSON_PAYLOAD' WHERE agent_id = 'ag_corrupt';")
    conn.commit()
    conn.close()

    # Intentar obtener el agente corrupto
    res = store.get_agent("ag_corrupt")
    assert res is None  # No rompe la app, sino que lo pone en cuarentena

    # Verificar aislamiento en tabla quarantined_records
    quarantined = store.list_quarantined(entity_type="agent")
    assert len(quarantined) == 1
    assert quarantined[0]["entity_id"] == "ag_corrupt"
    assert "INVALID_JSON_PAYLOAD" in quarantined[0]["raw_content"]
    assert "Error al deserializar AgentDefinition" in quarantined[0]["error_message"]

    # Verificar que el agente quedó marcado como QUARANTINED en la tabla agents
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM agents WHERE agent_id = 'ag_corrupt';").fetchone()
    conn.close()
    assert row["is_quarantined"] == 1
    assert row["status"] == AgentStatus.QUARANTINED.value

    # Liberación de cuarentena
    record_id = quarantined[0]["record_id"]
    released = store.release_quarantine(record_id)
    assert released is True
    assert len(store.list_quarantined()) == 0


def test_agent_registry_sqlite_and_quarantine_integration(tmp_path):
    """Valida la integración de AgentRegistry con SQLite y la captura de cuarentena en ficheros."""
    store = SqlitePersistenceStore(":memory:")
    registry = AgentRegistry(store=store)

    ag = sample_agent("ag_reg_01", "Registry Agent")
    registry.register(ag)

    # Consultas y actualización en registry
    assert registry.get("ag_reg_01") is not None
    updated = registry.update("ag_reg_01", {"name": "Registry Agent Updated"})
    assert updated.version == 2
    assert updated.name == "Registry Agent Updated"

    # Verificar persistencia en store subyacente
    from_store = store.get_agent("ag_reg_01")
    assert from_store is not None
    assert from_store.version == 2
    assert from_store.name == "Registry Agent Updated"

    # Historial a través de registry
    hist = registry.get_agent_history("ag_reg_01")
    assert len(hist) == 2

    # Poner en cuarentena manual
    registry.quarantine_agent("ag_reg_01", "Auditoría de seguridad reportó payload anómalo")
    q_list = registry.get_quarantined_agents()
    assert len(q_list) == 1
    assert q_list[0]["entity_id"] == "ag_reg_01"
    assert "Auditoría de seguridad reportó payload anómalo" in q_list[0]["error_message"]


def test_agent_registry_storage_dir_corrupted_quarantine(tmp_path):
    """Valida que un fichero JSON corrupto en storage_dir se envíe a cuarentena en lugar de ser silenciado."""
    storage_dir = tmp_path / "agents_storage"
    storage_dir.mkdir(parents=True)

    # Fichero válido
    valid_agent = sample_agent("ag_good", "Good Agent")
    (storage_dir / "ag_good.json").write_text(json.dumps(valid_agent.to_dict()), encoding="utf-8")

    # Fichero corrupto
    (storage_dir / "ag_broken.json").write_text("NOT_JSON_DATA", encoding="utf-8")

    registry = AgentRegistry(storage_dir=str(storage_dir))
    assert registry.get("ag_good") is not None
    assert registry.get("ag_broken") is None

    # Verificar que el fichero roto fue capturado en cuarentena y NO silenciado
    quarantined = registry.get_quarantined_agents()
    assert len(quarantined) == 1
    assert quarantined[0]["entity_id"] == "ag_broken"
    assert quarantined[0]["raw_content"] == "NOT_JSON_DATA"


def test_workflow_execution_persistence(tmp_path):
    """Valida la persistencia transaccional y restauración de WorkflowExecution."""
    store = SqlitePersistenceStore(":memory:")

    t_start = datetime(2026, 10, 5, 10, 0, 0, tzinfo=timezone.utc)
    t_retry = datetime(2026, 10, 5, 10, 0, 15, tzinfo=timezone.utc)

    execution = WorkflowExecution(
        execution_id="exec_persisted_01",
        workflow_id="wf_data_pipeline",
        status=WorkflowStatus.RUNNING,
        node_states={"start": NodeStatus.COMPLETED, "task_1": NodeStatus.RETRYING},
        node_outputs={"start": {"ok": True}},
        node_retries={"task_1": 1},
        node_started_at={"start": t_start, "task_1": t_start},
        node_retry_after={"task_1": t_retry},
        variables={"env": "production", "batch_size": 100},
        execution_history=["start"],
        started_at=t_start,
    )

    store.save_workflow_execution(execution)

    loaded = store.get_workflow_execution("exec_persisted_01")
    assert loaded is not None
    assert loaded.execution_id == "exec_persisted_01"
    assert loaded.workflow_id == "wf_data_pipeline"
    assert loaded.status == WorkflowStatus.RUNNING
    assert loaded.node_states["task_1"] == NodeStatus.RETRYING
    assert loaded.node_retry_after["task_1"] == t_retry
    assert loaded.variables["batch_size"] == 100

    # Listado
    executions = store.list_workflow_executions(workflow_id="wf_data_pipeline")
    assert len(executions) == 1
    assert executions[0].execution_id == "exec_persisted_01"

    # Eliminación
    ok = store.delete_workflow_execution("exec_persisted_01")
    assert ok is True
    assert store.get_workflow_execution("exec_persisted_01") is None
