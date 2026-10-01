"""Pruebas unitarias para Fase 5: CLI, MCPBridge y ProxyMiddleware."""

import json
from pathlib import Path
import pytest

from praxeon.cli import analyze_trace_file, simulate_trace_execution
from praxeon.interceptor.mcp_bridge import MCPBridge
from praxeon.interceptor.proxy_middleware import JEVProxyMiddleware

DATA_DIR = Path(__file__).parent.parent / "data" / "loop_traces"


def test_cli_analyze_all_sample_traces(capsys):
    """Verifica que el comando analyze procese sin errores todas las trazas de ejemplo."""
    for trace_file in DATA_DIR.glob("*.json"):
        analyze_trace_file(str(trace_file))
        captured = capsys.readouterr()
        assert "JEV-Reasoning-Navigator" in captured.out


def test_cli_simulate_execution(capsys):
    """Verifica que el comando simulate ejecute la traza paso a paso e intercepte bucles."""
    simulate_trace_execution(str(DATA_DIR / "tool_loop.json"))
    captured = capsys.readouterr()
    assert "Modo Simulación JEV" in captured.out
    assert "INTERCEPCIÓN ACTIVADA" in captured.out


def test_mcp_bridge_evaluate_next_step():
    """Verifica que MCPBridge evalúe una acción candidata y alerte sobre bucles repetitivos."""
    bridge = MCPBridge()
    goal = "Corregir fallo en parser.py"
    history = [
        {"step_type": "thought", "content": "Probar linter"},
        {"step_type": "tool_call", "tool_name": "run_command", "tool_args": {"cmd": "flake8"}},
        {"step_type": "observation", "content": "error E999"},
    ]
    # Acción repetida
    proposed_loop = {
        "step_type": "tool_call",
        "tool_name": "run_command",
        "tool_args": {"cmd": "flake8"},
    }

    res = bridge.evaluate_next_step(goal, history, proposed_loop)
    assert res["safe"] is False
    assert res["loop_detected"] is True
    assert res["directive"] is not None
    assert res["total_jev"] < 0.0


