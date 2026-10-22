"""Una salida grande (p. ej. el árbol de un proyecto MCP) no debe cortarse a los 100 KB, y el corte indica cuánto se perdió."""

from praxeon.policy.sanitizer import DataSanitizer


def test_default_limit_keeps_a_300kb_listing(monkeypatch):
    monkeypatch.delenv("PRAXEON_MAX_PAYLOAD_BYTES", raising=False)
    text = "x" * 300_000
    assert DataSanitizer().enforce_payload_limit(text) == text


def test_limit_is_configurable_and_truncation_reports_sizes(monkeypatch):
    monkeypatch.setenv("PRAXEON_MAX_PAYLOAD_BYTES", "2000")
    out = DataSanitizer().enforce_payload_limit("y" * 5000)
    assert "PAYLOAD_TRUNCATED_DUE_TO_SIZE_LIMIT" in out and "2000 de 5000" in out
