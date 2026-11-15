"""Los `except Exception` que la auditoría (AUD-23, BLE001) estrechó siguen tolerando lo que antes tragaban.

Estrechar una excepción es seguro solo si cubre todo lo que el cuerpo puede lanzar de verdad. Aquí están los casos que
no son obvios: JSON anidado a propósito, valores que JSON no serializa, rutas en discos distintos y un Origin ilegible.
"""

import os

from praxeon.core.session_context import SessionContextManager
from praxeon.models.schema import Step, StepType
from praxeon.models.trace import TraceParser

NESTED = "[" * 200_000          # json.loads lo rechaza con RecursionError, no con ValueError


def test_a_deeply_nested_json_argument_does_not_break_the_trace_parser():
    text = f"Thought: probar\nAction: read_file\nAction Input: {NESTED}\n"
    trajectory = TraceParser.from_scratchpad(text)
    tool_steps = [s for s in trajectory.steps if s.tool_name == "read_file"]
    assert tool_steps and tool_steps[0].tool_args == {"input": NESTED}


def test_the_semantic_hash_survives_arguments_json_cannot_serialize():
    """``json.dumps`` da TypeError con un valor no serializable (un set): se usa la representación del dict."""
    step = Step(id="s0", step_type=StepType.TOOL_CALL, tool_name="t", tool_args={"a": {1, 2}})
    assert step.semantic_hash and len(step.semantic_hash) == 16


def test_listing_a_directory_skips_paths_relpath_cannot_express(tmp_path, monkeypatch):
    """En Windows ``os.path.relpath`` lanza ValueError entre discos distintos; el listado devuelve lo que pudo."""
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")

    def other_drive(*_args, **_kwargs):
        raise ValueError("path is on mount 'C:', start on mount 'D:'")

    monkeypatch.setattr(os.path, "relpath", other_drive)
    assert SessionContextManager.get_directory_tree(str(tmp_path)) == []


def test_an_unreadable_origin_is_rejected_not_raised():
    """Fail-closed: un Origin que urlparse no puede leer (IPv6 sin cerrar) se rechaza."""
    from types import SimpleNamespace

    from praxeon.server.websocket import _is_origin_allowed

    websocket = SimpleNamespace(headers={"origin": "http://[::1"}, client=SimpleNamespace(host="203.0.113.9"))
    assert _is_origin_allowed(websocket) is False
