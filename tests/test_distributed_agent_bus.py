"""Pruebas unitarias para Orquestación Multiagente Distribuida (Fase 7 - ROAD-03).

Valida:
1. Publicación y lectura distribuida con Consumer Groups en InMemoryStreamTransport.
2. Intercambio de mensajes asíncronos entre nodos en DistributedAgentMessageBus.
3. Gestión del ciclo de vida de Pending Entries List (PEL) y confirmación explícita (ACK).
4. Tolerancia a caídas de nodo (Crash Recovery / Failover) mediante re-asignación de mensajes estancados.
5. Compatibilidad y adaptadores de transporte RedisStreamsTransport y RabbitMQTransport.
"""

import asyncio
import time
import pytest

from praxeon.agents.distributed import (
    ClusterNodeInfo,
    DistributedAgentMessageBus,
    InMemoryStreamTransport,
    PendingMessageRecord,
    RabbitMQTransport,
    RedisStreamsTransport,
    TransportType,
)
from praxeon.agents.protocol import AgentMessage, MessagePriority, MessageType


@pytest.mark.asyncio
async def test_in_memory_stream_publish_and_consumer_groups():
    """Valida la publicación de mensajes y la lectura independiente por múltiples consumer groups."""
    transport = InMemoryStreamTransport()
    stream_name = "praxeon:test_cluster:agent:ag_worker"

    msg1 = AgentMessage(
        message_id="msg_001",
        sender_id="supervisor",
        receiver_id="ag_worker",
        session_id="sess_dist_01",
        payload={"task": "Analizar logs"},
        message_type=MessageType.DELEGATE,
    )
    msg2 = AgentMessage(
        message_id="msg_002",
        sender_id="supervisor",
        receiver_id="ag_worker",
        session_id="sess_dist_01",
        payload={"task": "Ejecutar tests"},
        message_type=MessageType.DELEGATE,
    )

    id1 = await transport.publish(stream_name, msg1)
    id2 = await transport.publish(stream_name, msg2)

    assert id1 is not None
    assert id2 is not None
    assert id1 != id2

    # Consumir a través de un grupo
    items = await transport.read_group(
        stream_name=stream_name,
        group_name="grp_workers",
        consumer_name="worker_inst_1",
        count=10,
    )

    assert len(items) == 2
    assert items[0][1].payload["task"] == "Analizar logs"
    assert items[1][1].payload["task"] == "Ejecutar tests"

    # Los mensajes deben estar en PEL
    pending = await transport.get_pending(stream_name, "grp_workers")
    assert len(pending) == 2
    assert {p.stream_id for p in pending} == {id1, id2}

    # Confirmar id1 con ACK
    acked = await transport.acknowledge(stream_name, "grp_workers", id1)
    assert acked is True

    # Solo debe quedar id2 en PEL
    pending_after = await transport.get_pending(stream_name, "grp_workers")
    assert len(pending_after) == 1
    assert pending_after[0].stream_id == id2


@pytest.mark.asyncio
async def test_distributed_bus_multi_node_communication():
    """Valida el intercambio de mensajes entre dos nodos del cluster sobre el bus distribuido."""
    shared_transport = InMemoryStreamTransport()

    # Nodo 1 (Supervisor en región A)
    bus_node_1 = DistributedAgentMessageBus(
        node_id="node_us_east_1",
        cluster_id="prod_cluster",
        transport=shared_transport,
    )
    # Nodo 2 (Worker en región B)
    bus_node_2 = DistributedAgentMessageBus(
        node_id="node_eu_west_1",
        cluster_id="prod_cluster",
        transport=shared_transport,
    )

    bus_node_1.register_agent("supervisor")
    bus_node_2.register_agent("worker_agent")

    msg = AgentMessage(
        message_id="msg_cross_node",
        sender_id="supervisor",
        receiver_id="worker_agent",
        session_id="sess_cluster_prod",
        payload={"action": "Iniciar auditoría distribuida"},
        message_type=MessageType.REQUEST,
    )

    # Nodo 1 publica
    stream_id = await bus_node_1.async_publish(msg)
    assert stream_id is not None

    # Nodo 2 recibe
    received = await bus_node_2.async_receive("worker_agent", count=5, auto_ack=True)
    assert len(received) == 1
    assert received[0][0] == stream_id
    assert received[0][1].payload["action"] == "Iniciar auditoría distribuida"

    # Al haberse usado auto_ack, el mensaje no debe quedar pendiente
    stream_name = bus_node_2.get_stream_name("worker_agent")
    pending = await shared_transport.get_pending(stream_name, "grp_worker_agent")
    assert len(pending) == 0


