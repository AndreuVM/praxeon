"""Servicio Modular de Propuesta, Evaluación y Reconciliación de Decisiones (F2/SEC).

Centraliza:
- Almacenamiento y consulta durable de DecisionRecord en SqliteDecisionRepository.
- Reconciliación de riesgos (RiskReconciliationEngine) entre políticas y clasificaciones.
- Registro y aprobación/rechazo de decisiones en el árbol de deliberación.
"""

from datetime import datetime, timezone
import threading
from typing import Any, Dict, List, Optional

from praxeon.domain.models import ActionCandidate
from praxeon.domain.decision import DecisionStatus, PolicyDecision
from praxeon.domain.events import EventType
from praxeon.policy.engine import PolicyEngine
from praxeon.reasoning.classifier import CommandClassifier
from praxeon.reasoning.evidence import EvidenceEngine
from praxeon.reasoning.risk import RiskEngine
from praxeon.runtime.decision_store import SqliteDecisionRepository
from praxeon.runtime.event_bus import EventBus
from praxeon.server.schemas.decision import (
    DecisionDetailResponse,
    DecisionResponse,
    PolicyDTO,
    ProviderEvaluationDTO,
    RiskDTO,
)


class DecisionService:
    """Gestiona el ciclo de vida de evaluación, almacenamiento y confirmación de decisiones."""

    def __init__(
        self,
        event_bus: EventBus,
        decision_repository: SqliteDecisionRepository,
        policy_engine: PolicyEngine,
        risk_engine: RiskEngine,
        evidence_engine: EvidenceEngine,
        command_classifier: CommandClassifier,
    ):
        self.event_bus = event_bus
        self.decision_repository = decision_repository
        self.policy_engine = policy_engine
        self.risk_engine = risk_engine
        self.evidence_engine = evidence_engine
        self.command_classifier = command_classifier
        self._lock = threading.Lock()
        self._decisions: Dict[str, Dict[str, Any]] = {}

    def save_decision(self, decision_record: Dict[str, Any]) -> None:
        """Guarda un registro de decisión tanto en memoria como en SQLite WAL."""
        decision_id = decision_record["decision_id"]
        with self._lock:
            self._decisions[decision_id] = decision_record
        self.decision_repository.save(decision_record)

    def get_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        """Recupera un registro de decisión desde memoria o base de datos SQLite."""
        with self._lock:
            record = self._decisions.get(decision_id)
        if not record:
            record = self.decision_repository.get(decision_id)
            if record:
                with self._lock:
                    self._decisions[decision_id] = record
        return record

    def list_decisions(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lista las decisiones registradas filtradas opcionalmente por sesión."""
        results = self.decision_repository.list_all(session_id=session_id)
        return results

    def confirm_decision(
        self,
        session_id: str,
        decision_id: str,
        approved_by: str = "operator",
        modified_arguments: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> bool:
        """Confirma una decisión en espera de aprobación humana."""
        record = self.get_decision(decision_id)
        if not record:
            return False

        with self._lock:
            record["status"] = "ALLOW"
            record["approved_by"] = approved_by
            record["confirmed_at"] = datetime.now(timezone.utc).isoformat()
            if modified_arguments:
                record["modified_arguments"] = modified_arguments
            if reason:
                record["approval_reason"] = reason

        self.decision_repository.save(record)
        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.APPROVAL_COMPLETED,
            node_id=record.get("action_id", f"act_{decision_id}"),
            decision_id=decision_id,
            payload={
                "approved_by": approved_by,
                "modified_arguments": modified_arguments,
                "reason": reason,
                "action": "approved",
            },
        )
        return True

    def reject_decision(
        self,
        session_id: str,
        decision_id: str,
        rejected_by: str = "operator",
        reason: Optional[str] = None,
    ) -> bool:
        """Rechaza una decisión pendiente."""
        record = self.get_decision(decision_id)
        if not record:
            return False

        with self._lock:
            record["status"] = "BLOCK"
            record["rejected_by"] = rejected_by
            record["rejected_at"] = datetime.now(timezone.utc).isoformat()
            record["rejection_reason"] = reason

        self.decision_repository.save(record)
        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.APPROVAL_COMPLETED,
            node_id=record.get("action_id", f"act_{decision_id}"),
            decision_id=decision_id,
            payload={
                "rejected_by": rejected_by,
                "reason": reason,
                "action": "rejected",
            },
        )
        return True
