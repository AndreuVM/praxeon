"""Gestor de conexiones WebSocket y streaming de eventos en tiempo real (praxeon/server/websocket.py).

Especificación PRAXEON 1.0 (Sección 3.1 y Sección 4).
Canal en tiempo real /v1/sessions/{session_id}/stream con:
1. Streaming continuo de RuntimeEvent ordenados por secuencia.
2. Recuperación de huecos (gap recovery) tras reconexión sin duplicación.
3. Limpieza garantizada de colas al desconectar el cliente.
"""

import asyncio
from datetime import date, datetime
from enum import Enum
import json
import logging
from typing import Any, Dict, Optional, Set
import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import os
import secrets
from starlette.status import WS_1008_POLICY_VIOLATION
from praxeon.domain.events import RuntimeEvent
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    get_runtime_service,
    is_auth_required,
)

logger = logging.getLogger("praxeon.server.websocket")
ws_router = APIRouter()



def safe_json_dumps(data: Any) -> str:
    """Serializa estructuras de datos a JSON de forma segura ante datetime, UUID, Enum o modelos."""
    def _default(obj: Any) -> Any:
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.value
        if hasattr(obj, "model_dump") and callable(obj.model_dump):
            try:
                return obj.model_dump(mode="json")
            except Exception:
                return obj.model_dump()
        if hasattr(obj, "to_dict") and callable(obj.to_dict):
            return obj.to_dict()
        return str(obj)

    return json.dumps(data, default=_default, separators=(",", ":"), ensure_ascii=False)


async def send_ws_json(websocket: WebSocket, data: Any) -> None:
    """Envía un payload codificado en JSON por WebSocket de forma a prueba de fallos."""
    text = safe_json_dumps(data)
    await websocket.send_text(text)


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

        text_data = safe_json_dumps(message)
        for ws in conns:
            try:
                await ws.send_text(text_data)
            except Exception:
                pass


ws_manager = WebSocketConnectionManager()


from urllib.parse import urlparse

def _is_origin_allowed(websocket: WebSocket) -> bool:
    """Verifica si la cabecera Origin del WebSocket es legítima para prevenir CSWSH (Finding 20)."""
    origin = websocket.headers.get("origin")
    if not origin:
        # Clientes no-navegador (CLI, tests, curl)
        return True

    allowed_env = os.environ.get("PRAXEON_ALLOWED_ORIGINS")
    if allowed_env:
        if allowed_env.strip() == "*":
            return True
        allowed_list = [o.strip().lower() for o in allowed_env.split(",") if o.strip()]
        if origin.lower() in allowed_list:
            return True

    try:
        parsed = urlparse(origin)
        origin_host = (parsed.hostname or "").lower()
    except Exception:
        return False

    # Permitir loopback local y clientes de prueba
    if origin_host in ("localhost", "127.0.0.1", "::1", "testserver"):
        return True

    # Permitir si coincide con el host del propio servidor
    host_header = websocket.headers.get("host", "").split(":")[0].lower()
    if host_header and origin_host == host_header:
        return True

    return False


