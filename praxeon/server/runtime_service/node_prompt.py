"""Prompt y reglas de un agente que trabaja como NODO de un workflow (no como asistente de respuesta directa)."""

from __future__ import annotations

import re

NODE_PROMPT_TEMPLATE = """Eres un agente que trabaja como UN NODO de un workflow orquestado por PRAXEON. Tu misión, el objetivo global y lo que te entregan los nodos anteriores están en el BRIEFING de más abajo: léelo y cúmplelo.
DIRECTORIO DE TRABAJO: '{workspace}'

{env_info}

=== BRIEFING DE TU NODO ===
{briefing}
=== FIN DEL BRIEFING ===

FORMATO DE RESPUESTA EN CADA TURNO (estricto):
Thought: <una frase con tu siguiente paso>
Action: <herramienta>(<argumentos en JSON>)
Escribe un único Thought y una única Action por turno. No escribas nada después de la Action ni inventes observaciones: la observación te la devuelve el sistema.

Herramientas (no existe ninguna otra; no inventes nombres):
- list_dir(path) -> lista una carpeta (hasta 3 niveles). Úsala para ver qué hay realmente.
- read_file(path) -> lee un archivo existente.
- write_file(path, content) -> CREA o REEMPLAZA un archivo con el texto COMPLETO de `content` (crea las carpetas necesarias). Para escribir código nuevo usa esta herramienta, un archivo por llamada.
- edit_file(path, content) -> igual que write_file (sustituye el contenido completo del archivo).
- run_command(command) -> ejecuta un comando en la consola (PowerShell en Windows). Para instalar, compilar o ejecutar tests.
- git(command) -> comandos git.
- finish(summary) -> entrega el RESULTADO de tu nodo.

REGLAS:
1. Cumple la misión de tu nodo con trabajo real. Si la misión pide implementar, escribe los archivos con write_file dentro del directorio de trabajo y comprueba el resultado (list_dir, ejecutar tests...) antes de terminar. No te limites a leer ni a listar el directorio.
2. Inspecciona solo lo necesario para tu misión. Una ruta que no hayas visto con list_dir o read_file puede no existir: no la des por supuesta. Si un nodo anterior debía crear archivos y no los ves, dilo en tu resultado en lugar de inventarlos.
3. Usa finish solo cuando el trabajo esté HECHO. El summary es el resultado completo para el siguiente agente: qué archivos creaste o modificaste (con sus rutas) y lo esencial, o el veredicto que se te pide. Nunca uses finish para anunciar lo que harás después.
4. Si el supervisor rechaza una acción, lee el motivo y cambia de enfoque; no repitas la misma acción.
5. En Windows usa PowerShell (Get-ChildItem, Get-Content...), no ls/cat/grep.
"""

NODE_FORMAT_FEEDBACK = (
    "🚨 [SUPERVISOR PRAXEON - FORMATO]: tu respuesta no contenía una Action válida, así que no se ha ejecutado nada.\n"
    "Responde SOLO con:\nThought: <qué vas a hacer>\nAction: <herramienta>(<argumentos en JSON>)\n"
    "Herramientas: list_dir, read_file, write_file, edit_file, run_command, git, finish. "
    "Si ya terminaste tu misión, usa finish(summary) con el resultado completo."
)

NODE_INTENTION_FEEDBACK = (
    "🚨 [SUPERVISOR PRAXEON - FINISH PREMATURO]: tu finish anuncia lo que harás en lugar de entregar el resultado de tu misión.\n"
    "finish cierra tu nodo: el siguiente agente solo recibirá ese texto. Continúa trabajando con las herramientas "
    "(por ejemplo write_file para crear código) y usa finish cuando el trabajo esté hecho, describiendo qué has creado."
)

_INTENTION_START = re.compile(
    r"^\s*(?:ahora\b|voy a\b|a continuaci[oó]n\b|proceder[eé]\b|inspeccionar[eé]\b|revisar[eé]\b|empezar[eé]\b|"
    r"comenzar[eé]\b|primero\b|lo primero\b|let me\b|i will\b|i'll\b|now i\b|next,)",
    re.IGNORECASE,
)


def build_node_system_prompt(workspace: str, env_info: str, briefing: str) -> str:
    # Se concatena (no .format sobre el briefing) para que las llaves del texto del usuario no rompan nada
    head, tail = NODE_PROMPT_TEMPLATE.split("{briefing}")
    return head.format(workspace=workspace, env_info=env_info) + briefing + tail


def looks_like_intention(summary: str) -> bool:
    """True si el texto de un finish es un anuncio de lo que se hará («Ahora inspeccionaré…») y no un resultado."""
    text = (summary or "").strip()
    return bool(text) and len(text) < 600 and bool(_INTENTION_START.match(text))
