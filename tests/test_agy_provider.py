"""Proveedor agy (FE-014): la CLI de Antigravity como LLM puro, con TODAS sus herramientas bloqueadas.

`agy` es un agente con unas 60 herramientas propias que en modo `-p` puede arrancar con todo aprobado. Estas pruebas fijan, con un
`agy` falso que habla su protocolo (sin red ni cuenta), la configuración que lo mantiene como generador de texto y qué hace el
proveedor si la barrera fallara. Una prueba opcional (PRAXEON_TEST_REAL_AGY=1) lo comprueba con el agy instalado de verdad.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from praxeon import agent_llm, vision
from praxeon.agent_llm import AgyCLILLM, ClaudeCLILLM, SimulatedAgentLLM, create_agent_llm

# agy falso: protocolo NDJSON real (event init / step_update / result), anota lo que recibe y deja ver cómo se lanzó.
FAKE = r'''
import json, os, sys
args = sys.argv[1:]
log = os.environ["FAKE_AGY_LOG"]
mode = os.environ.get("FAKE_AGY_MODE", "ok")
settings = {}
if "--settings" in args:
    settings = json.load(open(args[args.index("--settings") + 1], encoding="utf-8"))
hooks_path = os.path.join(os.getcwd(), ".agents", "hooks.json")
hooks = json.load(open(hooks_path, encoding="utf-8")) if os.path.exists(hooks_path) else None
n = 0
def emit(obj):
    print(json.dumps(obj), flush=True)
emit({"event": "init", "conversation_id": "c1", "init": {"cwd": os.getcwd(), "tools": ["run_command", "write_to_file"], "permission_mode": "request-review"}})
for line in sys.stdin:
    msg = json.loads(line)
    n += 1
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"pid": os.getpid(), "n": n, "event": msg.get("event"), "msg": msg["message"]["content"], "args": args,
                            "settings": settings, "hooks": hooks, "cwd": os.getcwd(),
                            "has_gemini_key": "GEMINI_API_KEY" in os.environ, "has_google_key": "GOOGLE_API_KEY" in os.environ}) + "\n")
    if mode == "die":
        sys.exit(3)
    emit({"event": "step_update", "step_update": {"conversation_id": "c1", "step_index": 0, "state": "DONE", "step_type": "user_input"}})
    print("ruido que no es json", flush=True)
    if mode == "blocked":
        emit({"event": "step_update", "step_update": {"step_index": 1, "state": "ACTIVE", "step_type": "tool", "tool_name": "write_file"}})
        emit({"event": "step_update", "step_update": {"step_index": 1, "state": "ERROR", "step_type": "tool", "tool_name": "write_file"}})
    if mode == "executed":
        emit({"event": "step_update", "step_update": {"step_index": 1, "state": "ACTIVE", "step_type": "tool", "tool_name": "run_command"}})
        emit({"event": "step_update", "step_update": {"step_index": 1, "state": "DONE", "step_type": "tool", "tool_name": "run_command"}})
    if mode in ("login", "creds", "quota", "failed"):
        err = {"login": "not authenticated: please sign in", "creds": "saved credentials expired", "quota": "RESOURCE_EXHAUSTED: quota exceeded", "failed": "model exploded"}[mode]
        emit({"event": "result", "result": {"conversation_id": "c1", "status": "ERROR", "response": "", "error": err}})
        continue
    emit({"event": "result", "result": {"conversation_id": "c1", "status": "SUCCESS", "response": f"turno {n}\n", "num_turns": n,
          "usage": {"input_tokens": 1000 + n, "output_tokens": 7, "thinking_tokens": 3, "cache_read_tokens": 400}}})
'''


@pytest.fixture
def fake_agy(tmp_path, monkeypatch):
    script = tmp_path / "fake_agy.py"
    script.write_text(FAKE, encoding="utf-8")
    if sys.platform == "win32":
        exe = tmp_path / "agy.cmd"
        exe.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = tmp_path / "agy"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_AGY_LOG", str(log))

    def records():
        return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    return str(exe), records


U = lambda t: {"role": "user", "content": t}       # noqa: E731
A = lambda t: {"role": "assistant", "content": t}  # noqa: E731


def test_agy_is_launched_as_a_pure_llm_with_every_tool_closed(fake_agy, monkeypatch):
    exe, records = fake_agy
    monkeypatch.setenv("GEMINI_API_KEY", "clave-que-no-debe-usarse")
    monkeypatch.setenv("GOOGLE_API_KEY", "otra-clave")
    llm = AgyCLILLM(model="gemini-4-carbon-low", executable=exe)
    try:
        assert llm.generate([U("hola")], system_prompt="Eres un nodo.") == "turno 1"
        rec = records()[0]
        args = rec["args"]
        assert args[args.index("--input-format") + 1] == "stream-json" and args[args.index("--output-format") + 1] == "stream-json"
        assert args[args.index("--model") + 1] == "gemini-4-carbon-low"
        assert "-p=" in args                                  # «-p» a secas tomaría el argumento siguiente como prompt
        assert "--sandbox" in args and "--disable-slash-commands" in args
        assert "--dangerously-skip-permissions" not in args and "--mode" not in args
        assert rec["event"] == "user"
        assert rec["has_gemini_key"] is False and rec["has_google_key"] is False   # usa la sesión de la cuenta, no una clave
        assert "praxeon-agy-" in rec["cwd"]                   # directorio temporal y vacío, no el proyecto

        # barrera principal: el hook PreToolUse que rechaza cualquier herramienta (y falla cerrado)
        pre = rec["hooks"]["hooks"]["PreToolUse"]
        assert len(pre) == 1 and pre[0]["matcher"] == ".*"
        (hook,) = pre[0]["hooks"]
        assert hook["type"] == "command" and "agy_hook.py" in hook["command"] and " -I " in hook["command"]   # el hook de lista de permitidas, VACÍA: bloquea todo
        # barreras adicionales (solo esta sesión: `--settings`, nunca el settings.json del usuario)
        assert rec["settings"]["toolPermission"] == "request-review"
        assert rec["settings"]["allowNonWorkspaceAccess"] is False
        assert rec["settings"]["permissions"]["allow"] == []
        assert set(rec["settings"]["permissions"]["deny"]) == {
            "command(*)", "write_file(*)", "read_file(*)", "read_url(*)", "mcp(*)", "execute_url(*)", "download_url(*)",
        }
    finally:
        llm.end_session()


def test_the_system_prompt_travels_in_the_first_message_with_a_no_tools_note(fake_agy):
    exe, records = fake_agy
    llm = AgyCLILLM(executable=exe)
    try:
        llm.generate([U("Comienza")], system_prompt="Eres un nodo de razonamiento.")
        msg = records()[0]["msg"]
        assert msg.startswith("<instrucciones_del_sistema>\nEres un nodo de razonamiento.\n</instrucciones_del_sistema>")
        assert "NO uses ninguna de tus herramientas" in msg and msg.endswith("Comienza")
        # sin prompt de sistema, la nota de «sin herramientas» sigue yendo
        llm2 = AgyCLILLM(executable=exe)
        try:
            llm2.generate([U("solo")])
            assert "<instrucciones_del_sistema>" not in records()[-1]["msg"] and "NO uses ninguna de tus herramientas" in records()[-1]["msg"]
        finally:
            llm2.end_session()
    finally:
        llm.end_session()


def test_one_process_is_reused_and_only_new_messages_are_sent(fake_agy):
    exe, records = fake_agy
    llm = AgyCLILLM(executable=exe)
    try:
        s = "Eres un nodo."
        assert llm.generate([U("Comienza")], system_prompt=s) == "turno 1"
        assert llm.generate([U("Comienza"), A("lo que sea"), U("Observación 1")], system_prompt=s) == "turno 2"
        assert llm.generate([U("Comienza"), A("x"), U("Observación 1"), A("y"), U("Observación 2")], system_prompt=s) == "turno 3"
        recs = records()
        assert len({r["pid"] for r in recs}) == 1
        # los turnos siguientes llevan solo lo nuevo: ni el prompt de sistema ni el historial
        assert recs[1]["msg"] == "Observación 1" and recs[2]["msg"] == "Observación 2"
        assert llm.stateful_session is True
    finally:
        llm.end_session()


def test_history_that_does_not_continue_restarts_the_process(fake_agy):
    exe, records = fake_agy
    llm = AgyCLILLM(executable=exe)
    try:
        llm.generate([U("Comienza")], system_prompt="s")
        llm.generate([U("Comienza"), A("a"), U("obs 1")], system_prompt="s")
        out = llm.generate([U("Comienza"), A("a"), U("obs 1 distinta"), A("b"), U("obs 2")], system_prompt="s")
        assert out == "turno 1"                                        # proceso nuevo
        recs = records()
        assert recs[-1]["pid"] != recs[0]["pid"]
        assert "[USER]: obs 1 distinta" in recs[-1]["msg"] and recs[-1]["msg"].endswith("[ASSISTANT]:")
        assert "<instrucciones_del_sistema>" in recs[-1]["msg"]        # el proceso nuevo vuelve a recibir el sistema
    finally:
        llm.end_session()


def test_a_dead_process_is_replaced_and_end_session_cleans_up(fake_agy):
    exe, _ = fake_agy
    llm = AgyCLILLM(executable=exe)
    llm.generate([U("a")], system_prompt="s")
    session = next(iter(llm._sessions.values()))
    session.proc.kill()
    session.proc.wait(timeout=5)
    assert llm.generate([U("a"), A("r"), U("b")], system_prompt="s") == "turno 1"
    session = next(iter(llm._sessions.values()))
    workdir = session.workdir
    llm.end_session()
    assert not llm._sessions and session.proc.poll() is not None
    assert not os.path.exists(workdir)


def test_tool_attempts_that_the_hook_blocks_are_logged_but_do_not_fail_the_turn(fake_agy, monkeypatch, caplog):
    exe, _ = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", "blocked")
    llm = AgyCLILLM(executable=exe)
    try:
        with caplog.at_level(logging.WARNING, logger="praxeon.agent_llm"):
            assert llm.generate([U("x")]) == "turno 1"
        assert any("bloqueó 1 intento" in r.message and "write_file" in r.message for r in caplog.records)
    finally:
        llm.end_session()


def test_a_tool_that_actually_ran_kills_the_session_and_fails_loudly(fake_agy, monkeypatch):
    """Si algún día agy dejara de respetar el hook, no se sigue en silencio: se mata el proceso y se falla."""
    exe, _ = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", "executed")
    llm = AgyCLILLM(executable=exe)
    try:
        with pytest.raises(RuntimeError, match="ejecutó por su cuenta «run_command»"):
            llm.generate([U("x")])
        assert not llm._sessions
    finally:
        llm.end_session()


@pytest.mark.parametrize(("mode", "pattern"), [
    ("login", "no tiene sesión iniciada. Ejecuta 'agy'"),
    ("creds", "no tiene sesión iniciada"),
    ("quota", "Cuota o límite de uso agotado"),
    ("failed", "La CLI devolvió un error: model exploded"),
    ("die", "terminó inesperadamente"),
])
def test_errors_are_translated_and_reset_the_session(fake_agy, monkeypatch, mode, pattern):
    exe, _ = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", mode)
    llm = AgyCLILLM(executable=exe, timeout=15)
    try:
        with pytest.raises(RuntimeError, match=pattern):
            llm.generate([U("x")])
        assert not llm._sessions
    finally:
        llm.end_session()


def test_a_login_error_explains_the_own_profile(fake_agy, monkeypatch, tmp_path):
    exe, _ = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", "login")
    monkeypatch.setenv("PRAXEON_AGY_PROFILE_DIR", str(tmp_path / "perfil de agy"))
    monkeypatch.setenv("PRAXEON_AGY_ISOLATE", "1")
    llm = AgyCLILLM(executable=exe, timeout=15)
    try:
        with pytest.raises(RuntimeError, match=r"perfil propio de agy.*PRAXEON_AGY_ISOLATE=0"):
            llm.generate([U("x")])
    finally:
        llm.end_session()
    monkeypatch.setenv("PRAXEON_AGY_ISOLATE", "0")           # con el perfil del usuario el mensaje no habla del perfil propio
    llm = AgyCLILLM(executable=exe, timeout=15)
    try:
        with pytest.raises(RuntimeError, match="no tiene sesión iniciada") as exc:
            llm.generate([U("x")])
        assert "perfil propio" not in str(exc.value)
    finally:
        llm.end_session()


def test_usage_is_recorded_from_the_result_event(fake_agy):
    exe, _ = fake_agy
    llm = AgyCLILLM(executable=exe)
    try:
        llm.generate([U("x")], system_prompt="s")
        assert llm.last_usage == {"prompt_tokens": 1001, "output_tokens": 7, "cached_tokens": 400}
    finally:
        llm.end_session()


def test_images_are_rejected_with_a_reason(fake_agy):
    exe, _ = fake_agy
    llm = AgyCLILLM(executable=exe)
    try:
        png = {"mime": "image/png", "data": "iVBORw0KGgo=", "width": 1, "height": 1}
        with pytest.raises(RuntimeError, match=r"\[agy\] La CLI de agy no admite imágenes"):
            llm.generate([{"role": "user", "content": "mira", "images": [png]}])
    finally:
        llm.end_session()
    cap = vision.capability("agy", "gemini-3.8-flash-medium")
    assert cap["supported"] is False
    assert vision.model_supports_vision("agy", "claude-sonnet-5-5-high") is False


def test_factory_missing_cli_and_defaults(monkeypatch):
    monkeypatch.setattr("praxeon.agent_llm.shutil.which", lambda name: None)
    with pytest.raises(ValueError, match="CLI 'agy'"):
        create_agent_llm(provider="agy")

    monkeypatch.setattr("praxeon.agent_llm.shutil.which", lambda name: "C:/bin/agy.exe")
    llm = create_agent_llm(provider="agy")
    assert isinstance(llm, AgyCLILLM) and isinstance(llm, ClaudeCLILLM)
    assert llm.provider_name == "agy" and llm.model_name == "gemini-3.8-flash-medium"
    assert create_agent_llm(provider="antigravity", model="claude-opus-5-5-low").model_name == "claude-opus-5-5-low"
    assert agent_llm.PROVIDER_PRESETS["agy"]["default_model"] in agent_llm.PROVIDER_PRESETS["agy"]["recommended_models"]

    # «auto» nunca la elige
    monkeypatch.setattr("praxeon.agent_llm.is_ollama_online", lambda *a, **k: False)
    for name in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert isinstance(create_agent_llm(provider="auto"), SimulatedAgentLLM)


def test_the_claude_provider_is_unchanged_by_the_shared_session_code():
    assert ClaudeCLILLM._BINARY == "claude" and ClaudeCLILLM("sonnet", executable="x").provider_name == "claude_cli"
    assert ClaudeCLILLM._first_message(ClaudeCLILLM("sonnet", executable="x"), "sistema", "texto") == "texto"


@pytest.mark.skipif(not shutil.which("agy") or os.environ.get("PRAXEON_TEST_REAL_AGY") != "1",
                    reason="usa el agy instalado y la cuenta del usuario: se activa con PRAXEON_TEST_REAL_AGY=1")
def test_real_agy_cannot_act_on_its_own_even_when_asked():
    """Con el agy real: se le pide escribir un archivo y ejecutar un comando; el hook que genera el proveedor lo impide."""
    work = Path(tempfile.mkdtemp(prefix="praxeon-agy-real-"))
    try:
        (work / ".agents").mkdir()
        hook = work / "agy_hook.py"
        shutil.copyfile(Path(agent_llm.__file__).with_name("agy_hook.py"), hook)
        policy = work / "agy_policy.json"
        policy.write_text(json.dumps({"tools": [], "subagents": [], "caps": {}}), encoding="utf-8")
        command = f'"{sys.executable}" -I "{hook}" "{policy}" "{work}"'
        (work / ".agents" / "hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": command}]}]}}), encoding="utf-8")
        settings = work / "s.json"
        settings.write_text(json.dumps(agent_llm._AGY_SETTINGS), encoding="utf-8")
        prompt = "Crea un archivo prueba.txt con el texto hola usando tu herramienta de escribir archivos y ejecuta: echo hecho > cmd.txt"
        out = subprocess.run(
            [shutil.which("agy"), "-p", prompt, "--output-format", "stream-json", "--model", "gemini-3.8-flash-low", "--settings", str(settings)],
            cwd=work, capture_output=True, text=True, encoding="utf-8", timeout=240, check=False,
        )
        assert out.returncode == 0, out.stderr[-300:]
        assert not (work / "prueba.txt").exists() and not (work / "cmd.txt").exists()
        events = [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]
        executed = [e for e in events if e.get("event") == "step_update" and e["step_update"].get("step_type") == "tool" and e["step_update"].get("state") == "DONE"]
        assert executed == []
    finally:
        shutil.rmtree(work, ignore_errors=True)