def test_mcp_bridge_diagnose_trace():
    """Verifica el endpoint MCP de diagnóstico de trazas completas."""
    bridge = MCPBridge()
    with open(DATA_DIR / "search_cycle.json", "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    report = bridge.diagnose_trace(trace_data)
    assert report["loop_detected"] is True
    assert report["loop_type"] == "n_hop_cycle"
    assert report["total_steps"] == 11
    assert report["directive"] is not None


def test_proxy_middleware_interception():
    """Verifica que JEVProxyMiddleware bloquee llamadas degenerativas a herramientas."""
    middleware = JEVProxyMiddleware(goal="Compilar proyecto en C++")

    # Paso 1: comando inicial
    safe1, _ = middleware.intercept_tool_call("g++", {"file": "main.cpp"})
    assert safe1 is True
    middleware.record_observation("main.cpp:5: undefined reference to 'foo'")

    # Paso 2: reintento idéntico inmediato
    safe2, injection = middleware.intercept_tool_call("g++", {"file": "main.cpp"})
    assert safe2 is False
    assert injection is not None
    assert "<system_intervention" in injection


def test_mcp_bridge_stdio_framing_and_blank_lines(monkeypatch):
    """Verifica que run_stdio_server ignore líneas en blanco y procese JSON multilínea."""
    import io

    input_data = (
        "\n\n\r\n"
        '{"jsonrpc": "2.0", "id": 1, "method": "initialize"}\n'
        "\n"
        '{\n  "jsonrpc": "2.0",\n  "id": 2,\n  "method": "ping"\n}\n'
        "\n"
    )
    fake_stdin = io.StringIO(input_data)
    fake_stdout = io.StringIO()

    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout", fake_stdout)

    bridge = MCPBridge()
    bridge.run_stdio_server()

    output_lines = [json.loads(line) for line in fake_stdout.getvalue().strip().split("\n") if line.strip()]
    assert len(output_lines) == 2
    assert output_lines[0]["id"] == 1
    assert output_lines[0]["result"]["protocolVersion"] == "2024-11-05"
    assert output_lines[1]["id"] == 2
    assert output_lines[1]["result"] == {}


def test_proxy_middleware_intercept_step_chunk_clean():
    """Verifica que JEVProxyMiddleware apruebe un bloque de pasos convergentes sin llamadas redundantes."""
    middleware = JEVProxyMiddleware(goal="Compilar y probar proyecto Rust")
    proposed = [
        {"tool_name": "cargo", "tool_args": {"subcmd": "check"}, "thought_rationale": "Verificar compilación"},
        {"tool_name": "read_file", "tool_args": {"path": "src/lib.rs"}, "thought_rationale": "Leer definición"},
    ]
    res = middleware.intercept_step_chunk(proposed)
    assert res.all_safe is True
    assert res.valid_step_count == 2
    assert res.flagged_step_index is None
    assert len(res.step_scores) == 2


def test_proxy_middleware_intercept_step_chunk_with_loop():
    """Verifica que JEVProxyMiddleware detenga la validación en el paso divergente del bloque."""
    middleware = JEVProxyMiddleware(goal="Compilar y probar proyecto Rust")
    proposed = [
        {"tool_name": "cargo", "tool_args": {"subcmd": "check"}, "thought_rationale": "Verificar compilación"},
        {"tool_name": "cargo", "tool_args": {"subcmd": "check"}, "thought_rationale": "Reintentar idéntico"},
    ]
    res = middleware.intercept_step_chunk(proposed)
    assert res.all_safe is False
    assert res.valid_step_count == 1
    assert res.flagged_step_index == 1
    assert res.directive is not None
    assert "PODA" in res.directive.message or "Alerta" in res.directive.message


def test_mcp_bridge_evaluate_step_chunk():
    """Verifica el endpoint MCP de evaluación agrupada de bloques (chunking)."""
    bridge = MCPBridge()
    goal = "Resolver fallo en tests"
    history = [
        {"step_type": "thought", "content": "Inicio"},
        {"step_type": "tool_call", "tool_name": "pytest", "tool_args": {}},
        {"step_type": "observation", "content": "FAILED test_login"},
    ]
    proposed_chunk = [
        {"tool_name": "read_file", "tool_args": {"path": "test_login.py"}, "thought_rationale": "Inspeccionar test"},
        {"tool_name": "edit_file", "tool_args": {"path": "test_login.py"}, "thought_rationale": "Modificar test"},
    ]
    res = bridge.evaluate_step_chunk(goal, history, proposed_chunk)
    assert res["all_safe"] is True
    assert res["valid_step_count"] == 2
    assert len(res["step_scores"]) == 2


def test_parse_llm_steps():
    """Verifica que el parser de pasos extraiga bloques multi-paso y formatos individuales."""
    from praxeon.live_agent import parse_llm_steps

    multi_step_output = (
        "Step 1:\n"
        "Thought: Primero examino el archivo.\n"
        'Action: read_file {"path": "src/main.rs"}\n'
        "Step 2:\n"
        "Thought: Luego compilo para verificar.\n"
        'Action: run_command {"command": "cargo check"}'
    )
    steps = parse_llm_steps(multi_step_output)
    assert len(steps) == 2
    assert steps[0]["tool_name"] == "read_file"
    assert steps[0]["tool_args"]["path"] == "src/main.rs"
    assert steps[1]["tool_name"] == "run_command"
    assert steps[1]["tool_args"]["command"] == "cargo check"

    single_step_output = (
        "Thought: Voy a compilar directamente.\n"
        'Action: run_command {"command": "cargo build"}'
    )
    single = parse_llm_steps(single_step_output)
    assert len(single) == 1
    assert single[0]["tool_name"] == "run_command"


def test_live_agent_main_iterative_goals(monkeypatch):
    """Verifica que jev-live solicite y procese múltiples objetivos de manera iterativa."""
    import io
    from praxeon import live_agent

    executed_tasks = []

    def mock_run_agent(task, **kwargs):
        executed_tasks.append(task)

    monkeypatch.setattr(live_agent, "run_live_gemini_agent", mock_run_agent)
    monkeypatch.setattr("sys.argv", ["jev-live"])

    # Simular entrada estándar con dos objetivos y luego comando de salida
    fake_stdin = io.StringIO("Crear validador de emails\nGenerar documentación\nsalir\n")
    monkeypatch.setattr("sys.stdin", fake_stdin)

    live_agent.main()

    assert executed_tasks == ["Crear validador de emails", "Generar documentación"]


def test_live_agent_main_once_flag(monkeypatch):
    """Verifica que el flag --once ejecute únicamente la meta proporcionada y finalice."""
    from praxeon import live_agent

    executed_tasks = []

    def mock_run_agent(task, **kwargs):
        executed_tasks.append(task)

    monkeypatch.setattr(live_agent, "run_live_gemini_agent", mock_run_agent)
    monkeypatch.setattr("sys.argv", ["jev-live", "Tarea única directa", "--once"])

    live_agent.main()

    assert executed_tasks == ["Tarea única directa"]


def test_proxy_middleware_terminal_finish():
    """Verifica que la acción terminal finish sea reconocida como convergente y no bloqueada."""
    middleware = JEVProxyMiddleware(goal="Inspeccionar archivos y resumir objetivo")
    
    # 1. Registrar observación de inspección
    middleware.record_observation("pyproject.toml: JEV-Reasoning-Navigator")

    # 2. Proponer paso terminal finish
    proposed = [
        {
            "step_type": "tool_call",
            "tool_name": "finish",
            "tool_args": {"summary": "El proyecto es JEV-Reasoning-Navigator."},
            "thought_rationale": "Con los datos verificados, sintetizo la respuesta.",
        }
    ]

    res = middleware.intercept_step_chunk(proposed)
    assert res.all_safe is True
    assert res.hallucination_detected is False
    assert res.loop_report is None or res.loop_report.loop_detected is False

    # 3. Verificar intercept_tool_call
    allowed, directive = middleware.intercept_tool_call("finish", {"summary": "Fin"})
    assert allowed is True
    assert directive is None


def test_dashboard_interactive_loop(monkeypatch):
    """Verifica que el dashboard soporte ejecución interactiva continua y salida con 'salir'."""
    import io
    from praxeon import dashboard

    # 1. Probar corrida con --once
    dashboard.run_visual_demo(task="Tarea Once", once=True)

    # 2. Probar corrida interactiva con entrada estándar simulada
    fake_stdin = io.StringIO("Tarea Interactiva 1\nsalir\n")
    monkeypatch.setattr("sys.stdin", fake_stdin)
    dashboard.run_visual_demo(once=False)


def test_run_visual_live_interactive(monkeypatch):
    """Verifica que run_visual_live ejecute tareas y soporte memoria entre iteraciones."""
    from praxeon import dashboard

    recorded_calls = []

    def mock_exec(goal, **kwargs):
        session = kwargs.get("session_context")
        middleware = kwargs.get("middleware")
        recorded_calls.append((goal, session is not None))
        mock_dash = dashboard.JEVDashboard(goal=goal)
        return (True, "Completado", mock_dash, 1, session, middleware)

    monkeypatch.setattr(dashboard, "_execute_single_live_task", mock_exec)
    monkeypatch.setattr(dashboard, "_display_task_results", lambda *a, **k: None)

    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("Tarea 1\nTarea 2\nsalir\n"))
    dashboard.run_visual_live(once=False)

    assert len(recorded_calls) == 2
    assert recorded_calls[0][0] == "Tarea 1"
    assert recorded_calls[1][0] == "Tarea 2"



