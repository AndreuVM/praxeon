"""Esquemas Pydantic v2 para estados cognitivos, acciones, trazas e intervenciones (Saneado v0.2)."""

from enum import Enum
import hashlib
import json
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class StepType(str, Enum):
    """Tipo de paso en la cadena de razonamiento."""
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    OBSERVATION = "observation"
    RESPONSE = "response"
    USER_INPUT = "user_input"
    SYSTEM_DIRECTIVE = "system_directive"


class Step(BaseModel):
    """Representación de un paso cognitivo individual en la trayectoria del agente (purgado de embeddings)."""
    id: str = Field(description="Identificador único del paso (ej. step_0, step_1)")
    step_type: StepType = Field(description="Tipo de paso cognitivo")
    content: str = Field(default="", description="Contenido textual del pensamiento, respuesta o resultado")
    tool_name: Optional[str] = Field(default=None, description="Nombre de la herramienta invocada si aplica")
    tool_args: Optional[Dict[str, Any]] = Field(default=None, description="Argumentos de la herramienta invocada")
    parent_id: Optional[str] = Field(default=None, description="ID del paso padre en el árbol de razonamiento")
    timestamp: float = Field(default_factory=time.time, description="Marca de tiempo en segundos")
    semantic_hash: Optional[str] = Field(default=None, description="Hash semántico normalizado del paso")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadatos auxiliares del paso")

    def model_post_init(self, __context: Any) -> None:
        """Genera el hash semántico automáticamente si no está definido."""
        if not self.semantic_hash:
            base_repr = f"{self.step_type.value}:{self.tool_name or ''}:{self._normalized_args()}:{self.content.strip().lower()}"
            self.semantic_hash = hashlib.sha256(base_repr.encode("utf-8")).hexdigest()[:16]

    def _normalized_args(self) -> str:
        if not self.tool_args:
            return ""
        try:
            return json.dumps(self.tool_args, sort_keys=True, ensure_ascii=True)
        except Exception:
            return str(self.tool_args)

    @property
    def full_text_representation(self) -> str:
        """Texto consolidado para representación del paso."""
        components = []
        if self.step_type == StepType.THOUGHT:
            components.append(f"Thought: {self.content}")
        elif self.step_type == StepType.TOOL_CALL:
            components.append(f"Action: {self.tool_name} with arguments {self._normalized_args()}")
            if self.content:
                components.append(f"Rationale: {self.content}")
        elif self.step_type == StepType.OBSERVATION:
            components.append(f"Observation: {self.content[:500]}")
        elif self.step_type == StepType.RESPONSE:
            components.append(f"Response: {self.content}")
        else:
            components.append(self.content)
        return "\n".join(components)


class ActionCandidate(BaseModel):
    """Acción o siguiente paso candidato evaluado por el motor JEV."""
    id: str = Field(description="Identificador único del candidato")
    description: str = Field(description="Descripción del razonamiento o acción propuesta")
    tool_name: Optional[str] = Field(default=None, description="Herramienta propuesta a ejecutar")
    tool_args: Optional[Dict[str, Any]] = Field(default=None, description="Parámetros propuestos")
    rationale: Optional[str] = Field(default=None, description="Motivo o hipótesis asociada")
    estimated_cost: Optional[float] = Field(default=None, description="Coste estimado de computación o llamadas")

    @property
    def full_text_representation(self) -> str:
        parts = [self.description]
        if self.tool_name:
            parts.append(f"Tool: {self.tool_name}")
            if self.tool_args:
                parts.append(f"Args: {json.dumps(self.tool_args, sort_keys=True)}")
        if self.rationale:
            parts.append(f"Rationale: {self.rationale}")
        return " | ".join(parts)


class Trajectory(BaseModel):
    """Trayectoria completa o sesión de razonamiento del agente."""
    session_id: str = Field(description="Identificador de la sesión")
    goal: str = Field(description="Objetivo principal del usuario hacia el cual debe converger el razonamiento")
    steps: List[Step] = Field(default_factory=list, description="Lista cronológica de pasos cognitivos")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadatos globales de la sesión")

    def add_step(self, step: Step) -> None:
        """Agrega un paso y enlaza su padre por defecto si no lo tenía."""
        if not step.parent_id and self.steps:
            step.parent_id = self.steps[-1].id
        self.steps.append(step)


class DecisionScore(BaseModel):
    """Desglose analítico del cálculo de Joint Expected Value / Decision Score para un candidato o paso."""
    candidate_id: str = Field(description="ID del candidato evaluado")
    p_progress: float = Field(description="Probabilidad / Puntuación de progreso hacia la meta [0, 1]")
    delta_u: float = Field(description="Ganancia de información / reducción de incertidumbre [0, 1]")
    loop_penalty: float = Field(description="Penalización por bucles o redundancia en historial [0, 1]")
    total_jev: float = Field(description="Puntuación combinada normalizada [-1.0, 1.0]")
    details: Dict[str, Any] = Field(default_factory=dict, description="Desglose interno de similitudes y pesos")

    @property
    def total_score(self) -> float:
        """Alias canónico de total_jev (REF-01)."""
        return self.total_jev


# Alias de retrocompatibilidad controlada (REF-01)
JEVScore = DecisionScore


# --- Taxonomía Segregada de Anomalías (Hallazgo 3.13) ---

class ConvergenceAnomaly(str, Enum):
    """Dimensión analítica de convergencia algorítmica."""
    NONE = "none"
    ONE_HOP_REPEAT = "one_hop_repeat"
    N_HOP_CYCLE = "n_hop_cycle"
    ENTROPIC_STAGNATION = "entropic_stagnation"
    SEMANTIC_FIXATION = "semantic_fixation"
    DEPENDENCY_CYCLE = "dependency_cycle"
    GOAL_DRIFT = "goal_drift"
    SCOPE_DRIFT = "scope_drift"


