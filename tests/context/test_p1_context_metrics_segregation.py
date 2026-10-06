"""Pruebas unitarias y de integración para la segregación de métricas de contexto (Estimadas vs Facturadas)."""

import pytest
from praxeon.context.manager import ContextManager
from praxeon.domain.models import ActionCandidate, Goal, ToolCall
from praxeon.runtime.state import SessionState
from praxeon.server.schemas.context import (
    ActualLLMTokenMetricsDTO,
    ContextMetricsDTO,
    EstimatedContextMetricsDTO,
)


def test_context_dto_schemas_and_aliases():
    """Valida los DTOs de métricas segregadas y la compatibilidad con alias históricos."""
    est_dto = EstimatedContextMetricsDTO(
        estimated_context_tokens_before=1200,
        estimated_context_tokens_after=400,
        estimated_context_tokens_saved=800,
        estimated_reduction_ratio=0.6667,
    )
    assert est_dto.estimated_context_tokens_before == 1200
    assert est_dto.estimated_context_tokens_saved == 800

    actual_dto = ActualLLMTokenMetricsDTO(
        actual_prompt_tokens=500,
        actual_cached_tokens=300,
        actual_output_tokens=150,
        actual_billed_tokens=350,
        cache_hit_status="prefix_hit",
    )
    assert actual_dto.actual_prompt_tokens == 500
    assert actual_dto.actual_cached_tokens == 300
    assert actual_dto.actual_billed_tokens == 350
    assert actual_dto.cache_hit_status == "prefix_hit"

    # ContextMetricsDTO con nombres canónicos nuevos
    unified_dto = ContextMetricsDTO(
        estimated_context_tokens_before=1200,
        estimated_context_tokens_after=400,
        estimated_context_tokens_saved=800,
        estimated_reduction_ratio=0.6667,
        actual_prompt_tokens=500,
        actual_cached_tokens=300,
        actual_output_tokens=150,
        actual_billed_tokens=350,
        cache_hit_status="prefix_hit",
    )
    assert unified_dto.estimated_context_tokens_saved == 800
    assert unified_dto.context_tokens_saved == 800
    assert unified_dto.context_tokens_before == 1200
    assert unified_dto.context_tokens_after == 400

    # ContextMetricsDTO deserializado desde diccionario con claves históricas
    legacy_data = {
        "context_tokens_before": 1000,
        "context_tokens_after": 300,
        "context_tokens_saved": 700,
        "context_reduction_ratio": 0.70,
        "actual_prompt_tokens": 400,
        "actual_cached_tokens": 200,
        "actual_output_tokens": 100,
        "actual_billed_tokens": 300,
    }
    from_legacy = ContextMetricsDTO.model_validate(legacy_data)
    assert from_legacy.estimated_context_tokens_saved == 700
    assert from_legacy.context_tokens_saved == 700


def test_context_manager_segregates_estimated_and_actual_tokens():
    """Verifica que ContextManager compute tokens estimados y registre tokens facturados reales."""
    manager = ContextManager(enabled=True)
    state = SessionState(session_id="sess_token_seg", goal=Goal(objective="Refactorizar módulo de contexto"))
    cand = ActionCandidate(
        id="act_01",
        description="Analizar dependencias",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "manager.py"}),
    )

    # 1. Miss inicial
    snap1, hit1 = manager.build(state, cand)
    assert hit1 is False

    # 2. Hit idéntico
    snap2, hit2 = manager.build(state, cand)
    assert hit2 is True

    # 3. Registrar consumo facturado real del LLM
    manager.record_actual_tokens(
        prompt_tokens=850,
        output_tokens=120,
        cached_tokens=500,
        cache_hit_status="hit",
    )

    metrics = manager.get_metrics()

    # Métricas estimadas
    assert "estimated_context_tokens_before" in metrics
    assert "estimated_context_tokens_after" in metrics
    assert "estimated_context_tokens_saved" in metrics
    assert metrics["estimated_context_tokens_saved"] > 0
    assert metrics["estimated_reduction_ratio"] >= 0.0

    # Métricas reales facturadas
    assert metrics["actual_prompt_tokens"] == 850
    assert metrics["actual_cached_tokens"] == 500
    assert metrics["actual_output_tokens"] == 120
    assert metrics["actual_billed_tokens"] == 470  # (850 - 500) + 120
    assert metrics["cache_hit_status"] == "hit"

    # Retrocompatibilidad transparente
    assert metrics["context_tokens_saved"] == metrics["estimated_context_tokens_saved"]
    assert metrics["context_tokens_before"] == metrics["estimated_context_tokens_before"]
    assert metrics["context_tokens_after"] == metrics["estimated_context_tokens_after"]
    assert metrics["tokens_saved"] == metrics["estimated_context_tokens_saved"]


def test_session_context_stats_endpoint_integration(tmp_path, monkeypatch):
    """Verifica que la API REST exponga las métricas segregadas al cliente web."""
    from fastapi.testclient import TestClient
    from praxeon.server.app import create_app
    from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service

    VALID_KEY = "test_key_ctx_seg_123"
    monkeypatch.setenv("PRAXEON_SECRET_KEY", VALID_KEY)
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_KEY)

    service = RuntimeApplicationService(db_dir=str(tmp_path / "ctx_seg_cache"))
    service.context_manager.record_actual_tokens(
        prompt_tokens=1000,
        output_tokens=250,
        cached_tokens=600,
        cache_hit_status="prefix_hit",
    )
    set_runtime_service(service)

    app = create_app()
    client = TestClient(app)
    auth_headers = {"x-api-key": VALID_KEY}

    session_id = "sess_ctx_stats_test"
    client.post(
        "/v1/sessions",
        headers=auth_headers,
        json={"goal": "Verificar endpoint de estadísticas de contexto", "session_id": session_id},
    )

    res = client.get(f"/v1/sessions/{session_id}/context-stats", headers=auth_headers)
    assert res.status_code == 200
    resp_data = res.json()
    data = resp_data.get("data", resp_data)
    ctx_metrics = data["context_metrics"]

    assert ctx_metrics["actual_prompt_tokens"] == 1000
    assert ctx_metrics["actual_cached_tokens"] == 600
    assert ctx_metrics["actual_output_tokens"] == 250
    assert ctx_metrics["actual_billed_tokens"] == 650
    assert ctx_metrics["cache_hit_status"] == "prefix_hit"
    assert "estimated_context_tokens_saved" in ctx_metrics
    assert "context_tokens_saved" in ctx_metrics
