"""Raíz de composición y perfil de seguridad sin estado global (auditoría A-03, AUD-24)."""

import logging

import pytest
from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.server.bootstrap import build_components, build_runtime_service, resolve_security_profile
from praxeon.server.dependencies import RuntimeApplicationService


@pytest.fixture
def service(tmp_path):
    return build_runtime_service(db_dir=str(tmp_path / "a"))


def test_the_profile_comes_from_the_argument_or_the_environment(monkeypatch):
    monkeypatch.delenv("PRAXEON_PROFILE", raising=False)
    monkeypatch.delenv("PRAXEON_ENV", raising=False)
    assert resolve_security_profile() == "dev"
    monkeypatch.setenv("PRAXEON_ENV", "Production ")
    assert resolve_security_profile() == "production"
    assert resolve_security_profile("dev") == "dev"


def test_the_service_receives_its_collaborators_from_the_composition_root(tmp_path):
    components = build_components(db_dir=str(tmp_path), security_profile="dev")
    service = RuntimeApplicationService(components=components)
    assert service.policy_engine is components.policy_engine and service.executor is components.executor
    assert service.executor.registry is service.policy_engine.registry   # la política y el ejecutor comparten registro
    assert service.security_profile == "dev" and service.db_dir == str(tmp_path)


def test_each_app_answers_with_its_own_service_and_profile(service, tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXEON_CORS_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("PRAXEON_SECRET_KEY", "k" * 40)
    other = build_runtime_service(db_dir=str(tmp_path / "b"), security_profile="production")
    dev_app = create_app(profile="dev", service=service)
    prod_app = create_app(profile="production", service=other)
    # antes el perfil era una global: crear la segunda app cambiaba también el de la primera
    assert (dev_app.state.service, prod_app.state.service) == (service, other)
    assert (service.security_profile, other.security_profile) == ("dev", "production")
    assert TestClient(dev_app).get("/v1/agents").status_code == 200


def _push_awaiting_confirmation(service, tmp_path):
    from praxeon.server.schemas.action import ProposeActionRequest

    service.create_session(goal="publicar", session_id="s1", workspace_root=str(tmp_path))
    resp = service.propose_action("s1", ProposeActionRequest(tool="run_command", arguments={"command": "git push origin main"}))
    assert resp.status == "REVIEW"
    return resp.decision_id


def test_internal_confirmations_use_the_profile_of_the_service(service, tmp_path):
    service.security_profile = "production"
    with pytest.raises(PermissionError):
        service.confirm_decision(decision_id=_push_awaiting_confirmation(service, tmp_path), approved=True, reason="x")


def test_in_dev_an_internal_confirmation_does_not_need_an_operator_token(service, tmp_path):
    assert service.security_profile == "dev"
    result = service.confirm_decision(decision_id=_push_awaiting_confirmation(service, tmp_path), approved=True, reason="x")
    assert result.status == "ALLOW"


def test_running_without_authentication_is_warned_once_per_app(service, caplog):
    client = TestClient(create_app(profile="dev", service=service))
    with caplog.at_level(logging.WARNING, logger="praxeon.server.dependencies"):
        client.get("/v1/agents")
        client.get("/v1/agents")
    assert sum("without API key authentication" in r.getMessage() for r in caplog.records) == 1


def test_the_old_profile_setter_still_works_but_warns(service, monkeypatch):
    from praxeon.server import auth_deps, dependencies

    monkeypatch.setattr(dependencies, "_runtime_service_instance", service)
    with pytest.warns(DeprecationWarning, match="v2.0.0"):
        auth_deps.set_active_security_profile("production")
    assert service.security_profile == "production" and auth_deps.get_active_security_profile() == "production"
