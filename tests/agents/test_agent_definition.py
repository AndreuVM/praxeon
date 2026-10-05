"""Pruebas unitarias para la especificación formal de AgentDefinition (Fase 4 - F4-01).

Valida:
- Tipado e inmutabilidad de AgentDefinition y sub-esquemas.
- Determinismo y sensibilidad de definition_hash.
- Lógica de permisos de herramientas (allowed_tools y forbidden_tools).
- Operación de clonación de agentes.
- Serialización y deserialización a diccionario/JSON.
"""

import pytest
from pydantic import ValidationError

from praxeon.agents import (
    AgentContextPolicy,
    AgentDefinition,
    AgentStatus,
    ModelConfig,
    RiskProfile,
)
from praxeon.domain.models import RiskLevel


def test_agent_definition_immutability():
    """Valida que AgentDefinition sea inmutable (frozen=True)."""
    agent = AgentDefinition(
        agent_id="ag_reviewer_01",
        name="Security Reviewer",
        role="Code Reviewer",
        system_prompt="Eres un revisor de seguridad estricto.",
    )

    assert agent.agent_id == "ag_reviewer_01"
    assert agent.status == AgentStatus.ACTIVE

    with pytest.raises(ValidationError):
        agent.name = "Mutated Name"  # type: ignore


def test_definition_hash_determinism_and_sensitivity():
    """Valida que el hash sea determinista y cambie ante cualquier alteración de configuración."""
    agent1 = AgentDefinition(
        agent_id="ag_01",
        name="Dev Agent",
        role="Developer",
        system_prompt="Implementa código de alta calidad.",
        allowed_tools=["read_file", "write_file"],
        forbidden_tools=["run_destructive_command"],
    )

    agent2 = AgentDefinition(
        agent_id="ag_01",
        name="Dev Agent",
        role="Developer",
        system_prompt="Implementa código de alta calidad.",
        allowed_tools=["write_file", "read_file"],  # Mismo set en orden distinto
        forbidden_tools=["run_destructive_command"],
    )

    # El hash debe ser idéntico porque se normalizan los sets ordenados
    assert agent1.definition_hash == agent2.definition_hash

    # Alterar un parámetro del modelo debe alterar el hash
    agent_mod = AgentDefinition(
        agent_id="ag_01",
        name="Dev Agent",
        role="Developer",
        system_prompt="Implementa código de alta calidad.",
        allowed_tools=["read_file", "write_file"],
        forbidden_tools=["run_destructive_command"],
        model=ModelConfig(temperature=0.7),
    )
    assert agent_mod.definition_hash != agent1.definition_hash


def test_tool_permissions_logic():
    """Valida la resolución de allowed_tools vs forbidden_tools."""
    # 1. Agente con comodín permitido excepto bash y rm
    agent_sandbox = AgentDefinition(
        agent_id="ag_sandbox",
        name="Sandbox Agent",
        role="Tester",
        system_prompt="Pruebas controladas",
        allowed_tools=["*"],
        forbidden_tools=["bash", "rm", "delete_file"],
    )

    assert agent_sandbox.is_tool_allowed("read_file") is True
    assert agent_sandbox.is_tool_allowed("pytest") is True
    assert agent_sandbox.is_tool_allowed("bash") is False
    assert agent_sandbox.is_tool_allowed("rm") is False

    # 2. Agente con lista blanca estricta (solo lectura)
    agent_read_only = AgentDefinition(
        agent_id="ag_ro",
        name="Auditor",
        role="Auditor",
        system_prompt="Solo lectura",
        allowed_tools=["read_file", "list_dir"],
        forbidden_tools=[],
    )

    assert agent_read_only.is_tool_allowed("read_file") is True
    assert agent_read_only.is_tool_allowed("list_dir") is True
    assert agent_read_only.is_tool_allowed("write_file") is False
    assert agent_read_only.is_tool_allowed("run_command") is False


def test_agent_clone_and_roundtrip():
    """Valida la clonación y la deserialización completa."""
    original = AgentDefinition(
        agent_id="ag_orig",
        name="Base Worker",
        role="Worker",
        system_prompt="Trabajo general",
        allowed_tools=["read_file"],
        risk_profile=RiskProfile(max_risk_level=RiskLevel.LOW),
    )

    # Clonar
    cloned = original.clone(
        new_agent_id="ag_clone_01",
        new_name="Specialized Worker",
        overrides={"allowed_tools": ["read_file", "write_file"]},
    )

    assert cloned.agent_id == "ag_clone_01"
    assert cloned.name == "Specialized Worker"
    assert "write_file" in cloned.allowed_tools
    assert cloned.definition_hash != original.definition_hash

    # Roundtrip to_dict / from_dict
    data = cloned.to_dict()
    assert data["agent_id"] == "ag_clone_01"
    assert "definition_hash" in data

    reconstructed = AgentDefinition.from_dict(data)
    assert reconstructed.agent_id == cloned.agent_id
    assert reconstructed.definition_hash == cloned.definition_hash
    assert reconstructed.model.model_name == cloned.model.model_name
