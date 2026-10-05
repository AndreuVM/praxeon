"""Pruebas unitarias para el protocolo formal de mensajería AgentMessage (Fase 5 - F5-01).

Valida:
- Tipado, inmutabilidad y cálculo de hash de integridad de AgentMessage.
- Generación y correlación de respuestas (correlation_id).
- Construcción estructurada de delegaciones supervisadas (DELEGATE).
- Serialización y deserialización a diccionario/JSON.
"""

import pytest
from pydantic import ValidationError

from praxeon.agents import (
    AgentMessage,
    MessagePriority,
    MessageType,
)


def test_message_envelope_immutability_and_hash():
    """Valida la inmutabilidad de AgentMessage y el cálculo determinista de su hash de integridad."""
    msg = AgentMessage(
        message_id="msg_001",
        sender_id="ag_pm_01",
        receiver_id="ag_dev_01",
        session_id="sess_multi_01",
        task_id="task_audit_auth",
        message_type=MessageType.REQUEST,
        priority=MessagePriority.HIGH,
        payload={"action": "audit_code", "target_file": "src/auth.py"},
        evidence_refs=["ev_cloned_repo"],
    )

    assert msg.message_id == "msg_001"
    assert msg.priority == MessagePriority.HIGH
    assert msg.integrity_hash is not None
    assert len(msg.integrity_hash) == 16

    # Inmutabilidad
    with pytest.raises(ValidationError):
        msg.payload = {"action": "tampered"}  # type: ignore

    # Comprobar que dos mensajes con datos idénticos tienen el mismo hash
    msg_clone = AgentMessage(
        message_id="msg_001",
        sender_id="ag_pm_01",
        receiver_id="ag_dev_01",
        session_id="sess_multi_01",
        task_id="task_audit_auth",
        message_type=MessageType.REQUEST,
        priority=MessagePriority.HIGH,
        payload={"action": "audit_code", "target_file": "src/auth.py"},
        evidence_refs=["ev_cloned_repo"],
        timestamp=msg.timestamp,
    )
    assert msg.integrity_hash == msg_clone.integrity_hash


def test_message_response_correlation():
    """Valida que create_response invierta remitente/destinatario y correlacione el ID."""
    request = AgentMessage(
        message_id="req_999",
        sender_id="ag_pm_01",
        receiver_id="ag_dev_01",
        session_id="sess_multi_01",
        task_id="task_patch",
        message_type=MessageType.REQUEST,
        payload={"directive": "patch_vulnerability"},
    )

    response = request.create_response(
        sender_id="ag_dev_01",
        payload={"status": "completed", "tests_passed": True},
        evidence_refs=["ev_tests_ok"],
    )

    assert response.message_type == MessageType.RESPONSE
    assert response.sender_id == "ag_dev_01"
    assert response.receiver_id == "ag_pm_01"  # Invertido
    assert response.session_id == "sess_multi_01"
    assert response.correlation_id == "req_999"  # Correlación estricta
    assert response.payload["status"] == "completed"
    assert "ev_tests_ok" in response.evidence_refs


def test_message_delegation_creation():
    """Valida la creación de un mensaje de delegación formal con contexto y evidencias vinculadas."""
    delegation = AgentMessage.create_delegation(
        sender_id="ag_pm_01",
        receiver_id="ag_auditor_01",
        session_id="sess_multi_01",
        task_id="task_sec_review",
        payload={"scope": "sqli_prevention", "strict_mode": True},
        priority=MessagePriority.CRITICAL,
        evidence_refs=["ev_staging_ready"],
        context_snapshot_fingerprint="fp_l1_context_abc",
    )

    assert delegation.message_type == MessageType.DELEGATE
    assert delegation.priority == MessagePriority.CRITICAL
    assert delegation.context_snapshot_fingerprint == "fp_l1_context_abc"
    assert delegation.receiver_id == "ag_auditor_01"


def test_message_serialization_roundtrip():
    """Valida la serialización y deserialización a diccionario/JSON."""
    original = AgentMessage(
        message_id="msg_roundtrip",
        sender_id="ag_reviewer",
        receiver_id="*",
        session_id="sess_01",
        message_type=MessageType.BROADCAST,
        priority=MessagePriority.NORMAL,
        payload={"announcement": "Code freeze in effect"},
    )

    data = original.to_dict()
    assert data["message_type"] == "BROADCAST"
    assert data["integrity_hash"] == original.integrity_hash

    restored = AgentMessage.from_dict(data)
    assert restored.message_id == original.message_id
    assert restored.message_type == MessageType.BROADCAST
    assert restored.integrity_hash == original.integrity_hash
