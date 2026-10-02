"""Registro tipado de herramientas (ToolRegistry) y descriptores intrínsecos (ToolSpec) para v0.2.

Sustituye heurísticas hardcodeadas de strings por inspección de descriptores:
- Categoría (inspection, mutation, system, network)
- Nivel de riesgo (LOW, MEDIUM, HIGH, CRITICAL)
- Reversibilidad
- Efectos colaterales externos
- Permisos de lectura exclusiva (read_only)
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from praxeon.domain.models import RiskAssessment, RiskLevel


class ToolSpec(BaseModel):
    """Especificación declarativa de riesgo y capacidades intrínsecas de una herramienta."""

    name: str
    category: str
    risk_level: RiskLevel = RiskLevel.LOW
    read_only: bool = False
    reversible: bool = True
    external_side_effect: bool = False
    destructive: bool = False
    requires_confirmation: bool = False
    description: Optional[str] = None


class ToolRegistry:
    """Registro extensible de herramientas con perfiles de riesgo explícitos y soporte dinámico de comandos."""

    CLI_EXECUTABLES = {
        "git", "python", "python3", "pytest", "node", "npm", "npx", "yarn", "pnpm",
        "uv", "pip", "pip3", "cargo", "rustc", "go", "docker", "kubectl",
        "bash", "sh", "zsh", "cmd", "powershell", "pwsh",
        "curl", "wget", "tar", "zip", "unzip", "make", "cmake",
        "dir", "ls", "cat", "type", "find", "grep", "findstr", "echo",
        "head", "tail", "wc", "sed", "awk", "ruff", "flake8", "black", "mypy",
        "alembic", "poetry", "tox", "pipenv", "pytest",
    }

    def __init__(self, register_defaults: bool = True):
        self._tools: Dict[str, ToolSpec] = {}
        if register_defaults:
            self._register_default_tools()

    def _is_dynamic_cli_tool(self, name: str) -> bool:
        """Determina si un identificador corresponde a un comando o ejecutable admisible para supervisión."""
        if not name or not isinstance(name, str):
            return False
        clean = name.strip().lower()
        # Rechazar tokens explícitos de pruebas de exploit/plugins no registrados
        if any(bad in clean for bad in ("unregistered", "unknown", "malicious", "reverse_shell", "exploit", "act_unknown")):
            return False
        if clean in self.CLI_EXECUTABLES:
            return True
        import re, shutil
        if re.match(r"^[a-zA-Z0-9_\-\.]+$", clean):
            if shutil.which(clean) is not None:
                return True
            return False
        return False

    def _create_dynamic_tool_spec(self, name: str) -> ToolSpec:
        """Crea una especificación declarativa para una herramienta CLI dinámica."""
        clean = name.strip().lower()
        read_only = clean in ("git", "pytest", "grep", "find", "cat", "type", "dir", "ls", "echo", "head", "tail")
        return ToolSpec(
            name=name,
            category="system",
            risk_level=RiskLevel.LOW if read_only else RiskLevel.MEDIUM,
            read_only=read_only,
            reversible=read_only,
            external_side_effect=not read_only,
            destructive=False,
            requires_confirmation=False,
            description=f"Herramienta o comando de sistema supervisado: '{name}'.",
        )

    def register_tool(self, spec: ToolSpec) -> None:
        """Registra o actualiza la especificación de una herramienta."""
        self._tools[spec.name] = spec

    def register(self, spec: ToolSpec) -> None:
        """Alias conveniente para register_tool."""
        self.register_tool(spec)

    def get_tool(self, name: str) -> Optional[ToolSpec]:
        """Obtiene la especificación de una herramienta si está registrada o la clasifica dinámicamente."""
        if not name:
            return None
        if name in self._tools:
            return self._tools[name]
        if self._is_dynamic_cli_tool(name):
            spec = self._create_dynamic_tool_spec(name)
            self._tools[name] = spec
            return spec
        return None

    def is_known(self, name: str) -> bool:
        """Verifica si la herramienta está registrada o es un comando/ejecutable admisible."""
        if not name:
            return False
        if name in self._tools:
            return True
        if self._is_dynamic_cli_tool(name):
            self.get_tool(name)
            return True
        return False

    def is_observational(self, name: str, tool_args: Optional[Dict[str, Any]] = None) -> bool:
        """Determina si la herramienta o comando es puramente observacional / de lectura."""
        if name in ("read_file", "view_file", "list_dir", "grep_search", "search_web"):
            return True
        if name == "run_command" and tool_args:
            cmd = str(tool_args.get("command") or "").strip().lower()
            read_only_prefixes = (
                "ls", "dir", "type ", "cat ", "head ", "tail ", "get-content",
                "git status", "git log", "git diff", "git show", "git branch", "git tag",
                "find ", "findstr ", "grep ", "pwd", "echo ", "where ", "which ",
                "python --version", "python -v"
            )
            if any(cmd == p.strip() or cmd.startswith(p) for p in read_only_prefixes):
                return True
        spec = self._tools.get(name)
        if not spec:
            return False
        return spec.read_only and not spec.external_side_effect

    def assess_risk(self, tool_name: Optional[str]) -> RiskAssessment:
        """Calcula el RiskAssessment objetivo para una herramienta invocada."""
        if not tool_name:
            # Paso puramente cognitivo (pensamiento interno)
            return RiskAssessment(
                level=RiskLevel.LOW,
                requires_confirmation=False,
                executable=True,
                destructive_potential=False,
                reasons=["Paso puramente cognitivo (sin efectos colaterales)."],
            )

        spec = self._tools.get(tool_name)
        if not spec:
            return RiskAssessment(
                level=RiskLevel.HIGH,
                requires_confirmation=True,
                executable=True,
                destructive_potential=False,
                reasons=[f"Herramienta no registrada en catálogo estándar: '{tool_name}'. Requiere confirmación."],
            )

        reasons = [f"Herramienta registrada en categoría '{spec.category}' con nivel '{spec.risk_level.value}'."]
        if spec.destructive:
            reasons.append("La herramienta tiene potencial destructivo sobre datos o archivos.")
        if spec.external_side_effect:
            reasons.append("La herramienta genera efectos colaterales externos no reversibles.")

        return RiskAssessment(
            level=spec.risk_level,
            requires_confirmation=spec.requires_confirmation or (spec.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)),
            executable=not spec.destructive or spec.requires_confirmation,
            destructive_potential=spec.destructive,
            reasons=reasons,
        )

    def _register_default_tools(self) -> None:
        """Registra las herramientas estándar del ecosistema con sus descriptores de seguridad."""
        defaults = [
            # Lectura e inspección segura (read-only, reversible, low risk)
            ToolSpec(
                name="read_file",
                category="inspection",
                risk_level=RiskLevel.LOW,
                read_only=True,
                reversible=True,
                external_side_effect=False,
                description="Lee el contenido de un archivo en disco.",
            ),
            ToolSpec(
                name="view_file",
                category="inspection",
                risk_level=RiskLevel.LOW,
                read_only=True,
                reversible=True,
                external_side_effect=False,
                description="Visualiza el contenido de un archivo local.",
            ),
            ToolSpec(
                name="list_dir",
                category="inspection",
                risk_level=RiskLevel.LOW,
                read_only=True,
                reversible=True,
                external_side_effect=False,
                description="Lista los archivos en un directorio.",
            ),
            ToolSpec(
                name="grep_search",
                category="inspection",
                risk_level=RiskLevel.LOW,
                read_only=True,
                reversible=True,
                external_side_effect=False,
                description="Búsqueda de patrones en archivos.",
            ),
            ToolSpec(
                name="search_web",
                category="inspection",
                risk_level=RiskLevel.LOW,
                read_only=True,
                reversible=True,
                external_side_effect=False,
                description="Búsqueda informativa en la web.",
            ),
            # Finalización de tarea (supervisada por CompletionVerifier)
            ToolSpec(
                name="finish",
                category="lifecycle",
                risk_level=RiskLevel.LOW,
                read_only=True,
                reversible=True,
                external_side_effect=False,
                description="Declara la culminación de la tarea del agente.",
            ),
            # Mutaciones moderadas (edición/creación de archivos)
            ToolSpec(
                name="write_to_file",
                category="mutation",
                risk_level=RiskLevel.MEDIUM,
                read_only=False,
                reversible=True,
                external_side_effect=False,
                description="Crea o sobrescribe un archivo.",
            ),
            ToolSpec(
                name="replace_file_content",
                category="mutation",
                risk_level=RiskLevel.MEDIUM,
                read_only=False,
                reversible=True,
                external_side_effect=False,
                description="Reemplaza un fragmento de texto en un archivo.",
            ),
            ToolSpec(
                name="edit_file",
                category="mutation",
                risk_level=RiskLevel.MEDIUM,
                read_only=False,
                reversible=True,
                external_side_effect=False,
                description="Edita contenido de un archivo.",
            ),
            # Comandos del sistema (alto riesgo o crítico según contenido)
            ToolSpec(
                name="run_command",
                category="system",
                risk_level=RiskLevel.HIGH,
                read_only=False,
                reversible=False,
                external_side_effect=True,
                description="Ejecuta comandos de shell en el sistema operativo.",
            ),
            # Destructivas críticas
            ToolSpec(
                name="delete_file",
                category="mutation",
                risk_level=RiskLevel.CRITICAL,
                read_only=False,
                reversible=False,
                external_side_effect=True,
                destructive=True,
                requires_confirmation=True,
                description="Elimina de forma irreversible un archivo.",
            ),
            # Herramientas de desarrollo comunes integradas
            ToolSpec(
                name="git",
                category="system",
                risk_level=RiskLevel.MEDIUM,
                read_only=False,
                reversible=True,
                external_side_effect=False,
                description="Comandos de control de versiones Git.",
            ),
            ToolSpec(
                name="pytest",
                category="inspection",
                risk_level=RiskLevel.LOW,
                read_only=True,
                reversible=True,
                external_side_effect=False,
                description="Ejecución de pruebas unitarias y de integración.",
            ),
            ToolSpec(
                name="python",
                category="system",
                risk_level=RiskLevel.MEDIUM,
                read_only=False,
                reversible=True,
                external_side_effect=False,
                description="Intérprete y scripts de Python.",
            ),
            ToolSpec(
                name="npm",
                category="system",
                risk_level=RiskLevel.MEDIUM,
                read_only=False,
                reversible=True,
                external_side_effect=False,
                description="Gestor de paquetes y scripts de Node.js.",
            ),
            ToolSpec(
                name="cargo",
                category="system",
                risk_level=RiskLevel.MEDIUM,
                read_only=False,
                reversible=True,
                external_side_effect=False,
                description="Compilador y gestor de paquetes de Rust.",
            ),
            ToolSpec(
                name="bash",
                category="system",
                risk_level=RiskLevel.HIGH,
                read_only=False,
                reversible=False,
                external_side_effect=True,
                description="Intérprete de comandos Bash / Shell.",
            ),
            ToolSpec(
                name="curl",
                category="network",
                risk_level=RiskLevel.MEDIUM,
                read_only=False,
                reversible=True,
                external_side_effect=True,
                description="Cliente HTTP / transferencia de red.",
            ),
        ]
        for spec in defaults:
            self._tools[spec.name] = spec
