"""Módulo de sanitización de datos, redacción de secretos/PII y frontera de confianza.

Implementa los requisitos de las Secciones 12 y 22 de la Auditoría Técnica:
- Delimitación explícita de datos confiables (TRUSTED) vs no confiables (UNTRUSTED)
- Redacción automática de credenciales, secretos, API keys y contraseñas
- Enmascaramiento de información personal identificable (PII)
- Control y truncamiento seguro de payloads voluminosos
"""

import re
from typing import Any, Dict

# Patrones regex de secretos comunes
SECRET_PATTERNS = [
    # API Keys comunes (OpenAI, Anthropic, Google, TypeSafe)
    (re.compile(r"sk-[a-zA-Z0-9_-]{20,}", re.IGNORECASE), "[REDACTED_API_KEY]"),
    (re.compile(r"typesafe_[a-zA-Z0-9_-]{20,}", re.IGNORECASE), "[REDACTED_TYPESAFE_KEY]"),
    (re.compile(r"AIza[0-9A-Za-z-_]{35}", re.IGNORECASE), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE), "[REDACTED_AWS_KEY]"),
    # Bearer tokens y JWTs
    (re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{25,}", re.IGNORECASE), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"ey[a-zA-Z0-9_\-]{15,}\.ey[a-zA-Z0-9_\-]{15,}\.[a-zA-Z0-9_\-]{10,}", re.IGNORECASE), "[REDACTED_JWT]"),
    # Claves privadas
    (re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    # Asignaciones comunes de passwords o tokens en strings
    (re.compile(r"(api[_-]?key|password|secret|token|auth_token)\s*[:=]\s*['\"]?[^\s'\"]{6,}['\"]?", re.IGNORECASE), r"\1=[REDACTED]"),
]

# Patrones PII básicos
PII_PATTERNS = [
    # Correos electrónicos
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b"), "[REDACTED_EMAIL]"),
]

SENSITIVE_KEY_NAMES = {
    "password", "secret", "token", "api_key", "apikey", "access_token",
    "private_key", "authorization", "auth", "credentials", "typesafe_api_key",
}


class DataSanitizer:
    """Sanitiza strings y diccionarios para evitar fugas de información sensible."""

    def __init__(self, redact_secrets: bool = True, redact_pii: bool = True, max_payload_bytes: int = 100_000):
        self.redact_secrets = redact_secrets
        self.redact_pii = redact_pii
        self.max_payload_bytes = max_payload_bytes

    def redact_text(self, text: str) -> str:
        """Aplica sustitución regex de secretos y PII en un texto."""
        if not text or not isinstance(text, str):
            return text

        result = text
        if self.redact_secrets:
            for pattern, replacement in SECRET_PATTERNS:
                result = pattern.sub(replacement, result)

        if self.redact_pii:
            for pattern, replacement in PII_PATTERNS:
                result = pattern.sub(replacement, result)

        return result

    def mask_secrets(self, text: str) -> str:
        """Alias para redact_text."""
        return self.redact_text(text)

    def redact_dict(self, data: Any) -> Any:
        """Sanitiza recursivamente estructuras de datos (dicts, lists, primitives)."""
        if isinstance(data, dict):
            sanitized: Dict[str, Any] = {}
            for k, v in data.items():
                k_lower = str(k).lower().strip()
                if self.redact_secrets and any(sens in k_lower for sens in SENSITIVE_KEY_NAMES):
                    sanitized[k] = "[REDACTED]"
                else:
                    sanitized[k] = self.redact_dict(v)
            return sanitized
        elif isinstance(data, list):
            return [self.redact_dict(item) for item in data]
        elif isinstance(data, str):
            return self.redact_text(data)
        return data

    def enforce_payload_limit(self, content: str) -> str:
        """Trunca cadenas que excedan el límite máximo de bytes configurado."""
        if not isinstance(content, str):
            return content

        content_bytes = content.encode("utf-8", errors="replace")
        if len(content_bytes) <= self.max_payload_bytes:
            return content

        truncated_bytes = content_bytes[:self.max_payload_bytes]
        return truncated_bytes.decode("utf-8", errors="ignore") + "\n... [PAYLOAD_TRUNCATED_DUE_TO_SIZE_LIMIT]"

    @staticmethod
    def wrap_untrusted(content: str, source: str = "tool_output") -> str:
        """Envuelve contenido no confiable con delimitadores explícitos para mitigar prompt injection."""
        return f"<untrusted_content source='{source}'>\n{content}\n</untrusted_content>"
