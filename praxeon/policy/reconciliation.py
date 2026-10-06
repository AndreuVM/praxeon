"""Módulo de reconciliación determinista de riesgo (praxeon/policy/reconciliation.py).

Especificación de Auditoría P0:
Effective Risk = f(Tool-level risk, Operation-level classification, Evidence, Policy, Environment)

Desacopla formalmente la identidad genérica de la herramienta (ej. run_command) de la
semántica real de la operación (ej. git status, pytest, read_file).
Garantiza que la presencia de una RiskAssessment previa no bloquee la refinación hacia LOW
en operaciones de inspección o build/test, ni permita relajar barreras críticas (BLOCK).
"""

import re
from typing import Any, Dict, List, Optional, Set

from praxeon.domain.assessment import (
    CommandCategory,
    CommandRiskAssessment,
    RiskLevel,
)
from praxeon.domain.models import (
    ActionCandidate,
    Evidence,
    RiskAssessment,
)
from praxeon.policy.registry import ToolRegistry, ToolSpec


class RiskReconciler:
    """Fase explícita de reconciliación de riesgo (RiskReconciliation)."""

    PROTECTED_FILE_PATTERNS = [
        r"\.env\b",
        r"\.git[\\/]",
        r"id_rsa\b",
        r"credentials\b",
        r"token\b",
        r"secret\b",
        r"passwd\b",
        r"shadow\b",
        r"c:[\\/]windows",
        r"/etc/",
    ]

    def __init__(self, registry: Optional[ToolRegistry] = None):
        self.registry = registry or ToolRegistry(register_defaults=True)

    def reconcile(
        self,
        base_risk: RiskAssessment,
        operation_assessment: Optional[CommandRiskAssessment],
        tool_name: Optional[str] = None,
        tool_spec: Optional[ToolSpec] = None,
        action: Optional[ActionCandidate] = None,
        available_evidence: Optional[List[Evidence]] = None,
        execution_mode: Optional[str] = None,
        forbidden_tools: Optional[Set[str]] = None,
    ) -> RiskAssessment:
        """Reconcilia de forma determinista el riesgo base con la semántica concreta y el entorno.
        
        Precedencia estricta:
        1. Veto incondicional (forbidden tools, privilege escalation, destructive, adversarial evasion).
        2. Acceso a recursos protegidos/sensibles (HIGH o CRITICAL según verbo).
        3. Operaciones de inspección / build-test seguras (refinamiento determinista a LOW/ALLOW).
        4. Mutaciones locales (MEDIUM, requiere confirmación interactiva salvo en full_access).
        5. Efecto del modo de ejecución (full_access no puede saltarse CRITICAL).
        """
        forbidden_tools = forbidden_tools or set()
        mode_str = str(execution_mode.value if hasattr(execution_mode, "value") else execution_mode or "").lower()

        # 0. Pasos puramente cognitivos (sin herramienta)
        if not tool_name:
            return RiskAssessment(
                level=RiskLevel.LOW,
                requires_confirmation=False,
                executable=True,
                destructive_potential=False,
                reasons=["Paso puramente cognitivo (sin ejecución de herramientas)."],
            )

        # 1. Herramientas prohibidas explícitamente en el estado actual
        if tool_name in forbidden_tools:
            return RiskAssessment(
                level=RiskLevel.CRITICAL,
                requires_confirmation=False,
                executable=False,
                destructive_potential=False,
                reasons=[f"Herramienta '{tool_name}' prohibida explícitamente por el supervisor en este estado."],
            )

        # 2. Veto incondicional por barreras deterministas (PRIVILEGE, DESTRUCTIVE, ADVERSARIAL_EVASION)
        if operation_assessment is not None:
            if operation_assessment.category == CommandCategory.PRIVILEGE or operation_assessment.privilege_escalation:
                return RiskAssessment(
                    level=RiskLevel.CRITICAL,
                    requires_confirmation=True,
                    executable=False,
                    destructive_potential=False,
                    reasons=[f"Operación con escalada de privilegios detectada ({operation_assessment.category.value})."],
                )
            if operation_assessment.category == CommandCategory.DESTRUCTIVE or operation_assessment.destructive:
                return RiskAssessment(
                    level=RiskLevel.CRITICAL,
                    requires_confirmation=True,
                    executable=False,
                    destructive_potential=True,
                    reasons=[f"Operación con potencial destructivo crítico ({operation_assessment.category.value})."],
                )
            if operation_assessment.risk_level == RiskLevel.CRITICAL:
                return RiskAssessment(
                    level=RiskLevel.CRITICAL,
                    requires_confirmation=True,
                    executable=False,
                    destructive_potential=operation_assessment.destructive,
                    reasons=list(operation_assessment.reasons) or ["Riesgo operacional crítico determinista detectado en la operación."],
                )

        if base_risk.level == RiskLevel.CRITICAL or base_risk.destructive_potential:
            # Nunca se degrada un riesgo crítico preexistente
            return RiskAssessment(
                level=RiskLevel.CRITICAL,
                requires_confirmation=True,
                executable=False,
                destructive_potential=True,
                reasons=list(base_risk.reasons) or ["Riesgo operacional crítico determinista no revocable."],
            )

        if tool_spec and tool_spec.destructive:
            return RiskAssessment(
                level=RiskLevel.CRITICAL,
                requires_confirmation=True,
                executable=False,
                destructive_potential=True,
                reasons=[f"Herramienta intrínsecamente destructiva: '{tool_name}'."],
            )

        # 3. Comprobación de acceso a recursos protegidos o sensibles
        target_path = ""
        if action and action.tool_call and action.tool_call.arguments:
            args = action.tool_call.arguments
            target_path = str(args.get("path") or args.get("file") or args.get("command") or "").strip().lower()

        is_sensitive_target = False
        if target_path:
            for pattern in self.PROTECTED_FILE_PATTERNS:
                if re.search(pattern, target_path):
                    is_sensitive_target = True
                    break

        if is_sensitive_target:
            if tool_name in ("edit_file", "delete_file", "write_to_file") or (
                operation_assessment and operation_assessment.category == CommandCategory.LOCAL_MUTATION
            ):
                return RiskAssessment(
                    level=RiskLevel.CRITICAL,
                    requires_confirmation=True,
                    executable=False,
                    destructive_potential=True,
                    reasons=[f"Intento de mutación sobre archivo protegido/sensible: '{target_path}'."],
                )
            else:
                return RiskAssessment(
                    level=RiskLevel.HIGH,
                    requires_confirmation=True,
                    executable=True,
                    destructive_potential=False,
                    reasons=[f"Lectura de recurso sensible/protegido: '{target_path}'."],
                )

        # 4. Refinamiento semántico: INSPECTION (sólo lectura) y BUILD_TEST (pruebas)
        if operation_assessment is not None:
            if operation_assessment.category == CommandCategory.INSPECTION:
                return RiskAssessment(
                    level=RiskLevel.LOW,
                    requires_confirmation=False,
                    executable=True,
                    destructive_potential=False,
                    reasons=[f"Operación concreta clasificada como '{operation_assessment.category.value}' ({operation_assessment.risk_level.value})."],
                )
            if operation_assessment.category == CommandCategory.BUILD_TEST:
                return RiskAssessment(
                    level=RiskLevel.LOW,
                    requires_confirmation=False,
                    executable=True,
                    destructive_potential=False,
                    reasons=[f"Operación concreta clasificada como '{operation_assessment.category.value}' ({operation_assessment.risk_level.value})."],
                )
            if operation_assessment.category in (CommandCategory.REMOTE_MUTATION, CommandCategory.NETWORK):
                return RiskAssessment(
                    level=RiskLevel.HIGH,
                    requires_confirmation=True,
                    executable=True,
                    destructive_potential=False,
                    reasons=[f"Operación con efectos externos o red ({operation_assessment.category.value})."],
                )
            if operation_assessment.category == CommandCategory.LOCAL_MUTATION:
                return RiskAssessment(
                    level=RiskLevel.MEDIUM,
                    requires_confirmation=base_risk.requires_confirmation,
                    executable=True,
                    destructive_potential=False,
                    reasons=[f"Operación de mutación local controlada ({operation_assessment.category.value})."],
                )

        # 5. Herramientas intrínsecamente seguras y de sólo lectura registradas
        if tool_spec and tool_spec.read_only and not tool_spec.external_side_effect:
            return RiskAssessment(
                level=RiskLevel.LOW,
                requires_confirmation=False,
                executable=True,
                destructive_potential=False,
                reasons=[f"Herramienta observacional segura registrada: '{tool_name}'."],
            )

        if tool_name in ("read_file", "view_file", "list_dir", "grep_search", "search_web"):
            return RiskAssessment(
                level=RiskLevel.LOW,
                requires_confirmation=False,
                executable=True,
                destructive_potential=False,
                reasons=[f"Herramienta estándar de inspección: '{tool_name}'."],
            )

        # 6. Mutaciones locales genéricas
        if tool_spec and tool_spec.category == "mutation":
            return RiskAssessment(
                level=tool_spec.risk_level,
                requires_confirmation=tool_spec.requires_confirmation,
                executable=True,
                destructive_potential=tool_spec.destructive,
                reasons=[f"Herramienta de mutación local: '{tool_name}'."],
            )

        # 7. Ajuste para modo Full Access en operaciones no críticas
        if mode_str == "full_access":
            if base_risk.level != RiskLevel.CRITICAL:
                return RiskAssessment(
                    level=base_risk.level,
                    requires_confirmation=False,
                    executable=True,
                    destructive_potential=base_risk.destructive_potential,
                    reasons=list(base_risk.reasons) + ["Modo Full Access: confirmación interactiva relajada por consentimiento de sesión."],
                )

        # 8. Retorno preservando el riesgo base evaluado
        return base_risk
