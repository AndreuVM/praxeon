"""Bucle del agente como pipeline de pasos: cada paso por separado, su orden y el bucle (auditoría A-01)."""

import threading
from types import SimpleNamespace as NS

import pytest

from praxeon.server.runtime_service import agent_loop as L


class Bus:
    def __init__(self):
        self.events = []

    def emit(self, **kw):
        self.events.append(kw)


class Runtime:
    """Runtime mínimo: la política responde con `responses` en orden; la ejecución devuelve `exec_result`."""

    def __init__(self, responses=(), exec_result=None, human="ALLOW"):
        self.event_bus = Bus()
        self.state_store = NS(save_mission=lambda m: None)
        self.context_manager = NS(assemble_mission_prompt=lambda **kw: [], record_actual_tokens=lambda **kw: None)
        self._lock, self._sessions_meta = threading.Lock(), {"s1": {}}
        self.responses = list(responses)
        self.exec_result = exec_result or NS(output="ok", success=True, is_error=False)
        self.human = human
        self.proposals = []

    def propose_action(self, session_id, proposal):
        self.proposals.append(proposal)
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def execute_decision(self, decision_id):
        return self.exec_result

    def confirm_decision(self, **kw):
        return NS(status="ALLOW")

    def _await_human_decision(self, decision_id, mission):
        return self.human

    def _may_auto_confirm(self, tool):
        return True


class LLM:
    provider_name, model_name = "fake", "fake-1"

    def __init__(self, outputs):
        self.outputs = list(outputs)

    def generate(self, conversation, system_prompt=None):
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _resp(status="ALLOW", codes=(), confirm=False):
    return NS(status=status, decision_id="d1", policy=NS(reason_codes=list(codes), requires_confirmation=confirm))


def _ctx(runtime=None, llm=None, tmp_path=None, **kw):
    mission = {"session_id": "s1", "workspace_root": str(tmp_path) if tmp_path else None, **kw.pop("mission", {})}
    defaults = dict(
        runtime=runtime or Runtime(), mission=mission, agent_llm=llm or LLM([]), goal="Hola", max_steps=5, delay_sec=0,
        provider_name="fake", session_mode="local_restricted", system_prompt="SYS", id_prefix="", root_id="root_s1",
        owns_session=True, anchor_id="root_s1",
    )
    defaults.update(kw)
    ctx = L.LoopContext(**defaults)
    ctx.begin_turn()
    return ctx


def _act(ctx, tool="read_file", args=None, thought="t"):
    ctx.tool, ctx.args, ctx.thought = tool, args if args is not None else {"path": "README.md"}, thought
    return ctx


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(L.time, "sleep", lambda s: None)


# ------------------------------------------------------------------------------------------------ orden y bucle


def test_turn_steps_run_in_an_explicit_order():
    assert [step.name for step in L.TURN_STEPS] == [
        "persist_mission", "node_budget_warning", "generate", "parse_action", "semantic_fixation",
        "premature_node_finish", "finish_fields", "tool_permission", "supervise_and_execute",
        "missing_path_feedback", "pending_approval", "record_result", "finish",
    ]


def test_run_turn_stops_at_the_first_step_that_closes_the_turn():
    calls = []

    class Probe(L.Step):
        def __init__(self, name, outcome):
            self.name, self.outcome = name, outcome

        def run(self, ctx):
            calls.append(self.name)
            return self.outcome

    steps = (Probe("a", L.Outcome.CONTINUE), Probe("b", L.Outcome.END_TURN), Probe("c", L.Outcome.CONTINUE))
    assert L.run_turn(steps, _ctx()) is L.Outcome.END_TURN and calls == ["a", "b"]


