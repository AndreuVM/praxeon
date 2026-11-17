"""FE-015: herramientas de agy que el usuario activa a mano (imágenes, búsqueda web), con el hook de lista de permitidas.

El `agy` falso ejecuta DE VERDAD el comando del hook que le deja PRAXEON (como hace el real) y escribe las imágenes en un `brain/`
falso; así se prueban a la vez la política, el entrecomillado del comando (también con espacios en la ruta) y la recogida de lo
generado. Sin red ni cuenta. La prueba opt-in con el agy real es `PRAXEON_TEST_REAL_AGY=1`.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from praxeon import agy_hook, agy_tools, vision
from praxeon.agent_llm import AgyCLILLM, create_agent_llm

PIL = pytest.importorskip("PIL")

FAKE = r'''
import json, os, subprocess, sys, time
home = os.environ.get("PRAXEON_AGY_HOME") or os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), ".gemini", "antigravity-cli")
if os.environ.get("FAKE_AGY_ENVLOG"):
    open(os.environ["FAKE_AGY_ENVLOG"], "a", encoding="utf-8").write(json.dumps({"userprofile": os.environ.get("USERPROFILE"), "home": os.environ.get("HOME")}) + "\n")
conv = os.environ.get("FAKE_AGY_CONV", "conv-main")
hooks = json.load(open(os.path.join(os.getcwd(), ".agents", "hooks.json"), encoding="utf-8"))
cmd = hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
log = os.environ["FAKE_AGY_LOG"]
def emit(o):
    print(json.dumps(o), flush=True)
emit({"event": "init", "conversation_id": conv, "init": {"tools": []}})
n = 0
for line in sys.stdin:
    n += 1
    calls = json.loads(os.environ.get("FAKE_AGY_CALLS", "[]"))
    for c in calls:
        payload = json.dumps({"toolCall": {"name": c["name"], "args": c.get("args", {})}})
        r = subprocess.run(cmd, shell=True, input=payload, capture_output=True, text=True, cwd=os.path.dirname(os.getcwd()))
        allowed = r.returncode == 0
        step_type = "subagent" if c["name"] == "invoke_subagent" else "tool"
        emit({"event": "step_update", "step_update": {"step_index": n, "state": "ACTIVE", "step_type": step_type, "tool_name": c["name"]}})
        done = allowed or os.environ.get("FAKE_AGY_BYPASS") == "1"
        emit({"event": "step_update", "step_update": {"step_index": n, "state": "DONE" if done else "ERROR", "step_type": step_type, "tool_name": c["name"]}})
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"turn": n, "name": c["name"], "allowed": allowed, "rc": r.returncode, "stderr": r.stderr[:200]}) + "\n")
        if allowed and c["name"] == "generate_image":
            d = os.path.join(home, "brain", conv)
            os.makedirs(d, exist_ok=True)
            src = os.environ.get("FAKE_AGY_IMAGE")
            if src:
                name = c["args"].get("ImageName", "img") + "_" + str(int(time.time() * 1000)) + os.path.splitext(src)[1]
                open(os.path.join(d, name), "wb").write(open(src, "rb").read())
    if os.environ.get("FAKE_AGY_NOISE") == "1":     # agy informa un bloqueo como «error_message», sin tool_name: NO es una herramienta ejecutada
        emit({"event": "step_update", "step_update": {"step_index": n, "state": "DONE", "step_type": "error_message"}})
        emit({"event": "step_update", "step_update": {"step_index": n + 1, "state": "ERROR", "step_type": "generic"}})
        emit({"event": "step_update", "step_update": {"step_index": n + 2, "state": "DONE", "step_type": "planner_response"}})
    responses = json.loads(os.environ.get("FAKE_AGY_RESPONSES", "[]"))
    text = responses[n - 1] if n - 1 < len(responses) else f"turno {n}"
    if os.environ.get("FAKE_AGY_MSGLOG"):
        open(os.environ["FAKE_AGY_MSGLOG"], "a", encoding="utf-8").write(json.dumps({"msg": json.loads(line)["message"]["content"]}) + "\n")
    if n <= int(os.environ.get("FAKE_AGY_MALFORMED", "0")):      # el modelo intentó una llamada a función: agy la rechaza con este error
        emit({"event": "result", "result": {"conversation_id": conv, "status": "ERROR", "response": "", "error": text}})
        continue
    emit({"event": "result", "result": {"conversation_id": conv, "status": "SUCCESS", "response": text, "usage": {"input_tokens": 10, "output_tokens": 2}}})
'''

U = lambda t: {"role": "user", "content": t}       # noqa: E731
A = lambda t: {"role": "assistant", "content": t}  # noqa: E731
SUBAGENT = {"name": "invoke_subagent", "args": {"TypeName": "image-generator", "Workspace": "inherit", "Prompt": "dibuja"}}
GENERATE = {"name": "generate_image", "args": {"ImageName": "circulo", "Prompt": "un circulo rojo"}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """agy falso en una carpeta CON ESPACIOS, con su propio PRAXEON_AGY_HOME y la carpeta temporal de las sesiones también con espacios."""
    base = tmp_path / "carpeta con espacios"
    base.mkdir()
    script = base / "fake_agy.py"
    script.write_text(FAKE, encoding="utf-8")
    if sys.platform == "win32":
        exe = base / "agy.cmd"
        exe.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = base / "agy"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    home = base / "agy home"
    (home / "brain").mkdir(parents=True)
    monkeypatch.setenv("PRAXEON_AGY_HOME", str(home))
    monkeypatch.setenv("FAKE_AGY_LOG", str(base / "log.jsonl"))
    monkeypatch.setenv("FAKE_AGY_ENVLOG", str(base / "envlog.jsonl"))
    monkeypatch.setenv("FAKE_AGY_MSGLOG", str(base / "msglog.jsonl"))
    tmp = base / "tmp"
    tmp.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(tmp))

    def png(path: Path, exif: bool = False) -> Path:
        from PIL import Image

        im = Image.new("RGB", (64, 48), (200, 20, 20))
        if exif:
            ex = Image.Exif()
            ex[0x010F] = "MarcaSecretaDeCamara"
            im.save(path, "JPEG", exif=ex.tobytes())
        else:
            im.save(path, "PNG")
        return path

    def calls(c):
        monkeypatch.setenv("FAKE_AGY_CALLS", json.dumps(c))

    def log():
        p = base / "log.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    return {"exe": str(exe), "home": home, "png": png, "calls": calls, "log": log, "base": base}


def make(env, groups, **kw):
    return AgyCLILLM(executable=env["exe"], tools=groups, timeout=60, **kw)


# ---------------------------------------------------------------------------------------------------------- catálogo y confirmación
def test_catalog_lists_the_groups_with_risk_and_a_warning():
    cat = {g["id"]: g for g in agy_tools.catalog()}
    assert set(cat) == {"images", "web_search"}
    for g in cat.values():
        assert g["risk"] in agy_tools.RISK_LEVELS and "sin pasar por la política" in g["warning"] and g["title"] and g["summary"]
    assert "invoke_subagent:image-generator" in cat["images"]["tools"] and cat["web_search"]["tools"] == ["search_web"]


def test_the_server_side_rejects_unknown_repeated_or_unconfirmed_groups():
    assert agy_tools.validate(None, None) == () and agy_tools.validate([], ["images"]) == ()
    assert agy_tools.validate(["web_search"], ["web_search", "images"]) == ("web_search",)
    with pytest.raises(ValueError, match="desconocidas"):
        agy_tools.validate(["run_command"], ["run_command"])
    with pytest.raises(ValueError, match="repetidos"):
        agy_tools.validate(["web_search", "web_search"], ["web_search"])
    with pytest.raises(ValueError, match="confirmación explícita"):
        agy_tools.validate(["web_search"], [])
    with pytest.raises(ValueError, match="confirmación explícita"):
        agy_tools.validate(["images", "web_search"], ["images"])        # confirmar uno no confirma el otro


def test_images_need_pillow(monkeypatch):
    monkeypatch.setattr(vision, "available", lambda: False)
    with pytest.raises(ValueError, match="Pillow"):
        agy_tools.validate(["images"], ["images"])
    assert agy_tools.validate(["web_search"], ["web_search"]) == ("web_search",)


def test_policy_never_puts_invoke_subagent_in_the_tool_list():
    pol = agy_tools.build_policy(("images", "web_search"))
    assert "invoke_subagent" not in pol["tools"] and pol["subagents"] == ["image-generator"]
    assert pol["tools"] == ["generate_image", "send_message", "search_web"]
    assert agy_tools.build_policy(()) == {"tools": [], "subagents": [], "caps": {}}


# ------------------------------------------------------------------------------------------------------------------- el hook
POLICY = {"tools": ["search_web", "generate_image"], "subagents": ["image-generator"], "caps": {"generate_image": 2}}


@pytest.mark.parametrize(("name", "args", "ok"), [
    ("search_web", {"query": "x"}, True),
    ("generate_image", {}, True),
    ("run_command", {"CommandLine": "dir"}, False),
    ("write_file", {}, False),
    ("read_file", {}, False),
    ("call_mcp_tool", {}, False),
    ("schedule", {}, False),
    ("run_workflow", {}, False),
    ("invoke_subagent", {"TypeName": "image-generator"}, True),
    ("invoke_subagent", {"TypeName": "image-generator", "Workspace": "inherit"}, True),
    ("invoke_subagent", {"TypeName": "image-generator", "Workspace": "branch"}, False),
    ("invoke_subagent", {"TypeName": "self"}, False),
    ("invoke_subagent", {"TypeName": "research"}, False),
    ("invoke_subagent", {}, False),
    ("herramienta_nueva_de_una_version_futura", {}, False),
])
def test_hook_decisions(name, args, ok):
    assert agy_hook.decide(POLICY, name, args, 0)[0] is ok


def test_invoke_subagent_listed_as_a_tool_does_not_open_every_subagent():
    assert agy_hook.decide({"tools": ["invoke_subagent"], "subagents": []}, "invoke_subagent", {"TypeName": "self"})[0] is False


def test_hook_cap_per_turn():
    assert agy_hook.decide(POLICY, "generate_image", {}, 1)[0] is True
    allowed, why = agy_hook.decide(POLICY, "generate_image", {}, 2)
    assert allowed is False and "tope" in why


def run_hook(tmp_path, policy, stdin, state_dir=None):
    pol = tmp_path / "pol.json"
    if policy is not None:
        pol.write_text(policy if isinstance(policy, str) else json.dumps(policy), encoding="utf-8")
    state = state_dir or tmp_path
    return subprocess.run([sys.executable, "-I", agy_hook.__file__, str(pol), str(state)], input=stdin, capture_output=True, text=True, check=False)


def test_hook_process_allows_and_denies_and_records(tmp_path):
    ok = run_hook(tmp_path, POLICY, json.dumps({"toolCall": {"name": "search_web", "args": {"query": "capital " + "x" * 500}}}))
    no = run_hook(tmp_path, POLICY, json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": "rm -rf /"}}}))
    assert ok.returncode == 0 and no.returncode == 2 and "no esta activada" in no.stderr
    assert no.stderr.isascii()                                                                  # sin acentos: con ellos el mensaje llega roto al modelo
    assert "Thought/Action" in no.stderr and "Action: run_command(...)" in no.stderr             # y le dice qué hacer en lugar de dejarlo a ciegas
    recs = [json.loads(x) for x in (tmp_path / "hook_events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["decision"], r["name"]) for r in recs] == [("allow", "search_web"), ("deny", "run_command")]
    assert len(recs[0]["detail"]) <= agy_hook.MAX_DETAIL and recs[1]["detail"] == ""        # sin comandos ni prompts completos en el log


@pytest.mark.parametrize("stdin", ["", "no es json", "[]", "{}", '{"toolCall": 5}', '{"toolCall": {"args": {}}}', '{"toolCall": {"name": 7}}'])
def test_hook_fails_closed_on_bad_input(tmp_path, stdin):
    assert run_hook(tmp_path, POLICY, stdin).returncode == 2


@pytest.mark.parametrize("policy", [None, "", "no es json", "[]", "5"])
def test_hook_fails_closed_on_a_broken_policy(tmp_path, policy):
    r = run_hook(tmp_path, policy, json.dumps({"toolCall": {"name": "search_web", "args": {}}}))
    assert r.returncode == 2


def test_hook_fails_closed_when_it_cannot_write_its_log(tmp_path):
    (tmp_path / "hook_events.jsonl").mkdir()                                  # un directorio donde debería haber un archivo
    r = run_hook(tmp_path, POLICY, json.dumps({"toolCall": {"name": "search_web", "args": {}}}))
    assert r.returncode == 2


def test_hook_cap_counts_per_turn_through_the_state_files(tmp_path):
    call = json.dumps({"toolCall": {"name": "generate_image", "args": {}}})
    (tmp_path / "turn").write_text("1")
    assert [run_hook(tmp_path, POLICY, call).returncode for _ in range(3)] == [0, 0, 2]
    (tmp_path / "turn").write_text("2")
    assert run_hook(tmp_path, POLICY, call).returncode == 0                      # turno nuevo, cupo nuevo


# ------------------------------------------------------------------------------- con el agy falso: de la política a la imagen
def test_without_tools_the_hook_blocks_everything_with_an_empty_policy(env):
    llm = make(env, ())
    try:
        llm.generate([U("x")])
        session = next(iter(llm._sessions.values()))
        root = Path(session.workdir)
        cmd = json.loads((root / ".agents" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert "agy_hook.py" in cmd and llm.enabled_tools == ()
        assert json.loads((root / "agy_policy.json").read_text(encoding="utf-8")) == {"tools": [], "subagents": [], "caps": {}}
        assert session.allowed_tools == frozenset()
    finally:
        llm.end_session()


def test_without_tools_every_native_tool_is_blocked_and_the_model_is_told_what_to_do(env):
    env["calls"]([{"name": n, "args": {}} for n in ("run_command", "read_file", "write_file", "search_web", "invoke_subagent", "call_mcp_tool", "schedule")])
    llm = make(env, ())
    try:
        llm.generate([U("x")])
        assert [r["allowed"] for r in env["log"]()] == [False] * 7
        assert all(r["rc"] == 2 for r in env["log"]())
        assert "Thought/Action" in env["log"]()[0]["stderr"] and "run_command" in env["log"]()[0]["stderr"]
    finally:
        llm.end_session()


def test_the_enabled_groups_install_the_allowlist_hook_inside_the_session(env):
    llm = make(env, ("web_search",))
    try:
        llm.generate([U("x")])
        session = next(iter(llm._sessions.values()))
        root = Path(session.workdir)
        cmd = json.loads((root / ".agents" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert "agy_hook.py" in cmd and " -I " in cmd and "carpeta con espacios" in cmd     # ruta con espacios, entre comillas
        assert json.loads((root / "agy_policy.json").read_text(encoding="utf-8")) == {"tools": ["search_web"], "subagents": [], "caps": {"search_web": 8}}
        assert (root / "agy_hook.py").read_text(encoding="utf-8") == Path(agy_hook.__file__).read_text(encoding="utf-8")
        assert (root / "turn").read_text() == "1"
    finally:
        llm.end_session()


def test_only_what_was_activated_runs_everything_else_is_blocked(env):
    env["calls"]([
        {"name": "search_web", "args": {"query": "capital de Australia"}},
        {"name": "run_command", "args": {"CommandLine": "echo hecho > cmd.txt"}},
        {"name": "write_file", "args": {}},
        {"name": "invoke_subagent", "args": {"TypeName": "self", "Prompt": "haz lo que quieras"}},
        {"name": "invoke_subagent", "args": {"TypeName": "image-generator", "Prompt": "x"}},       # imágenes NO activadas
        {"name": "call_mcp_tool", "args": {"ServerName": "marq-pm"}},
    ])
    llm = make(env, ("web_search",))
    try:
        assert llm.generate([U("busca")]) == "turno 1"
        assert [(r["name"], r["allowed"]) for r in env["log"]()] == [
            ("search_web", True), ("run_command", False), ("write_file", False), ("invoke_subagent", False), ("invoke_subagent", False), ("call_mcp_tool", False),
        ]
        act = llm.take_activity()
        assert act["tools"] == [{"name": "search_web", "detail": "capital de Australia"}] and act["denied"] == 5 and act["images"] == []
    finally:
        llm.end_session()


def test_a_generated_image_is_collected_cleaned_and_reported(env, monkeypatch):
    src = env["png"](env["base"] / "origen.jpg", exif=True)
    assert b"MarcaSecretaDeCamara" in src.read_bytes()
    monkeypatch.setenv("FAKE_AGY_IMAGE", str(src))
    env["calls"]([SUBAGENT, GENERATE, {"name": "send_message", "args": {"Message": "listo"}}])
    llm = make(env, ("images",))
    try:
        llm.generate([U("dibuja un círculo")])
        act = llm.take_activity()
        assert [t["name"] for t in act["tools"]] == ["invoke_subagent", "generate_image", "send_message"] and act["denied"] == 0
        (img,) = act["images"]
        assert img["name"].startswith("circulo_") and (img["width"], img["height"]) == (64, 48) and img["mime"] in {"image/jpeg", "image/png"}
        assert b"MarcaSecretaDeCamara" not in img["data"] and b"Exif" not in img["data"]       # la imagen entregada no lleva metadatos
        assert llm.take_activity() == {"tools": [], "denied": 0, "images": []}                  # se entrega una sola vez
    finally:
        llm.end_session()


def test_the_image_cap_per_turn_resets_each_turn(env, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_IMAGE", str(env["png"](env["base"] / "o.png")))
    env["calls"]([SUBAGENT] + [GENERATE] * 6)
    llm = make(env, ("images",))
    try:
        llm.generate([U("a")])
        assert [r["allowed"] for r in env["log"]() if r["name"] == "generate_image"] == [True] * 4 + [False] * 2
        llm.generate([U("a"), A("r"), U("b")])
        turn2 = [r["allowed"] for r in env["log"]() if r["turn"] == 2 and r["name"] == "generate_image"]
        assert turn2 == [True] * 4 + [False] * 2
    finally:
        llm.end_session()


def test_files_that_are_not_images_or_are_old_or_outside_brain_are_never_collected(env, monkeypatch):
    brain = env["home"] / "brain"
    outside = env["base"] / "secreto.png"
    env["png"](outside)
    conv = brain / "conv-main"
    conv.mkdir()
    (conv / "falsa.png").write_bytes(b"esto no es un png")                         # extensión de imagen, contenido no
    (conv / "doc.txt").write_text("texto")
    old = env["png"](conv / "vieja.png")
    os.utime(old, (1, 1))                                                          # anterior al turno
    monkeypatch.setenv("FAKE_AGY_IMAGE", str(env["png"](env["base"] / "buena.png")))
    env["calls"]([SUBAGENT, GENERATE])
    llm = make(env, ("images",))
    try:
        llm.generate([U("x")])
        (img,) = llm.take_activity()["images"]
        assert img["name"].startswith("circulo_")                                  # solo la generada en este turno y válida
    finally:
        llm.end_session()


def test_a_conversation_id_that_points_outside_brain_collects_nothing(env, monkeypatch):
    outside = env["home"] / "otra"
    outside.mkdir()
    env["png"](outside / "robada.png")
    monkeypatch.setenv("FAKE_AGY_CONV", "../otra")
    monkeypatch.setenv("FAKE_AGY_IMAGE", str(env["png"](env["base"] / "b.png")))
    env["calls"]([SUBAGENT, GENERATE])
    llm = make(env, ("images",))
    try:
        llm.generate([U("x")])
        assert llm.take_activity()["images"] == []
        assert not any(p.suffix == ".png" for p in (env["home"] / "brain").rglob("*"))
    finally:
        llm.end_session()


def test_without_pillow_images_are_not_collected(env, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_IMAGE", str(env["png"](env["base"] / "b.png")))
    env["calls"]([SUBAGENT, GENERATE])
    llm = make(env, ("images",))
    monkeypatch.setattr(vision, "available", lambda: False)
    try:
        llm.generate([U("x")])
        assert llm.take_activity()["images"] == []
    finally:
        llm.end_session()


# ------------------------------------------------------------------------------------------------ el monitor del flujo
@pytest.mark.parametrize("groups", [(), ("web_search",)])
def test_a_tool_that_ran_without_being_activated_kills_the_session(env, monkeypatch, groups):
    monkeypatch.setenv("FAKE_AGY_BYPASS", "1")                                      # el agy falso «se salta» el hook
    env["calls"]([{"name": "run_command", "args": {"CommandLine": "dir"}}])
    llm = make(env, groups)
    try:
        with pytest.raises(RuntimeError, match="ejecutó por su cuenta «run_command»"):
            llm.generate([U("x")])
        assert not llm._sessions
    finally:
        llm.end_session()


@pytest.mark.parametrize("groups", [(), ("web_search",)])
def test_the_subagent_step_is_watched_too(env, monkeypatch, groups):
    """Los pasos de invoke_subagent llegan con step_type «subagent», no «tool»: el monitor no puede mirar solo «tool»."""
    monkeypatch.setenv("FAKE_AGY_BYPASS", "1")
    env["calls"]([SUBAGENT])
    llm = make(env, groups)
    try:
        with pytest.raises(RuntimeError, match="ejecutó por su cuenta «invoke_subagent»"):
            llm.generate([U("x")])
    finally:
        llm.end_session()


def test_an_activated_tool_that_runs_is_not_a_violation(env, monkeypatch):
    env["calls"]([SUBAGENT])
    monkeypatch.setenv("FAKE_AGY_IMAGE", "")
    llm = make(env, ("images",))
    try:
        assert llm.generate([U("x")]) == "turno 1"
        assert next(iter(llm._sessions.values())).executed_tools == ["invoke_subagent"]
    finally:
        llm.end_session()


# ----------------------------------------------------------------- un aviso de error de agy NO es una herramienta ejecutada
@pytest.mark.parametrize("groups", [(), ("web_search",), ("images",)])
def test_an_error_step_is_a_notice_not_a_violation(env, monkeypatch, groups, caplog):
    """Regresión de una misión real: el modelo llamó a su `run_command` nativo, el hook lo bloqueó y agy lo contó como «error_message»
    (sin tool_name). El monitor lo tomó por una herramienta ejecutada, mató el turno y la misión «terminó» tras tres fallos."""
    import logging

    monkeypatch.setenv("FAKE_AGY_NOISE", "1")
    monkeypatch.setenv("FAKE_AGY_RESPONSES", json.dumps(['Thought: sigo\nAction: list_dir(".")']))
    llm = make(env, groups)
    try:
        with caplog.at_level(logging.WARNING, logger="praxeon.agent_llm"):
            assert llm.generate([U("x")]) == 'Thought: sigo\nAction: list_dir(".")'
        session = next(iter(llm._sessions.values()))
        assert session.executed_tools == [] and set(session.notices) == {"error_message", "generic"}
        assert any("aviso(s) de error" in r.message for r in caplog.records)
    finally:
        llm.end_session()


def test_a_notice_does_not_hide_a_real_violation_in_the_same_turn(env, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_NOISE", "1")
    monkeypatch.setenv("FAKE_AGY_BYPASS", "1")
    env["calls"]([{"name": "run_command", "args": {}}])
    llm = make(env, ())
    try:
        with pytest.raises(RuntimeError, match="ejecutó por su cuenta «run_command»"):
            llm.generate([U("x")])
    finally:
        llm.end_session()


# ------------------------------------------------------------------------------------------- perfil propio de agy (rapidez)
def envlog(env):
    p = env["base"] / "envlog.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_agy_runs_in_a_clean_profile_of_its_own_by_default_on_windows_and_when_asked(env, monkeypatch):
    """Con el perfil del usuario agy tardaba 20-40 s por turno (plugins, skills, reglas, memoria, MCP y hooks globales): con uno limpio, 1-5 s."""
    profile = env["base"] / "perfil de agy"
    monkeypatch.setenv("PRAXEON_AGY_PROFILE_DIR", str(profile))
    monkeypatch.setenv("PRAXEON_AGY_ISOLATE", "1")
    monkeypatch.setenv("USERPROFILE", "C:/Users/alguien/real")
    monkeypatch.setenv("HOME", "/home/alguien/real")
    llm = make(env, ())
    try:
        llm.generate([U("x")])
        (rec,) = envlog(env)
        assert rec["userprofile"] == str(profile) and rec["home"] == str(profile)         # no ve el perfil real del usuario
        assert profile.is_dir()
    finally:
        llm.end_session()


def test_the_profile_can_be_turned_off_and_is_not_forced_elsewhere(env, monkeypatch):
    monkeypatch.setenv("PRAXEON_AGY_PROFILE_DIR", str(env["base"] / "no-debe-crearse"))
    monkeypatch.setenv("PRAXEON_AGY_ISOLATE", "0")
    monkeypatch.setenv("USERPROFILE", "C:/Users/alguien/real")
    llm = make(env, ())
    try:
        llm.generate([U("x")])
        (rec,) = envlog(env)
        assert rec["userprofile"] == "C:/Users/alguien/real"
        assert not (env["base"] / "no-debe-crearse").exists()
    finally:
        llm.end_session()
    monkeypatch.delenv("PRAXEON_AGY_ISOLATE")
    for platform, expected in (("win32", True), ("linux", False), ("darwin", False)):
        monkeypatch.setattr(sys, "platform", platform)
        assert AgyCLILLM._isolate_profile() is expected, platform
    for value, expected in (("1", True), ("true", True), ("on", True), ("0", False), ("off", False), ("no", False)):
        monkeypatch.setenv("PRAXEON_AGY_ISOLATE", value)
        assert AgyCLILLM._isolate_profile() is expected, value


def test_images_are_collected_from_the_profile_agy_actually_uses(env, monkeypatch):
    """Con el perfil propio, agy deja los artefactos bajo SU carpeta de usuario: PRAXEON tiene que mirar ahí, no en la del usuario."""
    profile = env["base"] / "perfil de agy"
    monkeypatch.setenv("PRAXEON_AGY_PROFILE_DIR", str(profile))
    monkeypatch.setenv("PRAXEON_AGY_ISOLATE", "1")
    monkeypatch.delenv("PRAXEON_AGY_HOME")                                          # sin sobrescribir la carpeta de datos
    monkeypatch.setenv("FAKE_AGY_IMAGE", str(env["png"](env["base"] / "origen.png")))
    env["calls"]([SUBAGENT, GENERATE])
    llm = make(env, ("images",))
    try:
        llm.generate([U("dibuja")])
        (img,) = llm.take_activity()["images"]
        assert img["name"].startswith("circulo_")
        assert list((profile / ".gemini" / "antigravity-cli" / "brain").rglob("circulo_*"))
    finally:
        llm.end_session()


def test_old_conversations_of_the_own_profile_are_pruned_and_nothing_else_is_touched(env, monkeypatch):
    profile = env["base"] / "perfil de agy"
    brain = profile / ".gemini" / "antigravity-cli" / "brain"
    old, recent = brain / "conv-vieja", brain / "conv-reciente"
    for d in (old, recent):
        d.mkdir(parents=True)
        (d / "x.txt").write_text("x")
    long_ago = time.time() - 30 * 86400
    os.utime(old, (long_ago, long_ago))
    outside = env["base"] / "datos del usuario"
    outside.mkdir()
    (outside / "importante.txt").write_text("no se toca")
    os.utime(outside, (long_ago, long_ago))
    monkeypatch.setenv("PRAXEON_AGY_PROFILE_DIR", str(profile))
    monkeypatch.setenv("PRAXEON_AGY_ISOLATE", "1")
    monkeypatch.delenv("PRAXEON_AGY_HOME")
    llm = make(env, ())
    try:
        llm.generate([U("x")])
    finally:
        llm.end_session()
    assert not old.exists() and recent.exists() and (outside / "importante.txt").exists()


def test_nothing_is_pruned_when_the_own_profile_is_turned_off_even_if_the_dir_is_inside_it(env, monkeypatch):
    profile = env["base"] / "perfil de agy"
    data = profile / ".gemini" / "antigravity-cli"
    old = data / "brain" / "conv-vieja"
    old.mkdir(parents=True)
    long_ago = time.time() - 30 * 86400
    os.utime(old, (long_ago, long_ago))
    monkeypatch.setenv("PRAXEON_AGY_PROFILE_DIR", str(profile))
    monkeypatch.setenv("PRAXEON_AGY_HOME", str(data))
    monkeypatch.setenv("PRAXEON_AGY_ISOLATE", "0")
    llm = make(env, ())
    try:
        llm.generate([U("x")])
    finally:
        llm.end_session()
    assert old.exists()


def test_pruning_never_touches_a_data_dir_outside_the_own_profile(env, monkeypatch):
    """Con PRAXEON_AGY_HOME apuntando fuera del perfil propio (p. ej. la carpeta del usuario) no se borra nada."""
    user_brain = env["home"] / "brain" / "conv-de-hace-meses"
    user_brain.mkdir(parents=True)
    long_ago = time.time() - 90 * 86400
    os.utime(user_brain, (long_ago, long_ago))
    monkeypatch.setenv("PRAXEON_AGY_PROFILE_DIR", str(env["base"] / "perfil de agy"))
    monkeypatch.setenv("PRAXEON_AGY_ISOLATE", "1")
    llm = make(env, ())
    try:
        llm.generate([U("x")])
    finally:
        llm.end_session()
    assert user_brain.exists()
    monkeypatch.setenv("PRAXEON_AGY_ISOLATE", "0")                                  # y con el perfil del usuario nunca se poda
    llm = make(env, ())
    try:
        llm.generate([U("x")])
    finally:
        llm.end_session()
    assert user_brain.exists()


# ------------------------------------------------------------- «Malformed function call»: el modelo intentó una llamada a función
MALFORMED = "Your previous response contained an improperly formatted function call: Malformed function call: Failed to parse function call: Function call is empty - no input to parse.\nPlease retry with a properly formatted function call\nRetries remaining: 3"


def msglog(env):
    p = env["base"] / "msglog.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_a_malformed_function_call_is_retried_once_in_text_instead_of_failing_the_turn(env, monkeypatch, caplog):
    import logging

    monkeypatch.setenv("FAKE_AGY_MALFORMED", "1")
    monkeypatch.setenv("FAKE_AGY_RESPONSES", json.dumps([MALFORMED, 'Thought: sigo\nAction: list_dir(".")']))
    llm = make(env, ())
    try:
        with caplog.at_level(logging.WARNING, logger="praxeon.agent_llm"):
            assert llm.generate([U("x")]) == 'Thought: sigo\nAction: list_dir(".")'
        msgs = [m["msg"] for m in msglog(env)]
        assert len(msgs) == 2 and "llamada a función mal formada" in msgs[1] and "SOLO con texto" in msgs[1]       # el segundo mensaje es el empujón
        assert any("reintento 1 de 2" in r.message for r in caplog.records)
    finally:
        llm.end_session()


def test_the_retry_is_bounded_and_then_the_error_is_reported(env, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_MALFORMED", "3")
    monkeypatch.setenv("FAKE_AGY_RESPONSES", json.dumps([MALFORMED] * 3))
    llm = make(env, ())
    try:
        with pytest.raises(RuntimeError, match="La CLI devolvió un error: .*improperly formatted function call"):
            llm.generate([U("x")])
        assert len(msglog(env)) == 3                       # el original y 2 reintentos, ninguno más
        assert not llm._sessions
    finally:
        llm.end_session()


def test_two_failures_then_success_is_recovered(env, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_MALFORMED", "2")
    monkeypatch.setenv("FAKE_AGY_RESPONSES", json.dumps([MALFORMED, MALFORMED, 'Thought: ok\nAction: finish("hecho")']))
    llm = make(env, ())
    try:
        assert llm.generate([U("x")]) == 'Thought: ok\nAction: finish("hecho")'
    finally:
        llm.end_session()


def test_other_errors_are_not_retried(env, monkeypatch):
    """Cuota, sesión caducada o un fallo cualquiera no se arreglan pidiendo lo mismo otra vez."""
    monkeypatch.setenv("FAKE_AGY_MALFORMED", "1")
    monkeypatch.setenv("FAKE_AGY_RESPONSES", json.dumps(["RESOURCE_EXHAUSTED: quota exceeded"]))
    llm = make(env, ())
    try:
        with pytest.raises(RuntimeError, match="Cuota o límite de uso agotado"):
            llm.generate([U("x")])
        assert len(msglog(env)) == 1
    finally:
        llm.end_session()


def test_a_successful_turn_mentioning_those_words_is_not_retried(env, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_RESPONSES", json.dumps(["Thought: el error decía «Malformed function call»\nAction: list_dir(\".\")"]))
    llm = make(env, ())
    try:
        assert "Malformed function call" in llm.generate([U("x")])
        assert len(msglog(env)) == 1
    finally:
        llm.end_session()


# --------------------------------------------------------------------------------------------------------- fábrica y mensaje
def test_the_first_message_warns_that_praxeon_and_agy_share_tool_names(env):
    """PRAXEON y agy llaman igual a run_command/read_file/write_file: hay que decir que la acción va como TEXTO, no como herramienta nativa."""
    for groups in ((), ("web_search",)):
        llm = make(env, groups)
        try:
            msg = llm._first_message("Eres un nodo.", "hola")
            assert "NO son tus herramientas nativas aunque se llamen igual" in msg and "Action: …" in msg and "Nunca las invoques como herramienta propia" in msg
            assert msg.index("<instrucciones_del_sistema>") < msg.index("OJO") < msg.index("hola")
        finally:
            llm.end_session()


def test_the_system_message_tells_the_model_what_is_enabled(env):
    env["calls"]([])
    llm = make(env, ("images", "web_search"))
    try:
        llm.generate([U("hola")], system_prompt="Eres un nodo.")
        # el agy falso no guarda el mensaje; se comprueba con el método directamente
        msg = llm._first_message("Eres un nodo.", "hola")
        assert "generación de imágenes, búsqueda web" in msg and "desactivadas" not in msg.split("Solo tienes")[0]
        assert "cualquier otra herramienta tuya está desactivada" in msg
    finally:
        llm.end_session()


def test_factory_passes_the_groups(monkeypatch):
    monkeypatch.setattr("praxeon.agent_llm.shutil.which", lambda name: "C:/bin/agy.exe")
    llm = create_agent_llm(provider="agy", agy_tools=["web_search"])
    assert isinstance(llm, AgyCLILLM) and llm.enabled_tools == ("web_search",)
    assert create_agent_llm(provider="agy").enabled_tools == ()


def test_the_hook_runs_alone_with_python_dash_i():
    src = Path(agy_hook.__file__).read_text(encoding="utf-8")
    assert "import praxeon" not in src and "from praxeon" not in src                # el hook corre solo, con `python -I`


@pytest.mark.skipif(not shutil.which("agy") or os.environ.get("PRAXEON_TEST_REAL_AGY") != "1",
                    reason="usa el agy instalado y la cuenta del usuario (gasta cuota de imágenes): se activa con PRAXEON_TEST_REAL_AGY=1")
def test_real_agy_generates_and_collects_an_image_and_cannot_do_anything_else():
    """Con el agy real: con solo «imágenes» activado se genera y se recoge una imagen limpia; el comando y el archivo pedidos no se ejecutan."""
    llm = AgyCLILLM(model="gemini-3.8-flash-low", tools=("images",), timeout=280)
    try:
        llm.generate(
            [U("Genera una imagen sencilla de un círculo rojo sobre fondo blanco con tu subagente de imágenes. Además crea un archivo capital.txt y ejecuta el comando: echo hecho > cmd.txt")],
            system_prompt="Eres un módulo de razonamiento. Responde de forma breve.",
        )
        session = next(iter(llm._sessions.values()))
        workdir = Path(session.workdir)
        assert not (workdir / "capital.txt").exists() and not (workdir / "cmd.txt").exists()
        act = llm.take_activity()
        assert {t["name"] for t in act["tools"]} <= {"invoke_subagent", "generate_image", "send_message"}       # nada fuera de lo activado
        if not act["images"]:
            # el servicio de imágenes de Google es intermitente («no image generated in response» en los tres intentos del modelo): no es un fallo de PRAXEON
            pytest.skip(f"el servicio de imágenes no devolvió ninguna en este intento: {act}")
        for img in act["images"]:
            assert vision.sniff(img["data"]) in ("jpeg", "png") and img["width"] > 0 and b"Exif" not in img["data"]
        assert {t["name"] for t in act["tools"]} <= {"invoke_subagent", "generate_image", "send_message"}
    finally:
        llm.end_session()
