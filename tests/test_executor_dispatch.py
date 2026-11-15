"""Despacho de herramientas del ejecutor: modo aislado (AUD-19), sandbox local y handlers propios (AUD-23, Q-03).

El modo aislado es la ruta de seguridad con menos cobertura de comportamiento: aquí se comprueba, con un sandbox falso que
anota las llamadas, que cada herramienta llega al método correcto, que lo que falta se rechaza antes de tocar el sandbox y
que una ruta fuera del workspace falla cerrado.
"""

import os
from types import SimpleNamespace

import pytest

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.decision import DecisionStatus, PolicyDecision
from praxeon.domain.goal import Goal
from praxeon.policy.engine import PolicyEngine
from praxeon.runtime.executor import PolicyViolation, SecureExecutor
from praxeon.runtime.state import SessionState


class FakeSandbox:
    """Anota lo que recibe; no toca disco ni lanza procesos."""

    def __init__(self, root, containment_error=None):
        self.workspace_root = root
        self.calls = []
        self.containment_error = containment_error

    @staticmethod
    def _result(text, ok=True):
        return SimpleNamespace(output=text, success=ok, is_error=not ok)

    def read_file(self, path):
        self.calls.append(("read", path))
        return self._result(f"leído {path}")

    def edit_file(self, path, content):
        self.calls.append(("edit", path, content))
        return self._result(f"escrito {path}")

    def execute_command(self, command, cwd=None):
        self.calls.append(("run", command, cwd))
        return self._result(f"ejecutado {command}")

    def _validate_path_containment(self, raw):
        if self.containment_error:
            raise self.containment_error
        return os.path.join(self.workspace_root, raw)


@pytest.fixture
def isolated(tmp_path):
    workspace = str(tmp_path)
    executor = SecureExecutor(secret_key="k" * 32)
    fake = FakeSandbox(os.path.realpath(workspace))
    executor._isolated_sandboxes[os.path.realpath(workspace)] = fake
    state = SimpleNamespace(metadata={"workspace_root": workspace, "working_directory": workspace})
    return executor, fake, state


def dispatch(executor, tool, args, state):
    return executor._execute_builtin_tool_in_container(tool, args, state=state)


# --- modo aislado: cada herramienta llega al método correcto ------------------------------------------------------------
@pytest.mark.parametrize("tool, args, call", [
    ("read_file", {"path": "  a.py  "}, ("read", "a.py")),
    ("view_file", {"file": "b.py"}, ("read", "b.py")),
    ("write_file", {"path": "c.txt", "content": "hola"}, ("edit", "c.txt", "hola")),
    ("edit_file", {"path": "d.txt", "content": "x"}, ("edit", "d.txt", "x")),
])
def test_isolated_mode_routes_file_tools_to_the_sandbox(isolated, tool, args, call):
    executor, fake, state = isolated
    output, success, is_error = dispatch(executor, tool, args, state)
    assert fake.calls == [call] and success and not is_error and output


@pytest.mark.parametrize("args", [{}, {"path": "c.txt"}, {"content": "sin ruta"}])
def test_isolated_mode_rejects_a_write_without_path_or_content_before_touching_the_sandbox(isolated, args):
    executor, fake, state = isolated
    output, success, is_error = dispatch(executor, "write_file", args, state)
    assert not success and is_error and "path y content" in output
    assert fake.calls == []


def test_isolated_mode_runs_commands_in_the_session_directory_unless_told_otherwise(isolated):
    executor, fake, state = isolated
    dispatch(executor, "run_command", {"command": "  pytest -q  "}, state)
    dispatch(executor, "run_command", {"cmd": "ls", "cwd": "sub"}, state)
    assert fake.calls == [("run", "pytest -q", state.metadata["working_directory"]), ("run", "ls", "sub")]


def test_isolated_mode_runs_registered_tools_as_a_command(isolated):
    executor, fake, state = isolated
    assert executor.registry.is_known("git")
    dispatch(executor, "git", {"command": "status"}, state)
    dispatch(executor, "git", {}, state)
    assert [c[1] for c in fake.calls] == ["git status", "git"]


@pytest.mark.parametrize("args, expected", [
    ({"summary": "  listo  "}, "Tarea concluida: listo"),
    ({"final_answer": "ok"}, "Tarea concluida: ok"),
    ({}, "Tarea concluida: Tarea completada."),
])
def test_isolated_mode_finish_does_not_need_the_sandbox(isolated, args, expected):
    executor, fake, state = isolated
    assert dispatch(executor, "finish", args, state) == (expected, True, False)
    assert fake.calls == []


def test_isolated_mode_refuses_a_tool_it_does_not_know(isolated):
    executor, fake, state = isolated
    output, success, is_error = dispatch(executor, "no_existe", {}, state)
    assert not success and is_error and "sin controlador" in output and fake.calls == []