def test_loop_reads_executes_and_finishes(tmp_path):
    (tmp_path / "README.md").write_text("hola")
    llm = LLM(['Thought: leo\nAction: read_file({"path": "README.md"})', 'Thought: fin\nAction: finish({"summary": "Listo."})'])
    runtime = Runtime([_resp(), _resp()])
    ctx = L.LoopContext(
        runtime=runtime, mission={"session_id": "s1", "workspace_root": str(tmp_path)}, agent_llm=llm, goal="Hola",
        max_steps=5, delay_sec=0, provider_name="fake", session_mode="local_restricted", system_prompt="SYS",
        id_prefix="", root_id="root_s1", owns_session=True, anchor_id="root_s1",
    )
    result = L.AgentLoop(ctx).run()
    assert result["final_answer"] == "Listo." and result["steps_used"] == 2
    assert [r["tool"] for r in result["step_records"]] == ["read_file", "finish"]
    assert result["last_action_id"] == "act_2"
    assert runtime._sessions_meta["s1"]["final_answer"] == "Listo."


def test_a_stopped_mission_does_not_run_any_turn():
    ctx = _ctx(mission={"stopped": True})
    ctx.step_idx = 0
    assert L.AgentLoop(ctx).run()["steps_used"] == 0


def test_a_node_that_runs_out_of_steps_hands_over_what_it_did():
    ctx = _ctx(trace={"node_id": "n1"}, max_steps=2)
    ctx.step_idx = 0
    ctx.step_records = [{"success": True, "tool": "write_file", "arguments": {"path": "a.py"}}]
    loop = L.AgentLoop(ctx, steps=())
    result = loop.run()
    assert result["final_answer"].startswith("[Nodo interrumpido por límite de 2 pasos") and "a.py" in result["final_answer"]


# ------------------------------------------------------------------------------------------------ pasos


def test_node_budget_warning_is_appended_to_the_last_observation():
    ctx = _ctx(trace={"node_id": "n1"}, max_steps=5)
    ctx.step_idx = 4
    ctx.step_records = [{"observation": "salida"}]
    assert L.NodeBudgetWarningStep().run(ctx) is L.Outcome.CONTINUE
    assert "te quedan 2 pasos" in ctx.step_records[-1]["observation"]


def test_generate_opens_the_technical_circuit_breaker_after_three_failures():
    ctx = _ctx(llm=LLM([RuntimeError("caído")]))
    ctx.technical_failures = 2
    assert L.GenerateStep().run(ctx) is L.Outcome.STOP
    assert ctx.mission["stopped"] and ctx.runtime.event_bus.events[-1]["payload"]["intervention"] == "TECHNICAL_CIRCUIT_BREAKER"


def test_generate_fail_closed_stops_at_the_first_failure():
    ctx = _ctx(llm=LLM([RuntimeError("caído")]), llm_fail_policy="fail_closed")
    assert L.GenerateStep().run(ctx) is L.Outcome.STOP and ctx.mission["stopped"]


def test_generate_falls_back_to_the_synthetic_planner():
    ctx = _ctx(llm=LLM([RuntimeError("caído")]))
    assert L.GenerateStep().run(ctx) is L.Outcome.CONTINUE
    assert ctx.llm_output.startswith("Thought: [DEGRADED_MODE: SYNTHETIC_PLANNER]") and ctx.technical_failures == 1


def test_parse_reads_a_tool_call():
    ctx = _ctx()
    ctx.llm_output = 'Thought: leo\nAction: read_file({"path": "x.py"})'
    assert L.ParseActionStep().run(ctx) is L.Outcome.CONTINUE
    assert (ctx.tool, ctx.args) == ("read_file", {"path": "x.py"})


@pytest.mark.parametrize("output, summary", [
    ("finish(summary='corta')", "corta"),
    ("He completado la tarea. Thought: la respuesta es 42", "la respuesta es 42"),
])
def test_parse_turns_free_text_into_a_finish(output, summary):
    ctx = _ctx()
    ctx.llm_output = output
    L.ParseActionStep().run(ctx)
    assert ctx.tool == "finish" and ctx.args == {"summary": summary}


def test_parse_forces_an_inspection_on_the_first_step_of_a_technical_task():
    ctx = _ctx(goal="Arregla el bug de los tests")
    ctx.llm_output = "Solo texto."
    L.ParseActionStep().run(ctx)
    assert ctx.tool == "run_command" and "os.listdir" in ctx.args["command"]