def test_model_recovery_menu_options(monkeypatch, tmp_path):
    """Verifica que el menú de recuperación permita seleccionar modelos alternativos o cancelar."""
    import io
    from praxeon.model_recovery import prompt_model_recovery_menu, _persist_api_key_to_env

    # 1. Opción 1: Groq (con GROQ_API_KEY presente en env)
    monkeypatch.setenv("GROQ_API_KEY", "mock_groq_key")
    monkeypatch.setattr("sys.stdin", io.StringIO("1\n"))
    action, payload = prompt_model_recovery_menu("429 Cuota agotada", "gemini-2.5-flash")
    assert action == "change_model"
    assert payload == "groq:llama-3.3-70b-versatile"

    # 2. Opción 3: Ollama local (sin requerir clave de API)
    monkeypatch.setattr("sys.stdin", io.StringIO("3\n"))
    action, payload = prompt_model_recovery_menu("429 Cuota agotada", "groq:llama-3.3-70b-versatile")
    assert action == "change_model"
    assert payload == "ollama:qwen2.5-coder:7b"

    # 3. Opción 6: gemini-3.6-flash (con GEMINI_API_KEY presente)
    monkeypatch.setenv("GEMINI_API_KEY", "mock_gemini_key")
    monkeypatch.setattr("sys.stdin", io.StringIO("6\n"))
    action, payload = prompt_model_recovery_menu("429 Cuota agotada", "ollama:qwen2.5-coder:7b")
    assert action == "change_model"
    assert payload == "gemini:gemini-3.6-flash"

    # 4. Opción 8: Modelo personalizado
    monkeypatch.setattr("sys.stdin", io.StringIO("8\ngroq:llama-3.1-8b-instant\n"))
    action, payload = prompt_model_recovery_menu("Error 404", "gemini-3.6-flash")
    assert action == "change_model"
    assert payload == "groq:llama-3.1-8b-instant"

    # 5. Opción 10: Cancelar
    monkeypatch.setattr("sys.stdin", io.StringIO("10\n"))
    action, payload = prompt_model_recovery_menu("Error fatal", "gemini-3.6-flash")
    assert action == "abort"
    assert payload is None

    # 6. Persistencia de API Key en archivo
    test_env = tmp_path / ".env.test"
    test_env.write_text("GEMINI_API_KEY=old_key\nTYPESAFE_API_KEY=safe_key\n", encoding="utf-8")
    _persist_api_key_to_env("new_secret_key", key_name="GEMINI_API_KEY", env_path=str(test_env))
    content = test_env.read_text(encoding="utf-8")
    assert "GEMINI_API_KEY=new_secret_key" in content
    assert "TYPESAFE_API_KEY=safe_key" in content


