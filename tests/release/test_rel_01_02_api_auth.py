"""Pruebas de aceptación formal de Release: REL-01 (API Auth E2E) y REL-02 (Production Security Config).

Conforme al documento de cierre 'PRAXEON v1.0.0 — Plan formal de cierre de versión antes de integrar Context Caching':
- REL-01: Probar E2E todos los endpoints sensibles con API key ausente, incorrecta y correcta.
- REL-02: Verificar que producción rechaza secret débil/default, CORS wildcard, y que un bind no-loopback exige autenticación.
- Criterio: 0 rutas sensibles accesibles sin la autorización exigida por el perfil de seguridad.
"""

import os
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from praxeon.server.app import create_app, validate_network_binding, validate_security_profile
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    set_active_security_profile,
    set_runtime_service,
)


VALID_RELEASE_KEY = "praxeon_v1_0_release_testing_api_key_secure_42"


@pytest.fixture(autouse=True)
def reset_security_context():
    """Limpia el perfil activo global antes y después de cada prueba."""
    set_active_security_profile(None)
    yield
    set_active_security_profile(None)


@pytest.fixture
def rel_auth_runtime(tmp_path, monkeypatch):
    """Configura un entorno de pruebas con autenticación estricta activa."""
    monkeypatch.setenv("PRAXEON_REQUIRE_AUTH", "true")
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_RELEASE_KEY)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "rel_auth_cache"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


# -----------------------------------------------------------------------------
# Matriz de endpoints sensibles para REL-01
# -----------------------------------------------------------------------------
SENSITIVE_ROUTES = [
    # 1. Mutaciones y control de misiones y sesiones
    ("POST", "/v1/sessions", {"goal": "Mission goal"}),
    ("POST", "/v1/sessions/run", {"goal": "Mission goal", "max_steps": 1}),
    ("POST", "/v1/sessions/sess_rel/actions", {"tool": "read_file", "arguments": {"path": "test.txt"}}),
    ("POST", "/v1/sessions/sess_rel/pause", {}),
    ("POST", "/v1/sessions/sess_rel/resume", {}),
    ("POST", "/v1/sessions/sess_rel/stop", {}),
    ("POST", "/v1/sessions/sess_rel/rollback", {"checkpoint_id": "chk_0"}),
    ("DELETE", "/v1/sessions/sess_rel", None),
    ("POST", "/v1/sessions/sess_rel/delete", None),
    ("DELETE", "/v1/sessions", None),
    ("POST", "/v1/sessions/clear", None),

    # 2. Consultas de estado, decisiones y eventos
    ("GET", "/v1/sessions", None),
    ("GET", "/v1/sessions/sess_rel", None),
    ("GET", "/v1/sessions/sess_rel/snapshot", None),
    ("GET", "/v1/sessions/sess_rel/decisions", None),
    ("GET", "/v1/sessions/sess_rel/events", None),
    ("GET", "/v1/decisions", None),
    ("GET", "/v1/decisions/dec_rel", None),

    # 3. Operaciones de decisión humana y ejecución física
    ("POST", "/v1/decisions/dec_rel/confirm", {"approved": True, "reason": "Operator release test"}),
    ("POST", "/v1/decisions/dec_rel/reject", {"reason": "Operator release reject"}),
    ("POST", "/v1/decisions/dec_rel/execute", {"execution_mode": "local_restricted"}),

    # 4. Endpoints privilegiados de telemetría y contexto de sistema
    ("GET", "/v1/metrics", None),
    ("GET", "/v1/context", None),
]


# =============================================================================
# REL-01: API Auth E2E
# =============================================================================

@pytest.mark.parametrize("method,endpoint,payload", SENSITIVE_ROUTES)
def test_rel_01_sensitive_endpoint_rejects_missing_auth(rel_auth_runtime, method, endpoint, payload):
    """REL-01: Toda ruta sensible rechaza con HTTP 401 Unauthorized ante petición sin credenciales."""
    app = create_app()
    client = TestClient(app)

    kwargs = {"json": payload} if payload is not None else {}
    response = client.request(method, endpoint, **kwargs)

    assert response.status_code == 401, (
        f"[REL-01 VIOLATION] Endpoint sensible {method} {endpoint} permitió acceso o no emitió 401 sin token. "
        f"Código obtenido: {response.status_code}, Body: {response.text}"
    )
    assert "Autenticación requerida" in response.text or "WWW-Authenticate" in response.headers


