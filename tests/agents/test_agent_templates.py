"""Pruebas unitarias para el catálogo de plantillas canónicas de agentes (Fase 4 - F4-02).

Valida:
- Existencia y propiedades de las 7 plantillas canónicas.
- Principio de mínimo privilegio y segregación de herramientas.
- Consulta e instanciación a través de AgentTemplateCatalog.
"""

import pytest

from praxeon.agents import (
    AgentStatus,
    AgentTemplateCatalog,
    create_code_reviewer_template,
    create_data_analyst_template,
    create_developer_template,
    create_project_manager_template,
    create_researcher_template,
    create_security_auditor_template,
    create_writer_template,
)
from praxeon.domain.models import RiskLevel


def test_catalog_lists_seven_canonical_templates():
    """Valida que el catálogo contenga exactamente las 7 plantillas especificadas."""
    templates = AgentTemplateCatalog.list_templates()
    assert len(templates) == 7

    roles = {t.role for t in templates}
    expected_roles = {
        "Project Manager",
        "Developer",
        "Code Reviewer",
        "Security Auditor",
        "Researcher",
        "Writer",
        "Data Analyst",
    }
    assert roles == expected_roles


def test_templates_segregation_and_least_privilege():
    """Valida la segregación de privilegios entre roles."""
    dev = create_developer_template()
    reviewer = create_code_reviewer_template()
    sec = create_security_auditor_template()
    pm = create_project_manager_template()
    writer = create_writer_template()

    # Developer puede escribir y testear
    assert dev.is_tool_allowed("write_file") is True
    assert dev.is_tool_allowed("pytest") is True
    assert dev.is_tool_allowed("rm -rf") is False  # Prohibido destructivo

    # Code Reviewer es estrictamente Read-Only
    assert reviewer.is_tool_allowed("read_file") is True
    assert reviewer.is_tool_allowed("grep_search") is True
    assert reviewer.is_tool_allowed("write_file") is False
    assert reviewer.is_tool_allowed("run_command") is False

    # Security Auditor no puede ejecutar comandos shell directamente
    assert sec.is_tool_allowed("run_command") is False
    assert sec.is_tool_allowed("read_file") is True
    assert sec.risk_profile.require_human_confirmation is True

    # Project Manager tiene herramientas de Marq PM y no muta código
    assert pm.is_tool_allowed("pm_create_task") is True
    assert pm.is_tool_allowed("pm_update_task_status") is True
    assert pm.is_tool_allowed("write_file") is False

    # Writer puede escribir documentación pero no correr comandos
    assert writer.is_tool_allowed("write_file") is True
    assert writer.is_tool_allowed("run_command") is False


def test_catalog_query_and_instantiation():
    """Valida la consulta por clave/rol e instanciación de agentes derivados."""
    # Búsqueda por clave canónica
    dev_tpl = AgentTemplateCatalog.get_template("developer")
    assert dev_tpl is not None
    assert dev_tpl.role == "Developer"

    # Búsqueda por rol
    sec_tpl = AgentTemplateCatalog.get_template("Security Auditor")
    assert sec_tpl is not None
    assert sec_tpl.role == "Security Auditor"

    # Instanciación de un agente concreto
    live_agent = AgentTemplateCatalog.instantiate(
        template_name="code_reviewer",
        agent_id="ag_cr_pr_102",
        name="PR 102 Code Reviewer",
        overrides={"metadata": {"assigned_pr": 102}},
    )

    assert live_agent.agent_id == "ag_cr_pr_102"
    assert live_agent.name == "PR 102 Code Reviewer"
    assert live_agent.role == "Code Reviewer"
    assert live_agent.status == AgentStatus.ACTIVE
    assert live_agent.metadata.get("assigned_pr") == 102
    assert live_agent.is_tool_allowed("write_file") is False  # Conserva restricciones de la plantilla