class GroundingAnomaly(str, Enum):
    """Dimensión analítica de solidez empírica y fundamentación fáctica."""
    NONE = "none"
    UNGROUNDED_PREMISE = "ungrounded_premise"
    HALLUCINATION = "hallucination"
    CONTRADICTED_EVIDENCE = "contradicted_evidence"
    STALE_STATE = "stale_state"
    CONTRADICTORY_OBSERVATION = "contradictory_observation"
    UNVERIFIED_SUCCESS = "unverified_success"
    PREMATURE_COMPLETION = "premature_completion"


class InstrumentalRiskAnomaly(str, Enum):
    """Dimensión analítica de riesgo instrumental y efectos colaterales."""
    NONE = "none"
    DESTRUCTIVE_CALL = "destructive_call"
    UNAUTHORIZED_TOOL = "unauthorized_tool"
    UNCONFIRMED_ACTION = "unconfirmed_action"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    INVALID_TOOL_ARGUMENT = "invalid_tool_argument"
    PERMISSION_VIOLATION = "permission_violation"
    EXCESSIVE_COST = "excessive_cost"


class LoopType(str, Enum):
    """Tipos clasificados de bucles o fallos de convergencia (mantenido para compatibilidad)."""
    NONE = "none"
    ONE_HOP_TOOL_REPEAT = "one_hop_tool_repeat"       # Reintento idéntico inmediato de herramienta
    N_HOP_CYCLE = "n_hop_cycle"                       # Ciclo cerrado A -> B -> C -> A
    SEMANTIC_FIXATION = "semantic_fixation"           # Fijación en hipótesis redundante
    ENTROPIC_STAGNATION = "entropic_stagnation"       # Rumiación verbal creciente sin aporte de información
    HALLUCINATION = "hallucination"                   # Alucinación de hechos o estados no fundamentados
    UNGROUNDED_PREMISE = "ungrounded_premise"         # Premisa o suposición no verificada empíricamente


class LoopReport(BaseModel):
    """Informe de detección de bucles y anomalías segregadas para la trayectoria actual."""
    loop_detected: bool = Field(default=False, description="Indica si se ha detectado un bucle o estancamiento")
    loop_type: LoopType = Field(default=LoopType.NONE, description="Tipo de bucle detectado")
    convergence: ConvergenceAnomaly = Field(default=ConvergenceAnomaly.NONE, description="Dimensión de convergencia algorítmica")
    grounding: GroundingAnomaly = Field(default=GroundingAnomaly.NONE, description="Dimensión de solidez empírica")
    instrumental_risk: InstrumentalRiskAnomaly = Field(default=InstrumentalRiskAnomaly.NONE, description="Dimensión de riesgo instrumental")
    severity: int = Field(default=0, ge=0, le=5, description="Severidad del bucle (0=ninguno, 1=leve, 5=crítico)")
    cycle_nodes: List[str] = Field(default_factory=list, description="Lista de IDs de pasos involucrados en el ciclo")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Nivel de confianza en la detección")
    explanation: str = Field(default="", description="Explicación legible de la causa del bucle")
    culprit_tool: Optional[str] = Field(default=None, description="Nombre de la herramienta causante si aplica")


class InterventionLevel(int, Enum):
    """Niveles de intervención obligatoria."""
    NONE = 0
    LEVEL_1_META_FEEDBACK = 1          # Aviso y reflexión obligatoria
    LEVEL_2_FORCED_BACKTRACKING = 2    # Retroceso a estado previo y prohibición de acción
    LEVEL_3_ABSTRACTION_SHIFT = 3      # Pausa táctica, replanteamiento estratégico o abogado del diablo


class InterventionDirective(BaseModel):
    """Directiva inyectable para redirigir el razonamiento del modelo."""
    level: InterventionLevel = Field(description="Nivel de severidad de la intervención")
    target_step_id: Optional[str] = Field(default=None, description="Nodo al que se debe retroceder si aplica")
    message: str = Field(description="Directiva legible y formal para inyectar en el contexto")
    forbidden_actions: List[str] = Field(default_factory=list, description="Herramientas o enfoques expresamente prohibidos")
    suggested_action: Optional[str] = Field(default=None, description="Acción de rescate o exploración recomendada")
    context_injection: str = Field(description="Texto formateado listo para inyección en el prompt")


class BatchSemantics(BaseModel):
    """Parámetros semánticos para el procesamiento por lotes (chunking)."""
    independent: bool = Field(default=False, description="Si es True, las acciones del bloque son independientes y un fallo no invalida a las demás")


class ChunkEvaluationResult(BaseModel):
    """Resultado consolidado de la evaluación de un bloque (chunk) de pasos cognitivos."""
    all_safe: bool = Field(default=True, description="Indica si todos los pasos del bloque son seguros y convergentes")
    valid_step_count: int = Field(default=0, description="Cantidad de pasos válidos antes del punto de divergencia/bloqueo")
    flagged_step_index: Optional[int] = Field(default=None, description="Índice relativo del primer paso con fallo o alucinación")
    step_scores: List[JEVScore] = Field(default_factory=list, description="Puntuaciones JEV calculadas para cada paso del bloque")
    directive: Optional[InterventionDirective] = Field(default=None, description="Directiva de intervención si el bloque requiere rectificación")
    loop_report: Optional[LoopReport] = Field(default=None, description="Informe de detección de bucles o anomalías")
    hallucination_detected: bool = Field(default=False, description="Indica si se identificó una alucinación en el bloque")
    hallucination_type: Optional[str] = Field(default=None, description="Tipo o clasificación de la alucinación detectada")
    explanation: str = Field(default="", description="Detalle explicativo del diagnóstico del bloque")
