"""Los modelos pequeños suelen llamar a las herramientas con argumentos posicionales: edit_file("a.py", "contenido")."""

import pytest

from praxeon.live_agent import parse_llm_steps


def _args(action: str):
    step = parse_llm_steps(f"Thought: x\nAction: {action}")[0]
    return step["tool_name"], step["tool_args"]


def test_positional_path_and_content_for_write_tools():
    assert _args('edit_file("a.py", "x = 1")') == ("edit_file", {"path": "a.py", "content": "x = 1"})
    assert _args("write_file('b.py', 'y = 2')") == ("edit_file", {"path": "b.py", "content": "y = 2"})


def test_escaped_newlines_and_triple_quotes_in_positional_content():
    bs = chr(92)
    tool, args = _args('edit_file("m.py", "def f():' + bs + 'n    return 1")')
    assert (tool, args["path"], args["content"]) == ("edit_file", "m.py", "def f():\n    return 1")
    tool, args = _args('edit_file("m.py", """def f():\n    return 1\n""")')
    assert args["content"] == "def f():\n    return 1\n"


@pytest.mark.parametrize("action,expected", [
    ('read_file("a.py")', ("read_file", {"path": "a.py"})),
    ('run_command("pytest -q")', ("run_command", {"command": "pytest -q"})),
    ('finish("hecho")', ("finish", {"summary": "hecho"})),
    ('edit_file(path="a.py", content="z")', ("edit_file", {"path": "a.py", "content": "z"})),
])
def test_single_positional_and_keyword_forms_keep_working(action, expected):
    assert _args(action) == expected
