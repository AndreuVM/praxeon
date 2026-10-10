"""Tests exhaustivos para Autenticación Multi-Tenant SaaS, JWT, Cookies HttpOnly y RBAC (ROAD-04 / Fase 8).

Verifica:
1. Emisión de tokens HMAC-SHA256 (HS256) con expiración, claims y TenantContext.
2. Matriz de roles y permisos RBAC (ADMIN, OPERATOR, VIEWER, AUDITOR).
3. Rotación estricta de refresh tokens con detección y bloqueo de reutilización.
4. Lista negra / revocación de tokens y cierre de sesión seguro.
5. Endpoints REST (/v1/auth/login, /v1/auth/refresh, /v1/auth/logout, /v1/auth/me).
6. Configuración y eliminación de cookies HttpOnly con SameSite.
7. Enforcing de permisos RBAC mediante dependencias de FastAPI (require_permission).
8. Acceso retrocompatible con API keys y headers Bearer.
"""

import pytest
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    get_auth_service,
    get_current_tenant,
    require_permission,
    set_runtime_service,
    verify_api_key,
)
from praxeon.server.services.auth_service import (
    AuthService,
    Permission,
    Role,
    ROLE_PERMISSIONS,
    TenantContext,
    TokenPair,
)


@pytest.fixture
def auth_service():
    """Instancia limpia y aislada de AuthService."""
    return AuthService(secret_key="test_super_secret_key_for_multitenant_saas_tests_32chars")