def test_parse_in_a_node_rejects_output_without_an_action():
    ctx = _ctx(trace={"node_id": "n1"})
    ctx.llm_output = "Solo texto."
    assert L.ParseActionStep().run(ctx) is L.Outcome.END_TURN
    assert ctx.step_records[-1]["reason_code"] == "INVALID_ACTION_FORMAT" and ctx.consecutive_failures == 1


def test_semantic_fixation_prunes_the_third_identical_action():
    ctx, step = _ctx(), L.SemanticFixationStep()
    outcomes = []
    for _ in range(3):
        _act(ctx, "read_file", {"path": "a"})
        outcomes.append(step.run(ctx))
    assert outcomes == [L.Outcome.CONTINUE, L.Outcome.CONTINUE, L.Outcome.END_TURN]
    assert ctx.step_records[-1]["reason_code"] == "SEMANTIC_CIRCUIT_BREAKER_LOOP" and ctx.semantic_fixations == 0


def test_a_node_cannot_finish_by_announcing_what_it_will_do():
    ctx = _act(_ctx(trace={"node_id": "n1"}), "finish", {"summary": "Voy a revisar el archivo y luego lo arreglo."})
    assert L.PrematureNodeFinishStep().run(ctx) is L.Outcome.END_TURN
    assert ctx.step_records[-1]["reason_code"] == "PREMATURE_NODE_FINISH"


def test_finish_fields_are_required_until_the_rejection_limit():
    ctx = _act(_ctx(finish_check=lambda summary: "Falta el campo resultado"), "finish", {"summary": "algo"})
    step = L.FinishFieldsStep()
    assert step.run(ctx) is L.Outcome.END_TURN and ctx.finish_rejections == 1
    ctx.finish_rejections = L.MAX_FINISH_REJECTIONS
    assert step.run(ctx) is L.Outcome.CONTINUE


def test_tool_permission_blocks_tools_the_agent_does_not_have():
    ctx = _act(_ctx(tool_filter=lambda tool: "no permitida" if tool == "run_command" else None), "run_command", {"command": "ls"})
    assert L.ToolPermissionStep().run(ctx) is L.Outcome.END_TURN
    assert ctx.step_records[-1]["verdict"] == "BLOCK" and ctx.consecutive_failures == 1
    assert L.ToolPermissionStep().run(_act(ctx, "read_file")) is L.Outcome.CONTINUE


def test_supervise_executes_an_allowed_action():
    ctx = _act(_ctx(Runtime([_resp("ALLOW")])))
    L.SuperviseAndExecuteStep().run(ctx)
    assert ctx.executed_successfully and ctx.obs_output == "ok" and ctx.runtime.proposals[0].action_id == "act_1"


def test_supervise_explains_a_veto():
    ctx = _act(_ctx(Runtime([_resp("BLOCK", ["TOOL_FORBIDDEN_BY_SUPERVISOR"])])))
    L.SuperviseAndExecuteStep().run(ctx)
    assert not ctx.executed_successfully and ctx.obs_output


def test_supervise_waits_for_a_person_and_marks_the_action_pending():
    ctx = _act(_ctx(Runtime([_resp("REVIEW")], human=None)))
    L.SuperviseAndExecuteStep().run(ctx)
    assert ctx.pending_approval and not ctx.executed_successfully


def test_supervise_auto_confirms_only_with_full_access_consent():
    ctx = _act(_ctx(Runtime([_resp("REVIEW")], human="BLOCKED"), session_mode="full_access",
                    mission={"full_access_authorized_by_operator": True}))
    L.SuperviseAndExecuteStep().run(ctx)
    assert ctx.executed_successfully


def test_a_failed_proposal_is_recorded_as_an_error_not_with_the_previous_verdict():
    """Antes, si propose_action fallaba, el turno heredaba el veredicto y los motivos de la respuesta anterior."""
    runtime = Runtime([_resp("BLOCK", ["TOOL_FORBIDDEN_BY_SUPERVISOR"]), RuntimeError("propose falló")])
    ctx = _act(_ctx(runtime))
    L.SuperviseAndExecuteStep().run(ctx)
    ctx.begin_turn()
    _act(ctx, "read_file", {"path": "otro.md"})
    L.SuperviseAndExecuteStep().run(ctx)
    assert ctx.resp is None and ctx.step_verdict == "ERROR" and ctx.step_reasons == ""


