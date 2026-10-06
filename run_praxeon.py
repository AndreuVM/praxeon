#!/usr/bin/env python3
"""Punto de Entrada Unificado y Orquestador de Arranque de PRAXEON (run_praxeon.py).

Consolida la inicialización de todos los subsistemas de la plataforma:
- modo 'all': Servidor FastAPI unificado (API + SPA Web) o backend + Vite en desarrollo.
- modo 'api': Servidor REST API y WebSockets sin frontend.
- modo 'web': Servidor FastAPI sirviendo la aplicación web interactiva con auto-apertura de navegador.
- modo 'cli': Interfaz de línea de comandos rica para análisis y benchmarks.
- modo 'dashboard': Dashboard interactivo en consola con Rich.
- modo 'mcp': Servidor de protocolo Model Context Protocol (MCP) para agentes externos.

Incluye verificación de puertos, carga automática de .env, perfiles de seguridad
y gestión limpia de señales de apagado (SIGINT/SIGTERM).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import List, Optional
import webbrowser

# Carga de variables de entorno si python-dotenv está disponible
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path)
except ImportError:
    pass


def is_port_in_use(host: str, port: int) -> bool:
    """Verifica si un puerto específico en un host ya se encuentra enlazado u ocupado."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            target_host = "127.0.0.1" if host in ("0.0.0.0", "localhost") else host
            result = s.connect_ex((target_host, port))
            return result == 0
        except OSError:
            return False


def find_available_port(host: str, start_port: int = 8000, max_attempts: int = 50) -> int:
    """Encuentra el primer puerto disponible comenzando desde start_port."""
    for p in range(start_port, start_port + max_attempts):
        if not is_port_in_use(host, p):
            return p
    raise RuntimeError(f"No se encontró ningún puerto libre en {host} entre {start_port} y {start_port + max_attempts - 1}.")


def configure_security_environment(profile: Optional[str] = None) -> str:
    """Configura y valida el perfil de seguridad del runtime."""
    active_profile = (profile or os.environ.get("PRAXEON_PROFILE") or os.environ.get("PRAXEON_ENV") or "dev").lower().strip()
    if profile:
        os.environ["PRAXEON_PROFILE"] = active_profile
    return active_profile


def run_api_mode(host: str, port: int, reload: bool = False) -> None:
    """Inicia el servidor backend Uvicorn / FastAPI."""
    import uvicorn
    from praxeon.server.app import validate_network_binding

    validate_network_binding(host)
    print(f"[PRAXEON] Iniciando servidor API en http://{host}:{port}")
    print(f"[PRAXEON] Documentacion OpenAPI en http://{host}:{port}/docs")
    uvicorn.run("praxeon.server.app:app", host=host, port=port, reload=reload)


def run_web_mode(host: str, port: int, no_browser: bool = False, reload: bool = False) -> None:
    """Inicia el servidor web unificado y opcionalmente abre el navegador."""
    import uvicorn
    from praxeon.server.app import validate_network_binding

    validate_network_binding(host)
    url = f"http://{host}:{port}"
    print("=" * 68)
    print("PRAXEON 1.0 -- Runtime Supervision & Agent Operations Platform")
    print(f"* Interfaz Web:     {url}")
    print(f"* Documentacion API: {url}/docs")
    print("=" * 68)

    if not no_browser:
        def _open():
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception as e:
                print(f"[PRAXEON] No se pudo abrir el navegador automáticamente: {e}")

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run("praxeon.server.app:app", host=host, port=port, reload=reload)


def run_all_dev_mode(host: str, port: int, no_browser: bool = False) -> None:
    """Modo desarrollo concurrente: API FastAPI + servidor Vite para SPA React."""
    repo_root = Path(__file__).resolve().parent
    web_dir = repo_root / "web"

    if not web_dir.is_dir():
        print(f"[PRAXEON] Directorio frontend '{web_dir}' no encontrado. Iniciando en modo 'web' integrado...")
        run_web_mode(host=host, port=port, no_browser=no_browser)
        return

    print("=" * 68)
    print("PRAXEON 1.0 -- Modo Desarrollo Dual (FastAPI + Vite HMR)")
    print(f"* Backend API: http://{host}:{port}")
    print("* Frontend SPA: http://localhost:5173 (Vite)")
    print("=" * 68)

    # Iniciar Vite
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    vite_proc = subprocess.Popen(
        [npm_cmd, "run", "dev"],
        cwd=str(web_dir),
        shell=(sys.platform == "win32"),
    )

    def _cleanup(signum=None, frame=None):
        print("\n[PRAXEON] Deteniendo servidores de desarrollo...")
        if vite_proc and vite_proc.poll() is None:
            vite_proc.terminate()
            try:
                vite_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                vite_proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    if not no_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open("http://localhost:5173")), daemon=True).start()

    try:
        import uvicorn
        uvicorn.run("praxeon.server.app:app", host=host, port=port, reload=True)
    finally:
        _cleanup()