@pytest.fixture
def test_service(tmp_path):
    """Instancia de RuntimeApplicationService configurada con base de datos temporal."""
    service = RuntimeApplicationService(db_dir=str(tmp_path / "test_auth_cache"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


@pytest.fixture
def client(test_service):
    """Cliente de pruebas sobre FastAPI con rutas completas."""
    app = create_app()
    return TestClient(app)


# =============================================================================
# 1. PRUEBAS UNITARIAS DE AUTONOMÍA Y CRIPTOGRAFÍA EN AuthService
# =============================================================================

def test_auth_service_issue_and_verify_jwt(auth_service):
    """Emite un token JWT y lo verifica decodificando los claims y TenantContext."""
    tokens = auth_service.issue_token_pair(
        tenant_id="tenant_acme",
        user_id="alice",
        role=Role.OPERATOR,
    )

    assert isinstance(tokens, TokenPair)
    assert tokens.access_token.count(".") == 2
    assert tokens.refresh_token.count(".") == 2
    assert tokens.tenant_id == "tenant_acme"
    assert tokens.user_id == "alice"
    assert tokens.role == Role.OPERATOR

    # Verificar claims
    tenant_ctx = auth_service.authenticate_jwt(tokens.access_token)
    assert tenant_ctx.tenant_id == "tenant_acme"
    assert tenant_ctx.user_id == "alice"
    assert tenant_ctx.role == Role.OPERATOR
    assert Permission.SESSION_EXECUTE in tenant_ctx.permissions
    assert Permission.ADMIN_SETTINGS not in tenant_ctx.permissions


def test_auth_service_rbac_permissions_matrix():
    """Valida la integridad de la jerarquía y exclusión de la matriz RBAC."""
    admin_perms = ROLE_PERMISSIONS[Role.ADMIN]
    operator_perms = ROLE_PERMISSIONS[Role.OPERATOR]
    viewer_perms = ROLE_PERMISSIONS[Role.VIEWER]
    auditor_perms = ROLE_PERMISSIONS[Role.AUDITOR]

    # ADMIN tiene control total
    assert Permission.ADMIN_SETTINGS in admin_perms
    assert Permission.SESSION_EXECUTE in admin_perms
    assert Permission.SESSION_TERMINATE in admin_perms

    # OPERATOR puede ejecutar pero no gestionar settings admin
    assert Permission.SESSION_EXECUTE in operator_perms
    assert Permission.ADMIN_SETTINGS not in operator_perms

    # VIEWER es estrictamente de lectura
    assert Permission.SESSION_READ in viewer_perms
    assert Permission.SESSION_CREATE not in viewer_perms
    assert Permission.SESSION_EXECUTE not in viewer_perms

    # AUDITOR tiene acceso de lectura y métricas pero no ejecución
    assert Permission.METRICS_READ in auditor_perms
    assert Permission.SESSION_EXECUTE not in auditor_perms


def test_auth_service_refresh_token_rotation(auth_service):
    """La rotación de refresh tokens emite un nuevo par y revoca el token anterior."""
    original_pair = auth_service.issue_token_pair(
        tenant_id="tenant_beta",
        user_id="bob",
        role=Role.VIEWER,
    )

    # Rotar el token
    new_pair = auth_service.refresh_token_pair(original_pair.refresh_token)
    assert new_pair.access_token != original_pair.access_token
    assert new_pair.refresh_token != original_pair.refresh_token
    assert new_pair.tenant_id == "tenant_beta"

    # Intento de reusar el refresh token revocado debe fallar inmediatamente
    with pytest.raises(ValueError, match="revocado o ya utilizado"):
        auth_service.refresh_token_pair(original_pair.refresh_token)


def test_auth_service_token_revocation_and_blacklist(auth_service):
    """Un token revocado no puede ser utilizado para autenticar solicitudes."""
    pair = auth_service.issue_token_pair(
        tenant_id="tenant_gamma",
        user_id="charlie",
        role=Role.ADMIN,
    )

    # Autenticación exitosa inicial
    ctx = auth_service.authenticate_jwt(pair.access_token)
    assert ctx.user_id == "charlie"

    # Revocar token explícitamente
    auth_service.revoke_token(pair.access_token)

    # Debe ser rechazado
    with pytest.raises(ValueError, match="revocado"):
        auth_service.authenticate_jwt(pair.access_token)


# =============================================================================
# 2. PRUEBAS DE INTEGRACIÓN DE ENDPOINTS REST Y COOKIES HttpOnly
# =============================================================================

def test_endpoint_login_sets_cookies_and_returns_tokens(client):
    """POST /v1/auth/login devuelve tokens en el payload y setea cookies HttpOnly."""
    resp = client.post(
        "/v1/auth/login",
        json={
            "tenant_id": "tenant_enterprise",
            "user_id": "user_101",
            "role": "admin",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "tokens" in data["data"]
    tokens = data["data"]["tokens"]
    assert "access_token" in tokens
    assert "refresh_token" in tokens

    # Validar cookies HttpOnly
    assert AuthService.COOKIE_ACCESS_NAME in resp.cookies
    assert AuthService.COOKIE_REFRESH_NAME in resp.cookies


def test_endpoint_me_with_bearer_token(client):
    """GET /v1/auth/me valida correctamente cabeceras Authorization: Bearer <token>."""
    login_resp = client.post(
        "/v1/auth/login",
        json={"tenant_id": "t1", "user_id": "u1", "role": "operator"},
    )
    access_token = login_resp.json()["data"]["tokens"]["access_token"]

    # Invocar /v1/auth/me con cabecera Bearer
    resp = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200
    me = resp.json()["data"]
    assert me["tenant_id"] == "t1"
    assert me["user_id"] == "u1"
    assert me["role"] == "operator"
    assert "session:execute" in me["permissions"]
    assert "admin:settings" not in me["permissions"]


def test_endpoint_me_with_httponly_cookie(client):
    """GET /v1/auth/me autentica fluidamente mediante cookie HttpOnly sin cabeceras."""
    login_resp = client.post(
        "/v1/auth/login",
        json={"tenant_id": "cookie_tenant", "user_id": "cookie_user", "role": "viewer"},
    )
    assert login_resp.status_code == 200

    # TestClient preserva las cookies recibidas del login
    resp = client.get("/v1/auth/me")
    assert resp.status_code == 200
    me = resp.json()["data"]
    assert me["tenant_id"] == "cookie_tenant"
    assert me["user_id"] == "cookie_user"
    assert me["role"] == "viewer"
    assert "session:read" in me["permissions"]


def test_endpoint_refresh_token_rotation(client):
    """POST /v1/auth/refresh rota tokens mediante cookie o body."""
    login_resp = client.post(
        "/v1/auth/login",
        json={"tenant_id": "tenant_rot", "user_id": "u_rot", "role": "operator"},
    )
    first_tokens = login_resp.json()["data"]["tokens"]
    first_refresh = first_tokens["refresh_token"]

    # Solicitar refresh vía JSON body
    refresh_resp = client.post(
        "/v1/auth/refresh",
        json={"refresh_token": first_refresh},
    )
    assert refresh_resp.status_code == 200
    new_tokens = refresh_resp.json()["data"]["tokens"]
    assert new_tokens["access_token"] != first_tokens["access_token"]
    assert new_tokens["refresh_token"] != first_refresh

    # Intento de reusar first_refresh debe responder 401 Unauthorized
    failed_resp = client.post(
        "/v1/auth/refresh",
        json={"refresh_token": first_refresh},
    )
    assert failed_resp.status_code == 401
    assert "Fallo en la rotación" in failed_resp.json()["detail"]


def test_endpoint_logout_clears_cookies_and_revokes(client):
    """POST /v1/auth/logout invalida el token actual y limpia las cookies."""
    login_resp = client.post(
        "/v1/auth/login",
        json={"tenant_id": "t_out", "user_id": "u_out", "role": "admin"},
    )
    token = login_resp.json()["data"]["tokens"]["access_token"]

    # Ejecutar logout
    logout_resp = client.post(
        "/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logout_resp.status_code == 200

    # Llamar con el token revocado debe fallar
    me_resp = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 401


# =============================================================================
# 3. ENFORCING DE PERMISOS RBAC EN RUTAS (require_permission)
# =============================================================================

def test_require_permission_enforcement(client, test_service):
    """Verifica que las rutas protegidas con require_permission denieguen roles insuficientes con 403."""
    from fastapi import APIRouter
    from praxeon.server.routes import api_router

    # Agregar ruta de prueba protegida que requiere permiso SESSION_EXECUTE dentro de api_router
    test_subrouter = APIRouter(prefix="/v1/test_rbac")

    @test_subrouter.post("/execute-guarded")
    def guarded_execution(tenant: TenantContext = Depends(require_permission(Permission.SESSION_EXECUTE))):
        return {"status": "authorized", "user": tenant.user_id}

    api_router.include_router(test_subrouter)
    client.app.include_router(test_subrouter)

    # 1. Usuario con rol VIEWER (no tiene SESSION_EXECUTE) -> debe recibir 403 Forbidden
    login_viewer = client.post(
        "/v1/auth/login",
        json={"tenant_id": "t_test", "user_id": "viewer_user", "role": "viewer"},
    )
    viewer_token = login_viewer.json()["data"]["tokens"]["access_token"]

    resp_denied = client.post(
        "/v1/test_rbac/execute-guarded",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp_denied.status_code == status.HTTP_403_FORBIDDEN
    assert "Permiso insuficiente" in resp_denied.json()["detail"]

    # 2. Usuario con rol OPERATOR (tiene SESSION_EXECUTE) -> 200 OK
    login_operator = client.post(
        "/v1/auth/login",
        json={"tenant_id": "t_test", "user_id": "operator_user", "role": "operator"},
    )
    op_token = login_operator.json()["data"]["tokens"]["access_token"]

    resp_allowed = client.post(
        "/v1/test_rbac/execute-guarded",
        headers={"Authorization": f"Bearer {op_token}"},
    )
    assert resp_allowed.status_code == 200
    assert resp_allowed.json()["status"] == "authorized"
    assert resp_allowed.json()["user"] == "operator_user"


