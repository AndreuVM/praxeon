"""Fallo latente que encontró mypy al activarlo en el núcleo (auditoría AUD-12): lanzaba AttributeError."""

from praxeon.domain.events import EventType, RuntimeEvent
from praxeon.runtime.decision_store import SqliteDecisionRepository


def test_reconstructing_decisions_from_a_proposed_action_event_works():
    repo = SqliteDecisionRepository(db_path=":memory:")
    events = [RuntimeEvent(session_id="s", sequence=1, type=EventType.ACTION_PROPOSED, node_id="a1", decision_id="d1",
                           payload={"tool": "read_file", "arguments": {"path": "README.md"}, "rationale": "leer"})]
    rec = repo.reconstruct_from_events("s", events)["d1"]
    assert rec["action"].tool_call.tool_name == "read_file"



# --- AUD-23: mypy en praxeon/server -------------------------------------------------------------------------------------
def _decision_service(tmp_path):
    from praxeon.server.services.decision_service import DecisionService

    repo = SqliteDecisionRepository(db_path=str(tmp_path / "decisions.db"))
    for decision_id, session_id in (("d1", "s1"), ("d2", "s1"), ("d3", "s2")):
        repo.save({"decision_id": decision_id, "session_id": session_id, "action_id": f"a-{decision_id}", "status": "ALLOW"})
    service = DecisionService(event_bus=None, decision_repository=repo, policy_engine=None, risk_engine=None,
                              evidence_engine=None, command_classifier=None)
    return service


def test_listing_decisions_filtered_by_session_no_longer_raises(tmp_path):
    """list_decisions(session_id=...) llamaba a ``list_all(session_id=...)``, que no acepta ese argumento (TypeError)."""
    service = _decision_service(tmp_path)
    assert sorted(d["decision_id"] for d in service.list_decisions("s1")) == ["d1", "d2"]
    assert [d["decision_id"] for d in service.list_decisions("s2")] == ["d3"]
    assert service.list_decisions("sin-decisiones") == []


def test_listing_decisions_without_session_returns_all_of_them(tmp_path):
    service = _decision_service(tmp_path)
    assert sorted(d["decision_id"] for d in service.list_decisions()) == ["d1", "d2", "d3"]


def test_fastapi_only_injects_the_request_when_it_is_annotated_with_the_class():
    """Por qué ``request: Request = None`` lleva ``type: ignore``: con ``Request | None`` FastAPI ni registra la ruta."""
    import pytest
    from fastapi import FastAPI, Request
    from fastapi.exceptions import FastAPIError
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/by-class")
    def by_class(request: Request = None):  # type: ignore[assignment]
        return {"injected": request is not None}

    assert TestClient(app).get("/by-class").json() == {"injected": True}
    with pytest.raises(FastAPIError):
        @app.get("/optional")
        def optional(request: Request | None = None):
            return {}


def test_the_service_mixins_find_the_attributes_they_declare():
    """RuntimeHost solo anota: los atributos los pone el servicio y los mixins los siguen encontrando."""
    from praxeon.server.dependencies import RuntimeApplicationService
    from praxeon.server.runtime_service.host import RuntimeHost

    assert not [name for name in vars(RuntimeHost) if not name.startswith("__")], "RuntimeHost no debe definir nada en ejecución"
    assert issubclass(RuntimeApplicationService, RuntimeHost)