def test_isolated_mode_lists_a_directory_inside_the_workspace(isolated, tmp_path):
    executor, _fake, state = isolated
    (tmp_path / "visible.txt").write_text("x", encoding="utf-8")
    output, success, is_error = dispatch(executor, "list_dir", {"path": "."}, state)
    assert success and not is_error and "visible.txt" in output


def test_isolated_mode_list_dir_fails_closed_when_the_path_leaves_the_workspace(tmp_path):
    executor = SecureExecutor(secret_key="k" * 32)
    workspace = os.path.realpath(str(tmp_path))
    executor._isolated_sandboxes[workspace] = FakeSandbox(workspace, containment_error=ValueError("fuera del workspace"))
    state = SimpleNamespace(metadata={"workspace_root": workspace})
    output, success, is_error = dispatch(executor, "list_dir", {"path": "../../etc"}, state)
    assert not success and is_error and "Ruta fuera del workspace" in output


def test_the_isolated_sandbox_is_reused_per_workspace(tmp_path):
    executor = SecureExecutor(secret_key="k" * 32)
    own = FakeSandbox(os.path.realpath(str(tmp_path)))
    executor.container_sandbox = own
    state = SimpleNamespace(metadata={"workspace_root": str(tmp_path)})
    assert executor._get_effective_container_sandbox(state) is own


# --- sandbox local -------------------------------------------------------------------------------------------------------
def test_local_mode_rejects_an_unknown_tool_and_an_incomplete_write():
    executor = SecureExecutor(secret_key="k" * 32)
    output, success, is_error = executor._execute_builtin_tool_in_sandbox("no_existe", {}, state=None)
    assert not success and is_error and "sin controlador físico" in output
    output, success, is_error = executor._execute_builtin_tool_in_sandbox("write_file", {"path": "x"}, state=None)
    assert not success and is_error and "path y content" in output


def test_without_a_session_the_executor_uses_its_own_sandbox():
    executor = SecureExecutor(secret_key="k" * 32)
    assert executor._get_effective_sandbox(None) is executor.sandbox


# --- de extremo a extremo: recibo firmado -> despacho --------------------------------------------------------------------
def _state(mode, workspace):
    return SessionState(session_id="s", goal=Goal(objective="o", success_criteria=["c"]),
                        metadata={"execution_mode": mode, "workspace_root": workspace, "working_directory": workspace})


def _authorized(engine, state, tool="read_file", arguments=None):
    action = ActionCandidate(id="a1", description="leer", tool_call=ToolCall(tool_name=tool, arguments=arguments or {"path": "README.md"}))
    decision, receipt = engine.evaluate_action(action, state, session_id="s")
    return action, decision, receipt


def test_a_signed_receipt_in_container_mode_reaches_the_isolated_sandbox(tmp_path):
    engine = PolicyEngine(secret_key="k" * 32)
    workspace = os.path.realpath(str(tmp_path))
    state = _state("container", workspace)
    action, decision, receipt = _authorized(engine, state)
    assert receipt.execution_mode == "container"
    executor = SecureExecutor(secret_key=engine.secret_key)
    fake = FakeSandbox(workspace)
    executor._isolated_sandboxes[workspace] = fake
    observation = executor.execute(action, state, receipt=receipt, decision=decision)
    assert observation.success and fake.calls == [("read", "README.md")]


def test_a_failing_custom_handler_becomes_an_error_observation_not_an_exception(tmp_path):
    engine = PolicyEngine(secret_key="k" * 32)
    state = _state("local_restricted", str(tmp_path))
    action, decision, receipt = _authorized(engine, state)
    executor = SecureExecutor(secret_key=engine.secret_key)

    def broken(_args):
        raise RuntimeError("el handler se rompió")

    executor.register_handler("read_file", broken)
    observation = executor.execute(action, state, receipt=receipt, decision=decision)
    assert not observation.success and observation.is_error
    assert "Error en handler de 'read_file'" in observation.output and "se rompió" in observation.output


def test_a_cognitive_step_without_a_tool_is_recorded_without_executing_anything(tmp_path):
    """Un paso sin herramienta solo llega al ejecutor en el modo de compatibilidad explícito, con decisión ALLOW."""
    state = _state("local_restricted", str(tmp_path))
    action = ActionCandidate(id="c1", description="pensar", rationale="primero leo el README")
    decision = PolicyDecision(status=DecisionStatus.ALLOW, reason="ok", action_id="c1")
    observation = SecureExecutor(strict_capability=False).execute(action, state, decision=decision)
    assert observation.success and observation.output == "primero leo el README" and observation.tool_name is None


def test_without_a_receipt_the_strict_executor_denies_even_an_allowed_decision(tmp_path):
    state = _state("local_restricted", str(tmp_path))
    action = ActionCandidate(id="r1", description="leer", tool_call=ToolCall(tool_name="read_file", arguments={"path": "a"}))
    decision = PolicyDecision(status=DecisionStatus.ALLOW, reason="ok", action_id="r1")
    with pytest.raises(PolicyViolation, match="capability"):
        SecureExecutor(secret_key="k" * 32).execute(action, state, decision=decision)
