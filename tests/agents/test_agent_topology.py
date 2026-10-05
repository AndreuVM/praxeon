"""Pruebas unitarias para Topología y Patrones de Mensajería Multiagente (Fase 5 - F5-02).

Valida:
- AgentMailbox con cola de prioridad estricta (CRITICAL > HIGH > NORMAL > LOW).
- AgentMessageBus con enrutamiento punto a punto y broadcast.
- Reglas topológicas: HUB_AND_SPOKE, PIPELINE, MESH.
- Canales bidireccionales con supervisor PRAXEON.
- Mecanismo Publish/Subscribe sobre temas (topics).
- Middleware de interceptores para inspección y descarte.
"""

import pytest

from praxeon.agents import (
    AgentMailbox,
    AgentMessage,
    AgentMessageBus,
    MessagePriority,
    MessageType,
    TopologyType,
)


def test_agent_mailbox_priority_ordering():
    """Valida que AgentMailbox despache con prioridad estricta (CRITICAL antes que LOW)."""
    mailbox = AgentMailbox(agent_id="agent_worker")

    msg_low = AgentMessage(
        message_id="m_low",
        sender_id="praxeon_supervisor",
        receiver_id="agent_worker",
        session_id="s1",
        priority=MessagePriority.LOW,
        payload={"task": "background_cleanup"},
    )
    msg_critical = AgentMessage(
        message_id="m_crit",
        sender_id="praxeon_supervisor",
        receiver_id="agent_worker",
        session_id="s1",
        priority=MessagePriority.CRITICAL,
        payload={"task": "mitigate_injection"},
    )
    msg_high = AgentMessage(
        message_id="m_high",
        sender_id="praxeon_supervisor",
        receiver_id="agent_worker",
        session_id="s1",
        priority=MessagePriority.HIGH,
        payload={"task": "fix_lint"},
    )

    # Insertar en orden desordenado
    mailbox.push(msg_low)
    mailbox.push(msg_critical)
    mailbox.push(msg_high)

    assert mailbox.count == 3
    assert not mailbox.is_empty()

    # Peek debe ver CRITICAL
    peeked = mailbox.peek()
    assert peeked is not None
    assert peeked.message_id == "m_crit"

    # Pop debe extraer en orden: CRITICAL -> HIGH -> LOW
    first = mailbox.pop()
    assert first is not None
    assert first.priority == MessagePriority.CRITICAL

    second = mailbox.pop()
    assert second is not None
    assert second.priority == MessagePriority.HIGH

    third = mailbox.pop()
    assert third is not None
    assert third.priority == MessagePriority.LOW

    assert mailbox.pop() is None
    assert mailbox.is_empty()


def test_bus_point_to_point_and_broadcast():
    """Valida el envío punto a punto y la difusión broadcast en topología MESH."""
    bus = AgentMessageBus(default_topology=TopologyType.MESH)
    bus.register_agent("ag_alpha")
    bus.register_agent("ag_beta")
    bus.register_agent("ag_gamma")

    # Envío punto a punto
    p2p_msg = AgentMessage(
        message_id="p2p_01",
        sender_id="ag_alpha",
        receiver_id="ag_beta",
        session_id="s1",
        payload={"ping": "pong"},
    )
    assert bus.send(p2p_msg) is True

    # Comprobar recepción en beta
    received_beta = bus.receive("ag_beta")
    assert received_beta is not None
    assert received_beta.message_id == "p2p_01"
    assert bus.receive("ag_gamma") is None

    # Broadcast
    bcast_msg = AgentMessage(
        message_id="bcast_01",
        sender_id="ag_alpha",
        receiver_id="*",
        session_id="s1",
        message_type=MessageType.BROADCAST,
        payload={"announcement": "team_sync"},
    )
    assert bus.send(bcast_msg) is True

    # El emisor no debe recibir su propio broadcast
    assert bus.receive("ag_alpha") is None
    # Los demás sí
    assert bus.receive("ag_beta") is not None
    assert bus.receive("ag_gamma") is not None


def test_bus_hub_and_spoke_restrictions():
    """Valida que en HUB_AND_SPOKE los agentes periféricos no puedan hablar directamente sin supervisor."""
    bus = AgentMessageBus(
        default_topology=TopologyType.HUB_AND_SPOKE,
        supervisor_id="praxeon_supervisor",
    )
    bus.register_agent("worker_1")
    bus.register_agent("worker_2")

    # Envío directo entre workers debe ser bloqueado por topología
    direct_msg = AgentMessage(
        message_id="direct_blocked",
        sender_id="worker_1",
        receiver_id="worker_2",
        session_id="s1",
        payload={"secret": "direct_comm"},
    )
    assert bus.send(direct_msg) is False
    assert bus.receive("worker_2") is None

    # Worker a Supervisor -> Permitido
    to_sup_msg = AgentMessage(
        message_id="to_sup",
        sender_id="worker_1",
        receiver_id="praxeon_supervisor",
        session_id="s1",
        payload={"status": "request_assistance"},
    )
    assert bus.send(to_sup_msg) is True
    assert bus.receive("praxeon_supervisor") is not None

    # Supervisor a Worker -> Permitido
    from_sup_msg = AgentMessage(
        message_id="from_sup",
        sender_id="praxeon_supervisor",
        receiver_id="worker_2",
        session_id="s1",
        payload={"instruction": "deploy_patch"},
    )
    assert bus.send(from_sup_msg) is True
    assert bus.receive("worker_2") is not None


