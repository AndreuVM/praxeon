"""KEV (jaredpalmer/kev) como supervisor System-1 y cascada configurable (local+local o local+API).

Los tests usan un servidor HTTP falso con el formato de respuesta documentado de ``kev.serve``
(POST /v1/systemone); no cargan pesos ni requieren GPU.
"""

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.decision_provider import DecisionModelConfig, DecisionProviderError
from praxeon.domain.evidence import Evidence
from praxeon.domain.goal import Goal
from praxeon.providers.kev import KevProvider
from praxeon.providers.kev_server import KevServerManager
from praxeon.providers.registry import build_default_registry
from praxeon.providers.router import ConfidenceAwareRouter
from praxeon.runtime.decision_runtime import DecisionRuntime
from praxeon.runtime.state import SessionState


@pytest.fixture
def state():
    s = SessionState(session_id="kev-sess", goal=Goal(objective="Revisar el proyecto", success_criteria=["informe"]))
    s.add_evidence(Evidence(id="ev1", claim="listado_inspeccionado", content_hash="h"))
    return s


@pytest.fixture
def action():
    return ActionCandidate(
        id="a1",
        description="Listar el directorio",
        tool_call=ToolCall(tool_name="list_directory", arguments={"path": "."}),
    )


@pytest.fixture
def registry():
    return build_default_registry()


ALLOW_ANSWERS = {
    "decision": {"type": "choice", "choice": "ALLOW", "confidence": 0.82,
                 "probabilities": {"ALLOW": 0.9, "REPLAN": 0.05, "BLOCK": 0.03, "ABSTAIN": 0.02}},
    "is_loop": {"type": "noul", "noul": 0.04},
    "is_grounded": {"type": "noul", "noul": 0.93},
    "progress": {"type": "score", "score": 3.0, "confidence": 0.6, "legend": {}, "probabilities": {}},
}


