"""Pruebas unitarias para la capa de política y ReplayProvider de v0.2."""

import pytest
from praxeon.domain import (
    ActionCandidate,
    DecisionStatus,
    Evidence,
    ProviderAssessment,
    RiskLevel,
    ToolCall,
)
from praxeon.policy import FailSafePolicy, PolicyEngine, ToolRegistry, ToolSpec
from praxeon.providers import ReplayProvider


def test_policy_allows_safe_and_grounded_action():
    """Verifica que una acción de bajo riesgo, fundamentada y sin bucle sea aprobada (ALLOW)."""
    engine = PolicyEngine()
    provider = ReplayProvider(default_scenario="safe_read")

    action = ActionCandidate(
        id="act_1",
        description="Leer archivo de configuración",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "pyproject.toml"}),
    )

    assessment = provider.evaluate(state={}, actions=[action])[0]
    decision, receipt = engine.evaluate_action(
        action=action,
        state={"turn": 1},
        provider_assessment=assessment,
    )

    assert decision.status == DecisionStatus.ALLOW
    assert "GROUNDED_LOW_RISK_AUTHORIZED" in decision.reason_codes
    assert receipt.decision == DecisionStatus.ALLOW
    assert receipt.action_id == "act_1"
    assert receipt.latency_ms >= 0


def test_policy_replans_on_missing_required_evidence():
    """Verifica que una acción que requiere evidencia ausente sea rechazada con REPLAN."""
    engine = PolicyEngine()
    action = ActionCandidate(
        id="act_2",
        description="Editar función en parser.py",
        tool_call=ToolCall(tool_name="edit_file", arguments={"path": "parser.py"}),
        requires_evidence=["file_exists", "symbol_ast_located"],
    )

    # Solo tenemos evidencia de que el archivo existe, falta 'symbol_ast_located'
    available_evidence = [
        Evidence(
            id="ev_1",
            claim="file_exists",
            content_hash="abc123hash",
        )
    ]

    decision, receipt = engine.evaluate_action(
        action=action,
        state={},
        available_evidence=available_evidence,
    )

    assert decision.status == DecisionStatus.REPLAN
    assert any("MISSING_REQUIRED_EVIDENCE" in code for code in decision.reason_codes)
    assert any("symbol_ast_located" in code for code in decision.reason_codes)
    assert receipt.decision == DecisionStatus.REPLAN


def test_policy_replans_on_high_loop_probability():
    """Verifica que un intento repetitivo diagnosticado con alto loop por JEV sea REPLAN."""
    engine = PolicyEngine(loop_threshold=0.65)
    provider = ReplayProvider(default_scenario="loop_detected")

    action = ActionCandidate(
        id="act_3",
        description="Reintentar comando fallido",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "flake8"}),
    )

    assessment = provider.evaluate(state={}, actions=[action])[0]
    decision, receipt = engine.evaluate_action(
        action=action,
        state={},
        provider_assessment=assessment,
    )

    assert decision.status == DecisionStatus.REPLAN
    assert any("HIGH_LOOP_PROBABILITY" in code for code in decision.reason_codes)


def test_policy_blocks_forbidden_tool():
    """Verifica que el supervisor bloquee físicamente herramientas marcadas en forbidden_tools."""
    engine = PolicyEngine()
    action = ActionCandidate(
        id="act_4",
        description="Borrar archivo temporal",
        tool_call=ToolCall(tool_name="delete_file", arguments={"path": "temp.txt"}),
    )

    decision, receipt = engine.evaluate_action(
        action=action,
        state={},
        forbidden_tools={"delete_file"},
    )

    assert decision.status == DecisionStatus.BLOCK
    assert "TOOL_FORBIDDEN_BY_SUPERVISOR" in decision.reason_codes


def test_policy_blocks_unregistered_unknown_tool():
    """CHG-01: Verifica que invocar una herramienta no registrada inocua emita ABSTAIN (requiriendo revisión)
    en vez de BLOCK ciego, y que una destructiva sí sea bloqueada (BLOCK)."""
    engine = PolicyEngine()
    action = ActionCandidate(
        id="act_5",
        description="Invocar plugin no autorizado inocuo",
        tool_call=ToolCall(tool_name="unknown_arbitrary_shell", arguments={}),
    )

    decision, receipt = engine.evaluate_action(
        action=action,
        state={},
    )

    # Inocuo desconocido -> ABSTAIN con confirmación requerida
    assert decision.status == DecisionStatus.ABSTAIN
    assert decision.requires_confirmation is True
    assert "UNKNOWN_TOOL_NOT_REGISTERED" in decision.reason_codes

    # Desconocido con patrón destructivo -> BLOCK incondicional
    action_destructive = ActionCandidate(
        id="act_5_dest",
        description="Invocar comando destructivo no registrado",
        tool_call=ToolCall(tool_name="unknown_tool", arguments={"command": "rm -rf / --no-preserve-root"}),
    )
    dec_dest, _ = engine.evaluate_action(action=action_destructive, state={})
    assert dec_dest.status == DecisionStatus.BLOCK
    assert "DESTRUCTIVE_COMMAND_BLOCK" in dec_dest.reason_codes



