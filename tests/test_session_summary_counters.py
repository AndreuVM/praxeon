"""Los contadores del resumen de sesión cuentan por estado exacto (auditoría F-01/F-02)."""

import pytest

from praxeon.domain.events import EventType
from praxeon.server import RuntimeApplicationService


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXEON_CACHE_DIR", str(tmp_path / "cache"))
    return RuntimeApplicationService(db_dir=str(tmp_path / "db"))


def _decide(service, sid, status):
    service.event_bus.emit(session_id=sid, event_type=EventType.POLICY_DECIDED, node_id=f"n-{status}", payload={"status": status})


def test_review_decisions_are_counted_and_only_by_exact_status(service):
    sid = service.session_coordinator.create_session(goal="contadores")["session_id"]
    for status in ("ALLOW", "ALLOW", "REVIEW", "BLOCK", "REPLAN", "ABSTAIN"):
        _decide(service, sid, status)
    s = service.get_session_summary(sid)
    assert (s["total_decisions"], s["allowed_count"], s["blocked_count"], s["review_count"]) == (6, 2, 1, 1)


def test_a_status_that_merely_contains_a_word_is_not_counted(service):
    sid = service.session_coordinator.create_session(goal="contadores exactos")["session_id"]
    _decide(service, sid, "NOT_ALLOWED_YET")        # antes contaba como «allow» por contener la subcadena
    _decide(service, sid, "PREREVIEW")
    s = service.get_session_summary(sid)
    assert (s["allowed_count"], s["blocked_count"], s["review_count"]) == (0, 0, 0)
