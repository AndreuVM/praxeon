"""Gestor de contexto y memoria episódica para tareas concatenadas en sesiones interactivas de JEV."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import platform
import re
import sys
from typing import Any, Dict, List, Optional, Set

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


@dataclass
class TaskRecord:
    """Registro de una tarea completada dentro de una sesión continua."""
    task_id: int
    goal: str
    summary: str
    final_answer: str
    executed_steps: int
    discovered_files: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)
    key_findings: List[str] = field(default_factory=list)
    history_steps: List[Dict[str, Any]] = field(default_factory=list)


class SessionContextManager:
    """Gestiona la memoria acumulada y el contexto conversacional entre tareas concatenadas."""

    def __init__(self, max_history_tasks: int = 8, max_context_chars: int = 12000):
        self.max_history_tasks = max_history_tasks
        self.max_context_chars = max_context_chars
        self.task_records: List[TaskRecord] = []
        self.all_discovered_files: Set[str] = set()
        self.all_modified_files: Set[str] = set()
        self.conversation_history: List[Dict[str, str]] = []

    def is_empty(self) -> bool:
        """Indica si es la primera tarea de la sesión (sin contexto previo)."""
        return len(self.task_records) == 0

    def record_completed_task(
        self,
        goal: str,
        summary: str,
        final_answer: str,
        executed_steps: int,
        history_steps: Optional[List[Dict[str, Any]]] = None,
    ) -> TaskRecord:
        """Registra la finalización de una tarea y extrae automáticamente hechos y archivos descubiertos."""
        discovered: Set[str] = set()
        modified: Set[str] = set()
        findings: List[str] = []

        if history_steps:
            for st in history_steps:
                tool = st.get("tool_name", "")
                args = st.get("tool_args", {}) or {}
                path = args.get("path") or args.get("file")
                if path and isinstance(path, str):
                    if tool in ("read_file", "view_file"):
                        discovered.add(path)
                    elif tool in ("edit_file", "write_file"):
                        modified.add(path)

                cmd = args.get("command", "")
                if cmd and isinstance(cmd, str) and tool == "run_command":
                    # Extraer posibles archivos mencionados en comandos
                    for part in cmd.split():
                        if "." in part and not part.startswith("-") and "/" not in part and "\\" not in part:
                            discovered.add(part)

        self.all_discovered_files.update(discovered)
        self.all_modified_files.update(modified)

        if summary and summary.strip():
            findings.append(summary.strip())
        elif final_answer and final_answer.strip():
            # Extraer primera frase o líneas representativas
            clean_ans = final_answer.strip().split("\n\n")[0]
            findings.append(clean_ans[:300])

        record = TaskRecord(
            task_id=len(self.task_records) + 1,
            goal=goal.strip(),
            summary=summary.strip() if summary else (final_answer.strip()[:200] if final_answer else "Completada"),
            final_answer=final_answer.strip(),
            executed_steps=executed_steps,
            discovered_files=sorted(list(discovered)),
            modified_files=sorted(list(modified)),
            key_findings=findings,
            history_steps=history_steps or [],
        )
        self.task_records.append(record)
        return record

    def get_task_fragments(self) -> List[Any]:
        """Convierte los registros de tareas previas en TaskFragments estructurados de ContextManager."""
        from praxeon.context.fragments import TaskFragment
        frags = []
        for rec in self.task_records:
            frags.append(
                TaskFragment(
                    task_id=rec.task_id,
                    goal=rec.goal,
                    summary=rec.summary,
                    metadata={
                        "discovered_files": rec.discovered_files,
                        "modified_files": rec.modified_files,
                        "executed_steps": rec.executed_steps,
                    },
                )
            )
        return frags

    def get_environment_fragment(self, root_dir: Optional[str] = None) -> Any:
        """Convierte la información del entorno anfitrión en un EnvironmentFragment cacheable."""
        from praxeon.context.fragments import EnvironmentFragment
        env_text = self.get_environment_info(root_dir=root_dir)
        return EnvironmentFragment(environment_info=env_text, source_id="session_host_env")

    def build_initial_system_prompt(self) -> str:
        """Genera el prompt de sistema base con instrucciones de eficiencia y formato ReAct."""
        return (
            "Eres un agente de ingeniería de software autónomo supervisado cognitivamente por JEV.\n"
            "REGLA DE EFICIENCIA CRÍTICA: Formula y emite SIEMPRE un bloque con 2 o 3 pasos estructurados (Step 1, Step 2, etc.) "
            "en cada turno cuando el flujo sea deducible, en lugar de un único paso.\n\n"
            "Formato requerido:\n"
            "Step 1:\n"
            "Thought: <razonamiento del paso 1>\n"
            "Action: <nombre_herramienta> <argumentos_en_json>\n"
            "Step 2:\n"
            "Thought: <razonamiento del paso 2>\n"
            "Action: <nombre_herramienta> <argumentos_en_json>\n\n"
            "Herramientas disponibles: run_command(command), read_file(path), edit_file(path, diff), finish(summary).\n"
            "REGLAS OBLIGATORIAS:\n"
            "- Queda TERMINANTEMENTE PROHIBIDO alucinar hechos, variables o archivos no fundamentados en las observaciones reales recibidas.\n"
            "- Queda TERMINANTEMENTE PROHIBIDO usar 'finish' como acción provisional, de planificación o con textos evasivos (ej. 'pendiente de lectura', 'sin analizar'). "
            "La herramienta 'finish' SOLO debe usarse para entregar la solución definitiva ya sintetizada a partir de las observaciones reales obtenidas.\n"
            "- NUNCA propongas 'finish' en el mismo bloque junto a herramientas de inspección (como read_file o run_command) que aún no hayan sido ejecutadas.\n"
            "- Emite EXCLUSIVAMENTE los bloques de pasos estructurados (Step 1, Step 2, etc.) con sus campos Thought y Action. NO agregues introducciones, preámbulos conversacionales ni texto suelto fuera de ese formato.\n"
            "- DISCERNIMIENTO DE CONTEXTO Y TIPO DE TAREA:\n"
            "  a) Para tareas de diseño, especificación, arquitectura, user stories, requisitos funcionales/no funcionales o propuestas para un nuevo proyecto o sistema: formula y entrega la solución técnica completa directamente en 'finish(summary=...)'. NO intentes inspeccionar ni forzar lecturas de archivos del repositorio anfitrión a menos que el usuario indique expresamente que se refiere a los archivos ya existentes en este espacio de trabajo.\n"
            "  b) Para consultas, análisis u opiniones conceptuales o externas: responde directamente con 'finish' sin forzar lectura de archivos no relacionados.\n"
            "  c) Para tareas de auditoría, modificación o pruebas sobre el código del repositorio local anfitrión: inspecciona con 'read_file' o comandos de terminal antes de editar o concluir.\n"
            "- En la acción 'finish', responde DIRECTAMENTE al objetivo del usuario. Queda TERMINANTEMENTE PROHIBIDO describir mensajes internos del supervisor, herramientas vetadas, advertencias de bucle o diagnósticos del sistema. Entrega siempre tu respuesta técnica fundamentada.\n"
            "Cuando hayas resuelto la tarea o encontrado la solución fundamentada, invoca obligatoriamente:\n"
            'Action: finish {"summary": "explicación clara, fundamentada y completa de la solución final"}'
        )

    def get_environment_info(self, root_dir: Optional[str] = None) -> str:
        """Recolecta información estructurada sobre el entorno de ejecución del sistema y el espacio de trabajo."""
        cwd = Path(root_dir) if root_dir else Path.cwd()
        os_name = platform.system()
        release = platform.release()
        arch = platform.machine() or (platform.architecture()[0] if hasattr(platform, "architecture") else "")
        shell_name = "PowerShell" if sys.platform == "win32" else "bash"

        ignored_dirs = {
            ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache",
            "node_modules", ".gemini", "scratch", ".idea", ".vscode"
        }

        system_hidden = {
            "$recycle.bin", "system volume information", "pagefile.sys",
            "swapfile.sys", "hiberfil.sys", "dumpstack.log", "dumpstack.log.tmp"
        }

        file_tree = []
        try:
            root_items = sorted(list(cwd.iterdir()), key=lambda p: (not p.is_dir(), p.name.lower()))
            for item in root_items:
                if item.name.startswith(".") and item.name not in (".env", ".gitignore"):
                    continue
                if item.name.lower() in system_hidden or item.name.startswith("$"):
                    continue
                if item.name in ignored_dirs:
                    continue
                if item.is_file():
                    file_tree.append(f"- {item.name}")
                elif item.is_dir():
                    sub_items = []
                    try:
                        for sub in sorted(list(item.iterdir()), key=lambda p: (not p.is_dir(), p.name.lower())):
                            if sub.name.startswith(".") or sub.name in ignored_dirs:
                                continue
                            sub_items.append(sub.name + ("/" if sub.is_dir() else ""))
                    except Exception:
                        pass
                    if sub_items:
                        preview = ", ".join(sub_items[:8])
                        if len(sub_items) > 8:
                            preview += f" ... (+{len(sub_items)-8} más)"
                        file_tree.append(f"[DIR] {item.name}/ [{preview}]")
                    else:
                        file_tree.append(f"[DIR] {item.name}/")
        except Exception as e:
            file_tree.append(f"- (Error explorando directorio: {e})")

        tree_str = "\n  ".join(file_tree[:25]) if file_tree else "- (Directorio vacío)"

        env_lines = [
            "🖥️ [INFORMACIÓN DEL ENTORNO DE EJECUCIÓN Y SISTEMA]:",
            f"• Sistema Operativo: {os_name} {release} ({arch})",
            f"• Shell del terminal (run_command): {shell_name}",
            f"• Directorio de trabajo raíz (CWD): {cwd}",
            f"• Separador de rutas: '{os.sep}' (en las herramientas puedes usar rutas relativas normales con '/')",
            "• Espacio de trabajo anfitrión montado (CWD del supervisor):",
            f"  {tree_str}",
            "  [AVISO DE ÁMBITO]: Este listado corresponde al entorno anfitrión donde corre el supervisor. Si el usuario te pide crear, diseñar, especificar o hacer un informe sobre otro proyecto, aplicación o arquitectura conceptual, esa tarea es de diseño y NO se refiere a estos archivos.",
            "",
            "💡 RECOMENDACIONES DE ENTORNO:",
            "1. Si el objetivo requiere inspeccionar código existente en este espacio de trabajo anfitrión, usa 'read_file(path)'. Si la tarea es conceptual, de diseño o sobre otro proyecto, sintetiza directamente en 'finish' sin lecturas innecesarias.",
        ]
        if sys.platform == "win32":
            env_lines.append(
                "2. En Windows, NO uses comandos Unix/Linux como 'ls', 'find', 'grep' o 'cat'. "
                "Si requieres usar la consola con run_command, utiliza comandos de PowerShell/CMD (como 'dir' o 'Get-ChildItem')."
            )
        else:
            env_lines.append(
                "2. En sistemas Unix puedes utilizar comandos estándar como 'ls', 'grep' o 'cat'."
            )

        return "\n".join(env_lines)

    def prepare_task_conversation(self, current_goal: str) -> List[Dict[str, str]]:
        """Prepara el historial de conversación inyectando la información de entorno y memoria de tareas previas."""
        system_content = self.build_initial_system_prompt()
        env_info = self.get_environment_info()

        if self.is_empty():
            # Primera tarea de la sesión
            initial_user_prompt = (
                f"{env_info}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Por favor, resuelve la siguiente tarea: {current_goal}"
            )
            self.conversation_history = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": initial_user_prompt},
            ]
            return self.conversation_history

        # Tarea concatenada subsiguiente: Inyectar memoria de sesión
        memory_lines = [
            "🧠 [MEMORIA DE SESIÓN CONTINUA — TAREAS PREVIAS COMPLETADAS]:",
            "Has estado trabajando en esta misma sesión y cuentas con el siguiente contexto acumulado:",
        ]

        # Resumen de tareas completadas
        recent_records = self.task_records[-self.max_history_tasks:]
        for rec in recent_records:
            ans_snippet = rec.final_answer.strip()
            if len(ans_snippet) > 400:
                ans_snippet = ans_snippet[:380] + "..."
            memory_lines.append(f"• Tarea {rec.task_id}: '{rec.goal}'")
            memory_lines.append(f"  Resultado obtenido: {rec.summary}")
            if ans_snippet and ans_snippet != rec.summary:
                memory_lines.append(f"  Detalle de solución: {ans_snippet}")
            if rec.discovered_files:
                memory_lines.append(f"  Archivos identificados: {', '.join(rec.discovered_files[:5])}")
            if rec.modified_files:
                memory_lines.append(f"  Archivos modificados: {', '.join(rec.modified_files[:5])}")

        if self.all_discovered_files:
            memory_lines.append(
                f"• Archivos globales conocidos en disco: {', '.join(sorted(list(self.all_discovered_files))[:10])}"
            )

        memory_context = "\n".join(memory_lines)

        user_content = (
            f"{env_info}\n\n"
            f"{memory_context}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 [NUEVA TAREA CONCATENADA ACTUAL (Tarea #{len(self.task_records)+1})]:\n"
            f"{current_goal}\n\n"
            f"INSTRUCCIÓN: Utiliza libremente toda la información, respuestas y archivos descubiertos en las tareas anteriores "
            f"para resolver esta nueva tarea sin tener que volver a descubrirlos desde cero a menos que sea necesario."
        )

        self.conversation_history = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        return self.conversation_history

    def reset(self) -> None:
        """Reinicia la memoria de la sesión para empezar desde cero."""
        self.task_records.clear()
        self.all_discovered_files.clear()
        self.all_modified_files.clear()
        self.conversation_history.clear()

    def get_summary_panel(self) -> Panel:
        """Genera un panel visual Rich resumiendo el estado de la sesión continua."""
        if not self.task_records:
            return Panel("[dim]Sin tareas previas en la sesión actual.[/]", title="Contexto de Sesión")

        table = Table(show_header=True, header_style="bold magenta", expand=True)
        table.add_column("#", style="dim", width=4)
        table.add_column("Objetivo Previo", style="cyan")
        table.add_column("Resultado / Hallazgos", style="green")
        table.add_column("Archivos", style="yellow")

        for r in self.task_records:
            files_desc = ", ".join(r.discovered_files + r.modified_files)[:30] or "N/A"
            table.add_row(
                str(r.task_id),
                r.goal[:40] + ("..." if len(r.goal) > 40 else ""),
                r.summary[:60] + ("..." if len(r.summary) > 60 else ""),
                files_desc,
            )

        return Panel(
            table,
            title=f"🔗 [bold cyan]Memoria de Sesión Activa ({len(self.task_records)} tareas acumuladas)[/]",
            border_style="cyan",
        )

    @staticmethod
    def get_directory_tree(root_dir: str, max_files: int = 100) -> List[str]:
        """Obtiene una lista representativa de archivos del directorio raíz respetando exclusiones estándar."""
        root_path = Path(root_dir)
        if not root_path.exists() or not root_path.is_dir():
            return []

        ignored = {".git", ".venv", "venv", "__pycache__", "node_modules", "dist", ".pytest_cache", ".eggs", "build"}
        found_files: List[str] = []
        try:
            for root, dirs, filenames in os.walk(root_path):
                dirs[:] = [d for d in dirs if d not in ignored and not d.startswith(".")]
                for f in filenames:
                    if f.startswith("."):
                        continue
                    rel = os.path.relpath(os.path.join(root, f), root_dir)
                    found_files.append(rel.replace("\\", "/"))
                    if len(found_files) >= max_files:
                        return found_files
        except Exception:
            pass
        return found_files


def get_default_workspace_root() -> str:
    """Retorna la ruta raíz predeterminada del workspace del proyecto."""
    env_root = os.environ.get("PRAXEON_WORKSPACE_ROOT")
    if env_root and os.path.exists(env_root):
        return str(Path(env_root).resolve())

    # Search for git or pyproject root from cwd
    curr = Path.cwd().resolve()
    for parent in [curr] + list(curr.parents):
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return str(parent)
    return str(curr)

