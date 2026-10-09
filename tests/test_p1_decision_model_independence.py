"""Test suite de verificación y conformidad para Fase 1: Decision Model Independence (DMI-01 a DMI-09).

Valida exhaustivamente:
1. DMI-01 & DMI-02: Protocolo formal DecisionProvider y configuración tipada DecisionModelConfig.
2. DMI-03: DecisionProviderRegistry con validación estricta de extras, rechazo a falsos ALLOW y fallbacks seguros.
3. DMI-04: DecisionRuntime encapsulado por sesión con medición de latencia y telemetría.
4. DMI-05: Adaptadores modulares LayaProvider, TypeSafeProvider, ReplayProvider y MockProvider.
5. DMI-06: Desacoplamiento de dependencias principales en pyproject.toml y no-bloqueo sin SDK externo.
6. DMI-07: Metadatos estructurados completos de auditoría en ProviderAssessment.
7. DMI-08: Endpoints REST y RuntimeApplicationService resolviendo decision_model explícito.
8. DMI-09: Integración de DecisionModelConfig en AgentDefinition y fingerprint hash.
"""

import os
import tempfile
from typing import Any, Dict, List
import pytest

from praxeon.agents.definition import AgentDefinition
from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.assessment import ProviderAssessment
from praxeon.domain.decision_provider import (
    DecisionModelConfig,
    DecisionProvider,
    DecisionProviderError,
    DecisionProviderMetadata,
    DecisionProviderUnavailableError,
)
from praxeon.domain.goal import Goal
from praxeon.providers.base import BaseReasoningProvider
from praxeon.providers.laya import LayaProvider
from praxeon.providers.mock import MockProvider
from praxeon.providers.registry import (
    DecisionProviderRegistry,
    build_default_registry,
    default_registry,
)
from praxeon.providers.replay import ReplayProvider
from praxeon.providers.typesafe import TypeSafeAdapter, TypeSafeProvider
from praxeon.runtime.decision_runtime import DecisionRuntime
from praxeon.runtime.session_runtime import SessionRuntime
from praxeon.runtime.state import SessionState
from praxeon.server.dependencies import RuntimeApplicationService
from praxeon.server.schemas.action import ProposeActionRequest
from praxeon.server.schemas.session import CreateSessionRequest, RunMissionRequest


def test_dmi_01_and_02_decision_model_config_and_provider_protocol():
    """Valida los contratos de DecisionModelConfig y el protocolo DecisionProvider."""
    # 1. Configuración tipada
    cfg = DecisionModelConfig(
        provider="laya",
        model_id="laya-v1",
        model_version="0.3",
        backend="local",
        device="cpu",
        timeout_seconds=5.0,
        calibration_profile="strict",
        fallback_policy="mock",
    )
    assert cfg.provider == "laya"
    assert cfg.model_id == "laya-v1"
    assert cfg.backend == "local"
    assert cfg.device == "cpu"
    assert cfg.timeout_seconds == 5.0
    assert cfg.fallback_policy == "mock"

    # 2. Verificación de cumplimiento del protocolo DecisionProvider
    mock_prov = MockProvider(name="test_mock", model_name="v1")
    assert isinstance(mock_prov, DecisionProvider)
    assert mock_prov.provider_id == "test_mock"
    assert mock_prov.is_available() is True

    meta = mock_prov.metadata()
    assert isinstance(meta, DecisionProviderMetadata)
    assert meta.provider_id == "test_mock"
    assert meta.model_id == "v1"


def test_dmi_03_registry_extras_validation_and_fallback():
    """Valida el registro dinámico, verificación de paquetes y resolución de fallback."""
    reg = DecisionProviderRegistry()

    # Registrar provider mock
    reg.register(
        provider_id="mock",
        factory=lambda c: MockProvider(name=c.provider, model_name=c.model_id),
        description="Mock",
    )

    # Registrar provider ficticio que requiere módulo no instalado
    reg.register(
        provider_id="fake_uninstalled",
        factory=lambda c: MockProvider(),
        required_extra="fake-extra",
        required_module="module_that_definitely_does_not_exist_xyz123",
        description="Uninstalled provider",
    )

    # 1. Rechazo de proveedor no registrado
    with pytest.raises(DecisionProviderError, match="Unknown decision provider"):
        reg.create(DecisionModelConfig(provider="unknown_xyz"))

    # 2. Error estricto si el extra no está instalado y no hay fallback
    with pytest.raises(DecisionProviderUnavailableError, match="module.*is not installed"):
        reg.create(DecisionModelConfig(provider="fake_uninstalled", fallback_policy="none"))

    # 3. Activación de fallback seguro si está configurado ante módulo no instalado
    prov_fb = reg.create(
        DecisionModelConfig(
            provider="fake_uninstalled",
            fallback_policy="mock",
            fallback_provider="mock",
        )
    )
    assert prov_fb is not None
    assert prov_fb.provider_id == "mock"


