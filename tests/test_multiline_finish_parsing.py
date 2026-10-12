"""El intérprete de acciones acepta finish/herramientas con argumentos en varias líneas."""

from praxeon.live_agent.llm_adapter import parse_llm_steps

TQ = '"""'
PLAN = "# PLAN\n\nPaso 1: arquitectura\nPaso 2: datos\nAction: esto es texto, no una acción"


def test_triple_quoted_multiline_finish_is_one_finish_with_full_text():
    out = f"Thought: ya tengo el plan\nAction: finish(summary={TQ}{PLAN}{TQ})"
    steps = parse_llm_steps(out)
    assert [s["tool_name"] for s in steps] == ["finish"]
    # el texto íntegro llega (la normalización previa solo puede colapsar líneas en blanco)
    summary = steps[0]["tool_args"]["summary"]
    assert summary.replace("\n\n", "\n") == PLAN.replace("\n\n", "\n")
    assert "Action: esto es texto" in summary


def test_multiline_json_finish_keeps_working():
    out = 'Thought: listo\nAction: finish({\n  "summary": "línea 1\\nlínea 2",\n  "approved": true\n})'
    (step,) = parse_llm_steps(out)
    assert step["tool_name"] == "finish"
    assert step["tool_args"]["approved"] is True
    assert step["tool_args"]["summary"] == "línea 1\nlínea 2"


def test_python_kwargs_with_escaped_quotes_and_newlines():
    out = 'Thought: x\nAction: finish(summary="dijo \\"hola\\"\\nfin")'
    (step,) = parse_llm_steps(out)
    assert step["tool_args"]["summary"] == 'dijo "hola"\nfin'


def test_steps_after_a_multiline_action_are_still_split():
    out = (
        f"Thought: primero\nAction: run_command(command={TQ}dir\n/b{TQ})\n"
        'Thought: luego\nAction: finish(summary="ok")'
    )
    steps = parse_llm_steps(out)
    assert [s["tool_name"] for s in steps] == ["run_command", "finish"]
    assert steps[0]["tool_args"]["command"] == "dir\n/b"
    assert steps[1]["tool_args"]["summary"] == "ok"


def test_single_line_actions_unchanged():
    steps = parse_llm_steps('Thought: a\nAction: read_file({"path": "a.txt"})\nThought: b\nAction: finish("hecho")')
    assert [(s["tool_name"], s["tool_args"]) for s in steps] == [
        ("read_file", {"path": "a.txt"}),
        ("finish", {"summary": "hecho"}),
    ]
