"""Pruebas unitarias para el registro de agentes y ciclo de vida CRUD (Fase 4 - F4-03).

Valida:
- Operaciones CRUD (register, get, list_all, update, delete).
- Control de estados operativos (activate, deactivate, duplicate).
- Incremento automático de version y consistencia de hash.
- Exportación e importación portable en formato JSON y YAML.
- Persistencia e importación de bundles.
"""

import json
import pytest

from praxeon.agents import (
    AgentDefinition,
    AgentRegistry,
    AgentStatus,
    AgentTemplateCatalog,
    ModelConfig,
)


@pytest.fixture
def clean_registry():
    return AgentRegistry()


def test_registry_crud_lifecycle(clean_registry):
    """Valida el ciclo de vida CRUD básico."""
    reg = clean_registry

    agent = AgentDefinition(
        agent_id="ag_test_01",
        name="Test Worker",
        role="Worker",
        system_prompt="Pruebas de ciclo de vida",
    )

    # 1. Register
    reg.register(agent)
    assert reg.get("ag_test_01") is not None
    assert reg.get("ag_test_01").version == 1

    # Intentar duplicar ID debe fallar
    with pytest.raises(ValueError):
        reg.register(agent)

    # 2. Update e incremento de versión
    updated = reg.update("ag_test_01", {"name": "Test Worker Enhanced", "skills": ["python"]})
    assert updated.name == "Test Worker Enhanced"
    assert updated.version == 2
    assert "python" in updated.skills
    assert updated.definition_hash != agent.definition_hash

    # 3. Soft Delete
    reg.delete("ag_test_01", hard_delete=False)
    del_agent = reg.get("ag_test_01")
    assert del_agent is not None
    assert del_agent.status == AgentStatus.TERMINATED

    # 4. Hard Delete
    reg.delete("ag_test_01", hard_delete=True)
    assert reg.get("ag_test_01") is None


def test_registry_filters_and_state_transitions(clean_registry):
    """Valida los filtros de list_all y las transiciones de estado."""
    reg = clean_registry

    ag1 = AgentTemplateCatalog.instantiate("developer", "ag_dev_1")
    ag2 = AgentTemplateCatalog.instantiate("code_reviewer", "ag_cr_1")
    ag3 = AgentTemplateCatalog.instantiate("researcher", "ag_res_1")

    reg.register(ag1)
    reg.register(ag2)
    reg.register(ag3)

    # Listar por rol
    devs = reg.list_all(role="Developer")
    assert len(devs) == 1
    assert devs[0].agent_id == "ag_dev_1"

    # Desactivar
    reg.deactivate("ag_dev_1")
    assert reg.get("ag_dev_1").status == AgentStatus.INACTIVE

    # Listar por status
    active_agents = reg.list_all(status=AgentStatus.ACTIVE)
    assert len(active_agents) == 2
    inactive_agents = reg.list_all(status=AgentStatus.INACTIVE)
    assert len(inactive_agents) == 1

    # Reactivar
    reg.activate("ag_dev_1")
    assert reg.get("ag_dev_1").status == AgentStatus.ACTIVE

    # Duplicar
    dup = reg.duplicate("ag_dev_1", new_agent_id="ag_dev_2", new_name="Developer 2")
    assert dup.agent_id == "ag_dev_2"
    assert dup.name == "Developer 2"
    assert reg.get("ag_dev_2") is not None


def test_registry_json_and_yaml_portability(clean_registry, tmp_path):
    """Valida la serialización y portabilidad en JSON y YAML."""
    reg = clean_registry
    original = AgentTemplateCatalog.instantiate("security_auditor", "ag_sec_export")
    reg.register(original)

    # Export / Import JSON
    json_str = reg.export_to_json("ag_sec_export")
    assert "ag_sec_export" in json_str
    assert "Security Auditor" in json_str

    reg_target = AgentRegistry()
    imported_json = reg_target.import_from_json(json_str)
    assert imported_json.agent_id == original.agent_id
    assert imported_json.definition_hash == original.definition_hash

    # Export / Import YAML
    yaml_str = reg.export_to_yaml("ag_sec_export")
    assert "role: Security Auditor" in yaml_str

    imported_yaml = reg_target.import_from_yaml(yaml_str.replace("ag_sec_export", "ag_sec_yaml"))
    assert imported_yaml.agent_id == "ag_sec_yaml"
    assert imported_yaml.role == "Security Auditor"

    # Bundle export/import
    bundle_file = str(tmp_path / "agents_bundle.json")
    reg_target.export_bundle(bundle_file)

    fresh_reg = AgentRegistry()
    count = fresh_reg.import_bundle(bundle_file)
    assert count == 2
    assert fresh_reg.get("ag_sec_export") is not None
    assert fresh_reg.get("ag_sec_yaml") is not None