@pytest.mark.parametrize("method,endpoint,payload", SENSITIVE_ROUTES)
def test_rel_01_sensitive_endpoint_rejects_invalid_token(rel_auth_runtime, method, endpoint, payload):
    """REL-01: Toda ruta sensible rechaza con HTTP 401 Unauthorized ante tokens inválidos (X-API-Key y Bearer)."""
    app = create_app()
    client = TestClient(app)

    kwargs = {"json": payload} if payload is not None else {}

    # Prueba con X-API-Key incorrecta
    res_x = client.request(method, endpoint, headers={"X-API-Key": "forged_invalid_api_token"}, **kwargs)
    assert res_x.status_code == 401, (
        f"[REL-01 VIOLATION] Endpoint {method} {endpoint} no rechazó con 401 una X-API-Key forjada."
    )

    # Prueba con Authorization Bearer incorrecto
    res_bearer = client.request(method, endpoint, headers={"Authorization": "Bearer forged_bearer_token"}, **kwargs)
    assert res_bearer.status_code == 401, (
        f"[REL-01 VIOLATION] Endpoint {method} {endpoint} no rechazó con 401 un Bearer forjado."
    )


@pytest.mark.parametrize("method,endpoint,payload", SENSITIVE_ROUTES)
def test_rel_01_sensitive_endpoint_accepts_valid_credentials(rel_auth_runtime, method, endpoint, payload):
    """REL-01: Toda ruta sensible supera la barrera de autenticación con token válido (código distinto de 401)."""
    app = create_app()
    client = TestClient(app)

    kwargs = {"json": payload} if payload is not None else {}

    # Verificación con X-API-Key válida
    res_x = client.request(method, endpoint, headers={"X-API-Key": VALID_RELEASE_KEY}, **kwargs)
    assert res_x.status_code != 401, (
        f"[REL-01 VIOLATION] Endpoint {method} {endpoint} rechazó con 401 credenciales válidas en X-API-Key: {res_x.text}"
    )

    # Verificación con Authorization: Bearer válido
    res_b = client.request(method, endpoint, headers={"Authorization": f"Bearer {VALID_RELEASE_KEY}"}, **kwargs)
    assert res_b.status_code != 401, (
        f"[REL-01 VIOLATION] Endpoint {method} {endpoint} rechazó con 401 credenciales válidas en Bearer: {res_b.text}"
    )


def test_rel_01_websocket_auth_handshake(rel_auth_runtime):
    """REL-01: El canal de streaming WebSocket exige autenticación estricta y rechaza accesos anónimos."""
    app = create_app()
    client = TestClient(app)

    # 1. Sin token -> rechazado con código 1008
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v1/sessions/sess_ws_rel/stream"):
            pass
    assert exc.value.code == 1008

    # 2. Token inválido -> rechazado con código 1008
    with pytest.raises(WebSocketDisconnect) as exc_bad:
        with client.websocket_connect("/v1/sessions/sess_ws_rel/stream?token=invalid_token"):
            pass
    assert exc_bad.value.code == 1008

    # 3. Token válido -> conexión establecida exitosamente
    with client.websocket_connect(f"/v1/sessions/sess_ws_rel/stream?token={VALID_RELEASE_KEY}") as ws:
        msg = ws.receive_json()
        assert msg["action"] == "connected"
        assert msg["session_id"] == "sess_ws_rel"


def test_rel_01_public_healthcheck_accessible(rel_auth_runtime):
    """REL-01: El endpoint de liveness /v1/health permanece disponible sin requerir autenticación."""
    app = create_app()
    client = TestClient(app)

    res = client.get("/v1/health")
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "healthy"


def test_rel_01_dev_mode_permissive_with_logging(tmp_path, monkeypatch, caplog):
    """REL-01: En modo de desarrollo sobre loopback sin claves, se permite el acceso pero emite advertencia de seguridad."""
    monkeypatch.delenv("PRAXEON_PROFILE", raising=False)
    monkeypatch.delenv("PRAXEON_ENV", raising=False)
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("PRAXEON_REQUIRE_AUTH", raising=False)

    import praxeon.server.dependencies as deps
    deps._warned_dev_auth = False

    service = RuntimeApplicationService(db_dir=str(tmp_path / "dev_cache"))
    set_runtime_service(service)

    try:
        app = create_app()
        client = TestClient(app)

        with caplog.at_level("WARNING"):
            res = client.get("/v1/sessions")
            assert res.status_code == 200
            assert res.json()["success"] is True

        # Verificar que se registró la advertencia operacional de modo desarrollo
        assert any("running without API key" in record.message for record in caplog.records)
    finally:
        set_runtime_service(None)


# =============================================================================
# REL-02: Production Security Config
# =============================================================================

