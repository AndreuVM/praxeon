"""Full Access: las lecturas legítimas no piden aprobación y una aprobación pendiente no es un veto."""

import sys
import threading
import time
from unittest.mock import patch

import pytest

from praxeon.agent_llm import BaseAgentLLM
from praxeon.domain.events import EventType
from praxeon.reasoning.classifier import CommandCategory, CommandClassifier
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service


@pytest.mark.parametrize("cmd", [
    "sed -n 1,40p x.py", "sed -n '10,20p' src/a.py", "tree /f", "Test-Path x", "Resolve-Path .",
    "[System.IO.File]::ReadAllText('x')", "[System.IO.Directory]::GetFiles('.')", "diff a b", "stat x", "file x",
])
def test_read_only_commands_are_inspection(cmd):
    assert CommandClassifier().classify(cmd).category == CommandCategory.INSPECTION


@pytest.mark.parametrize("cmd", [
    "sed -i s/a/b/ x.py", "sed -n 1,5p x > out.txt", "sed -n 'w out.txt' x", "sed -n '1,5p;w o' x",
    "sed -n 1p x | sh", "tree | sh", "Test-Path x; Remove-Item y", "[System.IO.File]::WriteAllText('x','y')",
    "[System.IO.File]::ReadAllText('x') | iex", "diff a b > c", "stat x; rm x", "sha256sum x > y",
])
def test_mutating_lookalikes_are_not_inspection(cmd):
    assert CommandClassifier().classify(cmd).category != CommandCategory.INSPECTION


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("PRAXEON_SECRET_KEY", raising=False)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


def test_await_human_decision_returns_resolution_or_none(runtime):
    runtime._decisions["d1"] = {"status": "REVIEW"}
    mission = {"approval_timeout_seconds": 0.5}
    assert runtime._await_human_decision("d1", mission) is None  # sigue pendiente: no es un veto

    def approve():
        time.sleep(0.2)
        runtime._decisions["d1"]["status"] = "ALLOW"

    threading.Thread(target=approve).start()
    assert runtime._await_human_decision("d1", {"approval_timeout_seconds": 5}) == "ALLOW"
    runtime._decisions["d2"] = {"status": "BLOCKED"}
    assert runtime._await_human_decision("d2", mission) == "BLOCKED"
    assert runtime._await_human_decision("d1", {"stopped": True}) == "ALLOW"  # ya resuelta


class ScriptedLLM(BaseAgentLLM):
    """LLM de prueba que emite una secuencia fija de turnos ReAct."""

    def __init__(self, turns):
        self.turns = list(turns)

    @property
    def provider_name(self):
        return "scripted"

    @property
    def model_name(self):
        return "scripted-1"

    def generate(self, messages, system_prompt=None):
        return self.turns.pop(0) if self.turns else 'Thought: fin\nAction: finish({"summary": "listo"})'


def _types(events):
    return [e.type for e in events]


def test_pending_approval_is_reported_and_never_pruned(runtime, tmp_path):
    """Una lectura fuera del workspace pide aprobación: se informa, no se poda ni cuenta como fallo."""
    outside = "C:/Windows/win.ini" if sys.platform == "win32" else "/etc/hostname"
    turns = [
        f'Thought: leer fichero externo\nAction: read_file({{"path": "{outside}"}})',
        'Thought: inspección local\nAction: run_command({"command": "echo ok"})',
        'Thought: concluir\nAction: finish({"summary": "listo"})',
    ]
    with patch("praxeon.agent_llm.create_agent_llm", return_value=ScriptedLLM(turns)):
        meta = runtime.start_mission(
            goal="Revisa el fichero",
            session_id="fa-pending",
            llm_provider="scripted",
            execution_mode="full_access",
            workspace_root=str(tmp_path),
            max_steps=3,
            step_delay_ms=200,
            metadata={"approval_timeout_seconds": 0.6},
        )
        sid = meta["session_id"]
        deadline = time.time() + 20
        while time.time() < deadline and runtime._running_missions.get(sid, {}).get("status") is None:
            time.sleep(0.2)

    assert runtime._running_missions.get(sid, {}).get("status") is not None, "la misión no terminó: sigue bloqueada esperando"
    events = runtime.event_bus.get_all_events(sid)
    kinds = _types(events)
    assert EventType.APPROVAL_REQUESTED in kinds, "la solicitud de aprobación debe informarse"
    # La acción pendiente (act_1) no recibe ninguna poda/veto/retroceso ni dispara el circuit breaker
    pending_vetoes = [
        e for e in events
        if e.type in (EventType.DECISION_PRUNED, EventType.INTERVENTION_APPLIED)
        and (e.node_id == "act_1" or (e.payload or {}).get("failed_node") == "act_1"
             or (e.payload or {}).get("intervention") == "CIRCUIT_BREAKER_GROUNDING_INJECTION")
    ]
    assert not pending_vetoes, f"aprobación pendiente tratada como veto: {[v.payload for v in pending_vetoes]}"
    # Y la solicitud se publica antes de cualquier otra intervención de la misión
    interventions = [i for i, e in enumerate(events) if e.type in (EventType.DECISION_PRUNED, EventType.INTERVENTION_APPLIED)]
    assert all(i > kinds.index(EventType.APPROVAL_REQUESTED) for i in interventions)