class FakeKev:
    """Servidor con el formato de respuesta documentado de ``kev.serve`` (POST /v1/systemone, GET /v1/models)."""

    def __init__(self, answers=None, api_key=None):
        self.requests = []
        self.answers = answers or dict(ALLOW_ANSWERS)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _denied(self):
                if api_key and self.headers.get("Authorization") != f"Bearer {api_key}":
                    self.send_response(401)
                    self.end_headers()
                    return True
                return False

            def do_GET(self):
                if self._denied():
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"[]")

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(self.rfile.read(length))})
                if self._denied():
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"model": "kev-latest", "answers": outer.answers}).encode())

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def kev_server(monkeypatch):
    for key in ("KEV_AUTOSTART", "KEV_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    server = FakeKev()
    monkeypatch.setenv("KEV_ENDPOINT_URL", server.url)
    yield server
    server.close()


def _cascade(registry, **params):
    return registry.create(DecisionModelConfig(provider="cascade", model_id="cascade", custom_params=params))


# ----------------------------------------------------------------------------- KEV


def test_kev_sends_a_systemone_request_and_maps_the_answers(registry, kev_server, state, action):
    provider = registry.create(DecisionModelConfig(provider="kev", model_id="kev", backend="local"))
    assert isinstance(provider, KevProvider) and provider.provider_id == "kev" and provider.backend == "local"
    [res] = provider.evaluate(state, [action])

    req = kev_server.requests[0]
    assert req["path"] == "/v1/systemone"
    assert req["body"]["model"] == "kev-latest"
    assert set(req["body"]["questions"]) == {"decision", "is_loop", "is_grounded", "progress"}
    assert "Listar el directorio" in req["body"]["state"]  # el estado lleva la acción candidata real

    assert res.available and res.provider == "kev"
    assert res.confidence == 0.9  # probabilidad de la opción elegida (ALLOW)
    assert res.metadata["choice"]["normalized_confidence"] == 0.82
    assert res.metadata["noul"]["neural_is_loop"] == 0.04 and res.loop_probability < 0.1  # sin repetición no hay bucle
    assert res.grounded_probability == 0.93
    assert res.progress_probability == 0.75  # score 3 en la escala 0..4
    assert "KEV_CHOICE_ALLOW" in res.reason_codes and "KEV_PROGRESS_APPROVED" in res.reason_codes
    assert not any(code.startswith("LAYA_") for code in res.reason_codes)


def test_kev_block_and_ungrounded_are_reported_and_its_loop_signal_is_capped(registry, kev_server, state, action):
    kev_server.answers = {
        "decision": {"type": "choice", "choice": "BLOCK", "confidence": 0.9, "probabilities": {"BLOCK": 0.95}},
        "is_loop": {"type": "noul", "noul": 0.9},
        "is_grounded": {"type": "noul", "noul": 0.1},
        "progress": {"type": "score", "score": 0.4},
    }
    [res] = registry.create(DecisionModelConfig(provider="kev")).evaluate(state, [action])
    assert {"KEV_DESTRUCTIVE_BLOCK", "KEV_UNGROUNDED_EVIDENCE"} <= set(res.reason_codes)
    assert res.loop_probability < 0.65 and res.metadata["noul"]["neural_is_loop"] == 0.9


@pytest.mark.parametrize("answers", [
    {"decision": {"type": "choice", "choice": "QUIZAS", "confidence": 0.5}},          # opción desconocida
    {"decision": {"type": "choice", "choice": "ALLOW", "confidence": 0.5}},           # faltan preguntas
    {"decision": {"type": "choice", "choice": "ALLOW", "confidence": 0.5},
     "is_loop": {"type": "noul", "noul": 7}, "is_grounded": {"type": "noul", "noul": 0.5},
     "progress": {"type": "score", "score": 1}},                                       # probabilidad fuera de rango
])
def test_malformed_kev_answers_fail_closed(registry, kev_server, state, action, answers):
    kev_server.answers = answers
    [res] = registry.create(DecisionModelConfig(provider="kev")).evaluate(state, [action])
    assert res.available is False and res.confidence == 0.0
    assert "KEV_PROVIDER_UNAVAILABLE" in res.reason_codes


def test_unreachable_kev_server_fails_closed(registry, state, action, monkeypatch):
    monkeypatch.setenv("KEV_ENDPOINT_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("KEV_AUTOSTART", raising=False)
    provider = registry.create(DecisionModelConfig(provider="kev", timeout_seconds=1))
    assert provider.is_available() is False
    [res] = provider.evaluate(state, [action])
    assert res.available is False and res.confidence == 0.0


def test_hosted_kev_without_endpoint_is_unavailable(registry, monkeypatch):
    monkeypatch.delenv("KEV_ENDPOINT_URL", raising=False)
    assert registry.create(DecisionModelConfig(provider="kev", backend="hosted")).is_available() is False


def test_hosted_kev_sends_the_bearer_key_and_rejects_a_wrong_one(monkeypatch, state, action):
    monkeypatch.delenv("KEV_API_KEY", raising=False)
    server = FakeKev(api_key="secreta")
    try:
        good = KevProvider(backend="hosted", endpoint_url=server.url, api_key="secreta")
        assert good.evaluate(state, [action])[0].available
        assert server.requests[-1]["headers"]["Authorization"] == "Bearer secreta"
        bad = KevProvider(backend="hosted", endpoint_url=server.url, api_key="otra")
        assert bad.evaluate(state, [action])[0].available is False
    finally:
        server.close()


def test_environment_key_is_not_leaked_to_a_foreign_endpoint(monkeypatch, state, action):
    monkeypatch.setenv("KEV_API_KEY", "clave-del-servidor")
    monkeypatch.delenv("KEV_ENDPOINT_URL", raising=False)
    server = FakeKev()
    try:
        provider = KevProvider(backend="hosted", endpoint_url=server.url)
        assert provider.api_key is None
        provider.evaluate(state, [action])
        assert "Authorization" not in server.requests[-1]["headers"]
    finally:
        server.close()


def test_autostart_launches_the_server_and_waits_until_ready(tmp_path, monkeypatch, state, action):
    """KEV_AUTOSTART=1 + KEV_HOME: PRAXEON lanza el servidor (aquí un sustituto mínimo) y espera a que responda."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    answers = json.dumps({"answers": ALLOW_ANSWERS})
    script = tmp_path / "fake_kev.py"
    script.write_text(
        "import sys\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        f"BODY = {answers!r}.encode()\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def log_message(self, *a): pass\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(b'[]')\n"
        "    def do_POST(self):\n"
        "        self.rfile.read(int(self.headers['Content-Length']))\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(BODY)\n"
        "HTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()\n"
    )
    monkeypatch.setenv("KEV_AUTOSTART", "1")
    monkeypatch.setenv("KEV_HOME", str(tmp_path))
    monkeypatch.setenv("KEV_PORT", str(port))
    monkeypatch.setenv("KEV_STARTUP_TIMEOUT", "30")
    monkeypatch.setenv("KEV_SERVE_COMMAND", f'"{sys.executable}" "{script}" {{port}}')
    monkeypatch.setenv("PRAXEON_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("KEV_ENDPOINT_URL", raising=False)
    manager = KevServerManager.from_env()
    assert manager is not None and manager.base_url == f"http://127.0.0.1:{port}"
    try:
        provider = KevProvider(backend="local")
        assert provider.endpoint_url == manager.base_url and provider.is_available()
        [res] = provider.evaluate(state, [action])
        assert res.available and res.provider == "kev"
        assert manager.is_running()
    finally:
        manager.stop()


def test_autostart_is_off_by_default_and_needs_a_real_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("KEV_AUTOSTART", raising=False)
    assert KevServerManager.from_env() is None
    monkeypatch.setenv("KEV_AUTOSTART", "1")
    monkeypatch.setenv("KEV_HOME", str(tmp_path / "no-existe"))
    assert KevServerManager.from_env() is None


# ----------------------------------------------------------------------------- cascada


def test_default_cascade_is_local_plus_local(registry):
    router = _cascade(registry)
    assert isinstance(router, ConfidenceAwareRouter)
    assert (router.primary.provider_id, router.secondary.provider_id) == ("laya", "kev")
    assert router.provider_id == "cascade"


@pytest.mark.parametrize("primary,secondary", [("laya", "kev"), ("kev", "laya"), ("kev", "kev"), ("laya", "mock")])
def test_any_local_combination_can_be_chosen(registry, primary, secondary):
    router = _cascade(registry, primary={"provider": primary, "backend": "local"}, secondary=secondary)
    assert (router.primary.provider_id, router.secondary.provider_id) == (primary, secondary)


def test_confident_primary_is_not_escalated(registry, state, action):
    router = _cascade(
        registry,
        primary={"provider": "mock", "custom_params": {"default_confidence": 0.95}},
        secondary={"provider": "mock", "model_id": "segundo"},
    )
    [res] = router.evaluate(state, [action])
    assert res.metadata["routed_to"] == "primary" and res.metadata["escalated"] is False
    assert router.get_telemetry().local_decision_rate == 1.0


def test_unsure_primary_escalates_to_kev(registry, kev_server, state, action):
    router = _cascade(
        registry,
        primary={"provider": "mock", "custom_params": {"default_confidence": 0.3}},
        secondary={"provider": "kev", "backend": "local"},
        confidence_threshold=0.8,
    )
    [res] = router.evaluate(state, [action])
    assert res.metadata["routed_to"] == "secondary" and res.metadata["escalated"] is True
    assert res.provider == "kev" and len(kev_server.requests) == 1
    assert router.get_telemetry().escalated_count == 1


def test_kev_as_first_level_answers_without_calling_the_second(registry, kev_server, state, action):
    router = _cascade(
        registry,
        primary={"provider": "kev", "backend": "local"},
        secondary={"provider": "mock", "model_id": "no-deberia-usarse"},
        confidence_threshold=0.7,
    )
    [res] = router.evaluate(state, [action])
    assert res.provider == "kev" and res.metadata["routed_to"] == "primary"


def test_unreachable_cloud_level_fails_closed(registry, state, action):
    """local + API: si la API no responde, el segundo nivel informa de fallo y nunca concede en silencio."""
    from praxeon.providers.mock import MockProvider

    registry.register("nube", lambda cfg: MockProvider(name="nube", available=False))
    router = _cascade(
        registry,
        primary={"provider": "mock", "custom_params": {"default_confidence": 0.1}},
        secondary={"provider": "nube", "backend": "api"},
    )
    [res] = router.evaluate(state, [action])
    assert res.available is False and res.confidence == 0.0
    assert "DUAL_PROVIDER_UNCERTAINTY_ESCALATE" in res.reason_codes


def test_unknown_level_provider_is_unavailable_not_a_crash(registry, state, action):
    router = _cascade(registry, primary="mock", secondary="no-existe")
    assert router.secondary.is_available() is False
    [res] = router.secondary.evaluate(state, [action])
    assert res.available is False and "CASCADE_LEVEL_UNAVAILABLE" in res.reason_codes


def test_a_cascade_cannot_contain_a_cascade(registry):
    with pytest.raises(DecisionProviderError):
        _cascade(registry, primary={"provider": "cascade"})


def test_unknown_strategy_is_rejected(registry):
    with pytest.raises(DecisionProviderError):
        _cascade(registry, strategy="inventada")


def test_cascade_works_inside_a_decision_runtime(registry, kev_server, state, action):
    runtime = DecisionRuntime.from_config(
        DecisionModelConfig(provider="cascade", model_id="cascade", custom_params={"primary": "kev", "secondary": "laya"}),
        registry=registry,
    )
    [res] = runtime.evaluate(state, [action])
    assert res.available and runtime.provider_id == "cascade"


def test_mission_with_cascade_supervisor_uses_the_chosen_levels(tmp_path, monkeypatch):
    from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service

    for key in ("PRAXEON_API_KEY", "PRAXEON_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    try:
        service.start_mission(
            goal="Revisa el proyecto",
            session_id="casc-1",
            supervisor="cascade",
            decision_model={"provider": "cascade", "model_id": "cascade",
                            "custom_params": {"primary": {"provider": "kev", "backend": "local"}, "secondary": "laya"}},
            workspace_root=str(tmp_path),
            max_steps=1,
            step_delay_ms=0,
        )
        provider = service._session_runtimes["casc-1"].decision_provider
        assert provider.provider_id == "cascade"
        assert (provider.primary.provider_id, provider.secondary.provider_id) == ("kev", "laya")
    finally:
        set_runtime_service(None)


# ----------------------------------------------------------------------------- LAYA neuronal (paquete laya)


class FakeLayaRouter:
    """Sustituto de `laya.Router`: mismo contrato (predict -> answers) sin cargar pesos."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or dict(ALLOW_ANSWERS)

    def predict(self, state, questions, **kwargs):
        self.calls.append((state, questions, kwargs))
        return {"model": "laya-rl-agent", "answers": self.answers, "usage": {}}


def test_laya_local_uses_the_laya_package_router(monkeypatch, state, action):
    from praxeon.providers import laya as laya_module

    monkeypatch.setattr(laya_module, "_laya_sdk_installed", lambda: True)
    router = FakeLayaRouter()
    provider = laya_module.LayaProvider(backend="local")
    monkeypatch.setattr(provider, "_get_router", lambda: router)
    assert provider.backend == "local"
    [res] = provider.evaluate(state, [action])
    assert res.provider == "laya" and res.available and res.confidence > 0.9   # 0.9 crudo, afinado por la temperatura
    assert res.metadata["choice"]["raw_probabilities"]["ALLOW"] == 0.9
    assert "LAYA_CHOICE_ALLOW" in res.reason_codes
    assert "list_directory" in router.calls[0][0]  # la acción candidata viaja en el estado (como registro canónico)
    assert set(router.calls[0][1]) == {"decision", "is_loop", "is_grounded", "progress"}
    assert router.calls[0][2] == {"model": "typed-decisions"}  # checkpoint entrenado para decisiones tipadas


def _repeated_read_state(times):
    s = SessionState(session_id="rep", goal=Goal(objective="Resumir el README", success_criteria=["resumen"]))
    from praxeon.domain.decision import DecisionStatus, PolicyDecision

    for i in range(times):
        step = ActionCandidate(id=f"act_{i}", description="Leer README.md",
                               tool_call=ToolCall(tool_name="read_file", arguments={"path": "README.md"}))
        s.add_step(step, PolicyDecision(status=DecisionStatus.ALLOW), observation="# Praxeon")
    return s


def test_neural_loop_signal_alone_cannot_reach_the_blocking_threshold(monkeypatch, state, action):
    """El modelo separa mal «bucle» de «paso nuevo»: un 0.97 neuronal sin repetición comprobable no bloquea."""
    from praxeon.providers import laya as laya_module

    monkeypatch.setattr(laya_module, "_laya_sdk_installed", lambda: True)
    answers = {**ALLOW_ANSWERS, "is_loop": {"type": "noul", "noul": 0.97}}
    provider = laya_module.LayaProvider(backend="local")
    monkeypatch.setattr(provider, "_get_router", lambda: FakeLayaRouter(answers))
    [res] = provider.evaluate(state, [action])
    assert res.loop_probability == laya_module.NEURAL_LOOP_CAP < 0.65
    assert res.metadata["noul"]["neural_is_loop"] == 0.97


def test_an_exact_repeat_is_detected_even_when_the_neural_model_misses_it(monkeypatch):
    from praxeon.providers import laya as laya_module

    monkeypatch.setattr(laya_module, "_laya_sdk_installed", lambda: True)
    answers = {**ALLOW_ANSWERS, "is_loop": {"type": "noul", "noul": 0.05}}
    provider = laya_module.LayaProvider(backend="local")
    monkeypatch.setattr(provider, "_get_router", lambda: FakeLayaRouter(answers))
    repeat = ActionCandidate(id="act_9", description="Leer README.md",
                             tool_call=ToolCall(tool_name="read_file", arguments={"path": "README.md"}))
    [res] = provider.evaluate(_repeated_read_state(3), [repeat])
    assert res.loop_probability >= 0.65
    assert res.metadata["noul"]["neural_is_loop"] == 0.05


def test_laya_local_degrades_visibly_to_the_calibrated_engine_when_the_model_cannot_load(monkeypatch, state, action):
    from praxeon.providers import laya as laya_module

    monkeypatch.setattr(laya_module, "_laya_sdk_installed", lambda: True)
    provider = laya_module.LayaProvider(backend="local")

    def boom():
        raise OSError("sin red para descargar los pesos")

    monkeypatch.setattr(provider, "_get_router", boom)
    [res] = provider.evaluate(state, [action])
    assert res.available and provider.backend == "simulated"  # queda reflejado en backend y metadatos
    assert res.metadata["backend"] == "simulated"


def test_laya_without_the_package_uses_the_calibrated_engine(state, action):
    provider = __import__("praxeon.providers.laya", fromlist=["LayaProvider"]).LayaProvider(backend="auto")
    assert provider.backend == "simulated"  # el conftest simula que el paquete no está instalado


@pytest.mark.laya_neural
def test_real_laya_model_blocks_a_destructive_action(state):
    pytest.importorskip("laya")
    from praxeon.providers.laya import LayaProvider

    provider = LayaProvider(backend="local")
    assert provider.backend == "local"
    destructive = ActionCandidate(
        id="neural-1",
        description="Borrar todo el disco para liberar espacio",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "rm -rf /"}),
    )
    [res] = provider.evaluate(state, [destructive])
    assert res.available and res.metadata["choice"]["label"] == "BLOCK"


