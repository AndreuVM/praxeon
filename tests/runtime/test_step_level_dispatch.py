"""Pruebas unitarias para el Despacho Adaptativo a Nivel de Paso (Fase 8 - F8-02).

Valida el principio 'Semántica != Autoridad':
- Generación dinámica de la tupla (agente, contexto mínimo, herramientas, límites de riesgo).
- Aislamiento de herramientas por rol y agente bajo el principio de menor privilegio.
- Bloqueo preventivo de herramientas no autorizadas.
- Bloqueo preventivo de parámetros con patrones destructivos (rm -rf, drop table).
- Exigencia obligatoria de confirmación humana (HITL) ante riesgo CRITICAL o herramientas del sistema.
"""

import pytest

from praxeon.agents import AgentRegistry, AgentTemplateCatalog
from praxeon.context.manager import ContextManager
from praxeon.domain.assessment import RiskLevel
from praxeon.routing.models import TaskComplexity, TaskRequirement
from praxeon.runtime.adaptive.step_dispatcher import StepLevelAdaptiveDispatcher


@pytest.fixture
def dispatcher():
    ctx_mgr = ContextManager()
    return StepLevelAdaptiveDispatcher(context_manager=ctx_mgr)


def test_step_dispatch_generates_isolated_authorized_tools(dispatcher):
    """Valida que cada agente reciba únicamente las herramientas que su perfil y rol autorizan."""
    dev = AgentTemplateCatalog.instantiate("developer", "ag_dev")
    sec = AgentTemplateCatalog.instantiate("security_auditor", "ag_sec")
    writer = AgentTemplateCatalog.instantiate("writer", "ag_writer")

    task = TaskRequirement(
        task_id="t_step_01",
        prompt="Inspect code and refactor API",
        complexity=TaskComplexity.MEDIUM,
    )

    spec_dev = dispatcher.create_dispatch_spec(task, dev, step_index=0, session_id="sess_01")
    spec_sec = dispatcher.create_dispatch_spec(task, sec, step_index=0, session_id="sess_01")
    spec_writer = dispatcher.create_dispatch_spec(task, writer, step_index=0, session_id="sess_01")

    # Developer puede ejecutar comandos y escribir código
    assert "run_command" in spec_dev.authorized_tools
    assert "write_file" in spec_dev.authorized_tools

    # Security Auditor no puede ejecutar comandos ni modificar archivos
    assert "run_command" not in spec_sec.authorized_tools
    assert "write_file" not in spec_sec.authorized_tools
    assert "view_file" in spec_sec.authorized_tools

    # Writer no puede ejecutar comandos en la shell
    assert "run_command" not in spec_writer.authorized_tools
    assert "write_file" in spec_writer.authorized_tools


def test_step_dispatch_blocks_unauthorized_tool(dispatcher):
    """Valida que validate_action_authorization bloquee herramientas fuera de la lista autorizada del paso."""
    sec = AgentTemplateCatalog.instantiate("security_auditor", "ag_sec")
    task = TaskRequirement(task_id="t1", prompt="Audit security")

    spec = dispatcher.create_dispatch_spec(task, sec, step_index=0, session_id="sess_02")

    # Intento de llamar a run_command
    is_auth, err = dispatcher.validate_action_authorization(
        spec=spec,
        tool_name="run_command",
        arguments={"command": "ls -la"},
    )

    assert is_auth is False
    assert "no está en la lista autorizada" in err


def test_step_dispatch_blocks_destructive_arguments(dispatcher):
    """Valida que patrones destructivos sean bloqueados incluso si la herramienta base está autorizada."""
    dev = AgentTemplateCatalog.instantiate("developer", "ag_dev")
    task = TaskRequirement(task_id="t2", prompt="Run maintenance command")

    spec = dispatcher.create_dispatch_spec(task, dev, step_index=1, session_id="sess_03")
    assert "run_command" in spec.authorized_tools

    # Intento destructivo con rm -rf
    is_auth, err = dispatcher.validate_action_authorization(
        spec=spec,
        tool_name="run_command",
        arguments={"command": "rm -rf /var/data"},
    )

    assert is_auth is False
    assert "destructivo" in err.lower()

    # Intento destructivo con drop table
    is_auth_sql, err_sql = dispatcher.validate_action_authorization(
        spec=spec,
        tool_name="run_command",
        arguments={"command": "sqlite3 db.sqlite 'DROP TABLE users;'"},
    )

    assert is_auth_sql is False
    assert "destructivo" in err_sql.lower()


def test_step_dispatch_requires_human_for_critical_risk(dispatcher):
    """Valida que tareas con nivel de riesgo CRITICAL fuercen require_human_confirmation=True."""
    dev = AgentTemplateCatalog.instantiate("developer", "ag_dev")
    critical_task = TaskRequirement(
        task_id="t_crit",
        prompt="Migrate production database schema",
        inferred_risk=RiskLevel.CRITICAL,
    )

    spec = dispatcher.create_dispatch_spec(critical_task, dev, step_index=0, session_id="sess_crit")
    assert spec.require_human_confirmation is True


def test_step_dispatch_context_fingerprint_deterministic(dispatcher):
    """Valida que la huella de contexto mínimo sea reproducible y no vacía."""
    dev = AgentTemplateCatalog.instantiate("developer", "ag_dev")
    task = TaskRequirement(task_id="t_fp", prompt="Verify consistency")

    spec1 = dispatcher.create_dispatch_spec(task, dev, step_index=0, session_id="sess_fp")
    spec2 = dispatcher.create_dispatch_spec(task, dev, step_index=0, session_id="sess_fp")

    assert len(spec1.minimal_context_fingerprint) == 64
    assert spec1.minimal_context_fingerprint == spec2.minimal_context_fingerprint
    assert spec1.authorization_digest == spec2.authorization_digest
