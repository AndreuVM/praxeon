"""Claves independientes por propósito (auditoría S-03).

- `PRAXEON_API_KEY`      credencial que presentan los clientes. Nunca firma nada.
- `PRAXEON_JWT_SECRET`   firma de los tokens JWT.
- `PRAXEON_RECEIPT_KEY`  firma HMAC de los recibos de capability.

`PRAXEON_SECRET_KEY` pasa a ser un secreto maestro opcional que NO es una credencial de cliente: de él se derivan (HKDF-SHA256) las
subclaves de JWT y de recibos que no se hayan configurado a mano, de modo que una despliegue con una sola variable sigue funcionando
pero cada función usa una clave distinta y conocer una no permite forjar la otra.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

MIN_LENGTH = 32
INSECURE_DEFAULTS = {"", "praxeon_secret_hmac_key_v1", "default", "secret", "change_me"}
_INFO_PREFIX = b"praxeon/v1/"


def derive_key(master: str, purpose: str, length: int = 32) -> str:
    """HKDF-SHA256 (RFC 5869) con `purpose` como contexto: subclaves independientes de un mismo secreto maestro."""
    prk = hmac.new(b"praxeon-hkdf-salt", master.encode("utf-8"), hashlib.sha256).digest()
    okm, block, counter = b"", b"", 1
    while len(okm) < length:
        block = hmac.new(prk, block + _INFO_PREFIX + purpose.encode("utf-8") + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length].hex()


def _env(name: str) -> Optional[str]:
    value = (os.environ.get(name) or "").strip()
    return value or None


def _sub_key(explicit_var: str, purpose: str) -> Optional[str]:
    explicit = _env(explicit_var)
    if explicit:
        return explicit
    master = _env("PRAXEON_SECRET_KEY")
    return derive_key(master, purpose) if master else None


def receipt_key() -> Optional[str]:
    """Clave de los recibos: la explícita, la derivada del secreto maestro o None (el llamante genera una aleatoria)."""
    return _sub_key("PRAXEON_RECEIPT_KEY", "receipt")


def jwt_secret() -> Optional[str]:
    """Secreto de los JWT: el explícito, el derivado del secreto maestro o None (el llamante genera uno aleatorio)."""
    return _sub_key("PRAXEON_JWT_SECRET", "jwt")


def _strong(value: Optional[str]) -> bool:
    return bool(value) and value not in INSECURE_DEFAULTS and len(value) >= MIN_LENGTH


def production_keys_problem() -> Optional[str]:
    """None si el perfil `production` tiene claves de firma válidas: un secreto maestro, o las dos subclaves explícitas."""
    if _strong(_env("PRAXEON_SECRET_KEY")):
        return None
    if _strong(_env("PRAXEON_JWT_SECRET")) and _strong(_env("PRAXEON_RECEIPT_KEY")):
        return None
    return (
        f"Perfil de seguridad 'production' requiere PRAXEON_SECRET_KEY (o bien PRAXEON_JWT_SECRET y PRAXEON_RECEIPT_KEY) "
        f"configuradas con al menos {MIN_LENGTH} caracteres y no use claves por defecto."
    )
