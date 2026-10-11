"""Notificaciones nativas de aprobaciones pendientes para la aplicación de escritorio.

`ApprovalNotifier` escucha el bus de eventos del runtime: cuando algo necesita aprobación humana
(`approval.requested`: decisiones en REVIEW, nodos de workflow en espera, herramientas que exigen
confirmación) muestra una notificación del sistema y mantiene un contador de pendientes. El envío
usa utilidades del propio sistema operativo, sin dependencias adicionales.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Protocol, Set, Tuple
from xml.sax.saxutils import escape

from praxeon.domain.events import EventType, RuntimeEvent

logger = logging.getLogger("praxeon.desktop.notifications")

APP_NAME = "PRAXEON"
# AppUserModelID de Windows PowerShell: permite mostrar toasts sin registrar un acceso directo propio.
_POWERSHELL_APP_ID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"


class Notifier(Protocol):
    """Destino de las notificaciones (sistema operativo, pruebas, etc.)."""

    def notify(self, title: str, body: str) -> None: ...


class NullNotifier:
    """No hace nada (notificaciones desactivadas)."""

    def notify(self, title: str, body: str) -> None:
        return None


class RecordingNotifier:
    """Guarda las notificaciones recibidas (útil en pruebas)."""

    def __init__(self) -> None:
        self.messages: List[Tuple[str, str]] = []

    def notify(self, title: str, body: str) -> None:
        self.messages.append((title, body))


def _windows_toast_script(title: str, body: str) -> str:
    """Script de PowerShell que muestra un toast con el XML de plantilla estándar (texto escapado)."""
    xml = (
        "<toast><visual><binding template=\"ToastGeneric\">"
        f"<text>{escape(title)}</text><text>{escape(body)}</text>"
        "</binding></visual></toast>"
    )
    # Dentro de un literal entre comillas simples de PowerShell, la comilla simple se duplica.
    xml_ps = xml.replace("'", "''")
    return (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
        "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$doc.LoadXml('{xml_ps}');"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($doc);"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_POWERSHELL_APP_ID}').Show($toast)"
    )


class SystemNotifier:
    """Notificaciones del sistema: toast en Windows, `osascript` en macOS y `notify-send` en Linux."""

    def __init__(self, platform: Optional[str] = None) -> None:
        self.platform = platform or sys.platform

    def _command(self, title: str, body: str) -> Optional[List[str]]:
        if self.platform.startswith("win"):
            return [
                "powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                "-Command", _windows_toast_script(title, body),
            ]
        if self.platform == "darwin":
            safe_title = title.replace("\\", "\\\\").replace("\"", "\\\"")
            safe_body = body.replace("\\", "\\\\").replace("\"", "\\\"")
            return ["osascript", "-e", f'display notification "{safe_body}" with title "{safe_title}"']
        if shutil.which("notify-send"):
            return ["notify-send", "--app-name", APP_NAME, title, body]
        return None

    def notify(self, title: str, body: str) -> None:
        cmd = self._command(title, body)
        if cmd is None:
            logger.info("Sin mecanismo de notificación disponible: %s - %s", title, body)
            return
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if self.platform.startswith("win") else 0
        try:
            subprocess.Popen(  # noqa: S603 - argumentos en lista, sin shell; el texto se escapa por plataforma
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError:
            logger.warning("No se pudo mostrar la notificación del sistema", exc_info=True)


def describe_event(event: RuntimeEvent) -> Tuple[str, str]:
    """Título y cuerpo legibles para un evento `approval.requested`."""
    payload: Dict[str, Any] = event.payload or {}
    if payload.get("execution_id") or payload.get("source") == "workflow":
        subject = payload.get("node_name") or payload.get("tool") or event.node_id or "nodo"
        detail = payload.get("prompt") or payload.get("reason") or "Un nodo del workflow espera tu aprobación."
        return "Aprobación requerida · Workflow", f"{subject}: {detail}"
    reasons = payload.get("reasons") or payload.get("reason_code") or []
    if isinstance(reasons, str):
        reasons = [reasons]
    detail = str(reasons[0]) if reasons else "Una acción necesita confirmación humana."
    level = payload.get("risk_level")
    suffix = f" (riesgo {level})" if level else ""
    return "Aprobación requerida", f"{detail}{suffix}"


class ApprovalNotifier:
    """Observa el bus de eventos, avisa de nuevas aprobaciones y lleva la cuenta de pendientes."""

    def __init__(
        self,
        notifier: Notifier,
        on_pending_changed: Optional[Callable[[int], None]] = None,
        dedupe_seconds: float = 30.0,
    ) -> None:
        self.notifier = notifier
        self.on_pending_changed = on_pending_changed
        self.dedupe_seconds = dedupe_seconds
        self._pending: Set[Tuple[str, str]] = set()
        self._recent: Dict[Tuple[str, str], float] = {}
        self._lock = threading.Lock()
        self._unsubscribe: Optional[Callable[[], None]] = None

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def attach(self, event_bus: Any) -> None:
        """Se suscribe al bus (un único suscriptor síncrono; no bloquea al emisor)."""
        self.detach()
        self._unsubscribe = event_bus.subscribe(self.handle_event)

    def detach(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def handle_event(self, event: RuntimeEvent) -> None:
        try:
            if event.type == EventType.APPROVAL_REQUESTED:
                self._on_requested(event)
            elif event.type == EventType.APPROVAL_COMPLETED:
                self._on_completed(event)
        except Exception:  # un fallo de notificación nunca debe afectar al runtime
            logger.warning("Error procesando evento de aprobación", exc_info=True)

    def _key(self, event: RuntimeEvent) -> Tuple[str, str]:
        return (event.session_id, event.node_id or event.decision_id or "")

    def _on_requested(self, event: RuntimeEvent) -> None:
        key = self._key(event)
        now = time.monotonic()
        with self._lock:
            is_new = key not in self._pending
            self._pending.add(key)
            last = self._recent.get(key)
            duplicated = last is not None and (now - last) < self.dedupe_seconds
            self._recent[key] = now
            count = len(self._pending)
        self._emit_count(count)
        if is_new and not duplicated:
            title, body = describe_event(event)
            self.notifier.notify(title, body)

    def _on_completed(self, event: RuntimeEvent) -> None:
        key = self._key(event)
        with self._lock:
            self._pending.discard(key)
            count = len(self._pending)
        self._emit_count(count)

    def _emit_count(self, count: int) -> None:
        if self.on_pending_changed:
            self.on_pending_changed(count)
