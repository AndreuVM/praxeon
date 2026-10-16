"""Full Access autorizado (doble confirmación) no pide confirmación por paso; BLOCK/REPLAN siguen siendo innegociables."""

import pytest
from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service
from praxeon.server.schemas.action import ProposeActionRequest

import sys

OUTSIDE = "C:/Windows/win.ini" if sys.platform == "win32" else "/etc/hostname"

REVIEW_CASES = [
    ("read_file", {"path": OUTSIDE}),                               # lectura fuera del workspace
    ("run_command", {"command": "git push origin main"}),           # mutación remota
    ("run_command", {"command": "algo-desconocido --flag"}),       # operación no clasificada
]


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for key in ("PRAXEON_API_KEY", "PRAXEON_SECRET_KEY", "PRAXEON_PROFILE", "PRAXEON_ENV", "PRAXEON_REQUIRE_AUTH"):
        monkeypatch.delenv(key, raising=False)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


def _session(runtime, tmp_path, sid, mode="full_access", **meta):
    runtime.create_session(goal="tarea", session_id=sid, execution_mode=mode, workspace_root=str(tmp_path), metadata=meta)


def _propose(runtime, sid, i, tool, args):
    return runtime.propose_action(
        session_id=sid,
        proposal=ProposeActionRequest(action_id=f"a{i}", tool=tool, operation=f"{i}. {tool}", arguments=args, thought_rationale="x"),
    )


@pytest.mark.parametrize("tool,args", REVIEW_CASES)
def test_authorized_full_access_allows_without_step_confirmation(runtime, tmp_path, tool, args):
    _session(runtime, tmp_path, "fa-ok", full_access_authorized_by_operator=True)  # sin "autonomous"
    resp = _propose(runtime, "fa-ok", 1, tool, args)
    assert resp.status == "ALLOW"
    assert resp.policy.requires_confirmation is False
    assert "FULL_ACCESS_OPERATOR_PREAUTHORIZED" in resp.policy.reason_codes


@pytest.mark.parametrize("tool,args", REVIEW_CASES)
def test_unauthorized_full_access_still_asks(runtime, tmp_path, tool, args):
    _session(runtime, tmp_path, "fa-no")
    assert _propose(runtime, "fa-no", 1, tool, args).status == "REVIEW"


@pytest.mark.parametrize("tool,args", REVIEW_CASES)
def test_authorization_flag_has_no_effect_outside_full_access(runtime, tmp_path, tool, args):
    _session(runtime, tmp_path, "lr", mode="local_restricted", full_access_authorized_by_operator=True)
    assert _propose(runtime, "lr", 1, tool, args).status == "REVIEW"


def test_step_confirmation_is_an_explicit_opt_in(runtime, tmp_path):
    _session(runtime, tmp_path, "fa-step", full_access_authorized_by_operator=True, require_step_confirmation=True)
    assert _propose(runtime, "fa-step", 1, "run_command", {"command": "git push origin main"}).status == "REVIEW"


def test_block_stays_unconditional_in_authorized_full_access(runtime, tmp_path):
    _session(runtime, tmp_path, "fa-block", full_access_authorized_by_operator=True)
    resp = _propose(runtime, "fa-block", 1, "run_command", {"command": "rm -rf /"})
    assert resp.status == "BLOCK"


def test_api_requires_the_double_confirmation_flag(runtime, tmp_path):
    client = TestClient(create_app(profile="dev"))
    base = {"goal": "g", "execution_mode": "full_access", "llm_provider": "simulator", "max_steps": 1,
            "step_delay_ms": 200, "workspace_root": str(tmp_path)}
    unconfirmed = client.post("/v1/sessions/run", json={**base, "session_id": "api-no"}).json()["data"]["session_id"]
    confirmed = client.post("/v1/sessions/run", json={**base, "session_id": "api-yes", "full_access_confirmed": True}).json()["data"]["session_id"]
    meta_no = runtime._sessions_meta[unconfirmed]["metadata"]
    meta_yes = runtime._sessions_meta[confirmed]["metadata"]
    assert meta_no["full_access_authorized_by_operator"] is False
    assert meta_yes["full_access_authorized_by_operator"] is True
    # La confirmación solo vale para full_access
    other = client.post("/v1/sessions/run", json={**base, "execution_mode": "local_restricted", "session_id": "api-lr",
                                                   "full_access_confirmed": True}).json()["data"]["session_id"]
    assert runtime._sessions_meta[other]["metadata"]["full_access_authorized_by_operator"] is False