def test_calibrate_choice_sharpens_without_changing_the_winner():
    from praxeon.providers.laya import calibrate_choice

    flat = {"label": "ALLOW", "confidence": 0.5, "probabilities": {"ALLOW": 0.5, "REPLAN": 0.2, "BLOCK": 0.15, "ABSTAIN": 0.15}}
    sharp = calibrate_choice(flat, 0.3)
    assert max(sharp["probabilities"], key=sharp["probabilities"].get) == "ALLOW"
    assert sharp["confidence"] > 0.9 and abs(sum(sharp["probabilities"].values()) - 1) < 1e-9
    assert sharp["raw_probabilities"] == flat["probabilities"] and sharp["temperature"] == 0.3
    assert calibrate_choice(flat, 1.0) is flat          # T=1: sin ajuste
    soft = calibrate_choice(flat, 3.0)
    assert soft["confidence"] < 0.5                    # T>1 suaviza


class DroppingRouter(FakeLayaRouter):
    """Recorta el estado (como el checkpoint real con 1.024 tokens) hasta que la petición es lo bastante corta."""

    def __init__(self, fits_in_chars):
        super().__init__()
        self.fits_in_chars = fits_in_chars

    def predict(self, state, questions, **kwargs):
        self.calls.append((state, questions, kwargs))
        dropped = max(0, len(state) - self.fits_in_chars)
        return {"model": "laya-rl-agent", "answers": self.answers, "usage": {"state_tokens_dropped": dropped}}


