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
from pathlib import Path

import pytest

from praxeon import agy_hook, agy_tools, vision
from praxeon.agent_llm import AgyCLILLM, create_agent_llm

PIL = pytest.importorskip("PIL")

FAKE = r'''
import json, os, subprocess, sys, time
home = os.environ["PRAXEON_AGY_HOME"]
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
    responses = json.loads(os.environ.get("FAKE_AGY_RESPONSES", "[]"))
    text = responses[n - 1] if n - 1 < len(responses) else f"turno {n}"
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
    assert ok.returncode == 0 and no.returncode == 2 and "no está permitida" in no.stderr
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
def test_without_tools_the_hook_is_still_the_fixed_exit_2(env):
    llm = make(env, ())
    try:
        llm.generate([U("x")])
        session = next(iter(llm._sessions.values()))
        hooks = json.loads(Path(session.workdir, ".agents", "hooks.json").read_text(encoding="utf-8"))
        assert hooks["hooks"]["PreToolUse"][0]["hooks"] == [{"type": "command", "command": "exit 2"}]
        assert not Path(session.workdir, "agy_hook.py").exists() and llm.enabled_tools == ()
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


# --------------------------------------------------------------------------------------------------------- fábrica y mensaje
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
        assert act["images"], act
        for img in act["images"]:
            assert vision.sniff(img["data"]) in ("jpeg", "png") and img["width"] > 0 and b"Exif" not in img["data"]
        assert {t["name"] for t in act["tools"]} <= {"invoke_subagent", "generate_image", "send_message"}
    finally:
        llm.end_session()
