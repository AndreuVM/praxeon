"""Test suite de verificación para Fase 0 (P0-01 a P0-04).

Valida:
1. P0-01: Restauración física real y formal en Rollback (SessionService y RuntimeApplicationService).
2. P0-02: Contrato unificado de CheckpointStore en InMemoryStateStore y SqliteStateStore.
3. P0-03: Erradicación del provider global mutable y aislamiento por SessionRuntime.
4. P0-04: Concurrencia e inmunidad contra contaminación cruzada entre misiones heterogéneas.
"""

from concurrent.futures import ThreadPoolExecutor
import os
import tempfile
import threading
from typing import Any, Dict, List
import pytest

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.decision import DecisionStatus, PolicyDecision
from praxeon.domain.events import EventType
from praxeon.domain.goal import Goal
from praxeon.domain.models import Checkpoint
from praxeon.providers.laya import LayaProvider
from praxeon.providers.mock import MockProvider
from praxeon.providers.replay import ReplayProvider
from praxeon.runtime.event_bus import EventBus, EventStore
from praxeon.runtime.session_runtime import SessionRuntime
from praxeon.runtime.state import SessionState, StepRecord
from praxeon.runtime.state_store import InMemoryStateStore, SqliteStateStore
from praxeon.server.dependencies import RuntimeApplicationService
from praxeon.server.schemas.action import ProposeActionRequest


def test_p0_02_checkpoint_store_contract_in_memory():
    """Valida el contrato unificado de CheckpointStore en InMemoryStateStore."""
    store = InMemoryStateStore()
    state = SessionState(session_id="sess_mem_1", goal=Goal(objective="Test in memory checkpoint"))
    state.add_step(
        action=ActionCandidate(id="act_1", description="Leer archivo a", tool_call=ToolCall(tool_name="read_file", arguments={"path": "a.txt"})),
        decision=PolicyDecision(status=DecisionStatus.ALLOW),
    )
    store.save_state(state)

    # 1. create_checkpoint
    chk = store.create_checkpoint(session_id="sess_mem_1", label="Paso 1 completado")
    assert chk.id.startswith("chk_sess_mem_1_")
    assert chk.session_id == "sess_mem_1"
    assert chk.step_index == 1
    assert "snapshot_data" in chk.model_dump()

    # 2. get_checkpoint y get alias
    assert store.get_checkpoint(chk.id) == chk
    assert store.get(chk.id) == chk

    # 3. get_latest_checkpoint
    assert store.get_latest_checkpoint("sess_mem_1").id == chk.id

    # 4. list_checkpoints y list alias
    assert len(store.list_checkpoints("sess_mem_1")) == 1
    assert len(store.list("sess_mem_1")) == 1

    # 5. Modificar estado y luego restaurar
    state.add_step(
        action=ActionCandidate(id="act_2", description="Eliminar raiz", tool_call=ToolCall(tool_name="rm_rf", arguments={"target": "/"})),
        decision=PolicyDecision(status=DecisionStatus.ALLOW),
    )

    store.save_state(state)
    assert len(store.load_state("sess_mem_1").steps) == 2

    restored = store.restore_checkpoint(
        session_id="sess_mem_1",
        checkpoint_id=chk.id,
        culprit_tool="rm_rf",
        reason="Peligro crítico detectado",
    )
    assert restored is not None
    assert len(restored.steps) == 1
    assert "rm_rf" in restored.forbidden_tools
    # Verificar persistencia en el store
    loaded_after = store.load_state("sess_mem_1")
    assert len(loaded_after.steps) == 1
    assert "rm_rf" in loaded_after.forbidden_tools


