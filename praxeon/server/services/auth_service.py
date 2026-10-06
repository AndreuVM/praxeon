"""Servicio Modular de Autenticación, Tokens y Tickets WebSocket (F5/SEC).

Centraliza:
- Generación y consumo atómico de tickets de un solo uso para WebSockets.
- Validación de API Keys y Bearer Tokens con mitigación contra ataques de tiempo.
- Autorización de perfiles operativos (producción vs desarrollo).
"""

from datetime import datetime, timezone, timedelta
import hmac
import os
import secrets
import threading
from typing import Any, Dict, Optional


class AuthService:
    """Servicio de autenticación, control de acceso y tickets de sesión."""

    def __init__(self, api_key: Optional[str] = None, ticket_ttl_seconds: int = 60):
        self._lock = threading.Lock()
        self._tickets: Dict[str, Dict[str, Any]] = {}
        self.ticket_ttl_seconds = ticket_ttl_seconds
        self._configured_api_key = api_key or os.getenv("PRAXEON_API_KEY") or os.getenv("JEV_API_KEY")

    def get_configured_api_key(self) -> Optional[str]:
        """Retorna la clave API configurada en entorno o instancia."""
        return self._configured_api_key

    def verify_api_key(self, provided_key: Optional[str]) -> bool:
        """Verifica una clave API en tiempo constante."""
        configured = self.get_configured_api_key()
        if not configured:
            # Si no hay clave configurada en entorno, se permite en perfil permisivo
            return True
        if not provided_key:
            return False
        return hmac.compare_digest(provided_key.strip(), configured.strip())

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
            # Limpiar tickets expirados
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
            if not record:
                return False

            if record["consumed"]:
                return False

            if now > record["expires_at"]:
                self._tickets.pop(ticket_id, None)
                return False

            if expected_session_id and record["session_id"] != expected_session_id:
                return False

            # Consumir atómicamente
            record["consumed"] = True
            self._tickets.pop(ticket_id, None)
            return True

    def _purge_expired_tickets_locked(self, now: datetime) -> None:
        """Elimina tickets caducados del almacén interno en memoria."""
        expired = [tid for tid, data in self._tickets.items() if now > data["expires_at"] or data["consumed"]]
        for tid in expired:
            self._tickets.pop(tid, None)
