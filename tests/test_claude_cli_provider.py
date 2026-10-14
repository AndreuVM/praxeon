"""Proveedor claude_cli: un proceso `claude -p` (stream-json) por conversación, sin herramientas ni claves de API."""

import json
import sys

import pytest

from praxeon.agent_llm import ClaudeCLILLM, SimulatedAgentLLM, create_agent_llm

# Ejecutable falso: habla stream-json, responde "turno N" y anota cada mensaje recibido junto con su PID y entorno.
FAKE = r'''
import json, os, sys
args = sys.argv[1:]
sp = ""
if "--system-prompt-file" in args:
    sp = open(args[args.index("--system-prompt-file") + 1], encoding="utf-8").read()
log = os.environ["FAKE_CLAUDE_LOG"]
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
n = 0
for line in sys.stdin:
    msg = json.loads(line)["message"]["content"]
    n += 1
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"pid": os.getpid(), "n": n, "msg": msg, "args": args, "system": sp,
                            "has_key": "ANTHROPIC_API_KEY" in os.environ, "cwd": os.getcwd()}) + "\n")
    if mode == "flagged":
        print(json.dumps({"type": "result", "is_error": True, "result": "API Error: safeguards flagged this message"}), flush=True)
        continue
    if mode == "die":
        sys.exit(3)
    print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
    print("ruido que no es json", flush=True)
    print(json.dumps({"type": "result", "is_error": False, "result": f"turno {n}", "permission_denials": []}), flush=True)
'''


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE, encoding="utf-8")
    if sys.platform == "win32":
        exe = tmp_path / "claude.cmd"
        exe.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = tmp_path / "claude"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    def records():
        return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    return str(exe), records


U = lambda t: {"role": "user", "content": t}       # noqa: E731
A = lambda t: {"role": "assistant", "content": t}  # noqa: E731


def test_cli_is_called_as_a_pure_llm_without_api_keys(fake_claude, monkeypatch):
    exe, records = fake_claude
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-no-debe-usarse")
    llm = ClaudeCLILLM(model="opus", executable=exe)
    try:
        assert llm.generate([U("hola")], system_prompt="Eres un nodo.") == "turno 1"
        rec = records()[0]
        args = rec["args"]
        assert args[args.index("--tools") + 1] == "" and "--no-session-persistence" in args and "--disable-slash-commands" in args
        assert args[args.index("--input-format") + 1] == "stream-json" and args[args.index("--model") + 1] == "opus"
        assert rec["system"] == "Eres un nodo."
        assert rec["msg"] == "hola"                      # un único mensaje de usuario se envía tal cual
        assert rec["has_key"] is False                   # la CLI usa la suscripción, no la clave de API
        assert "praxeon-claude-" in rec["cwd"]           # directorio temporal vacío, no el proyecto
    finally:
        llm.end_session()


def test_one_process_is_reused_and_only_new_messages_are_sent(fake_claude):
    exe, records = fake_claude
    llm = ClaudeCLILLM(executable=exe)
    try:
        sys_p = "Eres un nodo."
        assert llm.generate([U("Comienza")], system_prompt=sys_p) == "turno 1"
        assert llm.generate([U("Comienza"), A("lo que sea"), U("Observación 1")], system_prompt=sys_p) == "turno 2"
        assert llm.generate([U("Comienza"), A("x"), U("Observación 1"), A("y"), U("Observación 2")], system_prompt=sys_p) == "turno 3"
        recs = records()
        assert len({r["pid"] for r in recs}) == 1                       # mismo proceso en los tres turnos
        assert [r["msg"] for r in recs] == ["Comienza", "Observación 1", "Observación 2"]   # sin reenviar el historial
    finally:
        llm.end_session()


