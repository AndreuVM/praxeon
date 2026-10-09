"""Tests de Fase 2: UI, Observabilidad, Contexto Único y Resiliencia de Misiones.

Valida:
- UI-02: Endpoint /v1/providers y telemetría observada ("N/A" por defecto).
- CTX-01: ContextManager como única autoridad de ensamblado de prompt con compresión semántica y supervisión.
- REC-01: Persistencia SQLite de misiones y reconciliación de misiones interrumpidas.
"""

import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from praxeon.context.manager import ContextManager
from praxeon.runtime.state_store import SqliteStateStore, InMemoryStateStore
from praxeon.server.dependencies import RuntimeApplicationService
from praxeon.server.app import create_app


def test_context_manager_assemble_mission_prompt_empty_history():
    cm = ContextManager()
    prompt = cm.assemble_mission_prompt(
        goal="Diseñar arquitectura de microservicios",
        system_prompt="Eres un arquitecto experto.",
        steps_history=[],
    )
    assert len(prompt) >= 1
    assert prompt[0]["role"] == "user"
    assert "Diseñar arquitectura de microservicios" in prompt[0]["content"]
    assert "finish" in prompt[0]["content"]


def test_context_manager_assemble_mission_prompt_compression_and_supervision():
    cm = ContextManager()
    steps = [
        {
            "step": i,
            "thought": f"Pensamiento paso {i}",
            "tool": "read_file" if i % 2 == 0 else "run_command",
            "arguments": {"path": f"src/file_{i}.py"} if i % 2 == 0 else {"command": f"echo {i}"},
            "verdict": "BLOCK" if i == 3 else ("REPLAN" if i == 6 else "ALLOW"),
            "reason_code": "VETO_OPERACIONAL" if i == 3 else ("LOOP_DETECTED" if i == 6 else ""),
            "observation": f"Salida exitosa o veto del paso {i}",
            "success": i not in (3, 6),
        }
        for i in range(1, 10)  # 9 pasos
    ]

    # Preservar los últimos 4 pasos
    prompt = cm.assemble_mission_prompt(
        goal="Auditar vulnerabilidades",
        steps_history=steps,
        preserve_recent_steps=4,
    )

    content_full = "\n".join(m["content"] for m in prompt)

    # Debe contener el resumen semántico comprimido para los pasos 1 a 5
    assert "[RESUMEN SEMÁNTICO DE PASOS ANTERIORES 1-5 COMPRIMIDO POR CONTEXT_MANAGER]" in content_full
    assert "Veredicto Supervisor: BLOCK" in content_full  # Paso 3 estaba en los comprimidos

    # Debe contener con fidelidad causal completa los pasos recientes (6 a 9)
    assert "Thought: Pensamiento paso 9" in content_full
    assert "Thought: Pensamiento paso 8" in content_full
    assert "Thought: Pensamiento paso 7" in content_full
    assert "Thought: Pensamiento paso 6" in content_full


def test_sqlite_mission_persistence_and_reconciliation():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_state.db")
        store = SqliteStateStore(db_path=db_path)

        # 1. Guardar misiones
        store.save_mission({
            "session_id": "sess_1",
            "goal": "Tarea 1",
            "status": "running",
            "current_step": 3,
            "max_steps": 10,
            "provider": "simulator",
            "decision_model": "laya",
        })
        store.save_mission({
            "session_id": "sess_2",
            "goal": "Tarea 2",
            "status": "completed",
            "current_step": 5,
            "max_steps": 5,
            "provider": "groq",
            "decision_model": "typesafe",
        })

        # 2. Cargar misión
        m1 = store.load_mission("sess_1")
        assert m1 is not None
        assert m1["goal"] == "Tarea 1"
        assert m1["status"] == "running"
        assert m1["current_step"] == 3
        assert m1["decision_model"] == "laya"

        # 3. Listar misiones
        active_list = store.list_missions(status="running")
        assert len(active_list) == 1
        assert active_list[0]["session_id"] == "sess_1"

        # 4. Reconciliación de misiones interrumpidas
        reconciled = store.reconcile_interrupted_missions(reason="Reinicio de proceso")
        assert len(reconciled) == 1
        assert reconciled[0]["session_id"] == "sess_1"
        assert reconciled[0]["status"] == "Interrupted_Recovered"
        assert reconciled[0]["reconciliation_reason"] == "Reinicio de proceso"

        # 5. Comprobar que ya no hay ninguna en estado running
        assert len(store.list_missions(status="running")) == 0
        m1_updated = store.load_mission("sess_1")
        assert m1_updated["status"] == "Interrupted_Recovered"


def test_in_memory_mission_persistence():
    store = InMemoryStateStore()
    store.save_mission({"session_id": "mem_1", "goal": "En memoria", "status": "running"})
    m = store.load_mission("mem_1")
    assert m is not None
    assert m["goal"] == "En memoria"

    reconciled = store.reconcile_interrupted_missions(reason="Prueba memoria")
    assert len(reconciled) == 1
    assert reconciled[0]["status"] == "Interrupted_Recovered"
    assert reconciled[0]["reconciliation_reason"] == "Prueba memoria"


def test_runtime_service_reconciles_interrupted_missions_on_startup():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "state.db")
        initial_store = SqliteStateStore(db_path=db_path)
        initial_store.save_mission({
            "session_id": "sess_crash",
            "goal": "Misión interrumpida por caída de energía",
            "status": "running",
            "current_step": 7,
            "max_steps": 20,
        })

        # Inicializar RuntimeApplicationService con esa base de datos
        service = RuntimeApplicationService(state_store=initial_store, db_dir=tmpdir)

        # La misión debe haber sido reconciliada automáticamente a 'Interrupted_Recovered'
        crashed = initial_store.load_mission("sess_crash")
        assert crashed is not None
        assert crashed["status"] == "Interrupted_Recovered"
        assert "interrupted_at" in crashed


def test_get_providers_endpoint():
    with tempfile.TemporaryDirectory() as tmpdir:
        service = RuntimeApplicationService(db_dir=tmpdir)
        app = create_app(service=service)
        client = TestClient(app)

        # Con autenticación simulada (verify_api_key permite si PRAXEON_API_KEY no está seteada o coincide)
        headers = {"X-API-Key": "test"} if os.environ.get("PRAXEON_API_KEY") else {}
        resp = client.get("/v1/providers", headers=headers)
        assert resp.status_code == 200

        payload = resp.json()
        assert payload.get("success") is True
        data = payload.get("data", payload)
        assert "decision_providers" in data
        assert "llm_providers" in data

        dec_providers = data["decision_providers"]
        provider_ids = [p["provider_id"] for p in dec_providers]
        assert "laya" in provider_ids
        assert "typesafe" in provider_ids

        # Verificar que la latencia inicial reporta "N/A" si no hay mediciones
        for p in dec_providers:
            assert "average_latency_display" in p
            assert p["average_latency_display"] in ("N/A", f"{p.get('average_latency_ms')} ms")

        llm_providers = data["llm_providers"]
        llm_ids = [l["provider_id"] for l in llm_providers]
        assert "ollama" in llm_ids
        assert "simulator" in llm_ids
