"""El prompt de la misión solo da el entorno y el formato de respuesta: la supervisión es cosa de PRAXEON, no del prompt."""

import time
from unittest.mock import patch

from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service
from praxeon.server.runtime_service.mission_prompt import build_mission_system_prompt
from tests.test_full_access_read_approvals import ScriptedLLM

FORBIDDEN = ["PRAXEON", "JEV", "supervisor", "VETA", "LOW_GROUNDED", "PRINCIPIOS", "ANTI-SESGO", "Estricto", "obligatoriamente"]


def test_prompt_has_environment_and_format_and_nothing_about_supervision(tmp_path):
    prompt = build_mission_system_prompt(str(tmp_path))
    assert str(tmp_path) in prompt and "Sistema operativo" in prompt and "run_command usa" in prompt
    assert "Thought:" in prompt and "Action:" in prompt
    for tool in ("read_file", "list_dir", "write_file", "edit_file", "run_command", "git", "finish"):
        assert tool in prompt
    for word in FORBIDDEN:
        assert word.lower() not in prompt.lower(), word
    assert len(prompt) < 1300                      # antes: 4.757 caracteres


def test_a_real_mission_sends_that_prompt_and_only_the_task_as_first_message(tmp_path, monkeypatch):
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.setenv("PRAXEON_CACHE_DIR", str(tmp_path / "cache"))
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    seen = []

    class Recording(ScriptedLLM):
        def generate(self, messages, system_prompt=None):
            seen.append((system_prompt, [dict(m) for m in messages]))
            return super().generate(messages, system_prompt)

    (tmp_path / "a.txt").write_text("hola", encoding="utf-8")
    try:
        with patch("praxeon.agent_llm.create_agent_llm", return_value=Recording(['Thought: x\nAction: finish({"summary": "hola mundo ya"})'])):
            service.start_mission(goal="Di hola", session_id="min", llm_provider="scripted", supervisor="mock",
                                  workspace_root=str(tmp_path), max_steps=3, step_delay_ms=0, execution_mode="local_restricted",
                                  autonomous=True, allow_unattended_execution=True)
            deadline = time.time() + 30
            while time.time() < deadline and not any(e.type.value == "session.completed" for e in service.event_bus.get_all_events("min")):
                time.sleep(0.2)
    finally:
        set_runtime_service(None)
    system, messages = seen[0]
    base = build_mission_system_prompt(str(tmp_path))
    assert system.startswith(base)
    extra = system[len(base):]      # lo único que se añade es la sección de herramientas web (activadas por defecto)
    assert extra.startswith("\n\nHerramientas web (solo lectura)") and "web_fetch(url" in extra
    assert messages == [{"role": "user", "content": "Tarea: Di hola"}]


def test_bare_agent_of_the_benchmark_uses_the_same_prompt(tmp_path):
    from praxeon.evaluation.agentbench import arms
    from praxeon.evaluation.agentbench.metering import MeteredLLM
    from praxeon.evaluation.agentbench.tasks import build_suite

    seen = []

    class Rec:
        provider_name = model_name = "r"
        last_usage = None
        stateful_session = False

        def generate(self, messages, system_prompt=None):
            seen.append((system_prompt, messages))
            return 'Thought: x\nAction: finish({"summary": "ok"})'

        def end_session(self):
            return None

    task = build_suite(only=["qa_config"])[0]
    arms.run_bare(task, tmp_path, MeteredLLM(Rec()), 2)
    assert seen[0][0] == build_mission_system_prompt(str(tmp_path))
    assert seen[0][1][0]["content"] == f"Tarea: {task.goal}"