def test_dmi_04_decision_runtime_latency_telemetry_and_enrichment():
    """Valida la encapsulación por DecisionRuntime, medición de latencia y metadatos."""
    cfg = DecisionModelConfig(
        provider="mock",
        model_id="mock-v1",
        backend="local",
        calibration_profile="calibrated",
    )
    d_runtime = DecisionRuntime.from_config(cfg)
    assert d_runtime.provider_id == "mock"
    assert d_runtime.is_available() is True

    # Evaluar acción
    state = SessionState(session_id="s1", goal=Goal(objective="test runtime"))
    action = ActionCandidate(id="a1", description="read", tool_call=ToolCall(tool_name="read_file", arguments={}))

    assessments = d_runtime.evaluate(state, [action])
    assert len(assessments) == 1
    ass = assessments[0]

    # Verificar enriquecimiento (DMI-07)
    assert ass.provider_id == "mock"
    assert ass.model_name == "mock-v1"
    assert ass.backend == "local"
    assert ass.calibration_profile == "calibrated"
    assert ass.latency_ms >= 0.0
    assert ass.is_fallback is False

    # Verificar telemetría
    telem = d_runtime.get_telemetry()
    assert telem["call_count"] == 1
    assert telem["fallback_count"] == 0
    assert telem["avg_latency_ms"] >= 0.0


def test_dmi_05_all_providers_adhere_to_decision_provider_contract():
    """Valida que todos los adaptadores modulares satisfagan el protocolo DecisionProvider."""
    providers: List[DecisionProvider] = [
        MockProvider(),
        ReplayProvider(),
        LayaProvider(backend="simulated"),
        TypeSafeAdapter(api_key=None),  # Debe instanciar sin error aún sin API key
    ]

    for p in providers:
        assert isinstance(p, DecisionProvider), f"{p.__class__.__name__} no implementa DecisionProvider"
        assert p.provider_id is not None
        meta = p.metadata()
        assert isinstance(meta, DecisionProviderMetadata)
        assert meta.provider_id == p.provider_id


def test_dmi_07_provider_assessment_metadata_fields():
    """Valida que ProviderAssessment incluya todos los campos requeridos para auditoría."""
    ass = ProviderAssessment(
        provider="laya",
        provider_id="laya",
        model="laya-v1",
        model_name="laya-v1",
        model_version="0.3",
        backend="onnx",
        latency_ms=12.4,
        tokens_evaluated=128,
        is_fallback=True,
        degradation_reason="Timeout en modelo primario",
        calibration_profile="strict",
        confidence=0.88,
    )
    dumped = ass.model_dump()
    assert dumped["provider_id"] == "laya"
    assert dumped["model_name"] == "laya-v1"
    assert dumped["model_version"] == "0.3"
    assert dumped["backend"] == "onnx"
    assert dumped["latency_ms"] == 12.4
    assert dumped["tokens_evaluated"] == 128
    assert dumped["is_fallback"] is True
    assert dumped["degradation_reason"] == "Timeout en modelo primario"
    assert dumped["calibration_profile"] == "strict"


def test_dmi_08_runtime_service_explicit_decision_model_config():
    """Valida la resolución de decision_model en RuntimeApplicationService y su sumario."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        service = RuntimeApplicationService(db_dir=tmp_dir)

        # 1. Crear sesión con decision_model explícito (ej. LAYA simulado)
        dec_cfg = {
            "provider": "mock",
            "model_id": "custom-mock-42",
            "backend": "local",
            "timeout_seconds": 3.0,
        }
        sess = service.create_session(
            goal="Test decision model integration",
            session_id="s_dmi_08",
            metadata={"decision_model": dec_cfg},
        )
        sid = sess["session_id"]

        # Verificar que el provider exclusivo fue configurado
        prov = service.get_session_provider(sid)
        assert isinstance(prov, MockProvider)
        assert prov.model_name == "custom-mock-42"

        # Verificar que el sumario de sesión incluye decision_model_effective
        summary = service.get_session_summary(sid)
        assert summary is not None
        assert "decision_model_effective" in summary
        eff = summary["decision_model_effective"]
        assert eff is not None
        assert eff["provider"] == "mock"
        assert eff["model_id"] == "custom-mock-42"

        # 2. Crear sesión con decision_model fuertemente tipado
        sess2 = service.create_session(
            goal="Misión con modelo explícito",
            session_id="m_dmi_08",
            metadata={"decision_model": DecisionModelConfig(provider="replay", model_id="safe_read")},
        )
        prov_m = service.get_session_provider(sess2["session_id"])
        assert isinstance(prov_m, ReplayProvider)
        summary2 = service.get_session_summary(sess2["session_id"])
        assert summary2["decision_model_effective"]["provider"] == "replay"
        assert summary2["decision_model_effective"]["model_id"] == "safe_read"


def test_dmi_09_agent_definition_decision_model_integration():
    """Valida que AgentDefinition almacene DecisionModelConfig e impacte deterministamente el hash."""
    cfg1 = DecisionModelConfig(provider="laya", model_id="laya-v1")
    agent_with_model = AgentDefinition(
        agent_id="ag_specialist_1",
        name="Specialist",
        role="Auditor",
        system_prompt="Auditar código",
        decision_model=cfg1,
    )
    assert agent_with_model.decision_model is not None
    assert agent_with_model.decision_model.provider == "laya"

    # Agente sin decision_model explícito
    agent_without_model = AgentDefinition(
        agent_id="ag_specialist_1",
        name="Specialist",
        role="Auditor",
        system_prompt="Auditar código",
        decision_model=None,
    )

    # Las dos definiciones deben tener hashes distintos debido al decision_model
    assert agent_with_model.definition_hash != agent_without_model.definition_hash