def test_p0_02_checkpoint_store_contract_sqlite():
    """Valida el contrato unificado de CheckpointStore en SqliteStateStore persistente."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_state.db")
        store = SqliteStateStore(db_path=db_path)
        state = SessionState(session_id="sess_sql_1", goal=Goal(objective="Test sqlite checkpoint"))
        state.add_step(
            action=ActionCandidate(id="act_1", description="Listar archivos", tool_call=ToolCall(tool_name="list_files", arguments={})),
            decision=PolicyDecision(status=DecisionStatus.ALLOW),
        )
        store.save_state(state)

        # 1. create_checkpoint
        chk = store.create_checkpoint(session_id="sess_sql_1", label="Paso 1 SQLite")
        assert chk.session_id == "sess_sql_1"
        assert chk.step_index == 1

        # 2. get & get_latest
        retrieved = store.get(chk.id)
        assert retrieved is not None
        assert retrieved.id == chk.id
        assert store.get_latest_checkpoint("sess_sql_1").id == chk.id

        # 3. Mutar estado y restaurar
        state.add_step(
            action=ActionCandidate(id="act_2", description="Borrar todo", tool_call=ToolCall(tool_name="delete_all", arguments={})),
            decision=PolicyDecision(status=DecisionStatus.ALLOW),
        )

        store.save_state(state)
        assert len(store.load_state("sess_sql_1").steps) == 2

        # 4. restore_checkpoint
        restored = store.restore_checkpoint(
            session_id="sess_sql_1",
            checkpoint_id=chk.id,
            culprit_tool="delete_all",
        )
        assert restored is not None
        assert len(restored.steps) == 1
        assert "delete_all" in restored.forbidden_tools

        # Verificar persistencia reiniciando el store SQLite desde disco
        store2 = SqliteStateStore(db_path=db_path)
        persisted_state = store2.load_state("sess_sql_1")
        assert len(persisted_state.steps) == 1
        assert "delete_all" in persisted_state.forbidden_tools


def test_p0_01_real_physical_rollback_in_runtime_service():
    """Valida que rollback_session restaure físicamente el estado y no solo emita un evento."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        service = RuntimeApplicationService(db_dir=tmp_dir)

        # 1. Crear sesión: debe generar automáticamente un checkpoint génesis
        sess = service.create_session(goal="Desarrollar feature con checkpoints", session_id="sess_rb_test")
        sid = sess["session_id"]

        chks_initial = service.state_store.list_checkpoints(sid)
        assert len(chks_initial) >= 1
        genesis_chk_id = chks_initial[0].id

        # 2. Simular ejecución de pasos
        st = service.state_store.load_state(sid)
        st.add_step(
            action=ActionCandidate(id="act_1", description="Escribir archivo main", tool_call=ToolCall(tool_name="write_file", arguments={"path": "main.py"})),
            decision=PolicyDecision(status=DecisionStatus.ALLOW),
        )
        service.state_store.save_state(st)
        chk_safe = service.create_checkpoint(session_id=sid, label="Paso 1 seguro")
        safe_chk_id = chk_safe["id"]

        # Añadir paso degenerativo
        st = service.state_store.load_state(sid)
        st.add_step(
            action=ActionCandidate(id="act_2", description="Herramienta corrupta", tool_call=ToolCall(tool_name="corrupt_tool", arguments={})),
            decision=PolicyDecision(status=DecisionStatus.ALLOW),
        )

        service.state_store.save_state(st)
        assert len(service.state_store.load_state(sid).steps) == 2

        # 3. Ejecutar rollback hacia el checkpoint seguro
        res = service.rollback_session(
            session_id=sid,
            checkpoint_id=safe_chk_id,
            culprit_tool="corrupt_tool",
            reason="Corrupción de datos detectada",
        )
        assert res["status"] == "RolledBack"
        assert res["checkpoint_id"] == safe_chk_id
        assert res["steps_count"] == 1
        assert "corrupt_tool" in res["forbidden_tools"]

        # 4. Verificar que el estado persistido fue restaurado REALMENTE
        st_after = service.state_store.load_state(sid)
        assert len(st_after.steps) == 1
        assert st_after.steps[0].action.tool_call.tool_name == "write_file"
        assert "corrupt_tool" in st_after.forbidden_tools

        # 5. Verificar eventos en el EventBus
        events = service.event_bus.get_all_events(sid)
        rb_events = [e for e in events if e.type == EventType.SESSION_ROLLBACK]
        interv_events = [e for e in events if e.type == EventType.INTERVENTION_APPLIED]
        assert len(rb_events) >= 1
        assert len(interv_events) >= 1
        assert rb_events[-1].payload["culprit_tool"] == "corrupt_tool"
        assert rb_events[-1].payload["checkpoint_id"] == safe_chk_id