@ws_router.websocket("/v1/sessions/{session_id}/stream")
@ws_router.websocket("/ws/{session_id}")
async def websocket_session_stream(
    websocket: WebSocket,
    session_id: str,
    after_sequence: Optional[int] = None,
    token: Optional[str] = None,
):
    """Endpoint WebSocket para recibir en tiempo real los eventos de la sesión con gap recovery."""
    # 0. Finding 20: Prevención de CSWSH (Cross-Site WebSocket Hijacking)
    if not _is_origin_allowed(websocket):
        logger.warning(
            "Fallo de seguridad evitado: Conexión WebSocket rechazada por Origin no autorizado (CSWSH): %s",
            websocket.headers.get("origin"),
        )
        await websocket.close(code=WS_1008_POLICY_VIOLATION, reason="Origin no permitido.")
        return

    # 1. Verificación de autenticación de WebSocket
    app_profile = getattr(websocket.app.state, "security_profile", None) if (hasattr(websocket, "app") and hasattr(websocket.app, "state")) else None
    client_host = websocket.client.host if websocket.client else None
    if is_auth_required(profile=app_profile, client_host=client_host):
        expected_key = os.environ.get("PRAXEON_API_KEY") or os.environ.get("PRAXEON_SECRET_KEY")
        if not expected_key:
            logger.error("Fallo de seguridad evitado en WS: Autenticación requerida pero no hay PRAXEON_API_KEY configurada.")
            await websocket.close(code=WS_1008_POLICY_VIOLATION, reason="Servidor sin clave configurada.")
            return

        auth_token = token or websocket.query_params.get("token")
        if not auth_token:
            auth_hdr = websocket.headers.get("x-api-key") or websocket.headers.get("authorization")
            if auth_hdr:
                if auth_hdr.startswith("Bearer "):
                    auth_token = auth_hdr[7:].strip()
                else:
                    auth_token = auth_hdr.strip()
        if not auth_token or not secrets.compare_digest(auth_token, expected_key):
            await websocket.close(code=WS_1008_POLICY_VIOLATION, reason="Autenticacion requerida o token invalido.")
            return

    service = get_runtime_service()
    await ws_manager.connect(session_id, websocket)


    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    unregister = service.event_bus.register_async_queue(queue, session_id=session_id)
    sent_event_ids: Set[str] = set()

    try:
        # 1. Determinar after_sequence inicial
        after_seq_val: Optional[int] = after_sequence
        if after_seq_val is None and "after_sequence" in websocket.query_params:
            try:
                after_seq_val = int(websocket.query_params["after_sequence"])
            except Exception:
                after_seq_val = None

        all_events = service.event_bus.get_all_events(session_id)
        latest_seq = all_events[-1].sequence if all_events else 0

        # Mensaje de bienvenida con sincronización inicial
        await send_ws_json(websocket, {
            "action": "connected",
            "session_id": session_id,
            "event_count": len(all_events),
            "latest_sequence": latest_seq,
        })

        # Gap recovery inicial: si after_sequence fue especificado, enviar eventos posteriores
        if after_seq_val is not None:
            catchup_events = service.event_bus.get_events(session_id, after_sequence=after_seq_val)
            for ev in catchup_events:
                sent_event_ids.add(ev.event_id)
                await send_ws_json(websocket, {
                    "action": "event",
                    "data": ev.to_dict(),
                })

        # 2. Tarea concurrente para escuchar mensajes entrantes del cliente (sync / ping)
        async def client_listener():
            while True:
                try:
                    data_text = await websocket.receive_text()
                    msg = json.loads(data_text)
                    action = msg.get("action")
                    if action == "sync":
                        after_seq = int(msg.get("after_sequence", 0))
                        missing = service.event_bus.get_events(session_id, after_sequence=after_seq)
                        for ev in missing:
                            if ev.event_id not in sent_event_ids:
                                sent_event_ids.add(ev.event_id)
                                await send_ws_json(websocket, {
                                    "action": "event",
                                    "data": ev.to_dict(),
                                })
                    elif action == "ping":
                        await send_ws_json(websocket, {"action": "pong"})
                except (WebSocketDisconnect, asyncio.CancelledError):
                    break
                except Exception as ex:
                    logger.debug("Error procesando mensaje entrante WebSocket: %s", ex)

        # 3. Tarea concurrente para despachar eventos emitidos por el EventBus
        async def event_dispatcher():
            while True:
                try:
                    event: RuntimeEvent = await queue.get()
                except (asyncio.CancelledError, GeneratorExit):
                    break

                try:
                    if event.session_id == session_id and event.event_id not in sent_event_ids:
                        sent_event_ids.add(event.event_id)
                        await send_ws_json(websocket, {
                            "action": "event",
                            "data": event.to_dict(),
                        })
                except (WebSocketDisconnect, asyncio.CancelledError):
                    queue.task_done()
                    break
                except Exception as ex:
                    logger.error("Error despachando evento por WebSocket: %s", ex, exc_info=True)
                finally:
                    try:
                        queue.task_done()
                    except ValueError:
                        pass

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