def test_missing_path_feedback_lists_what_does_exist(tmp_path):
    (tmp_path / "real.py").write_text("x")
    ctx = _act(_ctx(tmp_path=tmp_path), "read_file", {"path": "ghost.py"})
    L.MissingPathFeedbackStep().run(ctx)
    assert "NO EXISTE" in ctx.obs_output and "real.py" in ctx.obs_output


def test_pending_approval_is_recorded_without_counting_as_a_failure():
    ctx = _act(_ctx())
    ctx.pending_approval, ctx.obs_output = True, "pendiente"
    assert L.PendingApprovalStep().run(ctx) is L.Outcome.END_TURN
    assert ctx.step_records[-1]["pending_approval"] and ctx.consecutive_failures == 0


def test_a_success_moves_the_tree_cursor_and_truncates_long_observations(monkeypatch):
    monkeypatch.setenv("PRAXEON_LLM_OBSERVATION_CHARS", "5000")
    ctx = _act(_ctx())
    ctx.action_node_id, ctx.executed_successfully, ctx.obs_output, ctx.consecutive_failures = "act_1", True, "x" * 9000, 3
    assert L.RecordResultStep().run(ctx) is L.Outcome.CONTINUE
    assert ctx.active_parent_id == "act_1" and ctx.consecutive_failures == 0
    assert "Truncado: 4000 caracteres" in ctx.step_records[-1]["observation"]


def test_the_second_failure_in_a_row_trips_the_circuit_breaker_once():
    ctx = _act(_ctx())
    ctx.step_idx, ctx.action_node_id, ctx.consecutive_failures, ctx.active_parent_id = 2, "act_2", 1, "act_1"
    assert L.RecordResultStep().run(ctx) is L.Outcome.END_TURN
    assert ctx.circuit_breaker_triggered and ctx.active_parent_id == ctx.anchor_id
    assert "[CIRCUIT BREAKER]" in ctx.step_records[-1]["observation"]
    interventions = [e["payload"]["intervention"] for e in ctx.runtime.event_bus.events]
    assert interventions == ["BACKTRACK_AND_BRANCH", "CIRCUIT_BREAKER_GROUNDING_INJECTION"]


def test_too_many_failures_in_a_row_end_the_mission():
    ctx = _act(_ctx())
    ctx.action_node_id, ctx.circuit_breaker_triggered = "act_9", True
    ctx.consecutive_failures = L.MAX_CONSECUTIVE_FAILURES - 1
    assert L.RecordResultStep().run(ctx) is L.Outcome.STOP
    assert ctx.forced_termination and ctx.final_answer.startswith("Misión concluida preventivamente")
    assert ctx.runtime._sessions_meta["s1"]["status"] == "Completed"


def test_an_executed_finish_ends_the_loop_with_the_answer():
    ctx = _act(_ctx(), "finish", {"summary": "Resultado."})
    ctx.executed_successfully = True
    assert L.FinishStep().run(ctx) is L.Outcome.STOP and ctx.final_answer == "Resultado."


def test_a_vetoed_finish_is_sent_back_to_the_agent():
    ctx = _act(_ctx(), "finish", {"summary": "Resultado."})
    ctx.record("BLOCK", "LOW_GROUNDED_PROBABILITY", "vetado")
    assert L.FinishStep().run(ctx) is L.Outcome.END_TURN
    last = ctx.step_records[-1]
    assert last["verdict"] == "REPLAN" and "VETO DEL SUPERVISOR" in last["observation"]


# ------------------------------------------------------------------------------------------------ prompt de sistema


def test_a_broken_prompt_section_is_skipped(tmp_path):
    class Broken:
        def ensure_started(self):
            raise RuntimeError("mcp roto")

    runtime = NS(mcp=Broken(), web=NS(prompt_section=lambda f: "WEB"), skills=None)
    prompt = L.build_loop_system_prompt(runtime, {"workspace_root": str(tmp_path)}, "objetivo", None, "ROL ", None)
    assert prompt.startswith("ROL ") and prompt.endswith("\n\nWEB")
