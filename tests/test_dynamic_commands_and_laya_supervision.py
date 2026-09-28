"""Pruebas unitarias para Clasificación Contextual de Comandos y Supervisión JEV-LAYA.

Verifica:
1. Que herramientas y comandos legítimos no se bloqueen como 'unknown'.
2. Que JEV-LAYA y el RiskEngine clasifiquen en función del contexto:
   - Peligrosos: BLOCK / CRITICAL (ej. rm -rf /, format, drop table)
   - Innecesarios: REPLAN (bucles, desvíos ociosos, falta de fundamentación)
   - Aprobados: ALLOW (inspección, tests, compilación, scripts alineados con el objetivo)
3. Que las pruebas de plugins maliciosos no registrados se mantengan bloqueadas.
"""

import pytest
from praxeon.domain.models import ActionCandidate, DecisionStatus, Goal, RiskLevel, ToolCall
from praxeon.policy.engine import PolicyEngine
from praxeon.policy.registry import ToolRegistry
from praxeon.providers.context import ProviderContext
from praxeon.providers.laya import LayaProvider
from praxeon.reasoning.risk import RiskEngine
from praxeon.runtime.event_bus import EventBus
from praxeon.runtime.executor import SecureExecutor
from praxeon.runtime.state_store import InMemoryStateStore
from praxeon.server.dependencies import RuntimeApplicationService
from praxeon.server.schemas.action import ProposeActionRequest


def test_dynamic_cli_commands_not_blocked_as_unknown():
    """Verifica que comandos CLI habituales (git, pytest, npm, python, cargo, etc.) no sean rechazados como 'unknown'."""
    registry = ToolRegistry(register_defaults=True)
    for cmd_tool in ["git", "pytest", "python", "npm", "cargo", "bash", "curl", "alembic", "ruff"]:
        assert registry.is_known(cmd_tool) is True
        spec = registry.get_tool(cmd_tool)
        assert spec is not None
        assert spec.category in ("system", "inspection", "network")

    # Exploit / unregistered tokens deben seguir siendo desconocidos
    assert registry.is_known("unregistered_malicious_plugin") is False
    assert registry.is_known("unknown_arbitrary_shell") is False
    assert registry.is_known("spawn_reverse_shell") is False


def test_laya_supervision_classifies_destructive_as_block():
    """Verifica que comandos destructivos sean clasificados como Peligrosos (BLOCK) por LAYA."""
    laya = LayaProvider(backend="simulated")
    ctx = ProviderContext(
        session_id="sess_dest",
        goal="Refactorizar módulo de usuarios",
        history_window=[],
        active_evidence=[],
        candidate_action={
            "id": "act_dest",
            "tool_name": "run_command",
            "arguments": {"command": "rm -rf / --no-preserve-root"},
            "description": "Limpieza agresiva",
        },
    )
    primitives = laya._infer_primitives_calibrated(ctx)
    assert primitives.choice["label"] == "BLOCK"
    assert primitives.score < 0.10


def test_laya_supervision_classifies_unnecessary_as_replan():
    """Verifica que comandos innecesarios, ociosos o en bucle sean clasificados como Innecesarios (REPLAN)."""
    laya = LayaProvider(backend="simulated")

    # Caso A: Comando ocioso/distractor
    ctx_idle = ProviderContext(
        session_id="sess_idle",
        goal="Arreglar bug de login",
        history_window=[],
        active_evidence=[],
        candidate_action={
            "id": "act_idle",
            "tool_name": "run_command",
            "arguments": {"command": "sleep 100"},
            "description": "Esperar tiempo indefinido",
        },
    )
    primitives_idle = laya._infer_primitives_calibrated(ctx_idle)
    assert primitives_idle.choice["label"] == "REPLAN"

    # Caso B: Bucle de repetición del mismo comando
    ctx_loop = ProviderContext(
        session_id="sess_loop",
        goal="Optimizar consultas SQL",
        history_window=[
            {"tool": "run_command", "arguments": {"command": "pytest tests/test_sql.py"}},
            {"tool": "run_command", "arguments": {"command": "pytest tests/test_sql.py"}},
        ],
        active_evidence=[],
        candidate_action={
            "id": "act_loop",
            "tool_name": "run_command",
            "arguments": {"command": "pytest tests/test_sql.py"},
            "description": "Repetir el mismo test por tercera vez sin cambios",
        },
    )
    primitives_loop = laya._infer_primitives_calibrated(ctx_loop)
    assert primitives_loop.choice["label"] == "REPLAN"
    assert primitives_loop.noul["is_loop"] >= 0.70


