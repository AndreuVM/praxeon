"""Tests de validación para el protocolo de confirmación/ACK y ciclo de vida de mensajería (F5-02 / Task 25)."""

import threading
import time
import pytest

from praxeon.agents.bus import AgentMessageBus, TopologyType
from praxeon.agents.protocol import AgentMessage, MessagePriority, MessageType


def test_message_ack_nack_result_helpers():
    request = AgentMessage(
        message_id="msg_req_101",
        sender_id="ag_orchestrator",
        receiver_id="ag_worker",
        session_id="sess_001",
        message_type=MessageType.AGENT_REQUEST,
        payload={"action": "audit_code", "file": "main.py"},
    )
    assert request.message_type == MessageType.AGENT_REQUEST
    assert request.in_reply_to is None

    # Probar create_ack
    ack = request.create_ack(sender_id="ag_worker", note="Task accepted")
    assert ack.message_type == MessageType.AGENT_ACK
    assert ack.sender_id == "ag_worker"
    assert ack.receiver_id == "ag_orchestrator"
    assert ack.in_reply_to == "msg_req_101"
    assert ack.correlation_id == "msg_req_101"
    assert ack.payload["status"] == "ACK"
    assert ack.payload["acknowledged_message_id"] == "msg_req_101"
    assert ack.payload["note"] == "Task accepted"

    # Probar create_nack
    nack = request.create_nack(
        sender_id="ag_worker",
        reason="Agent capacity exceeded",
        error_code="CAPACITY_LIMIT",
    )
    assert nack.message_type == MessageType.AGENT_NACK
    assert nack.sender_id == "ag_worker"
    assert nack.receiver_id == "ag_orchestrator"
    assert nack.in_reply_to == "msg_req_101"
    assert nack.payload["status"] == "NACK"
    assert nack.payload["reason"] == "Agent capacity exceeded"
    assert nack.payload["error_code"] == "CAPACITY_LIMIT"

    # Probar create_result
    result = request.create_result(
        sender_id="ag_worker",
        payload={"violations": 0, "status": "clean"},
    )
    assert result.message_type == MessageType.AGENT_RESULT
    assert result.sender_id == "ag_worker"
    assert result.receiver_id == "ag_orchestrator"
    assert result.in_reply_to == "msg_req_101"
    assert result.payload["violations"] == 0


def test_bus_acknowledge_and_nack():
    bus = AgentMessageBus(default_topology=TopologyType.MESH)
    bus.register_agent("ag_sender")
    bus.register_agent("ag_receiver")

    req = AgentMessage(
        message_id="msg_task_01",
        sender_id="ag_sender",
        receiver_id="ag_receiver",
        session_id="sess_001",
        message_type=MessageType.AGENT_REQUEST,
        payload={"query": "run_benchmark"},
    )
    assert bus.send(req) is True

    # El receptor extrae la solicitud
    received_req = bus.receive("ag_receiver")
    assert received_req is not None
    assert received_req.message_id == "msg_task_01"

    # El receptor emite un ACK al emisor
    bus.acknowledge_message(received_req, sender_id="ag_receiver", note="Job encolado")

    # El emisor recibe el ACK correlacionado
    ack = bus.receive_reply_for("ag_sender", "msg_task_01")
    assert ack is not None
    assert ack.message_type == MessageType.AGENT_ACK
    assert ack.in_reply_to == "msg_task_01"
    assert ack.payload["note"] == "Job encolado"


def test_send_and_wait_reply_synchronous():
    bus = AgentMessageBus(default_topology=TopologyType.MESH)
    bus.register_agent("ag_client")
    bus.register_agent("ag_server")

    req = AgentMessage(
        message_id="msg_call_42",
        sender_id="ag_client",
        receiver_id="ag_server",
        session_id="sess_001",
        message_type=MessageType.AGENT_REQUEST,
        payload={"compute": 42},
    )

    # Simular trabajador en hilo concurrente respondiendo
    def worker():
        time.sleep(0.05)
        incoming = bus.receive("ag_server")
        if incoming:
            bus.send_result(incoming, sender_id="ag_server", result_payload={"answer": 84})

    t = threading.Thread(target=worker)
    t.start()

    # El cliente envía y espera la respuesta
    reply = bus.send_and_wait_reply(req, timeout_seconds=1.0)
    t.join()

    assert reply is not None
    assert reply.message_type == MessageType.AGENT_RESULT
    assert reply.in_reply_to == "msg_call_42"
    assert reply.payload["answer"] == 84


def test_send_and_wait_reply_timeout():
    bus = AgentMessageBus(default_topology=TopologyType.MESH)
    bus.register_agent("ag_client")
    bus.register_agent("ag_unresponsive")

    req = AgentMessage(
        message_id="msg_timeout_01",
        sender_id="ag_client",
        receiver_id="ag_unresponsive",
        session_id="sess_001",
        message_type=MessageType.AGENT_REQUEST,
    )

    # Sin nadie respondiendo, debe expirar y retornar None
    reply = bus.send_and_wait_reply(req, timeout_seconds=0.1, poll_interval=0.02)
    assert reply is None


def test_send_with_retry_policy():
    bus = AgentMessageBus(default_topology=TopologyType.MESH)
    bus.register_agent("ag_client")
    bus.register_agent("ag_flaky")

    req = AgentMessage(
        message_id="msg_retry_99",
        sender_id="ag_client",
        receiver_id="ag_flaky",
        session_id="sess_001",
        message_type=MessageType.AGENT_REQUEST,
        payload={"action": "deploy"},
    )

    attempt_count = 0

    def flaky_worker():
        nonlocal attempt_count
        while attempt_count < 3:
            incoming = bus.receive("ag_flaky")
            if incoming:
                attempt_count += 1
                if attempt_count < 2:
                    # En el primer intento emite un NACK
                    bus.nack_message(incoming, sender_id="ag_flaky", reason="Busy")
                else:
                    # En el segundo intento responde exitosamente
                    bus.acknowledge_message(incoming, sender_id="ag_flaky", note="Success on retry")
                    break
            time.sleep(0.01)

    t = threading.Thread(target=flaky_worker)
    t.start()

    reply = bus.send_with_retry(req, max_retries=3, retry_delay=0.02, timeout_per_try=0.2)
    t.join()

    assert reply is not None
    assert reply.message_type == MessageType.AGENT_ACK
    assert reply.payload["note"] == "Success on retry"
    assert attempt_count >= 2