def test_bus_pipeline_topology_order():
    """Valida que en PIPELINE el flujo solo progrese secuencialmente al siguiente eslabón."""
    bus = AgentMessageBus()
    pipeline = ["stage_research", "stage_code", "stage_review"]
    bus.set_pipeline_order(pipeline)

    # stage_research a stage_code -> Permitido (eslabón inmediato)
    step1_msg = AgentMessage(
        message_id="p_01",
        sender_id="stage_research",
        receiver_id="stage_code",
        session_id="s_pipe",
        payload={"spec": "done"},
    )
    assert bus.send(step1_msg) is True
    assert bus.receive("stage_code") is not None

    # stage_research intentando saltarse a stage_review -> Bloqueado
    skip_msg = AgentMessage(
        message_id="p_skip",
        sender_id="stage_research",
        receiver_id="stage_review",
        session_id="s_pipe",
        payload={"bypass": "unreviewed"},
    )
    assert bus.send(skip_msg) is False
    assert bus.receive("stage_review") is None

    # stage_review reportando a supervisor -> Permitido
    sup_msg = AgentMessage(
        message_id="p_sup",
        sender_id="stage_review",
        receiver_id="praxeon_supervisor",
        session_id="s_pipe",
        payload={"verdict": "approved"},
    )
    assert bus.send(sup_msg) is True
    assert bus.receive("praxeon_supervisor") is not None


def test_bus_topic_pubsub():
    """Valida la suscripción a temas y la entrega selectiva a suscriptores."""
    bus = AgentMessageBus(default_topology=TopologyType.MESH)
    bus.subscribe_topic("sec_auditor", "security_events")
    bus.subscribe_topic("log_watcher", "security_events")
    bus.subscribe_topic("ui_agent", "ui_events")

    delivered = bus.publish_topic(
        sender_id="net_monitor",
        topic="security_events",
        payload={"incident": "port_scan_detected"},
        session_id="s_telemetry",
    )
    assert delivered == 2

    # Auditor y watcher reciben el evento
    msg_auditor = bus.receive("sec_auditor")
    assert msg_auditor is not None
    assert msg_auditor.payload["topic"] == "security_events"
    assert msg_auditor.payload["incident"] == "port_scan_detected"

    msg_watcher = bus.receive("log_watcher")
    assert msg_watcher is not None

    # El ui_agent no estaba suscrito a security_events
    assert bus.receive("ui_agent") is None


def test_bus_interceptors():
    """Valida que los middlewares interceptores puedan inspeccionar o abortar mensajes."""
    bus = AgentMessageBus(default_topology=TopologyType.MESH)
    bus.register_agent("dev_01")
    bus.register_agent("dev_02")

    # Interceptor que bloquea mensajes que contengan palabras prohibidas
    def profanity_filter(msg: AgentMessage):
        if "forbidden_token" in str(msg.payload):
            return None
        return msg

    bus.add_interceptor(profanity_filter)

    clean_msg = AgentMessage(
        message_id="clean",
        sender_id="dev_01",
        receiver_id="dev_02",
        session_id="s1",
        payload={"text": "hello"},
    )
    assert bus.send(clean_msg) is True
    assert bus.receive("dev_02") is not None

    dirty_msg = AgentMessage(
        message_id="dirty",
        sender_id="dev_01",
        receiver_id="dev_02",
        session_id="s1",
        payload={"text": "forbidden_token_exploit"},
    )
    assert bus.send(dirty_msg) is False
    assert bus.receive("dev_02") is None


def test_bus_history_and_filtering():
    """Valida el registro y consulta del historial de mensajes del bus."""
    bus = AgentMessageBus(default_topology=TopologyType.MESH)
    bus.register_agent("ag_a")
    bus.register_agent("ag_b")

    m1 = AgentMessage(
        message_id="hist_1",
        sender_id="ag_a",
        receiver_id="ag_b",
        session_id="sess_1",
        payload={"v": 1},
    )
    m2 = AgentMessage(
        message_id="hist_2",
        sender_id="ag_b",
        receiver_id="ag_a",
        session_id="sess_2",
        payload={"v": 2},
    )

    bus.send(m1)
    bus.send(m2)

    all_hist = bus.get_history()
    assert len(all_hist) == 2

    sess1_hist = bus.get_history(session_id="sess_1")
    assert len(sess1_hist) == 1
    assert sess1_hist[0].message_id == "hist_1"

    ag_a_hist = bus.get_history(agent_id="ag_a")
    assert len(ag_a_hist) == 2