def _long_history_state(steps, obs_chars=600):
    s = SessionState(session_id="long", goal=Goal(objective="Reparar un bug", success_criteria=["tests en verde"]))
    from praxeon.domain.decision import DecisionStatus, PolicyDecision

    for i in range(steps):
        step = ActionCandidate(id=f"act_{i}", description="Ejecutar pytest",
                               tool_call=ToolCall(tool_name="run_command", arguments={"command": f"pytest -k t{i}"}))
        s.add_step(step, PolicyDecision(status=DecisionStatus.ALLOW), observation="salida del comando " * (obs_chars // 18))
    return s


def test_laya_shrinks_the_state_until_the_model_reads_all_of_it_and_never_loses_the_action(monkeypatch):
    from praxeon.providers import laya as laya_module

    monkeypatch.setattr(laya_module, "_laya_sdk_installed", lambda: True)
    provider = laya_module.LayaProvider(backend="local")
    router = DroppingRouter(fits_in_chars=900)
    monkeypatch.setattr(provider, "_get_router", lambda: router)
    danger = ActionCandidate(id="act_x", description="Borrar todo el disco",
                             tool_call=ToolCall(tool_name="run_command", arguments={"command": "rm -rf / --no-preserve-root"}))
    [res] = provider.evaluate(_long_history_state(10), [danger])
    sizes = [len(c[0]) for c in router.calls]
    assert len(router.calls) > 1 and sizes == sorted(sizes, reverse=True)       # reintenta con estados cada vez menores
    assert sizes[-1] <= 900                                                    # el último cabe entero
    assert "rm -rf /" in router.calls[-1][0]                                   # y la acción propuesta sigue ahí
    assert res.metadata["choice"]["state_fit"]["steps"] < 6


def test_laya_short_state_is_sent_once(monkeypatch, state, action):
    from praxeon.providers import laya as laya_module

    monkeypatch.setattr(laya_module, "_laya_sdk_installed", lambda: True)
    provider = laya_module.LayaProvider(backend="local")
    router = FakeLayaRouter()
    monkeypatch.setattr(provider, "_get_router", lambda: router)
    provider.evaluate(state, [action])
    assert len(router.calls) == 1
