"""Aprobación humana y permisos de herramienta (``policy/permissions.py``; AUD-23, Q-03).

Un ticket de aprobación es lo único que convierte una acción que exige confirmación en una permitida: se comprueba que
caduca, que está ligado al hash de la acción, que sobrevive al reinicio y que un registro corrupto no tumba el arranque.
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.assessment import RiskAssessment
from praxeon.domain.decision import DecisionStatus
from praxeon.domain.models import RiskLevel
from praxeon.policy.permissions import HumanApprovalTicket, PermissionManager

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def ticket(**kw):
    return HumanApprovalTicket(action_id="a1", action_hash="h1", **kw)


# --- caducidad del ticket -----------------------------------------------------------------------------------------------
def test_a_ticket_without_expiry_never_expires():
    assert ticket().is_expired() is False


@pytest.mark.parametrize("offset, expired", [(timedelta(minutes=5), False), (-timedelta(minutes=5), True)])
def test_a_ticket_expires_at_its_deadline(offset, expired):
    assert ticket(expires_at=NOW + offset).is_expired(now=NOW) is expired


def test_a_naive_expiry_is_read_as_utc_against_an_aware_clock():
    naive_future = (NOW + timedelta(minutes=5)).replace(tzinfo=None)
    naive_past = (NOW - timedelta(minutes=5)).replace(tzinfo=None)
    assert ticket(expires_at=naive_future).is_expired(now=NOW) is False
    assert ticket(expires_at=naive_past).is_expired(now=NOW) is True


def test_an_aware_expiry_is_compared_against_a_naive_clock_as_utc():
    naive_now = NOW.replace(tzinfo=None)
    assert ticket(expires_at=NOW + timedelta(minutes=5)).is_expired(now=naive_now) is False
    assert ticket(expires_at=NOW - timedelta(minutes=5)).is_expired(now=naive_now) is True


# --- confirmación de acciones -------------------------------------------------------------------------------------------
def test_confirming_an_action_issues_an_auditable_ticket():
    manager = PermissionManager()
    issued = manager.confirm_action("a1", action_hash="h1", approver_id="ana", reason="revisado")
    assert manager.get_approval_ticket("a1") is issued
    assert (issued.approver_id, issued.reason, issued.action_hash) == ("ana", "revisado", "h1")
    assert issued.expires_at is not None
    assert manager.is_action_confirmed("a1") is True
    assert manager.get_approval_ticket("otra") is None


def test_a_confirmation_is_bound_to_the_hash_of_the_action():
    manager = PermissionManager()
    manager.confirm_action("a1", action_hash="h1")
    assert manager.is_action_confirmed("a1", action_hash="h1") is True
    assert manager.is_action_confirmed("a1", action_hash="distinto") is False        # acción manipulada tras aprobarla


def test_a_confirmation_without_hash_covers_any_version_of_the_action():
    manager = PermissionManager()
    assert manager.confirm_action("a1").action_hash == "*"
    assert manager.is_action_confirmed("a1", action_hash="cualquiera") is True


def test_an_expired_confirmation_no_longer_counts():
    manager = PermissionManager()
    manager.grant_approval(ticket(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert manager.is_action_confirmed("a1") is False


def test_a_confirmation_without_ttl_does_not_expire():
    assert PermissionManager().confirm_action("a1", ttl_seconds=None).expires_at is None


def test_an_action_marked_confirmed_without_ticket_still_counts():
    manager = PermissionManager()
    manager._confirmed_actions.add("heredada")
    assert manager.is_action_confirmed("heredada") is True and manager.is_action_confirmed("nueva") is False


# --- registro de auditoría ----------------------------------------------------------------------------------------------
def test_approvals_survive_a_restart_through_the_audit_log(tmp_path):
    log = str(tmp_path / "audit" / "approvals.jsonl")                 # el directorio se crea al escribir
    PermissionManager(audit_log_path=log).confirm_action("a1", action_hash="h1", approver_id="ana")
    restarted = PermissionManager(audit_log_path=log)
    assert restarted.is_action_confirmed("a1", action_hash="h1") is True
    assert restarted.get_approval_ticket("a1").approver_id == "ana"


def test_expired_approvals_are_not_restored_from_the_audit_log(tmp_path):
    log = tmp_path / "approvals.jsonl"
    log.write_text(ticket(expires_at=datetime.now(UTC) - timedelta(minutes=1)).model_dump_json() + "\n\n", encoding="utf-8")
    assert PermissionManager(audit_log_path=str(log)).is_action_confirmed("a1") is False


def test_a_corrupt_audit_log_is_reported_and_does_not_stop_the_start(tmp_path, caplog):
    log = tmp_path / "approvals.jsonl"
    log.write_text("esto no es json\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        manager = PermissionManager(audit_log_path=str(log))
    assert "No se pudieron cargar los tickets" in caplog.text
    assert manager.is_action_confirmed("a1") is False


# --- decisión de permisos ----------------------------------------------------------------------------------------------
def action(tool="read_file", action_id="a1"):
    return ActionCandidate(id=action_id, description="d", tool_call=ToolCall(tool_name=tool, arguments={}))


def risk(**kw):
    return RiskAssessment(level=RiskLevel.LOW, **kw)


def test_a_forbidden_tool_is_blocked():
    decision = PermissionManager().check_permissions(action("read_file"), risk(), forbidden_tools=["read_file"])
    assert decision.status == DecisionStatus.BLOCK and decision.reason_codes == ["FORBIDDEN_TOOL_RESTRAINT"]


def test_an_unregistered_tool_is_blocked():
    decision = PermissionManager().check_permissions(action("herramienta_inventada"), risk())
    assert decision.status == DecisionStatus.BLOCK and decision.reason_codes == ["UNKNOWN_TOOL_UNAUTHORIZED"]


def test_an_action_that_needs_confirmation_abstains_until_it_is_confirmed():
    manager = PermissionManager()
    needs = risk(requires_confirmation=True)
    first = manager.check_permissions(action(), needs)
    assert first.status == DecisionStatus.ABSTAIN and first.requires_confirmation and first.reason_codes == ["CONFIRMATION_REQUIRED"]
    manager.confirm_action("a1")
    assert manager.check_permissions(action(), needs).status == DecisionStatus.ALLOW


def test_a_cleared_action_is_allowed():
    decision = PermissionManager().check_permissions(action(), risk())
    assert decision.status == DecisionStatus.ALLOW and decision.reason_codes == ["PERMISSIONS_CLEARED"]


def test_an_action_without_a_tool_skips_the_tool_checks():
    cognitive = ActionCandidate(id="c1", description="pensar")
    assert PermissionManager().check_permissions(cognitive, risk(), forbidden_tools=["read_file"]).status == DecisionStatus.ALLOW