def test_parse_llm_steps_inline_action_not_finish():
    """Verifica que si el modelo emite Action en la misma línea que Thought, no se interprete como finish erróneo."""
    from praxeon.live_agent import parse_llm_steps

    inline_text = (
        'Thought: Necesito conocer todos los archivos del proyecto para poder auditarlos correctamente. '
        'Verificaremos qué archivos existen. Action: run_command {"command": "powershell -Command \\"Get-ChildItem -Recurse -File\\""}'
    )
    steps = parse_llm_steps(inline_text)
    assert len(steps) == 1
    assert steps[0]["tool_name"] == "run_command"
    assert "powershell" in steps[0]["tool_args"]["command"]
    assert steps[0]["tool_name"] != "finish"


def test_session_context_manager_lifecycle():
    """Verifica el ciclo de vida del SessionContextManager: acumulación, inyección y extracción de hechos."""
    from praxeon.core.session_context import SessionContextManager

    session = SessionContextManager()
    assert session.is_empty() is True

    # 1. Primera tarea
    conv1 = session.prepare_task_conversation("Listar archivos de configuración")
    assert len(conv1) == 2
    assert "Listar archivos de configuración" in conv1[1]["content"]

    # 2. Registrar finalización de tarea 1
    session.record_completed_task(
        goal="Listar archivos de configuración",
        summary="Se encontraron pyproject.toml y .env",
        final_answer="Los archivos detectados son pyproject.toml y .env.",
        executed_steps=3,
        history_steps=[
            {"tool_name": "read_file", "tool_args": {"path": "pyproject.toml"}},
            {"tool_name": "read_file", "tool_args": {"path": ".env"}},
        ],
    )
    assert session.is_empty() is False
    assert len(session.task_records) == 1
    assert "pyproject.toml" in session.all_discovered_files
    assert ".env" in session.all_discovered_files

    # 3. Segunda tarea concatenada (debe heredar contexto de la primera)
    conv2 = session.prepare_task_conversation("Modificar pyproject.toml agregando pytest")
    assert len(conv2) == 2
    task2_prompt = conv2[1]["content"]
    assert "MEMORIA DE SESIÓN CONTINUA" in task2_prompt
    assert "pyproject.toml" in task2_prompt
    assert "Se encontraron pyproject.toml y .env" in task2_prompt
    assert "Modificar pyproject.toml agregando pytest" in task2_prompt

    # 4. Verificar reinicio de sesión
    session.reset()
    assert session.is_empty() is True
    assert len(session.task_records) == 0
    assert len(session.all_discovered_files) == 0