@pytest.mark.asyncio
async def test_crash_recovery_stale_claim():
    """Valida la recuperación ante caídas de nodo (failover) reclamando mensajes huérfanos."""
    transport = InMemoryStreamTransport()
    bus = DistributedAgentMessageBus(
        node_id="node_primary",
        cluster_id="test_cluster",
        transport=transport,
    )

    msg = AgentMessage(
        message_id="msg_critical_task",
        sender_id="supervisor",
        receiver_id="worker_1",
        session_id="sess_failover",
        payload={"directive": "Trabajo crítico no confirmado"},
        message_type=MessageType.DELEGATE,
    )

    s_id = await bus.async_publish(msg)

    # Consumidor caído lee pero no confirma (ACK)
    stream_name = bus.get_stream_name("worker_1")
    read_items = await transport.read_group(
        stream_name=stream_name,
        group_name="grp_worker_1",
        consumer_name="crashed_node_consumer",
        count=1,
    )
    assert len(read_items) == 1

    # Simular paso del tiempo para que sea considerado huérfano/stale (min_idle_ms = 0)
    recovered = await bus.recover_stale_messages(agent_id="worker_1", min_idle_ms=0.0)
    assert len(recovered) == 1
    rec_id, rec_msg = recovered[0]
    assert rec_id == s_id
    assert rec_msg.payload["directive"] == "Trabajo crítico no confirmado"

    # Verificar que en PEL ahora pertenece al consumidor activo del nuevo nodo
    pending = await transport.get_pending(stream_name, "grp_worker_1")
    assert len(pending) == 1
    assert pending[0].consumer_name == "node_primary:worker_1"
    assert pending[0].delivery_count >= 2


@pytest.mark.asyncio
async def test_redis_and_rabbitmq_adapters():
    """Valida la operatividad transparente de los adaptadores RedisStreamsTransport y RabbitMQTransport."""
    redis_trans = RedisStreamsTransport()
    rabbit_trans = RabbitMQTransport()

    msg = AgentMessage(
        message_id="msg_adapter_01",
        sender_id="ag1",
        receiver_id="ag2",
        session_id="sess_adapter",
        payload={"data": "Payload distribuido"},
        message_type=MessageType.REQUEST,
    )

    # Redis
    r_id = await redis_trans.publish("stream_redis", msg)
    assert r_id is not None
    r_msgs = await redis_trans.read_group("stream_redis", "grp_r", "c1", count=1)
    assert len(r_msgs) == 1
    assert r_msgs[0][1].payload["data"] == "Payload distribuido"

    # RabbitMQ
    q_id = await rabbit_trans.publish("queue_rabbit", msg)
    assert q_id is not None
    q_msgs = await rabbit_trans.read_group("queue_rabbit", "grp_q", "c2", count=1)
    assert len(q_msgs) == 1
    assert q_msgs[0][1].payload["data"] == "Payload distribuido"


def test_cluster_topology_status():
    """Verifica el registro y telemetría de presencia de nodos en el cluster."""
    bus = DistributedAgentMessageBus(node_id="master_node_01", cluster_id="praxeon_fleet")
    node_info = ClusterNodeInfo(
        node_id="worker_node_02",
        cluster_id="praxeon_fleet",
        registered_agents=["ag_reviewer", "ag_tester"],
    )
    bus.register_cluster_node(node_info)

    status = bus.get_cluster_status()
    assert status["cluster_id"] == "praxeon_fleet"
    assert status["current_node_id"] == "master_node_01"
    assert status["registered_nodes_count"] == 2
