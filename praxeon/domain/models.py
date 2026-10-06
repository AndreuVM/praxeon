"""Modelos de dominio tipados para JEV Reasoning Navigator v0.2.

Re-exporta todas las entidades puras e inmutables organizadas modularmente en el paquete domain.
"""

from praxeon.domain.action import ActionCandidate, ToolCall, compute_action_hash
from praxeon.domain.governance import ActionProvenance, FallbackMode, resolve_fallback_mode
from praxeon.domain.assessment import (
    CommandCategory,
    CommandRiskAssessment,
    ProviderAssessment,
    RiskAssessment,
    RiskLevel,
)
from praxeon.domain.checkpoint import Checkpoint, ExecutionEnvironment
from praxeon.domain.decision import (
    CapabilityPayload,
    DecisionReceipt,
    DecisionStatus,
    ExecutionMode,
    PolicyDecision,
    compute_receipt_signature,
    compute_state_hash,
    sign_receipt,
    verify_capability_signature,
    verify_receipt_signature,
)
from praxeon.domain.evidence import Claim, Evidence
from praxeon.domain.goal import CriterionType, Goal, SuccessCriterion
from praxeon.domain.observation import Observation, ToolObservation
from praxeon.domain.state import StateSnapshot, StateStepRecord, TrajectoryState

__all__ = [
    "Goal",
    "CriterionType",
    "SuccessCriterion",
    "ToolCall",
    "ActionCandidate",
    "compute_action_hash",
    "Observation",
    "ToolObservation",
    "Evidence",
    "Claim",
    "ProviderAssessment",
    "RiskLevel",
    "RiskAssessment",
    "CommandCategory",
    "CommandRiskAssessment",
    "ExecutionMode",
    "CapabilityPayload",
    "DecisionStatus",
    "PolicyDecision",
    "DecisionReceipt",
    "compute_receipt_signature",
    "verify_receipt_signature",
    "verify_capability_signature",
    "compute_state_hash",
    "Checkpoint",
    "ExecutionEnvironment",
    "StateSnapshot",
    "StateStepRecord",
    "TrajectoryState",
    "ActionProvenance",
    "FallbackMode",
    "resolve_fallback_mode",
]
