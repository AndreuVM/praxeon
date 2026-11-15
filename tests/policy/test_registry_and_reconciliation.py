"""Registro de herramientas y reconciliación de riesgo (``policy/registry.py`` y ``policy/reconciliation.py``; AUD-26, Q-03).

La lista blanca de comandos de solo lectura decide qué se trata como observacional: un falso positivo trataría como inocuo algo
destructivo, así que se prueba con los intentos de evasión habituales (encadenar, redirigir, flags de borrado).
"""

import shutil

import pytest

from praxeon.domain.assessment import CommandCategory, CommandRiskAssessment, RiskAssessment
from praxeon.domain.models import RiskLevel
from praxeon.policy.reconciliation import RiskReconciler
from praxeon.policy.registry import ToolRegistry, ToolSpec


@pytest.fixture
def registry():
    return ToolRegistry(register_defaults=True)


# --- herramientas CLI dinámicas ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["", None, 42])
def test_an_empty_or_non_text_name_is_not_a_tool(registry, name):
    assert registry._is_dynamic_cli_tool(name) is False
    assert registry.is_known(name) is False
    assert registry.get_tool(name) is None


@pytest.mark.parametrize("name", ["exploit_kit", "unregistered_tool", "MALICIOUS", "reverse_shell", "act_unknown_1"])
def test_names_that_look_like_attacks_are_never_admitted(registry, name):
    assert registry.is_known(name) is False


@pytest.mark.parametrize("name", ["git", "PYTEST", " ls "])
def test_known_executables_are_admitted_and_cached(registry, name):
    assert registry.is_known(name) is True
    assert registry.get_tool(name) is registry.get_tool(name)