def test_policy_failsafe_blocks_destructive_tool_on_provider_down():
    """Verifica que ante la caída del proveedor JEV, una acción destructiva sea bloqueada (BLOCK)."""
    engine = PolicyEngine(failsafe=FailSafePolicy(block_destructive_on_provider_failure=True))
    provider = ReplayProvider(default_scenario="provider_down")

    action = ActionCandidate(
        id="act_6",
        description="Eliminar directorio de base de datos",
        tool_call=ToolCall(tool_name="delete_file", arguments={"path": "/data/db"}),
    )

    assessment = provider.evaluate(state={}, actions=[action])[0]
    assert assessment.available is False

    decision, receipt = engine.evaluate_action(
        action=action,
        state={},
        provider_assessment=assessment,
    )

    assert decision.status == DecisionStatus.BLOCK
    assert "PROVIDER_UNAVAILABLE_DESTRUCTIVE_BLOCK" in decision.reason_codes


def test_policy_failsafe_abstains_on_low_risk_tool_on_provider_down():
    """Verifica que ante la caída del proveedor JEV, una acción de bajo riesgo resulte en ABSTAIN."""
    engine = PolicyEngine(failsafe=FailSafePolicy(allow_read_only_on_provider_failure=False))
    provider = ReplayProvider(default_scenario="provider_down")

    action = ActionCandidate(
        id="act_7",
        description="Leer archivo de configuración",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "README.md"}),
    )

    assessment = provider.evaluate(state={}, actions=[action])[0]
    decision, receipt = engine.evaluate_action(
        action=action,
        state={},
        provider_assessment=assessment,
    )

    # Por defecto, no asume neutralidad: se abstiene
    assert decision.status == DecisionStatus.ABSTAIN
    assert "PROVIDER_UNAVAILABLE_FAILSAFE_ABSTAIN" in decision.reason_codes


def test_policy_failsafe_allows_read_only_when_configured():
    """Verifica que si la política permite explícitamente lectura en caída, resulte en ALLOW."""
    engine = PolicyEngine(failsafe=FailSafePolicy(allow_read_only_on_provider_failure=True))
    provider = ReplayProvider(default_scenario="provider_down")

    action = ActionCandidate(
        id="act_8",
        description="Leer archivo de configuración en modo offline",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "README.md"}),
    )

    assessment = provider.evaluate(state={}, actions=[action])[0]
    decision, receipt = engine.evaluate_action(
        action=action,
        state={},
        provider_assessment=assessment,
    )

    assert decision.status == DecisionStatus.ALLOW


def test_replay_provider_scenario_queueing_and_action_override():
    """Verifica el funcionamiento de la cola de escenarios y sobreescrituras en ReplayProvider."""
    provider = ReplayProvider()
    provider.queue_scenario("missing_evidence")
    provider.queue_scenario("loop_detected")

    actions = [
        ActionCandidate(id="a1", description="Acción 1"),
        ActionCandidate(id="a2", description="Acción 2"),
        ActionCandidate(id="a3", description="Acción 3"),
    ]

    # a3 tendrá una sobreescritura explícita
    provider.override_for_action(
        "a3",
        ProviderAssessment(
            provider="replay",
            available=True,
            confidence=0.99,
            reason_codes=["MANUAL_OVERRIDE"],
        ),
    )

    assessments = provider.evaluate(state={}, actions=actions)

    assert len(assessments) == 3
    # Primer elemento consumió 'missing_evidence' de la cola
    assert "UNGROUNDED_PREMISE" in assessments[0].reason_codes
    # Segundo elemento consumió 'loop_detected' de la cola
    assert "LOOP_REPETITION" in assessments[1].reason_codes
    # Tercer elemento tomó el override de acción
    assert "MANUAL_OVERRIDE" in assessments[2].reason_codes
