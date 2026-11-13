"""Autenticación y perfil de seguridad del servidor web (praxeon/server/auth_deps.py).

Dependencias de FastAPI para autenticar peticiones (API key, JWT, cookie HttpOnly con defensa CSRF), exigir permisos por
ruta y decidir si la autenticación es obligatoria según el perfil. Separado de `dependencies.py` (auditoría A-03); los
nombres siguen importándose también desde `praxeon.server.dependencies` por compatibilidad.
"""

import logging
import os
from typing import Any, Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from praxeon.server.dependencies import RuntimeApplicationService, get_runtime_service
from praxeon.server.services.auth_service import (
    AuthService,
    Permission,
    Role,
    ROLE_PERMISSIONS,
    TenantContext,
    constant_time_equals,
)

logger = logging.getLogger("praxeon.server.dependencies")


_ACTIVE_SECURITY_PROFILE: Optional[str] = None


def set_active_security_profile(profile: Optional[str]) -> None:
    """Establece el perfil de seguridad activo del runtime."""
    global _ACTIVE_SECURITY_PROFILE, _warned_dev_auth
    _ACTIVE_SECURITY_PROFILE = profile
    _warned_dev_auth = False


def get_active_security_profile() -> Optional[str]:
    """Retorna el perfil de seguridad activo si fue establecido."""
    return _ACTIVE_SECURITY_PROFILE


_warned_dev_auth = False


PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip")


def effective_client_host(conn: Any) -> Optional[str]:
    """Host del cliente para decidir si es local. Si la conexión llega con cabeceras de proxy inverso,
    el peer TCP (casi siempre loopback) no es el cliente real: se trata como externo ("proxied")."""
    headers = getattr(conn, "headers", None)
    if headers is not None and any(headers.get(h) for h in PROXY_HEADERS):
        return "proxied"
    client = getattr(conn, "client", None)
    return client.host if client else None


def enforce_cookie_csrf(request: Any, token_from_cookie: bool) -> None:
    """Defensa CSRF para autenticación por cookie: en métodos mutantes, un `Origin` de otro sitio se rechaza.

    Las peticiones con cabecera explícita (Authorization / X-API-Key) no son vulnerables a CSRF.
    """
    if not token_from_cookie or request is None or request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlparse

        if urlparse(origin).netloc.lower() != (request.headers.get("host") or "").lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Origen no permitido para una petición autenticada por cookie (protección CSRF).",
            )


def is_auth_required(profile: Optional[str] = None, client_host: Optional[str] = None) -> bool:
    """Determina si la autenticación por API key es obligatoria.
    
    Es obligatoria si:
    1. PRAXEON_PROFILE == 'production' o PRAXEON_ENV == 'production' (o perfil activo == 'production').
    2. PRAXEON_REQUIRE_AUTH == '1' / 'true'.
    3. PRAXEON_API_KEY está configurada explícitamente en el entorno.
    4. La petición proviene de una interfaz de red externa (no localhost/loopback).
    """
    if client_host and client_host.lower() not in ("127.0.0.1", "localhost", "::1", "testclient"):
        return True
    prof = (profile or _ACTIVE_SECURITY_PROFILE or os.environ.get("PRAXEON_PROFILE") or os.environ.get("PRAXEON_ENV") or "dev").lower().strip()
    if prof == "production":
        return True
    if os.environ.get("PRAXEON_REQUIRE_AUTH", "").strip().lower() in ("1", "true", "yes"):
        return True
    if bool(os.environ.get("PRAXEON_API_KEY", "").strip()):
        return True
    return False




def get_auth_service(runtime: RuntimeApplicationService = Depends(get_runtime_service)) -> AuthService:
    """Dependency para inyectar la instancia de AuthService del runtime."""
    return runtime.get_auth_service()


