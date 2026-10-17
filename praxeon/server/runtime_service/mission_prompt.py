"""Prompt de sistema de una misión: solo información del entorno y el formato de respuesta.

PRAXEON supervisa al LLM con modelos System-1 y con la política determinista; no se le escribe un arnés en el prompt
(reglas de qué herramienta usar, qué leer antes de terminar, qué comandos evitar…). Esas decisiones las toma el supervisor
sobre cada acción, y sus vetos llegan al agente como observaciones. Quien sabe qué hacer con la tarea es el modelo; el
prompt solo le dice dónde está ejecutándose y cómo debe contestar.

El mismo texto lo usa el agente «desnudo» del benchmark (praxeon.evaluation.agentbench), de modo que la comparación
mida la supervisión y no una diferencia de instrucciones.
"""

from __future__ import annotations

import os
import platform
import sys
from typing import Optional


def describe_system(ws_root: Optional[str]) -> str:
    """Dónde se ejecuta el agente: sistema operativo, shell, directorio de trabajo y separador de rutas."""
    shell = "PowerShell" if sys.platform == "win32" else "bash"
    arch = platform.machine() or ""
    return (
        f"Sistema operativo: {platform.system()} {platform.release()} ({arch}). "
        f"run_command usa {shell}.\n"
        f"Directorio de trabajo: {ws_root or os.getcwd()} (rutas relativas a él; puedes usar '/' como separador)."
    )


RESPONSE_FORMAT = (
    "En cada turno responde exactamente con:\n"
    "Thought: <una frase con tu siguiente paso>\n"
    "Action: <herramienta>(<argumentos en JSON o texto>)\n\n"
    "Herramientas:\n"
    "- read_file(path) - lee un archivo\n"
    "- list_dir(path) - lista una carpeta\n"
    "- write_file(path, content) / edit_file(path, content) - sustituyen el archivo entero por content\n"
    "- run_command(command) - ejecuta un comando\n"
    "- git(command) - ejecuta git\n"
    "- finish(summary) - termina y entrega la respuesta"
)


def build_mission_system_prompt(ws_root: Optional[str]) -> str:
    """Prompt de sistema de una misión: entorno + formato de respuesta y herramientas. Nada más."""
    return f"Eres un agente de software autónomo.\n{describe_system(ws_root)}\n\n{RESPONSE_FORMAT}"
