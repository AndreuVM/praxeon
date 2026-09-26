"""Gestor de conexiones WebSocket y streaming de eventos en tiempo real (praxeon/server/websocket.py).

Especificación PRAXEON 1.0 (Sección 3.1 y Sección 4).
Canal en tiempo real /v1/sessions/{session_id}/stream con:
1. Streaming continuo de RuntimeEvent ordenados por secuencia.
2. Recuperación de huecos (gap recovery) tras reconexión sin duplicación.
3. Limpieza garantizada de colas al desconectar el cliente.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from praxeon.domain.events import RuntimeEvent
from praxeon.server.dependencies import RuntimeApplicationService, get_runtime_service

logger = logging.getLogger("praxeon.server.websocket")
ws_router = APIRouter()


class WebSocketConnectionManager:
    """Administrador concurrente de conexiones WebSocket agrupadas por sesión."""

    def __init__(self):
        self._active_connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            if session_id not in self._active_connections:
                self._active_connections[session_id] = set()
            self._active_connections[session_id].add(websocket)

    async def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            if session_id in self._active_connections:
                self._active_connections[session_id].discard(websocket)
                if not self._active_connections[session_id]:
                    del self._active_connections[session_id]

    async def send_to_session(self, session_id: str, message: Dict[str, Any]) -> None:
        async with self._lock:
            conns = list(self._active_connections.get(session_id, []))

        text_data = json.dumps(message)
        for ws in conns:
            try:
                await ws.send_text(text_data)
            except Exception:
                pass


ws_manager = WebSocketConnectionManager()


@ws_router.websocket("/v1/sessions/{session_id}/stream")
async def websocket_session_stream(
    websocket: WebSocket,
    session_id: str,
):
    """Endpoint WebSocket para recibir en tiempo real los eventos de la sesión."""
    service = get_runtime_service()
    await ws_manager.connect(session_id, websocket)

    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    unregister = service.event_bus.register_async_queue(queue)

    try:
        # 1. Mensaje de bienvenida con sincronización inicial
        all_events = service.event_bus.get_all_events(session_id)
        await websocket.send_json({
            "action": "connected",
            "session_id": session_id,
            "event_count": len(all_events),
            "latest_sequence": all_events[-1].sequence if all_events else 0,
        })

        # 2. Tarea concurrente para escuchar mensajes entrantes del cliente (sync / ping)
        async def client_listener():
            while True:
                data_text = await websocket.receive_text()
                try:
                    msg = json.loads(data_text)
                    action = msg.get("action")
                    if action == "sync":
                        after_seq = int(msg.get("after_sequence", 0))
                        missing = service.event_bus.get_events(session_id, after_sequence=after_seq)
                        for ev in missing:
                            await websocket.send_json({
                                "action": "event",
                                "data": ev.to_dict(),
                            })
                    elif action == "ping":
                        await websocket.send_json({"action": "pong"})
                except Exception as ex:
                    logger.debug("Error procesando mensaje entrante WebSocket: %s", ex)

        # 3. Tarea concurrente para despachar eventos emitidos por el EventBus
        async def event_dispatcher():
            while True:
                event: RuntimeEvent = await queue.get()
                if event.session_id == session_id:
                    await websocket.send_json({
                        "action": "event",
                        "data": event.to_dict(),
                    })
                queue.task_done()

        listener_task = asyncio.create_task(client_listener())
        dispatcher_task = asyncio.create_task(event_dispatcher())

        done, pending = await asyncio.wait(
            [listener_task, dispatcher_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Excepción en conexión WebSocket para sesión '%s': %s", session_id, e)
    finally:
        unregister()
        await ws_manager.disconnect(session_id, websocket)