def test_history_that_does_not_continue_restarts_the_process(fake_claude):
    exe, records = fake_claude
    llm = ClaudeCLILLM(executable=exe)
    try:
        llm.generate([U("Comienza")], system_prompt="s")
        llm.generate([U("Comienza"), A("a"), U("obs 1")], system_prompt="s")
        # historial reescrito (p. ej. reintento del nodo): la observación 1 ya no coincide
        out = llm.generate([U("Comienza"), A("a"), U("obs 1 distinta"), A("b"), U("obs 2")], system_prompt="s")
        assert out == "turno 1"                                        # proceso nuevo
        recs = records()
        assert recs[-1]["pid"] != recs[0]["pid"]
        assert "[USER]: obs 1 distinta" in recs[-1]["msg"] and recs[-1]["msg"].endswith("[ASSISTANT]:")  # con el historial entero
    finally:
        llm.end_session()


def test_end_session_stops_the_process_and_a_dead_one_is_replaced(fake_claude):
    exe, records = fake_claude
    llm = ClaudeCLILLM(executable=exe)
    llm.generate([U("a")], system_prompt="s")
    session = next(iter(llm._sessions.values()))
    session.proc.kill()
    session.proc.wait(timeout=5)
    # el proceso murió: el siguiente turno abre otro con el historial
    assert llm.generate([U("a"), A("r"), U("b")], system_prompt="s") == "turno 1"
    session = next(iter(llm._sessions.values()))
    workdir = session.workdir
    llm.end_session()
    assert not llm._sessions and session.proc.poll() is not None
    import os
    assert not os.path.exists(workdir)


def test_different_system_prompts_use_separate_sessions(fake_claude):
    exe, records = fake_claude
    llm = ClaudeCLILLM(executable=exe)
    try:
        llm.generate([U("Comienza")], system_prompt="nodo A")
        llm.generate([U("Comienza")], system_prompt="nodo B")
        llm.generate([U("Comienza"), A("x"), U("obs")], system_prompt="nodo A")
        recs = records()
        assert len({r["pid"] for r in recs}) == 2 and recs[-1]["msg"] == "obs"
    finally:
        llm.end_session()


def test_errors_from_the_cli_are_reported_and_reset_the_session(fake_claude, monkeypatch):
    exe, _ = fake_claude
    llm = ClaudeCLILLM(executable=exe, timeout=10)
    try:
        monkeypatch.setenv("FAKE_CLAUDE_MODE", "flagged")
        with pytest.raises(RuntimeError, match="safeguards"):
            llm.generate([U("x")])
        assert not llm._sessions
        monkeypatch.setenv("FAKE_CLAUDE_MODE", "die")
        with pytest.raises(RuntimeError, match="terminó"):
            llm.generate([U("x")])
        assert not llm._sessions
    finally:
        llm.end_session()


def test_factory_and_missing_cli(monkeypatch):
    monkeypatch.setattr("praxeon.agent_llm.shutil.which", lambda name: None)
    with pytest.raises(ValueError, match="CLI 'claude'"):
        create_agent_llm(provider="claude_cli")

    monkeypatch.setattr("praxeon.agent_llm.shutil.which", lambda name: "C:/bin/claude.exe")
    llm = create_agent_llm(provider="claude_cli")
    assert isinstance(llm, ClaudeCLILLM) and llm.provider_name == "claude_cli" and llm.model_name == "sonnet"
    assert create_agent_llm(provider="claude-cli", model="haiku").model_name == "haiku"

    # «auto» nunca la elige
    monkeypatch.setattr("praxeon.agent_llm.is_ollama_online", lambda *a, **k: False)
    for name in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert isinstance(create_agent_llm(provider="auto"), SimulatedAgentLLM)


def test_the_node_prompt_asks_for_a_one_line_thought_not_a_justification():
    """«…y por qué» activaba la salvaguarda reasoning_extraction de Claude con Sonnet/Opus."""
    from praxeon.server.runtime_service.node_prompt import build_node_system_prompt

    prompt = build_node_system_prompt("C:/p", "env", "misión")
    assert "Thought: <una frase con tu siguiente paso>" in prompt and "y por qué" not in prompt


def test_workflow_driver_closes_the_llm_conversation_after_each_node():
    """El driver llama a end_session del LLM del nodo al terminar (también si falla)."""
    import inspect

    from praxeon.workflows import mission_driver

    src = inspect.getsource(mission_driver)
    assert "end_session" in src and "finally:" in src
