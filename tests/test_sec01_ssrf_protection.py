"""Tests de SEC-01: Blindaje contra SSRF en endpoints con base_url y endpoints remotos.

Valida:
- Validación de esquemas (HTTPS obligatorio en producción).
- Bloqueo estricto de endpoints de metadatos cloud (169.254.169.254, metadata.google.internal).
- Bloqueo de direcciones privadas y loopback en producción.
- Política de allowlist canónica cuando allow_custom_endpoints es False.
- Integración en ProviderConfig, DecisionModelConfig y RuntimeApplicationService.start_mission.
"""

import os
import pytest
from pydantic import ValidationError

from praxeon.config import PraxeonConfig, ProviderConfig, SecurityConfig
from praxeon.domain.decision_provider import DecisionModelConfig
from praxeon.policy.egress import (
    CANONICAL_PROVIDER_HOSTS,
    SSRFProtectionViolation,
    validate_provider_endpoint,
)
from praxeon.server.dependencies import RuntimeApplicationService


def test_canonical_provider_endpoints_allowed():
    """URLs legítimas en la allowlist canónica deben ser aprobadas."""
    valid_urls = [
        "https://api.openai.com/v1",
        "https://api.groq.com/openai/v1",
        "https://generativelanguage.googleapis.com/v1beta",
        "https://openrouter.ai/api/v1",
        "https://api.anthropic.com/v1",
        "https://api.typesafe.ai/v1",
    ]
    for url in valid_urls:
        res = validate_provider_endpoint(url, allow_custom=False, profile="dev")
        assert res == url


def test_cloud_metadata_blocked():
    """Intento de acceso a endpoints de metadatos cloud (AWS/GCP/Azure) debe ser bloqueado sin excepción."""
    metadata_urls = [
        "http://169.254.169.254/latest/meta-data/",
        "https://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://169.254.1.1/info",
    ]
    for url in metadata_urls:
        with pytest.raises(SSRFProtectionViolation, match="metadatos cloud"):
            validate_provider_endpoint(url, allow_custom=True, profile="dev")


def test_invalid_schemes_blocked():
    """Esquemas arbitrarios no HTTP/HTTPS (file, ftp, gopher) deben ser rechazados."""
    invalid_schemes = [
        "file:///etc/passwd",
        "ftp://files.example.com",
        "gopher://gopher.floodgap.com",
        "javascript:alert(1)",
    ]
    for url in invalid_schemes:
        with pytest.raises(SSRFProtectionViolation, match="Esquema de URL no permitido"):
            validate_provider_endpoint(url, allow_custom=True, profile="dev")


def test_embedded_credentials_blocked():
    """URLs con credenciales embebidas (user:pass@host) deben ser rechazadas."""
    with pytest.raises(SSRFProtectionViolation, match="credenciales embebidas"):
        validate_provider_endpoint("https://admin:secret123@api.openai.com/v1", allow_custom=True)


def test_production_profile_requires_https():
    """En perfil de producción 'production', el esquema HTTP no cifrado está prohibido."""
    with pytest.raises(SSRFProtectionViolation, match="exige estrictamente el esquema 'https://'"):
        validate_provider_endpoint("http://api.openai.com/v1", allow_custom=False, profile="production")


def test_production_profile_blocks_loopback_and_private_ips():
    """En producción, loopback y subredes privadas RFC1918 están estrictamente bloqueadas."""
    private_targets = [
        "https://localhost:8443",
        "https://127.0.0.1:8443",
        "https://10.0.0.1:8443",
        "https://192.168.1.1:8443",
        "https://172.16.0.1:8443",
    ]
    for target in private_targets:
        with pytest.raises(SSRFProtectionViolation, match="bloqueado"):
            validate_provider_endpoint(target, allow_custom=True, profile="production")


def test_disallow_custom_endpoints_by_default():
    """Si allow_custom_endpoints es False, cualquier host desconocido fuera de la allowlist se rechaza."""
    custom_target = "https://unauthorized-external-server.com/v1"
    with pytest.raises(SSRFProtectionViolation, match="allow_custom_endpoints=False"):
        validate_provider_endpoint(custom_target, allow_custom=False, profile="dev")

    # Si se habilita allow_custom explícitamente, se permite
    allowed_res = validate_provider_endpoint(custom_target, allow_custom=True, profile="dev")
    assert allowed_res == custom_target


def test_provider_config_validates_base_url_ssrf():
    """ProviderConfig de Pydantic debe validar base_url contra ataques SSRF."""
    # Inválido: metadata IP
    with pytest.raises(ValidationError):
        ProviderConfig(base_url="http://169.254.169.254/meta")

    # Válido: host canónico
    p = ProviderConfig(base_url="https://api.groq.com/openai/v1")
    assert p.base_url == "https://api.groq.com/openai/v1"


def test_decision_model_config_validates_endpoint_ssrf():
    """DecisionModelConfig debe validar el endpoint remoto contra ataques SSRF."""
    with pytest.raises(ValidationError):
        DecisionModelConfig(provider="custom", endpoint="http://169.254.169.254/api")

    # Válido
    dm = DecisionModelConfig(provider="custom", endpoint="https://api.openai.com/v1")
    assert dm.endpoint == "https://api.openai.com/v1"


def test_start_mission_blocks_ssrf_endpoint():
    """RuntimeApplicationService.start_mission debe interceptar y bloquear URLs SSRF maliciosas."""
    service = RuntimeApplicationService()
    with pytest.raises(SSRFProtectionViolation):
        service.start_mission(
            goal="Test SSRF injection",
            base_url="http://169.254.169.254/latest/meta-data",
        )
