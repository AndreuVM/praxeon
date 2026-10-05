"""Modelos de Dominio para el Adaptive Agent Runtime (Fase 8).

Especifica las estructuras inmutables para el ciclo adaptativo de ejecución:
- TrajectoryDirective: Directivas en vuelo (CONTINUE, PRUNE, RECOVER, ESCALATE, SUSPEND_HITL, TERMINATE).
- StepDispatchSpec: Tupla de despacho dinámico por paso (agente, contexto mínimo, herramientas, permisos).
- TrajectoryStepRecord: Registro inmutable de cada paso ejecutado con telemetría de tokens y coste.
- AdaptiveSessionState: Estado global unificado de la sesión de ejecución adaptativa.
- AdaptiveExecutionSummary: Resumen formal auditable de ejecución para reportes y telemetría.
"""

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from praxeon.domain.assessment import RiskLevel


class TrajectoryDirective(str, Enum):
    """Directivas adaptativas emitidas por el supervisor durante la trayectoria."""
    CONTINUE = "CONTINUE"            # Paso validado y seguro; avanzar al siguiente paso
    PRUNE = "PRUNE"                  # Rama estéril, redundante o muerta; podar y excluir contexto
    RECOVER = "RECOVER"              # Fallo o excepción recuperable; retroceder (backtrack) a checkpoint
    ESCALATE = "ESCALATE"            # Incertidumbre alta o capacidad insuficiente; escalar agente/modelo
    SUSPEND_HITL = "SUSPEND_HITL"    # Riesgo crítico o límite de reintentos; requerir aprobación humana
    TERMINATE = "TERMINATE"          # Meta satisfecha exitosamente o aborto final


class StepDispatchSpec(BaseModel):
    """Especificación declarativa de autorización y contexto para un paso atómico."""
    model_config = ConfigDict(frozen=True)

    step_index: int = Field(ge=0, description="Índice secuencial del paso en la trayectoria")
    agent_id: str = Field(description="ID del agente asignado para este paso específico")
    assigned_role: str = Field(description="Rol operacional activo en el paso")
    model_name: str = Field(description="Modelo configurado para el agente en este paso")
    minimal_context_fingerprint: str = Field(description="Huella SHA-256 del fragmento de contexto mínimo suficiente")
    authorized_tools: List[str] = Field(default_factory=list, description="Herramientas explícitamente permitidas")
    max_risk_level: RiskLevel = Field(default=RiskLevel.MEDIUM, description="Techo de riesgo tolerado para el paso")
    require_human_confirmation: bool = Field(default=False)
    sandbox_tier: str = Field(default="local", description="Nivel de aislamiento (local, dry_run, container)")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def authorization_digest(self) -> str:
        """Firma determinista de la autorización del paso."""
        raw = f"{self.step_index}:{self.agent_id}:{sorted(self.authorized_tools)}:{self.max_risk_level.value}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class TrajectoryStepRecord(BaseModel):
    """Registro inmutable de un paso ejecutado dentro de la trayectoria adaptativa."""
    model_config = ConfigDict(frozen=True)

    step_id: str
    step_index: int
    dispatch_spec: StepDispatchSpec
    action_proposed: Dict[str, Any] = Field(default_factory=dict)
    policy_decision: str = Field(default="ALLOW")
    observation: Optional[str] = Field(default=None)
    directive: TrajectoryDirective = Field(default=TrajectoryDirective.CONTINUE)
    tokens_used: int = Field(default=0, ge=0)
    step_cost: float = Field(default=0.0, ge=0.0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AdaptiveSessionState(BaseModel):
    """Estado global inmutable y auditable de una sesión del Adaptive Runtime."""
    model_config = ConfigDict(frozen=True)

    session_id: str
    goal: str
    status: str = Field(default="INITIALIZED")  # INITIALIZED, RUNNING, COMPLETED, SUSPENDED_HITL, FAILED
    primary_agent_id: str
    escalation_level: int = Field(default=0, ge=0)
    step_count: int = Field(default=0, ge=0)
    total_cost: float = Field(default=0.0, ge=0.0)
    total_tokens: int = Field(default=0, ge=0)
    current_branch_id: str = Field(default="root_main")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AdaptiveExecutionSummary(BaseModel):
    """Resumen consolidado post-ejecución del Adaptive Runtime."""
    model_config = ConfigDict(frozen=True)

    session_id: str
    task_id: str
    status: str
    goal: str
    total_steps: int
    total_cost: float
    total_tokens: int
    agents_involved: List[str]
    escalations_count: int
    directives_applied: List[str]
    final_artifact_or_result: Optional[str] = None
    duration_seconds: float = Field(default=0.0, ge=0.0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)
