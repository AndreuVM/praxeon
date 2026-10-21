"""La UI aprueba con la clave maestra que ya envía en X-API-Key/Bearer; no debe exigir un `operator_token` aparte."""

from praxeon.server.routes.decisions import _operator_token


class _Req:
    def __init__(self, headers):
        self.headers = headers


def test_body_token_wins():
    assert _operator_token("body", _Req({"x-api-key": "k"})) == "body"


def test_falls_back_to_authenticated_credential():
    assert _operator_token(None, _Req({"x-api-key": "k"})) == "k"
    assert _operator_token(None, _Req({"authorization": "Bearer k2"})) == "k2"
    assert _operator_token(None, _Req({"x-operator-token": "op", "x-api-key": "k"})) == "op"


def test_no_credential():
    assert _operator_token(None, _Req({})) is None
    assert _operator_token(None, None) is None



def test_master_api_key_confirms_when_signing_secret_differs(tmp_path, monkeypatch):
    """Modo escritorio: PRAXEON_SECRET_KEY es efímera y distinta de la clave maestra que envía la UI."""
    from praxeon.server.dependencies import RuntimeApplicationService
    from praxeon.server.schemas.action import ProposeActionRequest

    monkeypatch.setenv("PRAXEON_API_KEY", "master-key-1234567890")
    monkeypatch.setenv("PRAXEON_SECRET_KEY", "ephemeral-signing-secret-xyz")
    monkeypatch.delenv("PRAXEON_OPERATOR_KEY", raising=False)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "cache"))
    service.create_session(goal="g", session_id="s_master")
    dec = service.propose_action(session_id="s_master", proposal=ProposeActionRequest(
        tool="run_command", arguments={"command": "git push origin main"}, thought_rationale="x"))
    assert dec.status == "REVIEW"
    ok = service.confirm_decision(decision_id=dec.decision_id, approved=True,
                                  operator_token="master-key-1234567890", security_profile="production")
    assert ok.status == "ALLOW"
