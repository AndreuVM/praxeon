"""Tests formales para integridad autenticada de AgentMessage y firmas HMAC (P1-SECURITY).

Valida:
- Separación explícita entre received_integrity_hash y computed_integrity_hash.
- Verificación estricta de integridad y detección inmediata de alteraciones (tamper detection).
- Firma criptográfica HMAC-SHA256 y verificación de autenticidad de origen.
- Rechazo en AgentMessageBus de mensajes no íntegros o con firma inválida.
- Preservación de firmas y hashes en serialización to_dict / from_dict.
- Reemplazo de generadores de ID basados en módulo por identificadores UUID canónicos.
"""

from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from praxeon.agents import (
    AgentMessage,
    AgentMessageBus,
    MessagePriority,
    MessageType,
    TopologyType,
)


def test_agent_message_hash_separation_and_auto_initialization():
    """Valida que un mensaje nuevo inicialice received_integrity_hash igual a computed_integrity_hash."""
    msg = AgentMessage(
        message_id="msg_auth_01",
        sender_id="praxeon_supervisor",
        receiver_id="agent_analyst",
        session_id="sess_sec_01",
        task_id="task_audit",
        message_type=MessageType.REQUEST,
        priority=MessagePriority.HIGH,
        payload={"directive": "analyze_logs", "lines": 500},
    )

    assert msg.computed_integrity_hash is not None
    assert len(msg.computed_integrity_hash) == 64  # SHA-256 completo
    assert msg.integrity_hash == msg.computed_integrity_hash[:16]  # Truncado canónico 16
    assert msg.received_integrity_hash == msg.computed_integrity_hash
    assert msg.verify_integrity() is True


def test_agent_message_tamper_detection_on_payload():
    """Valida que alterar el payload en tránsito sea detectado como violación de integridad."""
    original = AgentMessage(
        message_id="msg_transfer_01",
        sender_id="praxeon_supervisor",
        receiver_id="agent_executor",
        session_id="sess_sec_01",
        payload={"amount": 100, "destination": "vault_a"},
    )
    assert original.verify_integrity() is True

    # Simular ataque man-in-the-middle alterando el payload serializado
    data = original.to_dict()
    assert data["received_integrity_hash"] == original.computed_integrity_hash

    # El atacante altera el payload sin poder alterar el hash emitido originalmente por el emisor
    data["payload"] = {"amount": 999999, "destination": "attacker_account"}

    tampered_msg = AgentMessage.from_dict(data)
    assert tampered_msg.received_integrity_hash == original.computed_integrity_hash
    assert tampered_msg.computed_integrity_hash != original.computed_integrity_hash
    assert tampered_msg.verify_integrity() is False


def test_agent_message_tamper_detection_on_headers():
    """Valida que alterar remitente, destinatario o prioridad rompa la integridad."""
    original = AgentMessage(
        message_id="msg_header_01",
        sender_id="agent_worker",
        receiver_id="agent_reviewer",
        session_id="sess_sec_01",
        priority=MessagePriority.NORMAL,
        payload={"status": "ready"},
    )

    # Alterar sender_id
    data_sender = original.to_dict()
    data_sender["sender_id"] = "praxeon_supervisor"  # Intento de suplantación
    msg_sender = AgentMessage.from_dict(data_sender)
    assert msg_sender.verify_integrity() is False

    # Alterar priority
    data_prio = original.to_dict()
    data_prio["priority"] = int(MessagePriority.CRITICAL)  # Intento de salto de prioridad
    msg_prio = AgentMessage.from_dict(data_prio)
    assert msg_prio.verify_integrity() is False


def test_agent_message_hmac_signing_and_verification():
    """Valida la firma HMAC-SHA256 con clave secreta compartida."""
    secret_key = "super_secure_session_secret_praxeon_2026"
    wrong_key = "attacker_invalid_secret_key"

    msg = AgentMessage(
        message_id="msg_signed_01",
        sender_id="praxeon_supervisor",
        receiver_id="agent_deployer",
        session_id="sess_sec_01",
        payload={"action": "deploy_prod", "version": "v1.2.0"},
    )

    # Inicialmente no está firmado
    assert msg.signature is None
    assert msg.verify_signature(secret_key) is False

    # Firmar el mensaje
    signed_msg = msg.sign(secret_key)
    assert signed_msg.signature is not None
    assert len(signed_msg.signature) == 64
    assert signed_msg.received_integrity_hash == signed_msg.computed_integrity_hash

    # Verificación exitosa con la clave correcta
    assert signed_msg.verify_signature(secret_key) is True
    assert signed_msg.verify_integrity() is True

    # Falla con clave incorrecta
    assert signed_msg.verify_signature(wrong_key) is False


