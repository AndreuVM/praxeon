"""Rutas y Controladores de Autenticación SaaS, Tokens JWT y Cookies HttpOnly (Fase 8 - ROAD-04).

Expone:
- POST /v1/auth/login: Autenticación por API Key o credenciales; emite access + refresh token (JSON + cookies HttpOnly).
- POST /v1/auth/refresh: Rotación de refresh token y emisión de nuevo access token.
- POST /v1/auth/logout: Revocación de tokens y limpieza segura de cookies HttpOnly.
- GET /v1/auth/me: Inspección del contexto del tenant actual y permisos RBAC.
"""

from typing import Any, Dict, Optional
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from praxeon.server.dependencies import get_auth_service, get_current_tenant
from praxeon.server.services.auth_service import (
    AuthService,
    Permission,
    Role,
    TenantContext,
    TokenPair,
)

auth_router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    api_key: Optional[str] = Field(default=None, description="Clave API de acceso")
    tenant_id: str = Field(default="default_tenant", description="Identificador del tenant SaaS")
    user_id: str = Field(default="admin", description="Identificador del usuario")
    role: Role = Field(default=Role.ADMIN, description="Rol solicitado")


class RefreshRequest(BaseModel):
    refresh_token: Optional[str] = Field(default=None, description="Refresh token para rotación si no se usa cookie")


def _set_auth_cookies(response: Response, tokens: TokenPair, secure: bool = False) -> None:
    """Configura las cookies seguras HttpOnly en la respuesta HTTP."""
    response.set_cookie(
        key=AuthService.COOKIE_ACCESS_NAME,
        value=tokens.access_token,
        max_age=tokens.expires_in,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    response.set_cookie(
        key=AuthService.COOKIE_REFRESH_NAME,
        value=tokens.refresh_token,
        max_age=tokens.refresh_expires_in,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    """Elimina las cookies de autenticación de la respuesta HTTP."""
    response.delete_cookie(key=AuthService.COOKIE_ACCESS_NAME, path="/")
    response.delete_cookie(key=AuthService.COOKIE_REFRESH_NAME, path="/")


@auth_router.post("/login", response_model=Dict[str, Any])
def login(
    req: LoginRequest,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> Dict[str, Any]:
    """Inicia sesión emitiendo un par de tokens JWT (HS256) y cookies HttpOnly."""
    # Verificar API key proporcionada si hay una configurada
    if req.api_key and not auth_service.verify_api_key(req.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales de acceso inválidas.",
        )

    tokens = auth_service.issue_token_pair(
        tenant_id=req.tenant_id,
        user_id=req.user_id,
        role=req.role,
    )

    _set_auth_cookies(response, tokens)

    return {
        "status": "success",
        "data": {
            "tokens": tokens.model_dump(),
            "tenant_id": req.tenant_id,
            "user_id": req.user_id,
            "role": req.role.value,
        },
    }


@auth_router.post("/refresh", response_model=Dict[str, Any])
def refresh_token(
    response: Response,
    req: Optional[RefreshRequest] = None,
    praxeon_refresh_token: Optional[str] = Cookie(None, alias=AuthService.COOKIE_REFRESH_NAME),
    auth_service: AuthService = Depends(get_auth_service),
) -> Dict[str, Any]:
    """Rota el refresh token y emite un nuevo par de tokens."""
    cookie_token = praxeon_refresh_token if isinstance(praxeon_refresh_token, str) else None
    token = (req.refresh_token if req and req.refresh_token else None) or cookie_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token no proporcionado (ni en body ni en cookie HttpOnly).",
        )

    try:
        new_tokens = auth_service.refresh_token_pair(token)
    except ValueError as e:
        _clear_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Fallo en la rotación de token: {str(e)}",
        )

    _set_auth_cookies(response, new_tokens)

    return {
        "status": "success",
        "data": {
            "tokens": new_tokens.model_dump(),
            "tenant_id": new_tokens.tenant_id,
            "user_id": new_tokens.user_id,
            "role": new_tokens.role.value,
        },
    }


@auth_router.post("/logout", response_model=Dict[str, Any])
def logout(
    response: Response,
    praxeon_access_token: Optional[str] = Cookie(None, alias=AuthService.COOKIE_ACCESS_NAME),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    auth_service: AuthService = Depends(get_auth_service),
) -> Dict[str, Any]:
    """Revoca el token actual y limpia las cookies HttpOnly."""
    token = praxeon_access_token if isinstance(praxeon_access_token, str) else None
    if not token and isinstance(authorization, str) and authorization.startswith("Bearer "):
        token = authorization[7:].strip()

    if token:
        auth_service.revoke_token(token)

    _clear_auth_cookies(response)

    return {
        "status": "success",
        "message": "Sesión cerrada y cookies eliminadas exitosamente.",
    }


@auth_router.get("/me", response_model=Dict[str, Any])
def get_me(
    tenant: TenantContext = Depends(get_current_tenant),
) -> Dict[str, Any]:
    """Devuelve la información de identidad del tenant actual y sus permisos RBAC."""
    return {
        "status": "success",
        "data": {
            "tenant_id": tenant.tenant_id,
            "user_id": tenant.user_id,
            "role": tenant.role.value,
            "permissions": [p.value for p in tenant.permissions],
        },
    }
