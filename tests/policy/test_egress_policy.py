"""Política de salida de red y validación anti-SSRF de endpoints (``policy/egress.py``; AUD-23, Q-03).

Es la defensa contra que un agente (o una configuración) apunte a metadatos cloud, a loopback o a la red interna: cada
regla se comprueba por separado y también la que gana cuando varias se solapan.
"""

import pytest

from praxeon.policy.egress import (
    EgressMode,
    EgressPolicy,
    SSRFProtectionViolation,
    validate_provider_endpoint,
)


# --- clasificación de destinos -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("host, expected", [
    ("169.254.169.254", True), ("METADATA.GOOGLE.INTERNAL", True), ("169.254.1.7", True),
    ("example.com", False), ("8.8.8.8", False),
])
def test_cloud_metadata_hosts(host, expected):
    assert EgressPolicy().is_cloud_metadata(host) is expected


@pytest.mark.parametrize("host, expected", [
    ("localhost", True), (" LOCALHOST ", True), ("127.0.0.1", True), ("::1", True), ("0.0.0.0", True),
    ("127.0.0.2", True), ("localhost.localdomain", True),
    ("example.com", False), ("8.8.8.8", False), ("10.0.0.1", False),
])
def test_loopback_hosts(host, expected):
    assert EgressPolicy().is_loopback(host) is expected


@pytest.mark.parametrize("host, expected", [
    ("10.0.0.1", True), ("192.168.1.1", True), ("172.16.0.9", True),
    ("8.8.8.8", False), ("no-es-una-ip", False), ("", False),
])
def test_private_ips(host, expected):
    assert EgressPolicy().is_private_ip(host) is expected


# --- check_destination -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("host, fragment", [
    ("169.254.169.254", "metadatos cloud"),
    ("localhost", "loopback"),
    ("10.1.2.3", "privada"),
])
def test_internal_destinations_are_blocked_even_when_everything_else_is_allowed(host, fragment):
    allowed, reason = EgressPolicy(mode=EgressMode.ALLOW_ALL).check_destination(host)
    assert allowed is False and fragment in reason


def test_block_all_blocks_any_external_destination():
    allowed, reason = EgressPolicy().check_destination("example.com", 443)
    assert allowed is False and "block_all" in reason


def test_an_allowlist_lets_only_listed_hosts_through():
    policy = EgressPolicy(mode=EgressMode.ALLOWLIST, allowed_hosts={"API.Example.com"})
    assert policy.check_destination("api.example.com", 443) == (True, None)
    allowed, reason = policy.check_destination("otro.example.com", 443)
    assert allowed is False and "lista blanca" in reason


def test_ports_outside_the_allowed_set_are_blocked():
    policy = EgressPolicy(mode=EgressMode.ALLOW_ALL)
    assert policy.check_destination("example.com", 443)[0] is True
    allowed, reason = policy.check_destination("example.com", 22)
    assert allowed is False and "22" in reason
    assert policy.check_destination("example.com", None)[0] is True            # sin puerto: no hay nada que comprobar


def test_the_internal_blocks_can_be_switched_off_explicitly():
    policy = EgressPolicy(mode=EgressMode.ALLOW_ALL, block_localhost=False, block_private_ips=False,
                          block_cloud_metadata=False)
    assert all(policy.check_destination(h, 80)[0] for h in ("localhost", "10.0.0.1", "169.254.169.254"))


# --- evaluate_command_egress -------------------------------------------------------------------------------------------
def test_an_empty_command_has_no_egress():
    assert EgressPolicy().evaluate_command_egress("   ") == (True, None)


@pytest.mark.parametrize("command", [
    "curl https://example.com", "wget -q http://example.com/x", r"C:\tools\curl.exe -s x",
    "echo hola; ssh host", "cat f | nc host 80", "Invoke-WebRequest https://example.com",
])
def test_block_all_rejects_network_utilities(command):
    allowed, reason = EgressPolicy().evaluate_command_egress(command)
    assert allowed is False and "bloqueado por política de egress" in reason


