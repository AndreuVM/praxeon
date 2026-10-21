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
