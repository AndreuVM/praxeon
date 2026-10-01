"""Pruebas de Gobernanza, Auditoría y Restricción de Modo FULL_ACCESS (P0.2).

Conforme a la Auditoría Técnica Sección 5 y Sección 7:
1. Invariante de Bloqueo Incondicional: Policy BLOCK o CRITICAL Risk NUNCA se transforman en ALLOW en Full Access.
2. Desacoplamiento de Privilegios y Autonomía:
   - FULL_ACCESS interactivo retiene obligatoriamente status='REVIEW' y requires_confirmation=True.
   - FULL_ACCESS autónomo requiere bandera explícita ('autonomous' o 'allow_unattended_execution').
3. Endurecimiento de Secretos: RuntimeApplicationService en producción falla si la clave secreta es insegura o vacía.
"""

import os
import pytest

from praxeon.domain.decision import DecisionStatus, ExecutionMode
from praxeon.domain.models import ActionCandidate, Goal, RiskLevel, ToolCall
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service
from praxeon.server.schemas.action import ProposeActionRequest


@pytest.fixture
def clean_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("PRAXEON_PROFILE", raising=False)
    monkeypatch.delenv("PRAXEON_ENV", raising=False)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "fa_gov_cache"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


def test_full_access_never_overrides_policy_block(clean_runtime):
    """P0.2: Garantizar que una decisión de Policy BLOCK nunca se transforme en ALLOW en Full Access."""
    # Crear sesión en modo full_access con autonomía activa
    session = clean_runtime.create_session(
        goal="Operaciones de alto riesgo",
        session_id="fa_block_test",
        execution_mode="full_access",
        metadata={"autonomous": True, "allow_unattended_execution": True},
    )

    # Simular una acción altamente destructiva que el clasificador/política veta
    proposal = ProposeActionRequest(
        tool="run_command",
        operation="rm -rf /",
        arguments={"command": "rm -rf /"},
        thought_rationale="Intento de borrar la raíz del sistema",
    )

    resp = clean_runtime.propose_action(session_id="fa_block_test", proposal=proposal)

    # Invariante de seguridad: Debe ser BLOCK estricto
    assert resp.status == "BLOCK", f"Se esperaba BLOCK pero se obtuvo: {resp.status}"
    assert resp.policy.decision == "BLOCK"
    assert resp.policy.requires_confirmation is False


def test_full_access_interactive_mode_retains_human_confirmation(clean_runtime):
    """P0.2: FULL_ACCESS estándar retiene obligatoriamente la confirmación interactiva humana."""
    # Crear sesión en full_access SIN bandera de autonomía
    session = clean_runtime.create_session(
        goal="Mantenimiento en host con supervisión humana",
        session_id="fa_interactive_test",
        execution_mode="full_access",
        metadata={"autonomous": False, "allow_unattended_execution": False},
    )

    # Acción de modificación en host (ej. git push o comando de alto impacto que exige confirmación)
    proposal = ProposeActionRequest(
        tool="git",
        operation="push origin main",
        arguments={"command": "push origin main"},
        thought_rationale="Empujando cambios al repositorio principal",
    )

    resp = clean_runtime.propose_action(session_id="fa_interactive_test", proposal=proposal)

    # Debe exigir confirmación humana (REVIEW), no auto-conmutar a ALLOW
    assert resp.status == "REVIEW", f"Se esperaba REVIEW para full_access interactivo pero fue: {resp.status}"
    assert resp.policy.requires_confirmation is True
    assert resp.policy.decision == "REQUIRE_HUMAN_CONFIRMATION"


def test_full_access_autonomous_mode_allows_unattended_when_explicit(clean_runtime, caplog):
    """P0.2: FULL_ACCESS + AUTONOMOUS permite ejecución desatendida sólo ante autorización previa explícita."""
    session = clean_runtime.create_session(
        goal="Automatización de pruebas en host",
        session_id="fa_autonomous_test",
        execution_mode="full_access",
        metadata={"autonomous": True, "allow_unattended_execution": True},
    )

    proposal = ProposeActionRequest(
        tool="git",
        operation="status",
        arguments={"command": "status"},
        thought_rationale="Consultando estado del repositorio",
    )

    resp = clean_runtime.propose_action(session_id="fa_autonomous_test", proposal=proposal)

    assert resp.status == "ALLOW"
    assert resp.policy.requires_confirmation is False


def test_production_profile_fails_fast_on_insecure_secret(tmp_path, monkeypatch):
    """P0.2 / Sección 7: RuntimeApplicationService en producción falla si la clave es insegura o fallback."""
    monkeypatch.setenv("PRAXEON_PROFILE", "production")
    monkeypatch.setenv("PRAXEON_SECRET_KEY", "praxeon_secret_hmac_key_v1")  # clave por defecto insegura

    with pytest.raises(ValueError) as exc_info:
        RuntimeApplicationService(db_dir=str(tmp_path / "prod_insecure_cache"))
    assert "no use claves por defecto" in str(exc_info.value)
