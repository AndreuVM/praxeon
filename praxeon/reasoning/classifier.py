"""Clasificador contextual de operaciones (praxeon/reasoning/classifier.py).

Especificación PRAXEON 1.0 (Secciones 4 y 5).
Separa formalmente la identidad de la herramienta (ToolSpec) de la semántica
de la operación concreta ejecutada (CommandRiskAssessment).

Garantiza el principio fundamental:
"Desconocido no significa peligroso; significa incierto. La incertidumbre
debe poder llevar a REVIEW. Las barreras deterministas tienen precedencia."
"""

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from praxeon.domain.assessment import (
    CommandCategory,
    CommandRiskAssessment,
    RiskLevel,
)


class CommandClassifier:
    """Clasifica operaciones de shell o llamadas a herramientas en una taxonomía multidimensional."""

    # 1. Reglas deterministas para PRIVILEGE (precedencia máxima)
    PRIVILEGE_PATTERNS = [
        r"\bsudo\b",
        r"\brunas\b",
        r"\bchmod\b",
        r"\bchown\b",
        r"\bchgrp\b",
        r"\bsetfacl\b",
        r"\bdoas\b",
        r"\bgsudo\b",
        r"\bsu\s+-\b",
    ]

    # 2. Reglas deterministas para DESTRUCTIVE (precedencia máxima)
    DESTRUCTIVE_PATTERNS = [
        r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\b",
        r"\brmdir\b.*(/[sq]|\s-[sq])",
        r"\bdel\b.*(/[fqs]|\s-[fqs])",
        r"\bformat\s+[a-z]:",
        r"\bdiskpart\b",
        r"\bdrop\s+(table|database|schema)\b",
        r"\btruncate\s+table\b",
        r"\bkill\s+-9\b",
        r"\bshutdown\b",
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",  # fork bomb
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\s+-[a-z]*f\b",
        r"\bmkfs\b",
        r"\bfdisk\b",
        r"\bdd\s+if=.*\sof=/dev/",
    ]

    # 3. Reglas deterministas para REMOTE_MUTATION
    REMOTE_MUTATION_PATTERNS = [
        r"\bgit\s+push\b",
        r"\bdocker\s+push\b",
        r"\bkubectl\s+(apply|delete|create|replace|scale|patch)\b",
        r"\bterraform\s+(apply|destroy)\b",
        r"\bansible-playbook\b",
        r"\baws\s+s3\s+(cp|sync|rm)\b",
        r"\bgcloud\s+(compute|run|container|app)\s+deploy",
        r"\bhelm\s+(install|upgrade|uninstall)\b",
    ]

    # 4. Reglas deterministas para INSPECTION (de sólo lectura)
    INSPECTION_PATTERNS = [
        r"^\s*(git\s+(status|diff|log|show|branch|tag|remote|rev-parse))(\s+.*)?$",
        r"^\s*(ls|dir|pwd|where|which|findstr|echo|whoami|uname|id|hostname|date)(\s+.*)?$",
        r"^\s*(cat|head|tail|wc|type|more|less)(\s+.*)?$",
        r"^\s*(grep|rg|ag)(\s+.*)?$",
        r"^\s*.*(--version|-v|--help|-h)\s*$",
        r"^\s*(get-childitem|get-content|get-command)(\s+.*)?$",
        r"^\s*read_file\b.*",
        r"^\s*view_file\b.*",
    ]

    # 5. Reglas deterministas para BUILD_TEST
    BUILD_TEST_PATTERNS = [
        r"\bpytest\b",
        r"\bpython\s+-m\s+(unittest|pytest)\b",
        r"\bnpm\s+test\b",
        r"\bnpx\s+vitest\b",
        r"\bnpx\s+jest\b",
        r"\bcargo\s+test\b",
        r"\bmvn\s+test\b",
        r"\bctest\b",
        r"\bgo\s+test\b",
        r"\bmake\s+test\b",
        r"\bdotnet\s+test\b",
    ]

    # 6. Reglas deterministas para PACKAGE_MANAGEMENT
    PACKAGE_MANAGEMENT_PATTERNS = [
        r"\bpip\s+install\b",
        r"\bpip\s+uninstall\b",
        r"\buv\s+(pip|add|remove)\b",
        r"\bnpm\s+(install|i|uninstall|add)\b",
        r"\byarn\s+(add|remove)\b",
        r"\bcargo\s+(add|install)\b",
        r"\bgem\s+install\b",
        r"\bapt(-get)?\s+(install|remove)\b",
        r"\bbrew\s+(install|uninstall)\b",
    ]

    # 7. Reglas deterministas para PROCESS_CONTROL
    PROCESS_CONTROL_PATTERNS = [
        r"\btaskkill\b",
        r"\bkill\b",
        r"\bpkill\b",
        r"\bkillall\b",
        r"\bsystemctl\s+(start|stop|restart)\b",
        r"\bservice\s+.*\s+(start|stop|restart)\b",
        r"\bstop-process\b",
    ]

    # 8. Reglas deterministas para NETWORK
    NETWORK_PATTERNS = [
        r"\bcurl\b",
        r"\bwget\b",
        r"\bssh\b",
        r"\bscp\b",
        r"\bsftp\b",
        r"\bftp\b",
        r"\bgit\s+(fetch|clone|pull)\b",
        r"\bping\b",
        r"\btracert\b",
        r"\btraceroute\b",
        r"\bnslookup\b",
        r"\bdig\b",
        r"\binvoke-webrequest\b",
        r"\binvoke-restmethod\b",
    ]

    # 9. Reglas deterministas para LOCAL_MUTATION
    LOCAL_MUTATION_PATTERNS = [
        r"\b(mkdir|touch|cp|mv|copy|move)\b",
        r"\bgit\s+(add|commit|checkout|switch|stash|merge)\b",
        r"\b(write_file|edit_file)\b",
        r">[^>]",  # redirección de escritura
        r">>",     # redirección append
    ]

    def classify(
        self,
        operation: str = "",
        context: Optional[Dict[str, Any]] = None,
        tool: Optional[str] = None,
        arguments: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> CommandRiskAssessment:
        """Clasifica contextualmente una operación y produce un CommandRiskAssessment completo."""
        op_clean = (operation or "").strip()
        if not op_clean:
            if arguments:
                op_clean = str(arguments.get("command") or arguments.get("cmd") or arguments.get("path") or "").strip()
            if not op_clean and tool:
                op_clean = tool
        elif tool and tool not in ("run_command", op_clean) and not op_clean.startswith(tool):
            op_clean = f"{tool} {op_clean}".strip()

        op_lower = op_clean.lower()
        context_str = str(context or {})
        ctx_hash = hashlib.sha256(f"{op_clean}|{context_str}".encode("utf-8")).hexdigest()

        # Operación vacía o de ciclo de vida conocida (ej. finish)
        if not op_clean or op_lower.startswith("finish") or tool == "finish":
            return CommandRiskAssessment(
                operation=op_clean or "noop",
                category=CommandCategory.INSPECTION,
                risk_level=RiskLevel.LOW,
                read_only=True,
                reversible=True,
                destructive=False,
                external_side_effect=False,
                network_access=False,
                privilege_escalation=False,
                confidence=1.0,
                reasons=["Operación de ciclo de vida / cognitiva concluida."],
                matched_rules=["LIFECYCLE_COMPLETION"],
                classifier="deterministic",
                context_hash=ctx_hash,
            )

        # Regla 1: PRIVILEGE (Criticidad absoluta)
        for pat in self.PRIVILEGE_PATTERNS:
            if re.search(pat, op_lower):
                return CommandRiskAssessment(
                    operation=op_clean,
                    category=CommandCategory.PRIVILEGE,
                    risk_level=RiskLevel.CRITICAL,
                    read_only=False,
                    reversible=False,
                    destructive=False,
                    external_side_effect=True,
                    network_access=False,
                    privilege_escalation=True,
                    confidence=1.0,
                    reasons=[f"Intento de escalada de privilegios detectado ('{pat}')"],
                    matched_rules=["RULE_PRIVILEGE_ESCALATION"],
                    classifier="deterministic",
                    context_hash=ctx_hash,
                )

        # Regla 2: DESTRUCTIVE (Veto determinista irrevocable)
        for pat in self.DESTRUCTIVE_PATTERNS:
            if re.search(pat, op_lower):
                return CommandRiskAssessment(
                    operation=op_clean,
                    category=CommandCategory.DESTRUCTIVE,
                    risk_level=RiskLevel.CRITICAL,
                    read_only=False,
                    reversible=False,
                    destructive=True,
                    external_side_effect=True,
                    network_access=False,
                    privilege_escalation=False,
                    confidence=1.0,
                    reasons=[f"Patrón de comando altamente destructivo detectado ('{pat}')"],
                    matched_rules=["RULE_DESTRUCTIVE_COMMAND"],
                    classifier="deterministic",
                    context_hash=ctx_hash,
                )

        # Regla 3: REMOTE_MUTATION (Efectos externos en infraestructura/repos remotos)
        for pat in self.REMOTE_MUTATION_PATTERNS:
            if re.search(pat, op_lower):
                return CommandRiskAssessment(
                    operation=op_clean,
                    category=CommandCategory.REMOTE_MUTATION,
                    risk_level=RiskLevel.HIGH,
                    read_only=False,
                    reversible=False,
                    destructive=False,
                    external_side_effect=True,
                    network_access=True,
                    privilege_escalation=False,
                    confidence=0.95,
                    reasons=[f"Modificación remota o despliegue en infraestructura externa ('{pat}')"],
                    matched_rules=["RULE_REMOTE_MUTATION"],
                    classifier="deterministic",
                    context_hash=ctx_hash,
                )

        # Regla 4: INSPECTION (Lecturas puras y consultas de estado)
        for pat in self.INSPECTION_PATTERNS:
            if re.search(pat, op_lower):
                return CommandRiskAssessment(
                    operation=op_clean,
                    category=CommandCategory.INSPECTION,
                    risk_level=RiskLevel.LOW,
                    read_only=True,
                    reversible=True,
                    destructive=False,
                    external_side_effect=False,
                    network_access=False,
                    privilege_escalation=False,
                    confidence=0.98,
                    reasons=["Comando de inspección/observación inocuo de sólo lectura."],
                    matched_rules=["RULE_SAFE_INSPECTION"],
                    classifier="deterministic",
                    context_hash=ctx_hash,
                )

        # Regla 5: BUILD_TEST (Ejecución de suites de prueba y compilación)
        for pat in self.BUILD_TEST_PATTERNS:
            if re.search(pat, op_lower):
                return CommandRiskAssessment(
                    operation=op_clean,
                    category=CommandCategory.BUILD_TEST,
                    risk_level=RiskLevel.LOW,
                    read_only=True,
                    reversible=True,
                    destructive=False,
                    external_side_effect=False,
                    network_access=False,
                    privilege_escalation=False,
                    confidence=0.95,
                    reasons=["Ejecución de suite de pruebas/build en espacio de trabajo."],
                    matched_rules=["RULE_BUILD_TEST"],
                    classifier="deterministic",
                    context_hash=ctx_hash,
                )

        # Regla 6: PACKAGE_MANAGEMENT (Gestión de dependencias)
        for pat in self.PACKAGE_MANAGEMENT_PATTERNS:
            if re.search(pat, op_lower):
                return CommandRiskAssessment(
                    operation=op_clean,
                    category=CommandCategory.PACKAGE_MANAGEMENT,
                    risk_level=RiskLevel.MEDIUM,
                    read_only=False,
                    reversible=True,
                    destructive=False,
                    external_side_effect=True,
                    network_access=True,
                    privilege_escalation=False,
                    confidence=0.92,
                    reasons=["Instalación o modificación de dependencias/paquetes."],
                    matched_rules=["RULE_PACKAGE_MANAGEMENT"],
                    classifier="deterministic",
                    context_hash=ctx_hash,
                )

        # Regla 7: PROCESS_CONTROL (Gestión de procesos locales)
        for pat in self.PROCESS_CONTROL_PATTERNS:
            if re.search(pat, op_lower):
                return CommandRiskAssessment(
                    operation=op_clean,
                    category=CommandCategory.PROCESS_CONTROL,
                    risk_level=RiskLevel.HIGH,
                    read_only=False,
                    reversible=False,
                    destructive=False,
                    external_side_effect=True,
                    network_access=False,
                    privilege_escalation=False,
                    confidence=0.90,
                    reasons=["Control de señales o terminación de procesos del sistema operativo."],
                    matched_rules=["RULE_PROCESS_CONTROL"],
                    classifier="deterministic",
                    context_hash=ctx_hash,
                )

        # Regla 8: NETWORK (Llamadas de red y egress)
        for pat in self.NETWORK_PATTERNS:
            if re.search(pat, op_lower):
                return CommandRiskAssessment(
                    operation=op_clean,
                    category=CommandCategory.NETWORK,
                    risk_level=RiskLevel.MEDIUM,
                    read_only=True,
                    reversible=True,
                    destructive=False,
                    external_side_effect=False,
                    network_access=True,
                    privilege_escalation=False,
                    confidence=0.90,
                    reasons=["Invocación de utilidades o protocolos de red."],
                    matched_rules=["RULE_NETWORK_EGRESS"],
                    classifier="deterministic",
                    context_hash=ctx_hash,
                )

        # Regla 9: LOCAL_MUTATION (Creación, movimiento o edición de archivos locales)
        for pat in self.LOCAL_MUTATION_PATTERNS:
            if re.search(pat, op_lower):
                return CommandRiskAssessment(
                    operation=op_clean,
                    category=CommandCategory.LOCAL_MUTATION,
                    risk_level=RiskLevel.LOW,
                    read_only=False,
                    reversible=True,
                    destructive=False,
                    external_side_effect=False,
                    network_access=False,
                    privilege_escalation=False,
                    confidence=0.88,
                    reasons=["Mutación de archivos o estado local del workspace."],
                    matched_rules=["RULE_LOCAL_MUTATION"],
                    classifier="deterministic",
                    context_hash=ctx_hash,
                )

        # =========================================================================
        # Regla 10: UNKNOWN (Principio: desconocido no significa peligroso, sino incierto)
        # =========================================================================
        return CommandRiskAssessment(
            operation=op_clean,
            category=CommandCategory.UNKNOWN,
            risk_level=RiskLevel.MEDIUM,
            read_only=False,
            reversible=True,
            destructive=False,
            external_side_effect=False,
            network_access=False,
            privilege_escalation=False,
            confidence=0.40,
            reasons=[
                "Operación sintáctica no catalogada determinísticamente; "
                "marcada como incierta para evaluación contextual o revisión humana (REVIEW)."
            ],
            matched_rules=["UNKNOWN_OPERATION_UNCERTAINTY"],
            classifier="deterministic_fallback",
            context_hash=ctx_hash,
        )
