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

    # 0. Reglas deterministas para ADVERSARIAL_EVASION (precedencia máxima)
    ADVERSARIAL_EVASION_PATTERNS = [
        r"\|\s*(ba?sh|zsh|dash|ksh|csh|tcsh|powershell|cmd|sh\.exe|bash\.exe)\b",
        r"\$\{(?:ifs|IFS)\}",

        r"base64\s+(-d|--decode)\s*\|",
        r"\|\s*(nc|ncat|netcat)\s+\S+",
        r"\b(curl|wget)\b.*(?:\/etc\/shadow|\/etc\/passwd|\.ssh\/id_)",
        r"(?:\/etc\/shadow|\/etc\/master\.passwd)",
        r"python\s+-c\s+['\"].*\\x[0-9a-fA-F]{2}",  # hex escaped execution
    ]

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
        r"\bdrop[_\s]+(all[_\s]+)?(table|tables|database|schema)(\b|_)",
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
        r"^\s*(get-childitem|get-content|get-command|get-item|get-process)(\s+.*)?$",
        r"^\s*(sort-object|select-object|where-object|format-table|format-list|out-string|select-string|measure-object)(\s+.*)?$",
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

    def _split_compound_command(self, cmd: str) -> List[str]:
        """Divide un comando compuesto en subcomandos respetando comillas y delimitadores shell."""
        if not any(sep in cmd for sep in ("&&", "||", ";", "\n", "|")):
            return [cmd]

        parts: List[str] = []
        current: List[str] = []
        in_single = False
        in_double = False
        i = 0
        n = len(cmd)

        while i < n:
            c = cmd[i]
            if c == "'" and not in_double:
                in_single = not in_single
                current.append(c)
                i += 1
            elif c == '"' and not in_single:
                in_double = not in_double
                current.append(c)
                i += 1
            elif not in_single and not in_double:
                if cmd[i : i + 2] in ("&&", "||"):
                    seg = "".join(current).strip()
                    if seg:
                        parts.append(seg)
                    current = []
                    i += 2
                elif c in (";", "\n", "|"):
                    seg = "".join(current).strip()
                    if seg:
                        parts.append(seg)
                    current = []
                    i += 1
                else:
                    current.append(c)
                    i += 1
            else:
                current.append(c)
                i += 1

        rem = "".join(current).strip()
        if rem:
            parts.append(rem)

        return parts if parts else [cmd]

    def _classify_compound(
        self,
        sub_commands: List[str],
        op_clean: str,
        tool: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> CommandRiskAssessment:
        """Agrega de forma segura y conservadora las evaluaciones de subcomandos encadenados."""
        sub_assessments = [
            self._classify_atomic(op_clean=sub, tool=tool, arguments=None, context=context)
            for sub in sub_commands
            if sub.strip()
        ]
        if not sub_assessments:
            return self._classify_atomic(op_clean=op_clean, tool=tool, arguments=None, context=context)

        level_order = {
            RiskLevel.LOW: 1,
            RiskLevel.MEDIUM: 2,
            RiskLevel.HIGH: 3,
            RiskLevel.CRITICAL: 4,
        }
        max_level_val = max(level_order.get(sub.risk_level, 2) for sub in sub_assessments)
        val_to_level = {1: RiskLevel.LOW, 2: RiskLevel.MEDIUM, 3: RiskLevel.HIGH, 4: RiskLevel.CRITICAL}
        overall_risk = val_to_level[max_level_val]

        has_redirection = bool(re.search(r">\s*\S+", op_clean))

        overall_read_only = all(sub.read_only for sub in sub_assessments) and not has_redirection
        overall_destructive = any(sub.destructive for sub in sub_assessments)
        overall_external_side_effect = any(sub.external_side_effect for sub in sub_assessments)
        overall_network = any(sub.network_access for sub in sub_assessments)
        overall_privilege = any(sub.privilege_escalation for sub in sub_assessments)
        overall_reversible = all(sub.reversible for sub in sub_assessments)

        cats = [sub.category for sub in sub_assessments]
        if CommandCategory.DESTRUCTIVE in cats or overall_destructive:
            overall_cat = CommandCategory.DESTRUCTIVE
        elif CommandCategory.PRIVILEGE in cats or overall_privilege:
            overall_cat = CommandCategory.PRIVILEGE
        elif CommandCategory.REMOTE_MUTATION in cats:
            overall_cat = CommandCategory.REMOTE_MUTATION
        elif CommandCategory.PROCESS_CONTROL in cats:
            overall_cat = CommandCategory.PROCESS_CONTROL
        elif CommandCategory.PACKAGE_MANAGEMENT in cats:
            overall_cat = CommandCategory.PACKAGE_MANAGEMENT
        elif CommandCategory.LOCAL_MUTATION in cats or has_redirection:
            overall_cat = CommandCategory.LOCAL_MUTATION
        elif CommandCategory.NETWORK in cats:
            overall_cat = CommandCategory.NETWORK
        elif all(c == CommandCategory.INSPECTION for c in cats) and not has_redirection:
            overall_cat = CommandCategory.INSPECTION
        elif all(c in (CommandCategory.INSPECTION, CommandCategory.BUILD_TEST) for c in cats):
            overall_cat = CommandCategory.BUILD_TEST
        else:
            overall_cat = CommandCategory.UNKNOWN

        combined_reasons: List[str] = [
            f"Comando encadenado/compuesto evaluado en {len(sub_assessments)} suboperaciones."
        ]
        for sub in sub_assessments:
            combined_reasons.extend([f"[{sub.operation}]: {r}" for r in sub.reasons])
        if has_redirection:
            combined_reasons.append("Redirección de salida a archivo detectada (mutación local no de sólo lectura).")

        matched_rules = list(dict.fromkeys(r for sub in sub_assessments for r in sub.matched_rules))
        matched_rules.append("RULE_COMPOUND_COMMAND_EVALUATION")

        ctx_hash = hashlib.sha256(f"{op_clean}|{str(context or {})}".encode("utf-8")).hexdigest()

        return CommandRiskAssessment(
            operation=op_clean,
            category=overall_cat,
            risk_level=overall_risk,
            read_only=overall_read_only,
            reversible=overall_reversible,
            destructive=overall_destructive,
            external_side_effect=overall_external_side_effect,
            network_access=overall_network,
            privilege_escalation=overall_privilege,
            confidence=min(sub.confidence for sub in sub_assessments),
            reasons=combined_reasons,
            matched_rules=matched_rules,
            classifier="deterministic_compound",
            context_hash=ctx_hash,
        )

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

        # 1. Evaluar primero de forma atómica la sentencia completa.
        # Si la regla de criticidad máxima (ej. DESTRUCTIVE / PRIVILEGE / EXFILTRATION / FORK_BOMB)
        # detecta un ataque sobre el string completo, se respeta el veto determinista directo.
        atomic_res = self._classify_atomic(op_clean=op_clean, tool=tool, arguments=arguments, context=context)
        if atomic_res.risk_level == RiskLevel.CRITICAL or atomic_res.category in (
            CommandCategory.DESTRUCTIVE,
            CommandCategory.PRIVILEGE,
        ):
            return atomic_res

        # 2. AUDITORÍA (Finding 1): Si contiene operadores shell o pipelines, descomponer y evaluar
        # cada subcomando para evitar que comandos destructivos/mutantes encadenados se camuflen
        # tras una regla permisiva (ej. cat archivo; rm -rf /).
        sub_commands = self._split_compound_command(op_clean)
        if len(sub_commands) > 1:
            compound_res = self._classify_compound(sub_commands, op_clean=op_clean, tool=tool, context=context)
            return compound_res

        return atomic_res

    def _classify_atomic(
        self,
        op_clean: str,
        tool: Optional[str] = None,
        arguments: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> CommandRiskAssessment:
        """Clasifica un comando o subcomando individual."""
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

        # Comprobación de redirección de escritura
        has_redirection = bool(re.search(r">\s*\S+", op_clean))

        # Regla 0: ADVERSARIAL_EVASION (Técnicas de evasión o ejecución encubierta)
        for pat in self.ADVERSARIAL_EVASION_PATTERNS:
            if re.search(pat, op_lower):
                return CommandRiskAssessment(
                    operation=op_clean,
                    category=CommandCategory.DESTRUCTIVE,
                    risk_level=RiskLevel.CRITICAL,
                    read_only=False,
                    reversible=False,
                    destructive=True,
                    external_side_effect=True,
                    network_access=True,
                    privilege_escalation=True,
                    confidence=1.0,
                    reasons=[f"Técnica de evasión adversaria o ejecución encubierta detectada ('{pat}')"],
                    matched_rules=["RULE_ADVERSARIAL_EVASION"],
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
        if not has_redirection:
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
        if not has_redirection:
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