def run_cli_mode(extra_args: List[str]) -> None:
    """Delega la ejecución al CLI nativo de PRAXEON."""
    from praxeon.cli import main as cli_main
    sys.argv = [sys.argv[0]] + extra_args
    cli_main()


def run_dashboard_mode(extra_args: List[str]) -> None:
    """Delega la ejecución al visualizador Rich interactivo de PRAXEON."""
    from praxeon.dashboard import main as dash_main
    sys.argv = [sys.argv[0]] + extra_args
    dash_main()


def run_mcp_mode(extra_args: List[str]) -> None:
    """Inicia el servidor Model Context Protocol (MCP) de PRAXEON."""
    from praxeon.integrations.mcp.server import main as mcp_main
    sys.argv = [sys.argv[0]] + extra_args
    mcp_main()


def build_parser() -> argparse.ArgumentParser:
    """Construye el analizador de argumentos de línea de comandos para run_praxeon.py."""
    parser = argparse.ArgumentParser(
        description="PRAXEON Unified Startup & Orchestration Runner",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-m", "--mode",
        choices=["all", "api", "web", "cli", "dashboard", "dash", "mcp"],
        default="all",
        help=(
            "Modo de operacion:\n"
            "  all       - Servidor unificado API + Web SPA (por defecto)\n"
            "  api       - Backend FastAPI REST y WebSockets exclusivamente\n"
            "  web       - Aplicacion web interactiva con apertura de navegador\n"
            "  cli       - Interfaz de comandos PRAXEON (analyze, live, simulate)\n"
            "  dashboard - Panel interactivo en terminal con Rich\n"
            "  mcp       - Servidor MCP para integracion con IDEs y agentes"
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host de escucha para servidores de red (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Puerto HTTP para el backend (default: 8000)")
    parser.add_argument("--auto-port", action="store_true", help="Buscar automáticamente el siguiente puerto libre si está ocupado")
    parser.add_argument("--profile", choices=["dev", "production", "test"], default=None, help="Perfil de seguridad (PRAXEON_PROFILE)")
    parser.add_argument("--no-browser", action="store_true", help="No abrir automáticamente el navegador web")
    parser.add_argument("--reload", action="store_true", help="Habilitar recarga automática de código (hot-reload)")
    parser.add_argument("--dev", action="store_true", help="En modo 'all', ejecutar backend y servidor Vite simultáneamente")
    parser.add_argument("--check-ports", action="store_true", help="Verificar estado de los puertos requeridos y salir")
    return parser


def main() -> None:
    """Punto de entrada principal."""
    parser = build_parser()
    args, extra_args = parser.parse_known_args()

    # Configurar perfil de seguridad
    active_profile = configure_security_environment(args.profile)

    # Diagnóstico de puertos si se solicita
    if args.check_ports:
        occupied = is_port_in_use(args.host, args.port)
        status_str = "[OCUPADO]" if occupied else "[LIBRE]"
        print(f"Puerto {args.host}:{args.port} -> {status_str}")
        sys.exit(1 if occupied else 0)

    # Modos que no requieren servidor HTTP
    if args.mode == "cli":
        run_cli_mode(extra_args)
        return
    elif args.mode in ("dashboard", "dash"):
        run_dashboard_mode(extra_args)
        return
    elif args.mode == "mcp":
        run_mcp_mode(extra_args)
        return

    # Verificación de puerto para modos de red
    effective_port = args.port
    if is_port_in_use(args.host, effective_port):
        if args.auto_port:
            effective_port = find_available_port(args.host, start_port=args.port + 1)
            print(f"[PRAXEON] Puerto {args.port} ocupado. Reasignado automáticamente a puerto {effective_port}.")
        else:
            print(
                f"[ERROR] El puerto {args.host}:{args.port} ya está en uso.\n"
                f"Sugerencias:\n"
                f"  - Especifica otro puerto con '--port <nuevo_puerto>'\n"
                f"  - Usa la opción '--auto-port' para asignación automática\n"
                f"  - Detén el proceso que actualmente ocupa el puerto {args.port}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Despacho de modos de servidor
    if args.mode == "api":
        run_api_mode(host=args.host, port=effective_port, reload=args.reload)
    elif args.mode == "web":
        run_web_mode(host=args.host, port=effective_port, no_browser=args.no_browser, reload=args.reload)
    elif args.mode == "all":
        if args.dev:
            run_all_dev_mode(host=args.host, port=effective_port, no_browser=args.no_browser)
        else:
            run_web_mode(host=args.host, port=effective_port, no_browser=args.no_browser, reload=args.reload)


if __name__ == "__main__":
    main()