def test_rel_02_production_rejects_empty_secret(monkeypatch):
    """REL-02: En perfil production, la ausencia de PRAXEON_SECRET_KEY detiene el arranque con ValueError."""
    monkeypatch.setenv("PRAXEON_PROFILE", "production")
    monkeypatch.delenv("PRAXEON_SECRET_KEY", raising=False)
    monkeypatch.setenv("PRAXEON_CORS_ORIGINS", "https://supervisor.praxeon.internal")

    with pytest.raises(ValueError, match="PRAXEON_SECRET_KEY"):
        validate_security_profile("production")


@pytest.mark.parametrize("insecure_secret", [
    "praxeon_secret_hmac_key_v1",
    "default",
    "secret",
    "change_me",
    "password",
    "admin",
    "12345678",
    "short_key_under_32_characters",
])
def test_rel_02_production_rejects_insecure_or_short_secret(monkeypatch, insecure_secret):
    """REL-02: En perfil production, secretos por defecto o menores de 32 caracteres son rechazados."""
    monkeypatch.setenv("PRAXEON_PROFILE", "production")
    monkeypatch.setenv("PRAXEON_SECRET_KEY", insecure_secret)
    monkeypatch.setenv("PRAXEON_CORS_ORIGINS", "https://supervisor.praxeon.internal")

    with pytest.raises(ValueError, match="al menos 32 caracteres|claves por defecto"):
        validate_security_profile("production")


@pytest.mark.parametrize("insecure_cors", [
    "*",
    "*,https://example.com",
    "https://trusted.com,*",
    "",
    "   ",
])
def test_rel_02_production_rejects_cors_wildcard_or_empty(monkeypatch, insecure_cors):
    """REL-02: En perfil production, el uso de comodín '*' o listas vacías de CORS está prohibido."""
    valid_secret_32 = "a_super_strong_production_secret_key_exceeding_32_chars!"
    monkeypatch.setenv("PRAXEON_PROFILE", "production")
    monkeypatch.setenv("PRAXEON_SECRET_KEY", valid_secret_32)
    monkeypatch.setenv("PRAXEON_CORS_ORIGINS", insecure_cors)

    with pytest.raises(ValueError, match="comodín|CORS"):
        validate_security_profile("production")


def test_rel_02_production_accepts_hardened_configuration(monkeypatch):
    """REL-02: En perfil production con secreto robusto y orígenes explícitos, la configuración es aceptada."""
    valid_secret_32 = "a_super_strong_production_secret_key_exceeding_32_chars!"
    trusted_origins = "https://app.praxeon.internal,https://admin.praxeon.internal"
    valid_api_key = "a_secure_production_api_key_16_chars"

    monkeypatch.setenv("PRAXEON_PROFILE", "production")
    monkeypatch.setenv("PRAXEON_SECRET_KEY", valid_secret_32)
    monkeypatch.setenv("PRAXEON_API_KEY", valid_api_key)
    monkeypatch.setenv("PRAXEON_CORS_ORIGINS", trusted_origins)

    origins = validate_security_profile("production")
    assert origins == ["https://app.praxeon.internal", "https://admin.praxeon.internal"]

    # Crear aplicación en producción sin error
    app = create_app(profile="production")
    assert app.state.security_profile == "production"


@pytest.mark.parametrize("external_host", [
    "0.0.0.0",
    "192.168.1.100",
    "10.0.0.5",
    "public-server.internal",
])
def test_rel_02_non_loopback_bind_requires_authentication(monkeypatch, external_host):
    """REL-02: Intentar vincular el servidor a una interfaz externa sin autenticación activa es bloqueado."""
    monkeypatch.delenv("PRAXEON_PROFILE", raising=False)
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("PRAXEON_REQUIRE_AUTH", raising=False)

    with pytest.raises(ValueError, match="Vincular el servidor a la interfaz de red.*sin autenticación"):
        validate_network_binding(external_host)


@pytest.mark.parametrize("external_host", [
    "0.0.0.0",
    "192.168.1.100",
    "10.0.0.5",
])
def test_rel_02_non_loopback_bind_allowed_when_auth_is_configured(monkeypatch, external_host):
    """REL-02: Vincular a interfaz externa es permitido si la autenticación por API key está configurada."""
    monkeypatch.setenv("PRAXEON_API_KEY", "secure_remote_management_api_key_123")

    # No debe levantar ninguna excepción
    validate_network_binding(external_host)


@pytest.mark.parametrize("loopback_host", [
    "127.0.0.1",
    "localhost",
    "::1",
])
def test_rel_02_loopback_bind_allowed_in_development(monkeypatch, loopback_host):
    """REL-02: Interfaces loopback locales están permitidas en modo desarrollo sin requerir forzosamente claves."""
    monkeypatch.delenv("PRAXEON_PROFILE", raising=False)
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("PRAXEON_REQUIRE_AUTH", raising=False)

    # No debe levantar excepción en loopback
    validate_network_binding(loopback_host)