def test_laya_supervision_classifies_constructive_command_as_allow():
    """Verifica que comandos constructivos alineados al objetivo sean clasificados como Aprobados (ALLOW)."""
    laya = LayaProvider(backend="simulated")
    ctx_good = ProviderContext(
        session_id="sess_good",
        goal="Corregir autenticación en API",
        history_window=[],
        active_evidence=["auth.py contiene validación de tokens JWT"],
        candidate_action={
            "id": "act_good",
            "tool_name": "run_command",
            "arguments": {"command": "pytest tests/test_auth.py"},
            "description": "Ejecutar pruebas del módulo de autenticación",
        },
    )
    primitives_good = laya._infer_primitives_calibrated(ctx_good)
    assert primitives_good.choice["label"] == "ALLOW"
    assert primitives_good.score >= 0.75
    assert primitives_good.noul["is_loop"] < 0.20


def test_runtime_service_proposes_dynamic_cli_tool_without_unknown_block():
    """Verifica el flujo completo a través del servicio de runtime para herramientas como git y pytest."""
    service = RuntimeApplicationService(
        state_store=InMemoryStateStore(),
        event_bus=EventBus(),
    )
    sid = "sess_dynamic_cli_test"
    service.create_session(
        goal="Revisar estado de git y ejecutar tests",
        session_id=sid,
        execution_mode="full_access",
    )

    # 1. Proponer acción directa 'git'
    req_git = ProposeActionRequest(
        tool="git",
        arguments={"command": "status"},
        thought_rationale="Comprobar working tree",
    )
    resp_git = service.propose_action(session_id=sid, proposal=req_git)
    assert resp_git.status == "ALLOW"
    assert "UNKNOWN_TOOL_NOT_REGISTERED" not in resp_git.policy.reason_codes

    # 2. Proponer acción directa 'pytest'
    req_test = ProposeActionRequest(
        tool="pytest",
        arguments={"command": "-v"},
        thought_rationale="Ejecutar suite de tests",
    )
    resp_test = service.propose_action(session_id=sid, proposal=req_test)
    assert resp_test.status == "ALLOW"
    assert "UNKNOWN_TOOL_NOT_REGISTERED" not in resp_test.policy.reason_codes


def test_pre_execution_grounding_rejects_nonexistent_file_as_replan():
    """Verifica que intentar leer un archivo que no existe en disco sea rechazado como REPLAN por falta de fundamentación empírica."""
    service = RuntimeApplicationService(
        state_store=InMemoryStateStore(),
        event_bus=EventBus(),
    )
    sid = "sess_nonexistent_file_check"
    service.create_session(
        goal="Auditar código inexistente",
        session_id=sid,
        execution_mode="full_access",
    )

    req_missing = ProposeActionRequest(
        tool="read_file",
        arguments={"path": "completely_invented_file_that_does_not_exist_xyz.py"},
        thought_rationale="Intentando leer archivo alucinado",
    )
    resp_missing = service.propose_action(session_id=sid, proposal=req_missing)
    assert resp_missing.status == "REPLAN"
    assert any("UNGROUNDED" in code or "LOW_GROUNDED" in code for code in resp_missing.policy.reason_codes)


def test_loop_detection_flags_repeated_failing_attempts_as_replan():
    """Verifica que LAYA detecte bucles cuando se repite la misma ruta de archivo o comando en el historial."""
    laya = LayaProvider(backend="simulated")
    ctx = ProviderContext(
        session_id="sess_loop_test",
        goal="Inspección de proyecto",
        history_window=[
            {"id": "step_1", "tool": "read_file", "arguments": {"path": "module_a.py"}, "observation": "Archivo 'module_a.py' no existe"},
            {"id": "step_2", "tool": "read_file", "arguments": {"path": "module_a.py"}, "observation": "Archivo 'module_a.py' no existe"},
        ],
        active_evidence=[],
        candidate_action={
            "id": "act_repeat",
            "tool_name": "read_file",
            "arguments": {"path": "module_a.py"},
            "description": "Reintentando leer module_a.py",
        },
    )
    primitives = laya._infer_primitives_calibrated(ctx)
    assert primitives.noul["is_loop"] >= 0.85
    assert primitives.choice["label"] == "REPLAN"


def test_premature_finish_without_observations_is_rejected_as_replan():
    """Verifica que invocar 'finish' de forma prematura en una tarea de informe/análisis sin observaciones previas sea rechazado como REPLAN."""
    service = RuntimeApplicationService(
        state_store=InMemoryStateStore(),
        event_bus=EventBus(),
    )
    sid = "sess_premature_finish_check"
    service.create_session(
        goal="Dame un informe del proyecto completo",
        session_id=sid,
        execution_mode="full_access",
    )

    req_premature = ProposeActionRequest(
        tool="finish",
        arguments={"summary": "El proyecto parece ser un asistente de IA."},
        thought_rationale="Concluyendo sin leer ningún archivo",
    )
    resp = service.propose_action(session_id=sid, proposal=req_premature)
    assert resp.status == "REPLAN"
    assert "PREMATURE_COMPLETION_WITHOUT_EVIDENCE" in resp.policy.reason_codes