def test_agent_message_hmac_tampering_invalidates_signature():
    """Valida que si un atacante recalcula received_integrity_hash sobre datos alterados, la firma HMAC falla indefectiblemente."""
    secret_key = "shared_confidential_secret_praxeon"

    signed_msg = AgentMessage(
        message_id="msg_tamper_hmac",
        sender_id="praxeon_supervisor",
        receiver_id="agent_worker",
        session_id="sess_sec_01",
        payload={"command": "clean_tmp"},
    ).sign(secret_key)

    # Caso A: Atacante altera payload y deja intacto el received_integrity_hash
    data_a = signed_msg.to_dict()
    data_a["payload"] = {"command": "rm -rf /"}
    tampered_a = AgentMessage.from_dict(data_a)
    assert tampered_a.verify_integrity() is False
    assert tampered_a.verify_signature(secret_key) is False

    # Caso B: Atacante altera payload y recalcula received_integrity_hash para evadir verify_integrity
    data_b = signed_msg.to_dict()
    data_b["payload"] = {"command": "malicious_payload"}
    # Instanciamos temporalmente para obtener el hash recalculado del contenido falso
    temp_fake = AgentMessage(**{k: v for k, v in data_b.items() if k not in ("signature", "received_integrity_hash", "integrity_hash", "computed_integrity_hash")})
    data_b["received_integrity_hash"] = temp_fake.computed_integrity_hash

    tampered_b = AgentMessage.from_dict(data_b)
    # verify_integrity engañado porque received_integrity_hash coincide con el payload falso
    assert tampered_b.verify_integrity() is True
    # PERO la firma HMAC falla indefectiblemente porque la firma no corresponde al contenido alterado
    assert tampered_b.verify_signature(secret_key) is False


def test_agent_message_bus_enforces_integrity_and_signature():
    """Valida que AgentMessageBus rechace mensajes no íntegros o sin firma válida cuando se configura."""
    secret = "praxeon_bus_channel_secret"
    bus = AgentMessageBus(
        default_topology=TopologyType.MESH,
        supervisor_id="praxeon_supervisor",
        shared_secret=secret,
        enforce_integrity=True,
    )
    bus.register_agent("worker_01")
    bus.register_agent("worker_02")

    # 1. Mensaje válido y firmado se entrega exitosamente
    valid_msg = AgentMessage(
        message_id="msg_valid_01",
        sender_id="worker_01",
        receiver_id="worker_02",
        session_id="sess_01",
        payload={"ping": True},
    ).sign(secret)

    assert bus.send(valid_msg) is True
    received = bus.receive("worker_02")
    assert received is not None
    assert received.message_id == "msg_valid_01"

    # 2. Mensaje no firmado es rechazado por el bus
    unsigned_msg = AgentMessage(
        message_id="msg_unsigned_01",
        sender_id="worker_01",
        receiver_id="worker_02",
        session_id="sess_01",
        payload={"ping": True},
    )
    assert bus.send(unsigned_msg) is False

    # 3. Mensaje firmado con clave espuria es rechazado por el bus
    forged_msg = AgentMessage(
        message_id="msg_forged_01",
        sender_id="worker_01",
        receiver_id="worker_02",
        session_id="sess_01",
        payload={"ping": True},
    ).sign("wrong_secret_key")
    assert bus.send(forged_msg) is False


def test_agent_message_canonical_uuid_generation():
    """Valida que create_response y create_delegation utilicen UUIDs sin colisiones de módulo."""
    req = AgentMessage(
        message_id="req_uuid_test",
        sender_id="supervisor",
        receiver_id="worker",
        session_id="s1",
    )

    resp1 = req.create_response(sender_id="worker", payload={"idx": 1})
    resp2 = req.create_response(sender_id="worker", payload={"idx": 2})

    assert resp1.message_id != resp2.message_id
    assert resp1.message_id.startswith("msg_resp_")
    assert len(resp1.message_id) >= 15  # Prefijo + 12 chars de hex UUID

    del1 = AgentMessage.create_delegation(
        sender_id="supervisor",
        receiver_id="worker",
        session_id="s1",
        task_id="t1",
        payload={},
    )
    del2 = AgentMessage.create_delegation(
        sender_id="supervisor",
        receiver_id="worker",
        session_id="s1",
        task_id="t1",
        payload={},
    )
    assert del1.message_id != del2.message_id
    assert del1.message_id.startswith("msg_del_")