def test_live_agent_reset_command_clears_context(monkeypatch):
    """Verifica que el comando reset en live_agent limpie la memoria de sesión entre tareas."""
    import io
    from praxeon import live_agent

    executed_tasks = []

    def mock_run_agent(task, **kwargs):
        executed_tasks.append(task)
        session = kwargs.get("session_context")
        if session:
            session.record_completed_task(goal=task, summary="OK", final_answer="Hecho", executed_steps=1)
        return (True, "Hecho", session, kwargs.get("middleware"))

    monkeypatch.setattr(live_agent, "run_live_gemini_agent", mock_run_agent)
    monkeypatch.setattr("sys.argv", ["jev-live"])

    # Entrada estándar: Tarea 1 -> reset -> Tarea 2 -> salir
    fake_stdin = io.StringIO("Tarea Inicial\nreset\nTarea Limpia\nsalir\n")
    monkeypatch.setattr("sys.stdin", fake_stdin)

    live_agent.main()

    assert executed_tasks == ["Tarea Inicial", "Tarea Limpia"]


def test_proxy_middleware_rejects_evasive_finish_single_call():
    """Verifica que intercept_tool_call rechace finalizaciones con resúmenes evasivos o excusas."""
    middleware = JEVProxyMiddleware(goal="Investigar fallo en main.py")
    safe, injection = middleware.intercept_tool_call(
        tool_name="finish",
        tool_args={"summary": "Pendiente de lectura de archivo"},
        thought_rationale="Como soy un agente, este paso es de planificación; la acción real será tras read_file",
    )
    assert safe is False
    assert injection is not None
    assert "Finalización evasiva rechazada" in injection


def test_proxy_middleware_rejects_evasive_finish_in_chunk():
    """Verifica que intercept_step_chunk detecte y bloquee finish evasivos como UNGROUNDED_PREMISE."""
    from praxeon.models.schema import LoopType

    middleware = JEVProxyMiddleware(goal="Diagnosticar fallo")
    proposed_steps = [
        {
            "step_type": "tool_call",
            "thought_rationale": "Planeando análisis",
            "tool_name": "finish",
            "tool_args": {"summary": "Pendiente de lectura del archivo"},
        }
    ]
    res = middleware.intercept_step_chunk(proposed_steps)
    assert res.all_safe is False
    assert res.loop_report is not None
    assert res.loop_report.loop_type == LoopType.UNGROUNDED_PREMISE
    assert "Finalización evasiva" in res.loop_report.explanation


def test_proxy_middleware_rejects_finish_coexisting_with_unexecuted_inspection():
    """Verifica que intercept_step_chunk bloquee un finish propuesto en el mismo bloque que un read_file sin ejecutar."""
    from praxeon.models.schema import LoopType

    middleware = JEVProxyMiddleware(goal="Leer y resolver")
    proposed_steps = [
        {
            "step_type": "tool_call",
            "thought_rationale": "Leer el archivo primero",
            "tool_name": "read_file",
            "tool_args": {"path": "config.json"},
        },
        {
            "step_type": "tool_call",
            "thought_rationale": "Concluir tarea",
            "tool_name": "finish",
            "tool_args": {"summary": "Tarea completada con éxito"},
        },
    ]
    res = middleware.intercept_step_chunk(proposed_steps)
    assert res.all_safe is False
    assert res.flagged_step_index == 1
    assert res.loop_report is not None
    assert res.loop_report.loop_type == LoopType.UNGROUNDED_PREMISE


def test_proxy_middleware_accepts_genuine_finish():
    """Verifica que un finish genuino y fundamentado sea aceptado con alto JEV score."""
    middleware = JEVProxyMiddleware(goal="Investigar fallo en main.py")
    safe, injection = middleware.intercept_tool_call(
        tool_name="finish",
        tool_args={"summary": "El bug se originaba por una división por cero en la línea 42 de main.py."},
        thought_rationale="Hemos inspeccionado main.py y verificado que el divisor era 0.",
    )
    assert safe is True
    assert injection is None


def test_session_context_injects_environment_info():
    """Verifica que SessionContextManager descubra e inyecte el entorno del sistema y el árbol de archivos."""
    from praxeon.core.session_context import SessionContextManager

    session = SessionContextManager()
    env_info = session.get_environment_info()
    assert "INFORMACIÓN DEL ENTORNO DE EJECUCIÓN" in env_info
    assert "Sistema Operativo:" in env_info
    assert "pyproject.toml" in env_info

    conv = session.prepare_task_conversation("Analizar dependencias")
    user_prompt = conv[1]["content"]
    assert "INFORMACIÓN DEL ENTORNO DE EJECUCIÓN" in user_prompt
    assert "pyproject.toml" in user_prompt

