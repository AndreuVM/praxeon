"""Fallo latente que encontró mypy al activarlo en el núcleo (auditoría AUD-12): lanzaba AttributeError."""

from praxeon.domain.events import EventType, RuntimeEvent
from praxeon.runtime.decision_store import SqliteDecisionRepository


def test_reconstructing_decisions_from_a_proposed_action_event_works():
    repo = SqliteDecisionRepository(db_path=":memory:")
    events = [RuntimeEvent(session_id="s", sequence=1, type=EventType.ACTION_PROPOSED, node_id="a1", decision_id="d1",
                           payload={"tool": "read_file", "arguments": {"path": "README.md"}, "rationale": "leer"})]
    rec = repo.reconstruct_from_events("s", events)["d1"]
    assert rec["action"].tool_call.tool_name == "read_file"