def test_network_utilities_are_not_banned_outside_block_all():
    policy = EgressPolicy(mode=EgressMode.ALLOWLIST, allowed_hosts={"api.example.com"})
    assert policy.evaluate_command_egress("curl https://api.example.com/v1") == (True, None)


def test_urls_inside_a_command_are_checked_against_the_policy():
    policy = EgressPolicy(mode=EgressMode.ALLOWLIST, allowed_hosts={"api.example.com"})
    allowed, reason = policy.evaluate_command_egress("python -c \"urlopen('https://evil.example.org/x')\"")
    assert allowed is False and "evil.example.org" in reason
    allowed, reason = policy.evaluate_command_egress("curl http://api.example.com:8080/x")
    assert allowed is False and "8080" in reason


def test_direct_ip_addresses_inside_a_command_are_checked():
    policy = EgressPolicy(mode=EgressMode.ALLOW_ALL)
    allowed, reason = policy.evaluate_command_egress("telnet 192.168.1.10:23")
    assert allowed is False and "192.168.1.10" in reason


def test_numbers_that_look_like_an_ip_but_are_not_do_not_trigger_a_block():
    policy = EgressPolicy(mode=EgressMode.ALLOW_ALL)
    assert policy.evaluate_command_egress("echo 999.999.999.999") == (True, None)


# --- validate_provider_endpoint (SSRF) ---------------------------------------------------------------------------------
@pytest.mark.parametrize("url", [None, "", "   "])
def test_an_absent_endpoint_is_not_an_error(url):
    assert validate_provider_endpoint(url) is None


@pytest.mark.parametrize("url, fragment", [
    ("ftp://api.openai.com/v1", "Esquema"),
    ("file:///etc/passwd", "Esquema"),
    ("https://user:pass@api.openai.com/v1", "credenciales"),
    ("https://", "hostname"),
    ("https://169.254.169.254/latest", "metadatos cloud"),
    ("http://metadata.google.internal/x", "metadatos cloud"),
])
def test_dangerous_endpoints_are_rejected_in_every_profile(url, fragment):
    with pytest.raises(SSRFProtectionViolation, match=fragment):
        validate_provider_endpoint(url, allow_custom=True)


def test_production_requires_https():
    with pytest.raises(SSRFProtectionViolation, match="https"):
        validate_provider_endpoint("http://api.openai.com/v1", profile="production")


@pytest.mark.parametrize("url", ["https://localhost/v1", "https://127.0.0.1/v1", "https://[::1]/v1", "https://0.0.0.0/v1"])
def test_production_blocks_loopback_even_with_custom_endpoints(url):
    with pytest.raises(SSRFProtectionViolation, match="loopback"):
        validate_provider_endpoint(url, allow_custom=True, profile="production")


@pytest.mark.parametrize("url", ["https://10.0.0.5/v1", "https://192.168.1.2/v1", "https://240.0.0.1/v1"])
def test_production_blocks_private_and_reserved_ranges(url):
    with pytest.raises(SSRFProtectionViolation, match="privadas|locales"):
        validate_provider_endpoint(url, allow_custom=True, profile="production")


def test_custom_endpoints_are_off_by_default_and_only_known_providers_pass():
    assert validate_provider_endpoint("https://api.openai.com/v1") == "https://api.openai.com/v1"
    with pytest.raises(SSRFProtectionViolation, match="allow_custom_endpoints=False"):
        validate_provider_endpoint("https://modelos.mi-empresa.example/v1")


def test_custom_endpoints_can_be_enabled_or_listed_explicitly():
    url = "https://modelos.mi-empresa.example/v1"
    assert validate_provider_endpoint(url, allow_custom=True) == url
    assert validate_provider_endpoint(url, allowed_hosts={"Modelos.Mi-Empresa.example"}) == url


@pytest.mark.parametrize("profile", ["dev", "test"])
def test_local_models_are_allowed_in_development_profiles(profile):
    assert validate_provider_endpoint("http://localhost:11434/v1", profile=profile) == "http://localhost:11434/v1"


def test_production_accepts_a_known_provider_over_https():
    assert validate_provider_endpoint("https://api.anthropic.com/v1", profile="production")
