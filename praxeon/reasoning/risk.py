"""Motor de Riesgo Contextual (RiskEngine) para JEV Reasoning Navigator v0.2.

Evalúa el riesgo operacional no solo por el nombre de la herramienta,
sino analizando sus argumentos, rutas de destino y patrones de comandos.
"""

import re
from typing import Dict, List, Optional, Set
from praxeon.domain.models import ActionCandidate, RiskAssessment, RiskLevel
from praxeon.policy.risk import ToolRegistry, ToolSpec


class RiskEngine:
    """Evalúa dinámicamente el riesgo operacional analizando herramientas y argumentos."""

    DESTRUCTIVE_SHELL_PATTERNS = [
        r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\b",
        r"\brmdir\b.*(/[sq]|\s-[sq])",
        r"\bdel\b.*(/[fqs]|\s-[fqs])",
        r"\bformat\s+[a-z]:",
        r"\bdrop\s+(table|database|schema)\b",
        r"\bkill\s+-9\b",
        r"\bshutdown\b",
        r"\bchmod\b.*\b777\b",
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",  # fork bomb
    ]

    PROTECTED_FILE_PATTERNS = [
        r"\.env($|\.)",
        r"\.git/",
        r"\.ssh/",
        r"id_rsa",
        r"credentials\.json",
        r"\bpasswd\b",
        r"shadow\b",
        r"c:[\\/]windows",
        r"/etc/",
    ]

    SAFE_SHELL_COMMANDS = {
        "dir", "ls", "grep", "cat", "findstr", "type", "echo",
        "head", "tail", "wc", "pwd", "where", "which",
        "get-childitem", "get-content",
        "git status", "git diff", "git log", "git show", "git branch", "git tag",
        "python --version", "pytest", "pytest -v", "uv --version", "npm --version", "node --version",
    }

    def __init__(self, registry: Optional[ToolRegistry] = None):
        self.registry = registry or ToolRegistry(register_defaults=True)

    def assess_action_risk(
        self,
        action: ActionCandidate,
        execution_mode: Optional[str] = None,
    ) -> RiskAssessment:
        """Calcula el riesgo contextual completo analizando herramienta, argumentos y modo de ejecución."""
        if not action.tool_call or not action.tool_call.tool_name:
            return RiskAssessment(
                level=RiskLevel.LOW,
                requires_confirmation=False,
                executable=True,
                reasons=["Paso puramente cognitivo (sin ejecución de herramientas)."],
            )

        tool_name = action.tool_call.tool_name
        args = action.tool_call.arguments or {}

        # 1. Obtener evaluación base del registro
        base_assessment = self.registry.assess_risk(tool_name)
        if not self.registry.is_known(tool_name):
            return base_assessment

        reasons = list(base_assessment.reasons)
        current_level = base_assessment.level
        requires_conf = base_assessment.requires_confirmation
        executable = base_assessment.executable

        # 2. Análisis contextual específico para run_command y herramientas de sistema/CLI
        spec = self.registry.get_tool(tool_name)
        is_cmd = (
            tool_name == "run_command"
            or (spec and spec.category in ("system", "network"))
            or (hasattr(self.registry, "CLI_EXECUTABLES") and tool_name in self.registry.CLI_EXECUTABLES)
        )
        if is_cmd:
            cmd = str(args.get("command") or args.get("cmd") or args.get("raw") or "").strip()
            if not cmd and tool_name != "run_command":
                cmd = tool_name
            elif tool_name != "run_command":
                cmd = f"{tool_name} {cmd}".strip()
            cmd_lower = cmd.lower()

            # A. Detección de comandos shell destructivos
            for pattern in self.DESTRUCTIVE_SHELL_PATTERNS:
                if re.search(pattern, cmd_lower):
                    current_level = RiskLevel.CRITICAL
                    requires_conf = True
                    executable = False
                    reasons.append(f"Patrón de comando shell destructivo detectado: '{pattern}'")
                    break

            # B. Detección de comandos de solo lectura inocuos
            is_purely_read_only = any(
                cmd_lower == safe or cmd_lower.startswith(f"{safe} ") or cmd_lower.startswith(f"{safe}\t")
                for safe in self.SAFE_SHELL_COMMANDS
            ) or any(
                cmd_lower.endswith(f" {flag}") for flag in ("--version", "-v", "--help", "-h")
            )
            if is_purely_read_only and current_level != RiskLevel.CRITICAL:
                current_level = RiskLevel.LOW
                requires_conf = False
                reasons.append("Comando de inspección identificado como seguro.")

            # C. Detección de comandos con efectos externos o despliegues que exigen confirmación
            external_side_effect_keywords = [
                "kubectl", "docker push", "git push", "terraform", "ansible",
                "aws ", "gcloud ", "curl -x post", "curl -x delete", "curl -x put",
            ]
            if any(kw in cmd_lower for kw in external_side_effect_keywords):
                if current_level != RiskLevel.CRITICAL:
                    current_level = RiskLevel.HIGH
                requires_conf = True
                reasons.append(f"Comando con efecto externo o despliegue que requiere confirmación: '{cmd}'")

        # 3. Análisis contextual para operaciones de archivo (edit, delete, read)
        path = str(args.get("path") or args.get("file") or "").strip().lower()
        if path:
            for pattern in self.PROTECTED_FILE_PATTERNS:
                if re.search(pattern, path):
                    if tool_name in ("edit_file", "delete_file", "run_command"):
                        current_level = RiskLevel.CRITICAL
                        requires_conf = True
                        executable = False
                        reasons.append(f"Operación dirigida a archivo protegido/sensible: '{path}'")
                    else:
                        # Lectura de archivo protegido: elevar a HIGH
                        if current_level == RiskLevel.LOW:
                            current_level = RiskLevel.HIGH
                            reasons.append(f"Lectura de recurso sensible: '{path}'")
                    break

        # 4. Ajuste automático para modo Full Access
        mode_val = str(execution_mode.value if hasattr(execution_mode, "value") else execution_mode or "").lower()
        if mode_val == "full_access":
            if current_level != RiskLevel.CRITICAL:
                requires_conf = False
                reasons.append("Modo Full Access: ejecución automática autorizada por consentimiento previo de sesión.")

        return RiskAssessment(
            level=current_level,
            requires_confirmation=requires_conf,
            executable=executable,
            reasons=reasons,
        )