def test_an_unlisted_executable_is_admitted_only_if_it_exists_on_the_path(registry, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/mi-herramienta")
    assert registry.is_known("mi-herramienta") is True
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert registry.is_known("otra-herramienta") is False


@pytest.mark.parametrize("name", ["rm -rf", "a;b", "../x", "x y"])
def test_a_name_with_shell_characters_is_never_an_executable(registry, monkeypatch, name):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/x")
    assert registry.is_known(name) is False


def test_dynamic_specs_distinguish_reads_from_everything_else(registry):
    reader, builder = registry.get_tool("ls"), registry.get_tool("make")
    assert reader.read_only and reader.risk_level == RiskLevel.LOW and not reader.external_side_effect
    assert not builder.read_only and builder.risk_level == RiskLevel.MEDIUM and builder.external_side_effect


def test_tools_can_be_registered_aliased_and_removed(registry):
    spec = ToolSpec(name="mcp__x__y", category="network", source="mcp:x")
    registry.register(spec)
    assert registry.get_tool("mcp__x__y") is spec and registry.is_known("mcp__x__y")
    registry.unregister_tool("mcp__x__y")
    registry.unregister_tool("mcp__x__y")                                   # quitarla otra vez no falla
    assert registry.is_known("mcp__x__y") is False


# --- lista blanca de comandos de solo lectura ---------------------------------------------------------------------------
@pytest.mark.parametrize("command", [
    "ls", "ls -la src", "cat README.md", "head -n 5 f.txt", "echo hola", "grep -r foo .", "pwd",
    "git status", "git log --oneline", "git diff", "git show HEAD", "git branch", "git tag",
    "python --version", "python3 -V",
])
def test_read_only_commands_are_recognized(registry, command):
    assert registry._is_read_only_command(command) is True


@pytest.mark.parametrize("command", [
    "", "   ",
    "ls; rm -rf x", "cat a > b", "cat a | sh", "echo $(whoami)", "echo `id`", "ls && rm x", "cat a\nrm b",
    "git", "git push", "git commit -m x", "git checkout main",
    "git branch nueva", "git branch -D vieja", "git branch --delete x", "git tag v1", "git diff --force",
    "find . -delete", "find . -exec rm {} +", "grep -f patrones f",
    "python script.py", "python -c 'print(1)'", "rm -rf /", "desconocido",
    "echo 'sin cerrar",                                                     # comillas sin cerrar: shlex falla -> no es solo lectura
])
def test_anything_else_is_not_read_only(registry, command):
    assert registry._is_read_only_command(command) is False


def test_is_observational_combines_fixed_tools_commands_and_specs(registry):
    assert registry.is_observational("read_file") and registry.is_observational("web_fetch")
    assert registry.is_observational("run_command", {"command": "git status"}) is True
    assert registry.is_observational("run_command", {"command": "git push"}) is False
    assert registry.is_observational("run_command", {"command": "ls; rm x"}) is False
    assert registry.is_observational("run_command") is False                  # sin argumentos
    assert registry.is_observational("herramienta_desconocida") is False


def test_a_read_only_spec_with_external_side_effects_is_not_observational(registry):
    registry.register(ToolSpec(name="lector", category="inspection", read_only=True))
    registry.register(ToolSpec(name="lector_red", category="network", read_only=True, external_side_effect=True))
    assert registry.is_observational("lector") is True
    assert registry.is_observational("lector_red") is False


def test_assess_risk_for_cognitive_unknown_and_registered_tools(registry):
    assert registry.assess_risk(None).level == RiskLevel.LOW
    unknown = registry.assess_risk("herramienta_inventada")
    assert unknown.level == RiskLevel.HIGH and unknown.requires_confirmation
    registry.register(ToolSpec(name="borrar", category="mutation", risk_level=RiskLevel.HIGH, destructive=True,
                               external_side_effect=True))
    risky = registry.assess_risk("borrar")
    assert risky.requires_confirmation and risky.destructive_potential and not risky.executable
    assert len(risky.reasons) == 3                                          # categoría, potencial destructivo y efectos externos


# --- reconciliación: precedencia de reglas -----------------------------------------------------------------------------
def op(category, level=RiskLevel.LOW, **kw):
    return CommandRiskAssessment(operation="op", category=category, risk_level=level, **kw)


def reconcile(operation=None, tool="herramienta", base_level=RiskLevel.MEDIUM, mode="local_restricted", **kw):
    base = RiskAssessment(level=base_level, requires_confirmation=True, reasons=["base"])
    return RiskReconciler().reconcile(base, operation, tool_name=tool, execution_mode=mode, **kw)


def test_a_purely_cognitive_step_is_low_risk_and_executable():
    result = reconcile(tool=None)
    assert result.level == RiskLevel.LOW and result.executable and not result.requires_confirmation


@pytest.mark.parametrize("operation", [
    op(CommandCategory.PRIVILEGE), op(CommandCategory.LOCAL_MUTATION, privilege_escalation=True),
    op(CommandCategory.DESTRUCTIVE), op(CommandCategory.LOCAL_MUTATION, destructive=True),
    op(CommandCategory.LOCAL_MUTATION, level=RiskLevel.CRITICAL, reasons=["lo dice el clasificador"]),
])
def test_a_critical_operation_is_vetoed_whatever_the_base_risk(operation):
    result = reconcile(operation, base_level=RiskLevel.LOW, mode="full_access")
    assert result.level == RiskLevel.CRITICAL and not result.executable and result.requires_confirmation


def test_the_reasons_of_a_critical_operation_come_from_the_classifier_or_a_default():
    assert reconcile(op(CommandCategory.LOCAL_MUTATION, RiskLevel.CRITICAL, reasons=["motivo"])).reasons == ["motivo"]
    assert "crítico" in reconcile(op(CommandCategory.LOCAL_MUTATION, RiskLevel.CRITICAL)).reasons[0]


def test_an_intrinsically_destructive_tool_is_critical():
    spec = ToolSpec(name="borrar_todo", category="mutation", destructive=True)
    result = reconcile(tool="borrar_todo", tool_spec=spec, base_level=RiskLevel.LOW)
    assert result.level == RiskLevel.CRITICAL and not result.executable and "destructiva" in result.reasons[0]


@pytest.mark.parametrize("category", [CommandCategory.INSPECTION, CommandCategory.BUILD_TEST])
def test_safe_operations_are_refined_down_to_low_risk(category):
    result = reconcile(op(category, RiskLevel.MEDIUM))
    assert result.level == RiskLevel.LOW and result.executable and not result.requires_confirmation


@pytest.mark.parametrize("category", [CommandCategory.NETWORK, CommandCategory.REMOTE_MUTATION])
def test_network_and_remote_operations_are_high_risk_with_confirmation(category):
    result = reconcile(op(category))
    assert result.level == RiskLevel.HIGH and result.requires_confirmation and result.executable


def test_a_local_mutation_keeps_the_base_confirmation_requirement():
    assert reconcile(op(CommandCategory.LOCAL_MUTATION)).level == RiskLevel.MEDIUM
    base = RiskAssessment(level=RiskLevel.MEDIUM, requires_confirmation=False)
    assert RiskReconciler().reconcile(base, op(CommandCategory.LOCAL_MUTATION), tool_name="x").requires_confirmation is False


def test_registered_observational_and_standard_inspection_tools_are_low_risk():
    spec = ToolSpec(name="lector", category="inspection", read_only=True)
    assert reconcile(tool="lector", tool_spec=spec).level == RiskLevel.LOW
    assert "observacional" in reconcile(tool="lector", tool_spec=spec).reasons[0]
    standard = reconcile(tool="read_file")                                  # sin especificación registrada
    assert standard.level == RiskLevel.LOW and "inspección" in standard.reasons[0]


def test_a_read_only_tool_with_external_side_effects_is_not_treated_as_observational():
    spec = ToolSpec(name="lector_red", category="network", read_only=True, external_side_effect=True)
    assert reconcile(tool="lector_red", tool_spec=spec).level == RiskLevel.MEDIUM          # cae al riesgo base


def test_a_generic_local_mutation_tool_takes_the_level_declared_in_its_spec():
    spec = ToolSpec(name="editar", category="mutation", risk_level=RiskLevel.HIGH, requires_confirmation=True)
    result = reconcile(tool="editar", tool_spec=spec, base_level=RiskLevel.LOW)
    assert result.level == RiskLevel.HIGH and result.requires_confirmation and "mutación local" in result.reasons[0]


def test_an_unclassified_operation_falls_through_to_the_base_risk():
    base = RiskAssessment(level=RiskLevel.MEDIUM, requires_confirmation=True, reasons=["base"])
    result = RiskReconciler().reconcile(base, op(CommandCategory.UNKNOWN), tool_name="algo")
    assert result is base
