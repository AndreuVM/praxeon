"""Bus de Mensajería y Topología de Comunicación Multiagente (AgentMessageBus) (F5-02).

Implementa los canales y patrones de comunicación para sistemas multiagente:
- Topologías soportadas: HUB_AND_SPOKE, PIPELINE, HIERARCHICAL y MESH.
- Canales bidireccionales: Agent->Agent, Agent->PRAXEON, PRAXEON->Agent, Broadcast.
- Buzones dedicados por agente (AgentMailbox) con ordenación estricta por prioridad.
- Patrones: Request/Response asíncrono, delegación supervisada y suscripción a temas (topics).
"""

from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum
import heapq
import queue
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid

from praxeon.agents.protocol import AgentMessage, MessagePriority, MessageType


class TopologyType(str, Enum):
    """Topologías arquitectónicas de comunicación y coordinación."""
    HUB_AND_SPOKE = "HUB_AND_SPOKE"  # Todas las comunicaciones pasan por el supervisor/orquestador central
    PIPELINE = "PIPELINE"            # Flujo secuencial predefinido (ej. Research -> Dev -> Review)
    HIERARCHICAL = "HIERARCHICAL"    # Estructura jerárquica de árbol supervisor-subordinado
    MESH = "MESH"                    # Comunicación bidireccional completa punto a punto


