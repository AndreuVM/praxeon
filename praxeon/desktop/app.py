"""Aplicación de escritorio de PRAXEON.

Arranca el servidor en `127.0.0.1` (puerto libre elegido por el sistema) con un token aleatorio
por ejecución y abre la interfaz en una ventana nativa (pywebview). Solo la ventana conoce el
token, de modo que ningún otro proceso o navegador puede usar la API aunque encuentre el puerto.

    praxeon app              # ventana de escritorio
    praxeon app --browser    # navegador por defecto (si pywebview no está disponible)
"""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

import uvicorn

APP_TITLE = "PRAXEON"
LOOPBACK = "127.0.0.1"


@dataclass
class LocalServer:
    """Servidor uvicorn ejecutándose en un hilo, con su token de sesión."""

    server: uvicorn.Server
    thread: threading.Thread
    host: str
    port: int
    token: str
    saved_env: Dict[str, Optional[str]] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def window_url(self) -> str:
        # El token viaja en el fragmento (#): no se envía al servidor ni queda en logs de acceso.
        return f"{self.base_url}/#token={quote(self.token, safe='')}"

    def stop(self, timeout: float = 5.0) -> None:
        """Detiene el servidor y restaura las variables de entorno modificadas al arrancar."""
        self.server.should_exit = True
        self.thread.join(timeout=timeout)
        for name, previous in self.saved_env.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        self.saved_env.clear()


def find_free_port(host: str = LOOPBACK) -> int:
    """Pide al sistema un puerto TCP libre en la interfaz indicada."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def start_local_server(port: Optional[int] = None, startup_timeout: float = 20.0) -> LocalServer:
    """Arranca el servidor local autenticado con un token efímero y espera a que esté listo."""
    token = secrets.token_urlsafe(32)
    chosen = port or find_free_port()
    overrides = {
        # La autenticación queda activa (PRAXEON_API_KEY presente) y los JWT se firman con un secreto efímero.
        "PRAXEON_API_KEY": token,
        "PRAXEON_SECRET_KEY": secrets.token_urlsafe(48),
        # Permite el origen de la propia ventana (CORS/WebSocket) sin abrir nada más.
        "PRAXEON_CORS_ORIGINS": f"http://{LOOPBACK}:{chosen}",
    }
    saved_env = {name: os.environ.get(name) for name in overrides}
    os.environ.update(overrides)

    from praxeon.server.app import create_app, validate_network_binding

    validate_network_binding(LOOPBACK)
    # La app se crea después de fijar el entorno para que CORS y la autenticación lo recojan.
    config = uvicorn.Config(
        create_app(),
        host=LOOPBACK,
        port=chosen,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="praxeon-local-server", daemon=True)
    thread.start()

    deadline = time.time() + startup_timeout
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("El servidor local de PRAXEON terminó durante el arranque.")
        if time.time() > deadline:
            server.should_exit = True
            for name, previous in saved_env.items():
                if previous is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = previous
            raise TimeoutError("Tiempo de espera agotado arrancando el servidor local de PRAXEON.")
        time.sleep(0.05)

    return LocalServer(server=server, thread=thread, host=LOOPBACK, port=chosen, token=token, saved_env=saved_env)


def frontend_available() -> bool:
    """Indica si existe la interfaz compilada (paquete o web/dist)."""
    server_dir = Path(__file__).resolve().parent.parent / "server"
    candidates = [
        server_dir / "static" / "index.html",
        server_dir.parent.parent / "web" / "dist" / "index.html",
    ]
    return any(p.is_file() for p in candidates)


def attach_notifications(window_getter=None, notifier=None):
    """Conecta las notificaciones de aprobación al bus de eventos del runtime en ejecución.

    Si se pasa `window_getter`, el título de la ventana muestra el nº de aprobaciones pendientes.
    """
    from praxeon.desktop.notifications import ApprovalNotifier, SystemNotifier
    from praxeon.server.dependencies import get_runtime_service

    def update_title(count: int) -> None:
        window = window_getter() if window_getter else None
        if window is not None:
            try:
                window.set_title(f"{APP_TITLE} · {count} pendiente(s)" if count else APP_TITLE)
            except Exception:  # la ventana puede estar cerrándose
                pass

    approval_notifier = ApprovalNotifier(notifier or SystemNotifier(), on_pending_changed=update_title)
    approval_notifier.attach(get_runtime_service().event_bus)
    return approval_notifier


def run_window(local: LocalServer, debug: bool = False, notifications: bool = True) -> None:
    """Muestra la interfaz en una ventana nativa. Bloquea hasta que se cierra."""
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError(
            "Falta pywebview. Instálalo con:  pip install \"praxeon[desktop]\"  "
            "(o usa  praxeon app --browser)."
        ) from exc

    class DesktopApi:
        """Funciones nativas que la interfaz puede invocar (window.pywebview.api)."""

        def pick_folder(self, start: str = "") -> Optional[str]:
            """Abre el selector de carpetas del sistema. Devuelve la ruta o None si se cancela."""
            dialog_type = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
            if dialog_type is None:
                dialog_type = webview.FOLDER_DIALOG
            start_dir = start if start and os.path.isdir(start) else ""
            result = window.create_file_dialog(dialog_type, directory=start_dir)
            return str(result[0]) if result else None

    window = webview.create_window(
        APP_TITLE,
        local.window_url,
        js_api=DesktopApi(),
        width=1440,
        height=900,
        min_size=(1024, 680),
        text_select=True,
    )
    approval_notifier = attach_notifications(lambda: window) if notifications else None
    try:
        webview.start(debug=debug)
    finally:
        if approval_notifier:
            approval_notifier.detach()


def run_browser(local: LocalServer, notifications: bool = True) -> None:
    """Alternativa sin pywebview: abre el navegador por defecto y espera a Ctrl+C."""
    import webbrowser

    approval_notifier = attach_notifications() if notifications else None
    webbrowser.open(local.window_url)
    print(f"[PRAXEON] Interfaz en {local.base_url} (token válido solo en esta sesión). Ctrl+C para salir.")
    try:
        while local.thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if approval_notifier:
            approval_notifier.detach()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PRAXEON — aplicación de escritorio")
    parser.add_argument("--browser", action="store_true", help="Usar el navegador por defecto en lugar de la ventana nativa")
    parser.add_argument("--port", type=int, default=None, help="Puerto local fijo (por defecto, uno libre)")
    parser.add_argument("--debug", action="store_true", help="Habilitar herramientas de desarrollo de la ventana")
    parser.add_argument("--no-notifications", action="store_true", help="Desactivar las notificaciones de aprobaciones pendientes")
    args = parser.parse_args(argv)

    if not frontend_available():
        print(
            "[PRAXEON] No se encuentra la interfaz compilada. Ejecuta:  cd web && npm ci && npm run build",
            file=sys.stderr,
        )
        return 2

    local = start_local_server(port=args.port)
    try:
        if args.browser:
            run_browser(local, notifications=not args.no_notifications)
        else:
            run_window(local, debug=args.debug, notifications=not args.no_notifications)
    except RuntimeError as exc:
        print(f"[PRAXEON] {exc}", file=sys.stderr)
        return 1
    finally:
        local.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