def test_laya_supervisor_middleware():
    """Verifica que JEVProxyMiddleware funcione con el supervisor LAYA System-1."""
    from praxeon.config import PraxeonConfig, ProviderConfig
    from praxeon.models.schema import BatchSemantics

    cfg = PraxeonConfig(
        provider=ProviderConfig(
            name="laya",
            model="laya-v1-calibrated",
            laya_backend="simulated",
        )
    )
    middleware = JEVProxyMiddleware(goal="Investigar fallo en main.py", config=cfg)
    assert middleware.engine.laya_provider is not None
    assert middleware.engine.laya_provider.backend == "simulated"

    # Paso seguro constructivo
    chunk_res = middleware.intercept_step_chunk([
        {
            "step_type": "tool_call",
            "tool_name": "read_file",
            "tool_args": {"path": "README.md"},
            "thought_rationale": "Leer el archivo README para entender el proyecto.",
        }
    ])
    assert chunk_res.all_safe is True
    assert chunk_res.valid_step_count == 1


def test_dashboard_laya_supervisor_header():
    """Verifica que el dashboard refleje LAYA System-1 cuando está activo."""
    from praxeon.config import PraxeonConfig, ProviderConfig
    from praxeon.dashboard import JEVDashboard

    cfg = PraxeonConfig(
        provider=ProviderConfig(
            name="laya",
            model="laya-v1-calibrated",
            laya_backend="simulated",
        )
    )
    dash = JEVDashboard(goal="Demo de LAYA", model_name="qwen2.5-coder:1.5b (OLLAMA)", config=cfg)
    assert dash.is_laya is True
    assert "LAYA" in dash.supervisor_status

    header = dash.make_header()
    assert header is not None

    sup_panel = dash.make_supervisor_panel()
    assert "LAYA System-1" in str(sup_panel.title)


def test_proxy_middleware_observational_loop_repetition():
    """Verifica que lecturas repetidas de un mismo archivo se permitan 2 veces y la 3ra active bucle sin error NameError."""
    middleware = JEVProxyMiddleware(goal="Inspeccionar README.md")

    step_read = {
        "step_type": "tool_call",
        "tool_name": "read_file",
        "tool_args": {"path": "README.md"},
        "thought_rationale": "Leer README",
    }

    # 1era lectura: permitida
    res1 = middleware.intercept_step_chunk([step_read])
    assert res1.all_safe is True

    # 2da lectura: permitida (idempotente/contraste)
    res2 = middleware.intercept_step_chunk([step_read])
    assert res2.all_safe is True

    # 3era lectura idéntica: bucle detectado (no alucinación, y usa json.dumps sin error)
    res3 = middleware.intercept_step_chunk([step_read])
    assert res3.all_safe is False
    assert res3.hallucination_detected is False
    assert res3.hallucination_type == "loop_repetition"
    assert "README.md" in res3.explanation
    assert res3.loop_report is not None
    assert res3.loop_report.loop_detected is True


def test_proxy_middleware_rejects_supervisor_meta_leakage_in_finish():
    """Verifica que si el agente reproduce advertencias de supervisión en finish, sea rechazado."""
    middleware = JEVProxyMiddleware(goal="Que opinas de este proyecto?")

    middleware.record_observation("README.md: Praxeon runtime supervisor")

    leakage_finish = {
        "step_type": "tool_call",
        "tool_name": "finish",
        "tool_args": {
            "summary": "El proceso ha detectado un bucle o estancamiento en el paso anterior, lo que ha causado que la herramienta edit_file haya sido vetada temporalmente."
        },
        "thought_rationale": "Resumiendo estado del sistema.",
    }

    res = middleware.intercept_step_chunk([leakage_finish])
    assert res.all_safe is False

    allowed, directive = middleware.intercept_tool_call(
        "finish",
        {"summary": "Herramienta edit_file vetada temporalmente por el supervisor."},
    )
    assert allowed is False
    assert "Tu llamada a 'finish' ha sido RECHAZADA" in str(directive)











