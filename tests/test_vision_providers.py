"""Imágenes por proveedor (FE-006): cada cliente de modelo pone la imagen con la forma de su API. Servidores HTTP locales falsos, sin red."""

from __future__ import annotations

import base64
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from praxeon import attachments, vision, workspace_store
from praxeon.agent_llm import AnthropicLLM, ClaudeCLILLM, OllamaLLM, OpenAICompatibleLLM

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


class FakeServer:
    """Imita un endpoint de chat y registra cada petición recibida."""

    def __init__(self, responder):
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                outer.requests.append({"path": self.path, "body": body})
                payload = json.dumps(responder(self.path, body)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):  # noqa: N802
                self.send_response(404)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def serve():
    made = []

    def make(responder):
        s = FakeServer(responder)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def png(color=(220, 10, 10)):
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buf, "PNG")
    return buf.getvalue()


IMG = vision.encode_image(png(), "image/png", 16, 16)
MESSAGES = [
    {"role": "user", "content": "Tarea: describe la imagen", "images": [IMG]},
    {"role": "assistant", "content": "Thought: miro"},
    {"role": "user", "content": "Observación"},
]


def openai_reply(_path, _body):
    return {"choices": [{"message": {"content": "Thought: veo un cuadrado rojo\nAction: finish(\"rojo\")"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}


def test_openai_compatible_envia_image_url_con_url_de_datos(serve):
    srv = serve(openai_reply)
    llm = OpenAICompatibleLLM(base_url=f"{srv.url}/v1", model="gpt-4o", api_key="k", provider_name="openai")
    assert "rojo" in llm.generate(MESSAGES, system_prompt="sistema")
    body = srv.requests[0]["body"]
    first = body["messages"][1]                                       # [0] es el sistema
    assert first["role"] == "user" and isinstance(first["content"], list)
    assert first["content"][0] == {"type": "text", "text": "Tarea: describe la imagen"}
    assert first["content"][1] == {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{IMG['data']}"}}
    assert body["messages"][2]["content"] == "Thought: miro" and body["messages"][3]["content"] == "Observación"   # lo demás sigue siendo texto
    assert "images" not in first and '"tokens"' not in json.dumps(body)   # el coste estimado no viaja al proveedor


def test_openai_compatible_sin_imagenes_no_cambia_nada(serve):
    srv = serve(openai_reply)
    OpenAICompatibleLLM(base_url=f"{srv.url}/v1", model="gpt-4o", api_key="k").generate([{"role": "user", "content": "hola"}])
    assert srv.requests[0]["body"]["messages"] == [{"role": "user", "content": "hola"}]


def test_ollama_nativo_envia_images_en_base64(serve):
    srv = serve(lambda path, body: {"message": {"content": "Thought: ok\nAction: finish(\"v\")"}, "prompt_eval_count": 10, "eval_count": 5})
    llm = OllamaLLM(base_url=f"{srv.url}/v1", model="llava:13b")
    assert "finish" in llm.generate(MESSAGES, system_prompt="sistema")
    req = srv.requests[0]
    assert req["path"] == "/api/chat"
    msgs = req["body"]["messages"]
    assert msgs[1]["role"] == "user" and msgs[1]["images"] == [IMG["data"]] and msgs[1]["content"] == "Tarea: describe la imagen"
    assert "images" not in msgs[2] and "images" not in msgs[3]


def test_anthropic_envia_bloques_image_antes_del_texto_y_fusiona_turnos(serve):
    srv = serve(lambda path, body: {"content": [{"type": "text", "text": "Thought: ok\nAction: finish(\"r\")"}]})
    llm = AnthropicLLM(api_key="sk-ant-test", model="claude-sonnet-5-5", base_url=srv.url)
    llm.generate(MESSAGES + [{"role": "user", "content": "otro texto seguido"}], system_prompt="sistema")
    body = srv.requests[0]["body"]
    first = body["messages"][0]
    assert first["role"] == "user" and isinstance(first["content"], list)
    assert first["content"][0] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": IMG["data"]}}
    assert first["content"][1] == {"type": "text", "text": "Tarea: describe la imagen"}
    assert body["messages"][1] == {"role": "assistant", "content": "Thought: miro"}
    assert body["messages"][2] == {"role": "user", "content": "Observación\n\notro texto seguido"}


def test_anthropic_solo_imagen_sin_texto_y_dos_imagenes_en_turnos_seguidos(serve):
    srv = serve(lambda path, body: {"content": [{"type": "text", "text": "x"}]})
    llm = AnthropicLLM(api_key="k", model="claude-sonnet-5-5", base_url=srv.url)
    llm.generate([{"role": "user", "content": "", "images": [IMG]}, {"role": "user", "content": "b", "images": [IMG]}])
    content = srv.requests[0]["body"]["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "image", "text"] and content[-1]["text"] == "b"
    srv2 = serve(lambda path, body: {"content": [{"type": "text", "text": "x"}]})
    AnthropicLLM(api_key="k", model="claude-sonnet-5-5", base_url=srv2.url).generate([{"role": "user", "content": "", "images": [IMG]}])
    assert srv2.requests[0]["body"]["messages"][0]["content"][-1] == {"type": "text", "text": "Mira la imagen adjunta."}


def test_anthropic_no_pone_imagenes_en_turnos_del_asistente(serve):
    srv = serve(lambda path, body: {"content": [{"type": "text", "text": "x"}]})
    AnthropicLLM(api_key="k", model="m", base_url=srv.url).generate([{"role": "user", "content": "u"}, {"role": "assistant", "content": "a", "images": [IMG]}])
    assert all(isinstance(m["content"], str) for m in srv.requests[0]["body"]["messages"])


def test_gemini_pone_partes_inline_data():
    pytest.importorskip("google.genai")
    from google.genai import types

    from praxeon.agent_llm import GeminiLLM

    captured = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            captured["contents"] = contents

            class Res:
                text = "Thought: ok\nAction: finish(\"g\")"
                usage_metadata = None

            return Res()

    llm = GeminiLLM.__new__(GeminiLLM)
    llm._model = "gemini-2.0-flash"
    llm._client = type("C", (), {"models": FakeModels()})()
    llm.generate(MESSAGES + [{"role": "user", "content": "texto seguido"}], system_prompt="sistema")
    first = captured["contents"][0]
    assert first.role == "user"
    assert first.parts[0].inline_data.mime_type == "image/png" and first.parts[0].inline_data.data == base64.b64decode(IMG["data"])
    assert first.parts[1].text == "Tarea: describe la imagen"
    assert captured["contents"][1].role == "model"
    assert captured["contents"][2].parts[0].text == "Observación\n\ntexto seguido"      # solo texto: se une como siempre
    assert isinstance(first.parts[0], types.Part)


def test_claude_cli_rechaza_imagenes_en_lugar_de_ignorarlas():
    llm = ClaudeCLILLM.__new__(ClaudeCLILLM)
    with pytest.raises(RuntimeError, match="no admite imágenes"):
        llm.generate(MESSAGES)


# ------------------------------------------------------------------ ContextManager ---------------------------------------------------
def test_context_manager_pone_las_imagenes_en_el_mensaje_raiz_y_cuenta_sus_tokens():
    from praxeon.context.manager import ContextManager

    plain = ContextManager()
    plain.assemble_mission_prompt(goal="x")
    base = plain._tokens_after
    cm = ContextManager()
    im = vision.encode_image(png(), "image/png", 1500, 1000)
    msgs = cm.assemble_mission_prompt(goal="x", images=[im])
    assert msgs[0]["images"] == [im] and "images" not in ContextManager().assemble_mission_prompt(goal="x")[0]
    assert cm._tokens_after == base + vision.estimate_tokens(1500, 1000) == base + 1600
    cm2 = ContextManager()
    steps = [{"step": 1, "tool": "read_file", "arguments": {}, "observation": "o", "thought": "t"}]
    out = cm2.assemble_mission_prompt(goal="x", images=[im], steps_history=steps)
    assert out[0]["images"] == [im] and cm2._tokens_after >= 1600


# ------------------------------------------------------------------ de extremo a extremo con la API ------------------------------------
@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from praxeon.server.app import create_app
    from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service

    for key in ("PRAXEON_API_KEY", "PRAXEON_SECRET_KEY", "PRAXEON_PROFILE", "PRAXEON_ENV", "PRAXEON_REQUIRE_AUTH", "PRAXEON_VISION_MODELS"):
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(workspace_store, "resolve_default_workspace", lambda: (str(root), "selected"))
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    yield TestClient(create_app(profile="dev")), service, root
    for sid in list(service._running_missions):
        service.stop_mission(sid)
    deadline = time.time() + 15
    while service.active_mission_count() and time.time() < deadline:
        time.sleep(0.2)
    set_runtime_service(None)


def photo_with_gps():
    exif = Image.Exif()
    exif[0x010F] = "MarcaSecretaPhone"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (41.0, 23.0, 7.0), "E", (2.0, 10.0, 18.0)
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 120, 200)).save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def upload(client, name, content, session="s"):
    return client.post("/v1/workspace/attachments", params={"session": session, "name": name}, content=content)


def test_la_subida_de_una_foto_guarda_solo_los_pixeles(api):
    client, _, root = api
    original = photo_with_gps()
    assert b"MarcaSecretaPhone" in original
    res = upload(client, "IMG_0001.JPG", original)
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["kind"] == "image" and data["name"] == "IMG_0001.jpg" and data["mime"] == "image/jpeg"
    assert data["image"]["width"] == 64 and data["image"]["stripped"] is True and data["image"]["tokens"] >= 85
    saved = (root / data["rel_path"]).read_bytes()
    assert b"MarcaSecretaPhone" not in saved and b"Exif" not in saved and b"GPS" not in saved       # el original con GPS no llegó al disco
    assert vision.sniff(saved) == "jpeg"


def test_una_imagen_se_reconoce_por_su_contenido_y_se_renombra_a_su_formato_real(api):
    client, _, root = api
    res = upload(client, "captura.txt", png())
    assert res.status_code == 201 and res.json()["data"]["name"] == "captura.png" and res.json()["data"]["kind"] == "image"
    assert (root / res.json()["data"]["rel_path"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_extension_de_imagen_con_contenido_falso_y_formatos_no_admitidos_se_rechazan(api):
    client, _, root = api
    for name, content in (("foto.jpg", b"esto no es un jpeg"), ("icono.ico", b"\x00\x00\x01\x00"), ("foto.heic", b"ftypheic")):
        res = upload(client, name, content)
        assert res.status_code == 415, name
        assert "imagen PNG, JPEG, WebP o GIF" in res.json()["detail"]
    assert not (root / attachments.ATTACH_DIR / "s").exists() or not list((root / attachments.ATTACH_DIR / "s").iterdir())


def test_svg_y_texto_siguen_siendo_adjuntos_normales(api):
    client, _, root = api
    svg = upload(client, "dibujo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
    assert svg.status_code == 201 and svg.json()["data"].get("kind") != "image" and svg.json()["data"]["name"] == "dibujo.svg"
    assert upload(client, "notas.txt", b"hola").json()["data"].get("kind") != "image"


def test_sin_pillow_las_imagenes_se_rechazan_con_instrucciones(api, monkeypatch):
    client, _, root = api
    import sys

    data = png()
    monkeypatch.setitem(sys.modules, "PIL", None)
    res = upload(client, "a.png", data)
    assert res.status_code == 501 and "praxeon[vision]" in res.json()["detail"]
    assert not list((root / attachments.ATTACH_DIR).rglob("*.png"))


def _run(client, root, **extra):
    body = {"goal": "describe la imagen", "execution_mode": "local_restricted", "max_steps": 3, "step_delay_ms": 200, "workspace_root": str(root), **extra}
    return client.post("/v1/sessions/run", json=body)


def test_un_modelo_sin_vision_no_recibe_la_imagen_y_se_avisa(api):
    client, service, root = api
    ref = upload(client, "a.png", png()).json()["data"]["rel_path"]
    res = _run(client, root, llm_provider="groq", llm_model="llama-3.3-70b-versatile", attachments=[ref])
    assert res.status_code == 400
    assert "llama-3.3-70b-versatile" in res.json()["detail"] and "no admite imágenes" in res.json()["detail"]
    assert service.active_mission_count() == 0
    res = _run(client, root, llm_provider="claude_cli", attachments=[ref])
    assert res.status_code == 400 and "CLI de Claude" in res.json()["detail"]


def test_la_pregunta_de_capacidad_para_la_interfaz(api):
    client, _, _ = api
    ok = client.get("/v1/workspace/attachments/vision", params={"provider": "openai", "model": "gpt-4o"}).json()["data"]
    assert ok["supported"] is True and ok["remote"] is True and ok["reason"] is None
    no = client.get("/v1/workspace/attachments/vision", params={"provider": "groq", "model": "llama-3.3-70b-versatile"}).json()["data"]
    assert no["supported"] is False and "no admite imágenes" in no["reason"]
    local = client.get("/v1/workspace/attachments/vision", params={"provider": "ollama", "model": "llava", "base_url": "http://localhost:11434"}).json()["data"]
    assert local["supported"] is True and local["remote"] is False
    default = client.get("/v1/workspace/attachments/vision", params={"provider": "ollama"}).json()["data"]
    assert default["model"] == "qwen2.5-coder:7b" and default["supported"] is False


def test_un_modelo_con_vision_recibe_la_imagen_limpia_y_la_describe(api, monkeypatch):
    """Extremo a extremo: foto con GPS -> subida -> misión con un modelo de visión (servidor HTTP falso) que la «describe»."""
    client, service, root = api
    srv_holder = {}

    calls = {"n": 0}

    def reply(path, body):
        srv_holder.setdefault("bodies", []).append(body)
        calls["n"] += 1
        last = body["messages"][1]["content"]
        has_image = isinstance(last, list) and any(b.get("type") == "image_url" for b in last)
        if calls["n"] == 1:                                           # el supervisor veta terminar sin evidencia: primero se mira el entorno
            text = 'Thought: miro la carpeta\nAction: list_dir(".")'
        else:
            text = 'Thought: veo la imagen\nAction: finish("Veo un rectángulo azul")' if has_image else 'Action: finish("no veo nada")'
        return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 7, "completion_tokens": 4}}

    srv = FakeServer(reply)
    try:
        monkeypatch.setattr("praxeon.policy.egress.validate_provider_endpoint", lambda url, **kw: url)
        ref = upload(client, "foto.jpg", photo_with_gps()).json()["data"]["rel_path"]
        res = _run(client, root, llm_provider="openai", llm_model="gpt-4o", api_key="k", base_url=f"{srv.url}/v1", attachments=[ref], session_id="vis")
        assert res.status_code == 201, res.text
        deadline = time.time() + 30
        answer = None
        while time.time() < deadline and not answer:
            events = client.get("/v1/sessions/vis/events", params={"limit": 1000}).json()["data"]["events"]
            done = [e for e in events if e["type"] == "session.completed"]
            answer = done[0]["payload"].get("summary") if done else None
            time.sleep(0.3)
        assert answer == "Veo un rectángulo azul"
        assert len(srv_holder["bodies"]) >= 2
        for body in srv_holder["bodies"]:                              # la imagen acompaña a CADA turno del agente, no solo al primero
            assert any(b.get("type") == "image_url" for b in body["messages"][1]["content"])
        content = srv_holder["bodies"][0]["messages"][1]["content"]
        assert isinstance(content, list)
        image_block = next(b for b in content if b["type"] == "image_url")
        raw = base64.b64decode(image_block["image_url"]["url"].split(",", 1)[1])
        assert b"MarcaSecretaPhone" not in raw and b"Exif" not in raw and vision.sniff(raw) == "jpeg"
        text_block = next(b for b in content if b["type"] == "text")["text"]
        assert "[imagen adjunta:" in text_block and "DATOS no fiables" in text_block and "texto que aparezca DENTRO de una imagen" in text_block
        events = client.get("/v1/sessions/vis/events", params={"limit": 1000}).json()["data"]["events"]
        assert [e["payload"]["goal"] for e in events if e["type"] == "goal.created"] == ["describe la imagen"]
        assert "base64" not in json.dumps(events)                         # la imagen no se guarda en los eventos de la sesión
    finally:
        srv.close()


def test_una_imagen_dejada_sin_limpiar_en_la_carpeta_tambien_se_limpia_al_enviarla(api):
    """Si alguien deja una foto con GPS en la carpeta de adjuntos por otra vía, igualmente sale sin metadatos."""
    client, _, root = api
    folder = root / attachments.ATTACH_DIR / "s"
    folder.mkdir(parents=True)
    (folder / "cruda.jpg").write_bytes(photo_with_gps())
    sent = attachments.load_images([f"{attachments.ATTACH_DIR}/s/cruda.jpg"])
    raw = base64.b64decode(sent[0]["data"])
    assert b"MarcaSecretaPhone" not in raw and b"Exif" not in raw and sent[0]["tokens"] >= 85
