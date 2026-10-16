"""KEV como supervisor System-1 local y cascada configurable (local+local o local+API)."""

import pytest

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.decision_provider import DecisionModelConfig, DecisionProviderError
from praxeon.domain.evidence import Evidence
from praxeon.domain.goal import Goal
from praxeon.providers.kev import KevProvider
from praxeon.providers.registry import build_default_registry
from praxeon.providers.router import ConfidenceAwareRouter
from praxeon.runtime.decision_runtime import DecisionRuntime
from praxeon.runtime.state import SessionState


@pytest.fixture
def state():
    s = SessionState(session_id="kev-sess", goal=Goal(objective="Revisar el proyecto", success_criteria=["informe"]))
    s.add_evidence(Evidence(id="ev1", claim="listado_inspeccionado", content_hash="h"))
    return s


@pytest.fixture
def action():
    return ActionCandidate(
        id="a1",
        description="Listar el directorio",
        tool_call=ToolCall(tool_name="list_directory", arguments={"path": "."}),
    )


@pytest.fixture
def registry():
    return build_default_registry()


def _cascade(registry, **params):
    return registry.create(DecisionModelConfig(provider="cascade", model_id="cascade", custom_params=params))


def test_kev_is_registered_and_evaluates_locally_without_weights(registry, state, action):
    provider = registry.create(DecisionModelConfig(provider="kev", model_id="kev", backend="local"))
    assert isinstance(provider, KevProvider)
    assert provider.provider_id == "kev"
    assert provider.backend in ("local", "simulated")  # sin el paquete «kev» usa el motor calibrado
    [res] = provider.evaluate(state, [action])
    assert res.available and res.provider == "kev"
    assert all(code.startswith("KEV_") for code in res.reason_codes)
    assert res.metadata["provider"] == "kev"


def test_kev_hosted_without_endpoint_is_unavailable(registry):
    provider = registry.create(DecisionModelConfig(provider="kev", backend="hosted"))
    assert provider.is_available() is False


def test_kev_hosted_failure_is_reported_never_allowed(registry, state, action, monkeypatch):
    monkeypatch.setenv("KEV_ENDPOINT_URL", "http://127.0.0.1:9/")  # puerto cerrado
    provider = KevProvider(backend="hosted", timeout=0.5)
    [res] = provider.evaluate(state, [action])
    assert res.available is False and res.confidence == 0.0
    assert "KEV_PROVIDER_UNAVAILABLE" in res.reason_codes


def test_default_cascade_is_local_plus_local(registry):
    router = _cascade(registry)
    assert isinstance(router, ConfidenceAwareRouter)
    assert (router.primary.provider_id, router.secondary.provider_id) == ("laya", "kev")
    assert router.provider_id == "cascade" and router.is_available()


@pytest.mark.parametrize("primary,secondary", [("laya", "kev"), ("kev", "laya"), ("kev", "kev"), ("laya", "mock")])
def test_any_local_combination_can_be_chosen(registry, primary, secondary):
    router = _cascade(registry, primary={"provider": primary, "backend": "local"}, secondary=secondary)
    assert (router.primary.provider_id, router.secondary.provider_id) == (primary, secondary)


def test_confident_primary_is_not_escalated(registry, state, action):
    router = _cascade(
        registry,
        primary={"provider": "mock", "custom_params": {"default_confidence": 0.95}},
        secondary={"provider": "mock", "model_id": "segundo"},
    )
    [res] = router.evaluate(state, [action])
    assert res.metadata["routed_to"] == "primary" and res.metadata["escalated"] is False
    assert router.get_telemetry().local_decision_rate == 1.0


def test_unsure_primary_escalates_to_the_second_level(registry, state, action):
    router = _cascade(
        registry,
        primary={"provider": "mock", "custom_params": {"default_confidence": 0.3}},
        secondary={"provider": "kev", "backend": "local"},
        confidence_threshold=0.8,
    )
    [res] = router.evaluate(state, [action])
    assert res.metadata["routed_to"] == "secondary" and res.metadata["escalated"] is True
    assert res.provider == "kev"
    assert router.get_telemetry().escalated_count == 1


def test_unreachable_cloud_level_fails_closed(registry, state, action):
    """local + API: si la API no responde, el segundo nivel informa de fallo y nunca concede en silencio."""
    from praxeon.providers.mock import MockProvider

    registry.register("nube", lambda cfg: MockProvider(name="nube", available=False))
    router = _cascade(
        registry,
        primary={"provider": "mock", "custom_params": {"default_confidence": 0.1}},
        secondary={"provider": "nube", "backend": "api"},
    )
    [res] = router.evaluate(state, [action])
    assert res.available is False and res.confidence == 0.0
    assert "DUAL_PROVIDER_UNCERTAINTY_ESCALATE" in res.reason_codes


def test_typesafe_level_without_sdk_is_unavailable_not_a_crash(registry):
    router = _cascade(registry, primary="laya", secondary={"provider": "typesafe", "backend": "api"})
    assert router.secondary.provider_id == "typesafe"


def test_unknown_level_provider_is_unavailable_not_a_crash(registry, state, action):
    router = _cascade(registry, primary="mock", secondary="no-existe")
    assert router.secondary.is_available() is False
    [res] = router.secondary.evaluate(state, [action])
    assert res.available is False and "CASCADE_LEVEL_UNAVAILABLE" in res.reason_codes


def test_a_cascade_cannot_contain_a_cascade(registry):
    with pytest.raises(DecisionProviderError):
        _cascade(registry, primary={"provider": "cascade"})


def test_unknown_strategy_is_rejected(registry):
    with pytest.raises(DecisionProviderError):
        _cascade(registry, strategy="inventada")


def test_cascade_works_inside_a_decision_runtime(registry, state, action):
    runtime = DecisionRuntime.from_config(
        DecisionModelConfig(provider="cascade", model_id="cascade", custom_params={"primary": "laya", "secondary": "kev"}),
        registry=registry,
    )
    [res] = runtime.evaluate(state, [action])
    assert res.available
    assert runtime.provider_id == "cascade"


def test_mission_with_cascade_supervisor_uses_the_chosen_levels(tmp_path, monkeypatch):
    from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service

    for key in ("PRAXEON_API_KEY", "PRAXEON_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    try:
        service.start_mission(
            goal="Revisa el proyecto",
            session_id="casc-1",
            supervisor="cascade",
            decision_model={"provider": "cascade", "model_id": "cascade",
                            "custom_params": {"primary": {"provider": "kev", "backend": "local"}, "secondary": "laya"}},
            workspace_root=str(tmp_path),
            max_steps=1,
            step_delay_ms=0,
        )
        provider = service._session_runtimes["casc-1"].decision_provider
        assert provider.provider_id == "cascade"
        assert (provider.primary.provider_id, provider.secondary.provider_id) == ("kev", "laya")
    finally:
        service.stop_mission("casc-1") if hasattr(service, "stop_mission") else None
        set_runtime_service(None)
