"""Módulo de recuperación de errores de cuota/modelo y selección interactiva de LLM."""

import os
from typing import Optional, Tuple
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

AVAILABLE_MODELS = [
    ("groq:llama-3.3-70b-versatile", "Groq Cloud (Plan gratuito de 14.400 req/día y 30 RPM - Ultra rápido)", "GROQ_API_KEY"),
    ("groq:qwen-2.5-coder-32b", "Groq Cloud Qwen Coder (Especializado en programación)", "GROQ_API_KEY"),
    ("ollama:qwen2.5-coder:7b", "Ollama Local (100% local, offline, sin coste ni límites de cuota)", None),
    ("ollama:llama3.2:3b", "Ollama Local Llama 3.2 (Ultraligero en CPU o GPU local)", None),
    ("openrouter:qwen/qwen-2.5-coder-32b-instruct:free", "OpenRouter Free (Tier gratuito sin tarjeta)", "OPENROUTER_API_KEY"),
    ("gemini:gemini-3.6-flash", "Google Gemini 3.6 Flash (Sujeto a cuota Google)", "GEMINI_API_KEY"),
    ("gemini:gemma-4-26b-a4b-it", "Google Gemma 4 (26B Compacto)", "GEMINI_API_KEY"),
]


def prompt_model_recovery_menu(
    error_message: str,
    current_model: str,
    console: Optional[Console] = None,
) -> Tuple[str, Optional[str]]:
    """Muestra un menú interactivo cuando la cuota de un proveedor se ha excedido o hay error en el modelo.

    Retorna:
        Tuple[str, Optional[str]]:
            - ("change_model", "provider:model" o "model_name")
            - ("update_key", "KEY_NAME:new_key")
            - ("abort", None)
    """
    con = console or Console()

    options_text = ""
    for idx, (m_id, m_desc, _) in enumerate(AVAILABLE_MODELS, start=1):
        active_tag = " [bold cyan](actual)[/]" if (m_id == current_model or m_id.split(":", 1)[-1] == current_model) else ""
        options_text += f"  [bold cyan]{idx})[/] [white]{m_id}[/] - [dim]{m_desc}[/]{active_tag}\n"

    custom_opt = len(AVAILABLE_MODELS) + 1
    key_opt = len(AVAILABLE_MODELS) + 2
    abort_opt = len(AVAILABLE_MODELS) + 3

    options_text += f"  [bold cyan]{custom_opt})[/] [white]Introducir otro modelo o endpoint manualmente...[/]\n"
    options_text += f"  [bold cyan]{key_opt})[/] [white]Configurar o actualizar claves de API (GROQ_API_KEY, GEMINI_API_KEY, etc.)...[/]\n"
    options_text += f"  [bold red]{abort_opt})[/] [red]Cancelar y detener ejecución actual[/]"

    con.print("\n")
    con.print(Panel(
        f"[bold red]❌ Se ha producido un error o límite de cuota con el modelo actual ({current_model}):[/]\n"
        f"[yellow]{error_message}[/]\n\n"
        "[bold white]Selecciona un proveedor alternativo más generoso o un modelo local:[/]\n"
        f"{options_text}",
        title="⚠️ [bold yellow]Menú de Recuperación y Selección de Proveedor LLM[/]",
        border_style="yellow",
        padding=(1, 2),
    ))

    valid_choices = [str(i) for i in range(1, abort_opt + 1)]
    default_choice = "1"

    try:
        choice = Prompt.ask(
            "[bold cyan]👉 Elige una opción[/]",
            choices=valid_choices,
            default=default_choice,
            console=con,
        )
    except (KeyboardInterrupt, EOFError):
        return ("abort", None)

    choice_num = int(choice)
    if 1 <= choice_num <= len(AVAILABLE_MODELS):
        selected_target, _, req_env = AVAILABLE_MODELS[choice_num - 1]
        prov_part = selected_target.split(":", 1)[0]

        if req_env and not os.getenv(req_env):
            con.print(f"[yellow]El proveedor '{prov_part}' requiere la clave [bold]{req_env}[/].[/]")
            if prov_part == "groq":
                con.print("[dim]Puedes obtener una clave gratuita al instante en: https://console.groq.com/keys[/]")
            elif prov_part == "openrouter":
                con.print("[dim]Puedes obtener una clave gratuita en: https://openrouter.ai/keys[/]")
            try:
                new_key = Prompt.ask(f"[bold cyan]Introduce tu {req_env}[/]", console=con, password=True)
            except (KeyboardInterrupt, EOFError):
                return ("abort", None)
            new_key = (new_key or "").strip()
            if new_key:
                _persist_api_key_to_env(key_name=req_env, new_key=new_key)
                con.print(f"[green]✓ Clave {req_env} guardada en .env y activada.[/]\n")
            else:
                con.print("[red]No se introdujo ninguna clave. Cancelando selección.[/]\n")
                return ("abort", None)

        con.print(f"[green]✓ Cambiando a:[/] [bold white]{selected_target}[/]\n")
        return ("change_model", selected_target)
    elif choice_num == custom_opt:
        try:
            custom_name = Prompt.ask("[bold cyan]Introduce identificador (ej. 'groq:llama-3.3-70b-versatile' u 'ollama:qwen2.5-coder:7b')[/]", console=con)
        except (KeyboardInterrupt, EOFError):
            return ("abort", None)
        custom_name = (custom_name or "").strip()
        if custom_name:
            con.print(f"[green]✓ Modelo seleccionado:[/] [bold white]{custom_name}[/]\n")
            return ("change_model", custom_name)
        return ("abort", None)
    elif choice_num == key_opt:
        try:
            con.print("[bold cyan]¿Qué clave deseas actualizar?[/]")
            con.print(" 1) GROQ_API_KEY (Recomendada: 14.400 peticiones/día gratuitas)")
            con.print(" 2) GEMINI_API_KEY")
            con.print(" 3) OPENROUTER_API_KEY")
            con.print(" 4) OPENAI_API_KEY")
            k_choice = Prompt.ask("Opción", choices=["1", "2", "3", "4"], default="1", console=con)
            k_map = {"1": "GROQ_API_KEY", "2": "GEMINI_API_KEY", "3": "OPENROUTER_API_KEY", "4": "OPENAI_API_KEY"}
            target_env = k_map.get(k_choice, "GROQ_API_KEY")
            new_key = Prompt.ask(f"[bold cyan]Introduce el valor para {target_env}[/]", console=con, password=True)
        except (KeyboardInterrupt, EOFError):
            return ("abort", None)
        new_key = (new_key or "").strip()
        if new_key:
            _persist_api_key_to_env(key_name=target_env, new_key=new_key)
            con.print(f"[green]✓ Clave {target_env} guardada con éxito en .env.[/]\n")
            return ("update_key", f"{target_env}:{new_key}")
        return ("abort", None)
    else:
        con.print("[yellow]Ejecución cancelada por el usuario.[/]\n")
        return ("abort", None)


def _persist_api_key_to_env(new_key: str, key_name: str = "GEMINI_API_KEY", env_path: str = ".env") -> None:
    """Actualiza una variable de API key en el archivo .env y en os.environ con permisos seguros 0600."""
    os.environ[key_name] = new_key
    # Finding 17: Enforce 0600 permissions on .env file to prevent ambient secret reading by other local users
    if not os.path.exists(env_path):
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(env_path, flags, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            f.write(f"{key_name}={new_key}\n")
        try:
            os.chmod(env_path, 0o600)
        except OSError:
            pass
        return

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        prefix = f"{key_name}="
        key_found = False
        new_lines = []
        for line in lines:
            if line.strip().startswith(prefix):
                new_lines.append(f"{key_name}={new_key}\n")
                key_found = True
            else:
                new_lines.append(line)

        if not key_found:
            new_lines.append(f"{key_name}={new_key}\n")

        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(env_path, flags, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        try:
            os.chmod(env_path, 0o600)
        except OSError:
            pass
    except Exception:
        pass