def test_p0_03_provider_isolation_and_no_global_mutation():
    """Valida que RuntimeApplicationService aísle los proveedores de decisión por sesión."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        service = RuntimeApplicationService(db_dir=tmp_dir)

        # Crear sesión 1 con LAYA
        s1 = service.create_session(
            goal="Tarea con LAYA",
            session_id="sess_laya",
            metadata={"supervisor": "laya"},
        )
        prov1 = service.get_session_provider("sess_laya")
        assert isinstance(prov1, LayaProvider)

        # Crear sesión 2 con Replay
        s2 = service.create_session(
            goal="Tarea con Replay",
            session_id="sess_replay",
            metadata={"supervisor": "replay"},
        )
        prov2 = service.get_session_provider("sess_replay")
        assert isinstance(prov2, ReplayProvider)

        # Crear sesión 3 con Mock
        s3 = service.create_session(
            goal="Tarea con Mock",
            session_id="sess_mock",
            metadata={"supervisor": "mock"},
        )
        prov3 = service.get_session_provider("sess_mock")
        assert isinstance(prov3, MockProvider)

        # Verificar que consultar de nuevo sesión 1 NO ha sido contaminado por las sesiones 2 y 3
        prov1_recheck = service.get_session_provider("sess_laya")
        assert isinstance(prov1_recheck, LayaProvider)
        assert prov1_recheck is prov1
        assert prov1 is not prov2
        assert prov2 is not prov3


def test_p0_04_concurrent_heterogeneous_missions_no_cross_contamination():
    """Valida la ejecución y evaluación concurrente de misiones heterogéneas sin contaminación cruzada."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        service = RuntimeApplicationService(db_dir=tmp_dir)

        # Sesión A: LAYA
        service.create_session(goal="Misión A Laya", session_id="mission_A", metadata={"supervisor": "laya"})
        # Sesión B: Mock
        service.create_session(goal="Misión B Mock", session_id="mission_B", metadata={"supervisor": "mock"})

        results: Dict[str, List[str]] = {"mission_A": [], "mission_B": []}
        lock = threading.Lock()

        def run_step_for_mission(sid: str, step_num: int):
            prop = ProposeActionRequest(
                tool="read_file",
                arguments={"path": f"file_{sid}_{step_num}.txt"},
                thought_rationale=f"Leyendo archivo para {sid} en paso {step_num}",
            )
            resp = service.propose_action(session_id=sid, proposal=prop)
            with lock:
                results[sid].append(resp.providers[0].name.lower())


        # Ejecutar 20 propuestas concurrentes alternadas en ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = []
            for i in range(10):
                futures.append(executor.submit(run_step_for_mission, "mission_A", i))
                futures.append(executor.submit(run_step_for_mission, "mission_B", i))
            for f in futures:
                f.result()

        # Verificar resultados:
        # Todos los pasos de mission_A deben haber sido evaluados exclusivamente por Laya
        assert len(results["mission_A"]) == 10
        assert all("laya" in p for p in results["mission_A"]), f"Contaminación en mission_A: {results['mission_A']}"

        # Todos los pasos de mission_B deben haber sido evaluados exclusivamente por Mock
        assert len(results["mission_B"]) == 10
        assert all("mock" in p for p in results["mission_B"]), f"Contaminación en mission_B: {results['mission_B']}"