class AgentMailbox:
    """Buzón de entrada seguro por agente con cola de prioridad."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        # heapq almacena tuplas (-prioridad, timestamp_epoch, counter, mensaje) para ordenación descendente
        self._queue: List[tuple] = []
        self._counter: int = 0

    def push(self, message: AgentMessage) -> None:
        """Introduce un mensaje en el buzón ordenado por prioridad."""
        prio_weight = -int(message.priority)  # Mayor prioridad = menor número negativo
        epoch = message.timestamp.timestamp()
        self._counter += 1
        heapq.heappush(self._queue, (prio_weight, epoch, self._counter, message))

    def pop(self) -> Optional[AgentMessage]:
        """Extrae el mensaje de mayor prioridad disponible."""
        if not self._queue:
            return None
        _, _, _, msg = heapq.heappop(self._queue)
        return msg

    def pop_matching(self, predicate: Callable[[AgentMessage], bool]) -> Optional[AgentMessage]:
        """Extrae el mensaje de mayor prioridad que satisfaga el predicado dado."""
        if not self._queue:
            return None
        sorted_items = sorted(self._queue)
        for item in sorted_items:
            msg = item[3]
            if predicate(msg):
                self._queue.remove(item)
                heapq.heapify(self._queue)
                return msg
        return None


    def peek(self) -> Optional[AgentMessage]:
        """Consulta el siguiente mensaje sin extraerlo."""
        if not self._queue:
            return None
        return self._queue[0][3]

    @property
    def count(self) -> int:
        return len(self._queue)

    def is_empty(self) -> bool:
        return len(self._queue) == 0


class AgentMessageBus:
    """Bus centralizado de mensajería multiagente en memoria con gobernanza topológica y criptográfica."""

    def __init__(
        self,
        default_topology: TopologyType = TopologyType.HUB_AND_SPOKE,
        supervisor_id: str = "praxeon_supervisor",
        shared_secret: Optional[str] = None,
        enforce_integrity: bool = False,
    ):
        self.default_topology = default_topology
        self.supervisor_id = supervisor_id
        self.shared_secret = shared_secret
        self.enforce_integrity = enforce_integrity
        self._mailboxes: Dict[str, AgentMailbox] = {}
        self._agent_topologies: Dict[str, TopologyType] = {}
        self._topic_subscriptions: Dict[str, Set[str]] = defaultdict(set)
        self._message_history: List[AgentMessage] = []
        self._pipeline_order: List[str] = []
        self._interceptors: List[Callable[[AgentMessage], Optional[AgentMessage]]] = []

        # Registrar automáticamente el supervisor central
        self.register_agent(self.supervisor_id, topology_type=TopologyType.MESH)

    def register_agent(
        self,
        agent_id: str,
        topology_type: Optional[TopologyType] = None,
    ) -> None:
        """Registra un agente en el bus y le asigna un buzón dedicado."""
        if agent_id not in self._mailboxes:
            self._mailboxes[agent_id] = AgentMailbox(agent_id=agent_id)
            self._agent_topologies[agent_id] = topology_type or self.default_topology

    def unregister_agent(self, agent_id: str) -> None:
        """Da de baja a un agente y remueve sus suscripciones."""
        self._mailboxes.pop(agent_id, None)
        self._agent_topologies.pop(agent_id, None)
        for subs in self._topic_subscriptions.values():
            subs.discard(agent_id)

    def set_pipeline_order(self, order: List[str]) -> None:
        """Establece la secuencia estricta para topología PIPELINE."""
        self._pipeline_order = list(order)
        for aid in order:
            self.register_agent(aid, topology_type=TopologyType.PIPELINE)

    def add_interceptor(self, interceptor: Callable[[AgentMessage], Optional[AgentMessage]]) -> None:
        """Añade un middleware de inspección o gobernanza que puede validar, mutar o descartar mensajes."""
        self._interceptors.append(interceptor)

    def validate_routing_topology(self, message: AgentMessage) -> bool:
        """Verifica si el mensaje respeta las restricciones de la topología activa."""
        sender = message.sender_id
        receiver = message.receiver_id

        # 1. Comunicaciones con el supervisor siempre están permitidas
        if sender == self.supervisor_id or receiver == self.supervisor_id:
            return True

        # 2. Alertas críticas de seguridad siempre fluyen al supervisor
        if message.message_type == MessageType.ALERT:
            return True

        # 3. Topología HUB_AND_SPOKE: agentes periféricos no pueden comunicarse directamente
        # Deben canalizar a través del supervisor
        sender_topo = self._agent_topologies.get(sender, self.default_topology)
        if sender_topo == TopologyType.HUB_AND_SPOKE and receiver != self.supervisor_id and receiver != "*":
            return False

        # 4. Topología PIPELINE: solo puede enviar al siguiente eslabón en la cadena
        if sender_topo == TopologyType.PIPELINE and self._pipeline_order:
            if sender in self._pipeline_order:
                idx = self._pipeline_order.index(sender)
                expected_next = self._pipeline_order[idx + 1] if idx + 1 < len(self._pipeline_order) else None
                if receiver != expected_next and receiver != self.supervisor_id:
                    return False

        return True

    def send(self, message: AgentMessage) -> bool:
        """Envía un mensaje al destinatario respetando topología, verificando integridad y entregando a su buzón."""
        # 0. Verificación criptográfica de integridad y firma (si están activadas)
        if self.enforce_integrity and not message.verify_integrity():
            return False
        if self.shared_secret and not message.verify_signature(self.shared_secret):
            return False

        # 1. Validar topología de enrutamiento
        if not self.validate_routing_topology(message):
            # Rechazado por restricción de topología
            return False

        # 2. Ejecutar interceptores de gobernanza (si alguno retorna None, el mensaje es bloqueado)
        processed_msg: Optional[AgentMessage] = message
        for interceptor in self._interceptors:
            if processed_msg is None:
                return False
            processed_msg = interceptor(processed_msg)

        if processed_msg is None:
            return False

        # 3. Registrar en historial
        self._message_history.append(processed_msg)

        # 4. Caso Broadcast: entregar a todos los buzones excepto al emisor
        if processed_msg.receiver_id == "*":
            delivered = 0
            for aid, box in self._mailboxes.items():
                if aid != processed_msg.sender_id:
                    box.push(processed_msg)
                    delivered += 1
            return delivered > 0

        # 5. Entrega punto a punto
        target_box = self._mailboxes.get(processed_msg.receiver_id)
        if not target_box:
            # Destinatario no registrado
            return False

        target_box.push(processed_msg)
        return True

    def receive(self, agent_id: str) -> Optional[AgentMessage]:
        """Extrae el siguiente mensaje prioritario del buzón de un agente."""
        box = self._mailboxes.get(agent_id)
        if not box:
            return None
        return box.pop()

    def peek(self, agent_id: str) -> Optional[AgentMessage]:
        """Consulta sin extraer el siguiente mensaje."""
        box = self._mailboxes.get(agent_id)
        if not box:
            return None
        return box.peek()

    def subscribe_topic(self, agent_id: str, topic: str) -> None:
        """Suscribe a un agente para recibir publicaciones de un tema."""
        self.register_agent(agent_id)
        self._topic_subscriptions[topic.lower()].add(agent_id)

    def publish_topic(
        self,
        sender_id: str,
        topic: str,
        payload: Dict[str, Any],
        session_id: str = "default_session",
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> int:
        """Publica un mensaje sobre un tema a todos los agentes suscriptores."""
        subscribers = self._topic_subscriptions.get(topic.lower(), set())
        delivered = 0

        for sub_id in subscribers:
            if sub_id != sender_id:
                msg = AgentMessage(
                    message_id=f"topic_{topic}_{uuid.uuid4().hex[:12]}_{delivered}",
                    sender_id=sender_id,
                    receiver_id=sub_id,
                    session_id=session_id,
                    message_type=MessageType.BROADCAST,
                    priority=priority,
                    payload={"topic": topic, **payload},
                )
                if self.shared_secret:
                    msg = msg.sign(self.shared_secret)
                if self.send(msg):
                    delivered += 1

        return delivered

    def get_history(
        self,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> List[AgentMessage]:
        """Consulta el historial de mensajes registrados con filtros opcionales."""
        msgs = list(self._message_history)
        if session_id:
            msgs = [m for m in msgs if m.session_id == session_id]
        if agent_id:
            msgs = [m for m in msgs if m.sender_id == agent_id or m.receiver_id in (agent_id, "*")]
        return msgs

    def acknowledge_message(
        self,
        message: AgentMessage,
        sender_id: str,
        note: Optional[str] = None,
    ) -> AgentMessage:
        """Emite una confirmación positiva (ACK) al remitente de un mensaje."""
        ack_msg = message.create_ack(sender_id=sender_id, note=note)
        if self.shared_secret:
            ack_msg = ack_msg.sign(self.shared_secret)
        self.send(ack_msg)
        return ack_msg

    def nack_message(
        self,
        message: AgentMessage,
        sender_id: str,
        reason: str,
        error_code: Optional[str] = None,
    ) -> AgentMessage:
        """Emite un rechazo o confirmación negativa (NACK) al remitente de un mensaje."""
        nack_msg = message.create_nack(sender_id=sender_id, reason=reason, error_code=error_code)
        if self.shared_secret:
            nack_msg = nack_msg.sign(self.shared_secret)
        self.send(nack_msg)
        return nack_msg

    def send_result(
        self,
        message: AgentMessage,
        sender_id: str,
        result_payload: Dict[str, Any],
    ) -> AgentMessage:
        """Emite un resultado estructurado (AGENT_RESULT) en respuesta a un mensaje."""
        res_msg = message.create_result(sender_id=sender_id, payload=result_payload)
        if self.shared_secret:
            res_msg = res_msg.sign(self.shared_secret)
        self.send(res_msg)
        return res_msg

    def receive_reply_for(
        self,
        sender_id: str,
        message_id: str,
        expected_types: Optional[Set[MessageType]] = None,
    ) -> Optional[AgentMessage]:
        """Extrae del buzón del emisor un mensaje que correlacione o responda al message_id."""
        box = self._mailboxes.get(sender_id)
        if not box:
            return None

        def matches(m: AgentMessage) -> bool:
            correlates = (m.correlation_id == message_id or m.in_reply_to == message_id)
            if not correlates:
                return False
            if expected_types and m.message_type not in expected_types:
                return False
            return True

        return box.pop_matching(matches)

    def send_and_wait_reply(
        self,
        message: AgentMessage,
        timeout_seconds: float = 2.0,
        poll_interval: float = 0.02,
        expected_types: Optional[Set[MessageType]] = None,
    ) -> Optional[AgentMessage]:
        """Envía un mensaje y espera de forma síncrona una respuesta o confirmación correlacionada."""
        if not self.send(message):
            return None

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            reply = self.receive_reply_for(
                sender_id=message.sender_id,
                message_id=message.message_id,
                expected_types=expected_types,
            )
            if reply is not None:
                return reply
            time.sleep(poll_interval)

        return None

    def send_with_retry(
        self,
        message: AgentMessage,
        max_retries: int = 3,
        retry_delay: float = 0.05,
        timeout_per_try: float = 0.5,
    ) -> Optional[AgentMessage]:
        """Envía un mensaje con política de reintentos ante timeout o NACK."""
        for attempt in range(max_retries):
            reply = self.send_and_wait_reply(
                message=message,
                timeout_seconds=timeout_per_try,
            )
            if reply is not None:
                if reply.message_type != MessageType.AGENT_NACK:
                    return reply
            if attempt < max_retries - 1 and retry_delay > 0:
                time.sleep(retry_delay)

        return None