def get_current_tenant(
    request: Request = None,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    praxeon_access_token: Optional[str] = Cookie(None, alias=AuthService.COOKIE_ACCESS_NAME),
    auth_service: AuthService = Depends(get_auth_service),
) -> TenantContext:
    """Dependency que autentica e inyecta el TenantContext con RBAC desde JWT, Cookie o API Key."""
    app_profile = getattr(request.app.state, "security_profile", None) if (request and hasattr(request, "app") and hasattr(request.app, "state")) else None
    client_host = effective_client_host(request) if request else None
    required = is_auth_required(profile=app_profile, client_host=client_host)

    cookie_val = praxeon_access_token if isinstance(praxeon_access_token, str) else None
    api_key_val = x_api_key if isinstance(x_api_key, str) else None
    auth_val = authorization if isinstance(authorization, str) else None
    service = auth_service if isinstance(auth_service, AuthService) else get_runtime_service().get_auth_service()

    # 1. Intentar token desde cookie HttpOnly o API Key
    token = cookie_val or api_key_val
    enforce_cookie_csrf(request, bool(cookie_val) and not api_key_val)

    # 2. Intentar token desde cabeceras
    if not token and auth_val:
        if auth_val.startswith("Bearer "):
            token = auth_val[7:].strip()
        else:
            token = auth_val.strip()

    if not required and not token:
        # Modo dev sin autenticación obligatoria
        global _warned_dev_auth
        if not _warned_dev_auth:
            logger.warning("WARNING: PRAXEON running without API key authentication. Do not use in production.")
            _warned_dev_auth = True
        return TenantContext(
            tenant_id="dev_tenant",
            user_id="dev_user",
            role=Role.ADMIN,
            permissions=ROLE_PERMISSIONS[Role.ADMIN],
        )

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida: proporcione cookie 'praxeon_access_token', 'Authorization: Bearer <token>' o 'X-API-Key'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Si parece un JWT (3 segmentos)
    if token.count(".") == 2:
        try:
            return service.authenticate_jwt(token)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Token JWT inválido o expirado: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"},
            ) from e

    # Si es una API Key estática
    expected_key = os.environ.get("PRAXEON_API_KEY")
    if expected_key and constant_time_equals(token, expected_key):
        return TenantContext(
            tenant_id="default_tenant",
            user_id="admin_apikey",
            role=Role.ADMIN,
            permissions=ROLE_PERMISSIONS[Role.ADMIN],
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales inválidas: token o API key no reconocidos.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_permission(permission: Permission):
    """Dependency factory para enforcing declarativo de permisos RBAC en rutas FastAPI."""
    def _perm_checker(tenant: TenantContext = Depends(get_current_tenant)) -> TenantContext:
        if not tenant.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permiso insuficiente: se requiere '{permission.value}'.",
            )
        return tenant
    return _perm_checker


def require_route_permission(read: Permission, write: Permission):
    """Dependency para routers: exige `read` en GET/HEAD/OPTIONS y `write` en métodos mutantes."""
    def _checker(
        request: Request,
        tenant: TenantContext = Depends(get_current_tenant),
    ) -> TenantContext:
        needed = read if request.method in ("GET", "HEAD", "OPTIONS") else write
        if not tenant.has_permission(needed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permiso insuficiente: se requiere '{needed.value}'.",
            )
        return tenant
    return _checker


def verify_api_key(
    request: Request = None,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    praxeon_access_token: Optional[str] = Cookie(None, alias=AuthService.COOKIE_ACCESS_NAME),
    auth_service: AuthService = Depends(get_auth_service),
) -> Optional[str]:
    """Dependency de FastAPI para validar la presencia y autenticidad de API Key o JWT (compatibilidad retroactiva)."""
    global _warned_dev_auth

    app_profile = getattr(request.app.state, "security_profile", None) if (request and hasattr(request, "app") and hasattr(request.app, "state")) else None
    client_host = effective_client_host(request) if request else None
    required = is_auth_required(profile=app_profile, client_host=client_host)
    expected_key = os.environ.get("PRAXEON_API_KEY")

    cookie_val = praxeon_access_token if isinstance(praxeon_access_token, str) else None
    api_key_val = x_api_key if isinstance(x_api_key, str) else None
    auth_val = authorization if isinstance(authorization, str) else None
    service = auth_service if isinstance(auth_service, AuthService) else get_runtime_service().get_auth_service()

    # Extraer token de cookie o cabeceras
    token = cookie_val or api_key_val
    enforce_cookie_csrf(request, bool(cookie_val) and not api_key_val)
    if not token and auth_val:
        if auth_val.startswith("Bearer "):
            token = auth_val[7:].strip()
        else:
            token = auth_val.strip()

    if not required and not token:
        if not _warned_dev_auth:
            logger.warning("WARNING: PRAXEON running without API key authentication. Do not use in production.")
            _warned_dev_auth = True
        return None

    if not token:
        if not expected_key:
            logger.error("Fallo de seguridad evitado: Autenticación requerida pero no hay PRAXEON_API_KEY configurada.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error de configuración del servidor: Autenticación requerida pero no se ha establecido PRAXEON_API_KEY.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida: proporcione 'X-API-Key' o 'Authorization: Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 1. Si es un JWT válido
    if token.count(".") == 2:
        try:
            service.authenticate_jwt(token)
            return token
        except ValueError:
            pass

    # 2. Si es una API Key estática
    if expected_key and constant_time_equals(token, expected_key):
        return token

    # Si no había expected_key pero tampoco es JWT válido
    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error de configuración del servidor: Autenticación requerida pero no se ha establecido PRAXEON_API_KEY.",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales inválidas: API key o token no coincide con el servidor.",
        headers={"WWW-Authenticate": "Bearer"},
    )
