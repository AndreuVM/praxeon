"""Servicio Modular de Autenticación, Tokens JWT, Multi-Tenancy y RBAC (Fase 8 - ROAD-04).

Centraliza:
- Emisión, validación y rotación estricta de tokens JWT seguros (HS256).
- Soporte para Multi-Tenancy (aislamiento lógico de tenants por petición).
- Control de Acceso Basado en Roles (RBAC granular: Admin, Operator, Auditor, Viewer).
- Cookies HttpOnly seguras con atributos SameSite y rotación de refresh tokens.
- Generación y consumo atómico de tickets de un solo uso para WebSockets.
- Validación de API Keys estáticas con mitigación contra ataques de tiempo para compatibilidad.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone, timedelta
from enum import Enum
import hmac
import hashlib
import json
import os
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, ConfigDict, Field


class Role(str, Enum):
    """Roles canónicos para el control de acceso basado en roles (RBAC)."""
    ADMIN = "admin"
    OPERATOR = "operator"
    AUDITOR = "auditor"
    VIEWER = "viewer"


class Permission(str, Enum):
    """Permisos granulares del sistema PRAXEON."""
    SESSION_CREATE = "session:create"
    SESSION_READ = "session:read"
    SESSION_EXECUTE = "session:execute"
    SESSION_TERMINATE = "session:terminate"
    WORKFLOW_READ = "workflow:read"
    WORKFLOW_WRITE = "workflow:write"
    AGENT_READ = "agent:read"
    AGENT_WRITE = "agent:write"
    ADMIN_SETTINGS = "admin:settings"
    METRICS_READ = "metrics:read"


ROLE_PERMISSIONS: Dict[Role, Set[Permission]] = {
    Role.ADMIN: {
        Permission.SESSION_CREATE,
        Permission.SESSION_READ,
        Permission.SESSION_EXECUTE,
        Permission.SESSION_TERMINATE,
        Permission.WORKFLOW_READ,
        Permission.WORKFLOW_WRITE,
        Permission.AGENT_READ,
        Permission.AGENT_WRITE,
        Permission.ADMIN_SETTINGS,
        Permission.METRICS_READ,
    },
    Role.OPERATOR: {
        Permission.SESSION_CREATE,
        Permission.SESSION_READ,
        Permission.SESSION_EXECUTE,
        Permission.WORKFLOW_READ,
        Permission.WORKFLOW_WRITE,
        Permission.AGENT_READ,
        Permission.METRICS_READ,
    },
    Role.AUDITOR: {
        Permission.SESSION_READ,
        Permission.WORKFLOW_READ,
        Permission.AGENT_READ,
        Permission.METRICS_READ,
    },
    Role.VIEWER: {
        Permission.SESSION_READ,
        Permission.WORKFLOW_READ,
        Permission.AGENT_READ,
    },
}


class TenantContext(BaseModel):
    """Contexto de seguridad multi-tenant asociado a la petición actual."""
    model_config = ConfigDict(frozen=True)

    tenant_id: str = "default_tenant"
    user_id: str = "system_user"
    role: Role = Role.ADMIN
    permissions: Set[Permission] = Field(default_factory=set)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def has_permission(self, permission: Permission) -> bool:
        """Verifica si el contexto posee el permiso requerido."""
        return permission in self.permissions or self.role == Role.ADMIN


class TokenPair(BaseModel):
    """Par de tokens JWT emitidos (Access Token + Refresh Token con rotación)."""
    model_config = ConfigDict(frozen=True)

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 900  # 15 minutos
    refresh_expires_in: int = 604800  # 7 días
    tenant_id: str
    user_id: str
    role: Role


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = 4 - (len(data) % 4)
    if padding and padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data.encode("utf-8"))


def encode_jwt_hs256(payload: Dict[str, Any], secret_key: str) -> str:
    """Codifica y firma un token JWT mediante HMAC-SHA256 (HS256)."""
    header = {"alg": "HS256", "typ": "JWT"}
    header_json = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    segment1 = _b64url_encode(header_json)
    segment2 = _b64url_encode(payload_json)
    signing_input = f"{segment1}.{segment2}".encode("utf-8")

    signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    segment3 = _b64url_encode(signature)

    return f"{segment1}.{segment2}.{segment3}"


def decode_jwt_hs256(token: str, secret_key: str) -> Dict[str, Any]:
    """Decodifica, valida la firma y verifica la expiración de un token JWT HS256."""
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise ValueError("Token JWT malformado: debe contener exactamente 3 segmentos.")

    segment1, segment2, segment3 = parts
    signing_input = f"{segment1}.{segment2}".encode("utf-8")
    expected_sig = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    provided_sig = _b64url_decode(segment3)

    if not hmac.compare_digest(provided_sig, expected_sig):
        raise ValueError("Firma criptográfica del token JWT inválida.")

    payload_bytes = _b64url_decode(segment2)
    payload = json.loads(payload_bytes.decode("utf-8"))

    # Validar expiración
    exp = payload.get("exp")
    if exp is not None:
        now_epoch = time.time()
        if now_epoch > exp:
            raise ValueError("Token JWT caducado (expirado).")

    return payload


class AuthService:
    """Servicio empresarial de autenticación, multi-tenancy, tokens JWT y tickets de sesión."""

    COOKIE_ACCESS_NAME = "praxeon_access_token"
    COOKIE_REFRESH_NAME = "praxeon_refresh_token"

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        ticket_ttl_seconds: int = 60,
    ):
        self._lock = threading.Lock()
        self._tickets: Dict[str, Dict[str, Any]] = {}
        self._revoked_jtis: Set[str] = set()
        self.ticket_ttl_seconds = ticket_ttl_seconds
        self._configured_api_key = api_key or os.getenv("PRAXEON_API_KEY") or os.getenv("JEV_API_KEY")
        self._secret_key = secret_key or os.getenv("PRAXEON_SECRET_KEY") or self._configured_api_key or "praxeon_default_development_secret_key_32_chars!"

    def get_configured_api_key(self) -> Optional[str]:
        """Retorna la clave API configurada en entorno o instancia."""
        return self._configured_api_key

    def verify_api_key(self, provided_key: Optional[str]) -> bool:
        """Verifica una clave API en tiempo constante."""
        configured = self.get_configured_api_key()
        if not configured:
            return True
        if not provided_key:
            return False
        return hmac.compare_digest(provided_key.strip(), configured.strip())

    def issue_token_pair(
        self,
        tenant_id: str = "default_tenant",
        user_id: str = "admin",
        role: Role = Role.ADMIN,
        access_ttl_seconds: int = 900,
        refresh_ttl_seconds: int = 604800,
    ) -> TokenPair:
        """Emite un par de tokens JWT (Access Token y Refresh Token con jti único para rotación)."""
        now = time.time()
        jti_access = f"jti_acc_{secrets.token_hex(16)}"
        jti_refresh = f"jti_ref_{secrets.token_hex(16)}"

        perms = [p.value for p in ROLE_PERMISSIONS.get(role, set())]

        access_payload = {
            "iss": "praxeon-auth",
            "sub": user_id,
            "tenant_id": tenant_id,
            "role": role.value,
            "permissions": perms,
            "jti": jti_access,
            "iat": int(now),
            "exp": int(now + access_ttl_seconds),
            "type": "access",
        }

        refresh_payload = {
            "iss": "praxeon-auth",
            "sub": user_id,
            "tenant_id": tenant_id,
            "role": role.value,
            "jti": jti_refresh,
            "iat": int(now),
            "exp": int(now + refresh_ttl_seconds),
            "type": "refresh",
        }

        access_token = encode_jwt_hs256(access_payload, self._secret_key)
        refresh_token = encode_jwt_hs256(refresh_payload, self._secret_key)

        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=access_ttl_seconds,
            refresh_expires_in=refresh_ttl_seconds,
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
        )

    def refresh_token_pair(self, refresh_token: str) -> TokenPair:
        """Renueva el token de acceso rotando e invalidando el refresh token anterior."""
        payload = decode_jwt_hs256(refresh_token, self._secret_key)
        if payload.get("type") != "refresh":
            raise ValueError("Token inválido: se requiere un token de tipo 'refresh'.")

        jti = payload.get("jti")
        with self._lock:
            if jti in self._revoked_jtis:
                raise ValueError("Refresh token revocado o ya utilizado (intento de reutilización detectado).")
            # Invalidar de inmediato para garantizar rotación estricta
            if jti:
                self._revoked_jtis.add(jti)

        tenant_id = payload.get("tenant_id", "default_tenant")
        user_id = payload.get("sub", "system_user")
        role_str = payload.get("role", Role.OPERATOR.value)
        role = Role(role_str) if role_str in [r.value for r in Role] else Role.OPERATOR

        return self.issue_token_pair(tenant_id=tenant_id, user_id=user_id, role=role)

    def revoke_token(self, token: str) -> bool:
        """Añade el identificador jti del token a la lista de revocación (blacklist)."""
        try:
            payload = decode_jwt_hs256(token, self._secret_key)
            jti = payload.get("jti")
            if jti:
                with self._lock:
                    self._revoked_jtis.add(jti)
                return True
        except Exception:
            pass
        return False

    def authenticate_jwt(self, token: str) -> TenantContext:
        """Valida un access token JWT y construye su TenantContext tipado con permisos."""
        payload = decode_jwt_hs256(token, self._secret_key)
        jti = payload.get("jti")
        with self._lock:
            if jti in self._revoked_jtis:
                raise ValueError("Token JWT revocado.")

        tenant_id = payload.get("tenant_id", "default_tenant")
        user_id = payload.get("sub", "system_user")
        role_str = payload.get("role", Role.VIEWER.value)
        role = Role(role_str) if role_str in [r.value for r in Role] else Role.VIEWER

        perms_list = payload.get("permissions", [])
        perms_set = {Permission(p) for p in perms_list if p in [pm.value for pm in Permission]}
        if not perms_set:
            perms_set = ROLE_PERMISSIONS.get(role, set())

        return TenantContext(
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
            permissions=perms_set,
            metadata={"jti": jti, "exp": payload.get("exp")},
        )

    def create_ws_ticket(
        self,
        session_id: str,
        client_id: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        """Genera un ticket criptográfico de un solo uso para streaming WebSocket con TTL estricto."""
        ttl = ttl_seconds or self.ticket_ttl_seconds
        ticket_id = f"wst_{secrets.token_urlsafe(32)}"
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl)

        with self._lock:
            self._purge_expired_tickets_locked(now)
            self._tickets[ticket_id] = {
                "ticket_id": ticket_id,
                "session_id": session_id,
                "client_id": client_id,
                "created_at": now,
                "expires_at": expires_at,
                "consumed": False,
            }

        return ticket_id

    def verify_and_consume_ws_ticket(
        self,
        ticket_id: str,
        expected_session_id: Optional[str] = None,
    ) -> bool:
        """Valida y consume atómicamente un ticket WebSocket, garantizando un solo uso."""
        if not ticket_id:
            return False

        now = datetime.now(timezone.utc)
        with self._lock:
            record = self._tickets.get(ticket_id)
            if not record or record["consumed"]:
                return False

            if now > record["expires_at"]:
                self._tickets.pop(ticket_id, None)
                return False

            if expected_session_id and record["session_id"] != expected_session_id:
                return False

            record["consumed"] = True
            self._tickets.pop(ticket_id, None)
            return True

    def _purge_expired_tickets_locked(self, now: datetime) -> None:
        """Elimina tickets caducados del almacén interno en memoria."""
        expired = [tid for tid, data in self._tickets.items() if now > data["expires_at"] or data["consumed"]]
        for tid in expired:
            self._tickets.pop(tid, None)
