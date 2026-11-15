"""Full Access nunca relaja un riesgo crítico (``policy/reconciliation.py``; AUD-23).

mypy --strict demostró que la comprobación ``base_risk.level != CRITICAL`` del ajuste de Full Access era código muerto: el
nivel crítico ya se resuelve antes y no llega ahí. Estos tests fijan el comportamiento real, para poder quitar esa
comprobación redundante sin que cambie nada.
"""

import pytest

from praxeon.domain.assessment import RiskAssessment
from praxeon.domain.models import RiskLevel
from praxeon.policy.reconciliation import RiskReconciler


def base(level, **kw):
    kw.setdefault("requires_confirmation", True)
    return RiskAssessment(level=level, reasons=["base"], **kw)


def reconcile(risk, mode="full_access", tool="herramienta_personalizada", **kw):
    return RiskReconciler().reconcile(risk, operation_assessment=None, tool_name=tool, execution_mode=mode, **kw)


@pytest.mark.parametrize("mode", ["full_access", "FULL_ACCESS", "local_restricted"])
def test_a_critical_base_risk_is_never_relaxed_by_any_mode(mode):
    result = reconcile(base(RiskLevel.CRITICAL), mode=mode)
    assert result.level == RiskLevel.CRITICAL and result.requires_confirmation and not result.executable


def test_destructive_potential_is_critical_even_if_the_level_is_lower():
    result = reconcile(base(RiskLevel.MEDIUM, destructive_potential=True))
    assert result.level == RiskLevel.CRITICAL and result.requires_confirmation and not result.executable


def test_a_forbidden_tool_stays_forbidden_in_full_access():
    result = reconcile(base(RiskLevel.LOW), tool="rm_rf", forbidden_tools={"rm_rf"})
    assert result.level == RiskLevel.CRITICAL and not result.executable


@pytest.mark.parametrize("level", [RiskLevel.MEDIUM, RiskLevel.HIGH])
def test_full_access_only_drops_the_interactive_confirmation_of_non_critical_risk(level):
    result = reconcile(base(level))
    assert result.level == level and result.requires_confirmation is False and result.executable
    assert result.reasons[0] == "base" and "Full Access" in result.reasons[-1]


def test_outside_full_access_the_base_risk_is_returned_unchanged():
    risk = base(RiskLevel.HIGH)
    assert reconcile(risk, mode="local_restricted") is risk
