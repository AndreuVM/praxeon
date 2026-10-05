"""Mecanismo de Poda (Pruning) Multidimensional para Búsqueda en Árbol (F3-03).

Evalúa y descarta tempranamente ramas en exploración según 6 dimensiones ortogonales:
1. Seguridad y Normativa (SECURITY_VIOLATION): Políticas BLOCK del PolicyEngine.
2. Riesgo Operacional (RISK_THRESHOLD): Evaluaciones de riesgo crítico de RiskEngine.
3. Bucles y Ciclos (LOOP_DETECTED): Repeticiones unihop o ciclos topológicos de LoopDetector.
4. Sustento y Contradicción Fáctica (EVIDENCE_CONTRADICTION): Falta o contradicción de evidencias.
5. Límite Presupuestario (BUDGET_EXHAUSTION): Exceso de profundidad máxima de pasos.
6. Degradación de Puntuación (SCORE_DEGRADATION): Puntuación absoluta o relativa insostenible.

Las ramas podadas se integran automáticamente en state.metadata["pruned_branches"]
asegurando que el selector de contexto (Fase 2) las excluya de la memoria activa.
"""

from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set
from pydantic import BaseModel, ConfigDict, Field

from praxeon.domain.action import ActionCandidate
from praxeon.domain.branch import BranchPath, BranchStatus
from praxeon.domain.decision import DecisionStatus, PolicyDecision
from praxeon.domain.models import RiskLevel
from praxeon.models.schema import ConvergenceAnomaly, Step, StepType
from praxeon.reasoning.loop_detector import LoopDetector
from praxeon.reasoning.risk import RiskEngine

if TYPE_CHECKING:
    from praxeon.runtime.state import SessionState


class PruningCategory(str, Enum):
    """Categorías funcionales de causas de poda en árboles de razonamiento."""
    SECURITY_VIOLATION = "SECURITY_VIOLATION"
    RISK_THRESHOLD = "RISK_THRESHOLD"
    LOOP_DETECTED = "LOOP_DETECTED"
    EVIDENCE_CONTRADICTION = "EVIDENCE_CONTRADICTION"
    BUDGET_EXHAUSTION = "BUDGET_EXHAUSTION"
    SCORE_DEGRADATION = "SCORE_DEGRADATION"


class PruningDecision(BaseModel):
    """Decisión formal e inmutable emitida por el mecanismo de poda."""
    model_config = ConfigDict(frozen=True)

    should_prune: bool = False
    category: Optional[PruningCategory] = None
    reason: Optional[str] = None
    severity: int = Field(default=0, ge=0, le=5)
    details: Dict[str, Any] = Field(default_factory=dict)


class BranchPruner:
    """Motor de poda multidimensional para árboles y grafos de decisión."""

    def __init__(
        self,
        policy_engine: Optional[Any] = None,
        risk_engine: Optional[RiskEngine] = None,
        loop_detector: Optional[LoopDetector] = None,
        min_composite_score: float = 0.15,
        max_relative_gap: float = 0.55,
        max_branch_depth: int = 20,
    ):
        if policy_engine is None:
            from praxeon.policy.engine import PolicyEngine
            self.policy_engine = PolicyEngine()
        else:
            self.policy_engine = policy_engine

        self.risk_engine = risk_engine or RiskEngine()
        self.loop_detector = loop_detector or LoopDetector()
        self.min_composite_score = min_composite_score
        self.max_relative_gap = max_relative_gap
        self.max_branch_depth = max_branch_depth

    def evaluate_branch(
        self,
        branch: BranchPath,
        state: "SessionState",
        candidate_action: Optional[ActionCandidate] = None,
        current_best_score: Optional[float] = None,
    ) -> PruningDecision:
        """Evalúa si una rama debe ser podada de acuerdo a las 6 dimensiones."""

        # 1. Dimensión: Evidencias y Premisas Requeridas
        if candidate_action and candidate_action.requires_evidence:
            existing_evidence_ids = {ev.id for ev in state.evidence}
            missing_ev = [req for req in candidate_action.requires_evidence if req not in existing_evidence_ids]
            if missing_ev:
                action_name = candidate_action.tool_call.tool_name if candidate_action.tool_call else ""
                if action_name in ("run_command", "write_file", "edit_file", "modify_state"):
                    return PruningDecision(
                        should_prune=True,
                        category=PruningCategory.EVIDENCE_CONTRADICTION,
                        reason=f"Operación con efectos colaterales carece de evidencias previas requeridas: {missing_ev}",
                        severity=3,
                        details={"missing_evidence": missing_ev},
                    )

        # 2. Dimensión: Riesgo Operacional Extremo
        if candidate_action:
            risk_ass = self.risk_engine.assess_action_risk(candidate_action)
            if risk_ass.level == RiskLevel.CRITICAL:
                reasons_str = ", ".join(risk_ass.reasons) if risk_ass.reasons else "Riesgo no mitigable"
                return PruningDecision(
                    should_prune=True,
                    category=PruningCategory.RISK_THRESHOLD,
                    reason=f"Riesgo operacional crítico no mitigable: {reasons_str}",
                    severity=4,
                    details={"risk_level": risk_ass.level.value, "reasons": risk_ass.reasons},
                )

        # 3. Dimensión: Seguridad y Políticas Normativas
        if branch.steps:
            last_step = branch.steps[-1]
            if last_step.decision and last_step.decision.status == DecisionStatus.BLOCK:
                reasons = ", ".join(last_step.decision.reason_codes or ["BLOCK"])
                return PruningDecision(
                    should_prune=True,
                    category=PruningCategory.SECURITY_VIOLATION,
                    reason=f"Decisión de política BLOCK: {reasons}",
                    severity=5,
                    details={"step_id": last_step.step_id, "codes": last_step.decision.reason_codes},
                )

        if candidate_action:
            cand_decision, _ = self.policy_engine.evaluate_action(action=candidate_action, state=state)
            if cand_decision.status == DecisionStatus.BLOCK:
                reasons = ", ".join(cand_decision.reason_codes or ["BLOCK"])
                return PruningDecision(
                    should_prune=True,
                    category=PruningCategory.SECURITY_VIOLATION,
                    reason=f"Acción candidata bloqueada por política: {reasons}",
                    severity=5,
                    details={"action_id": candidate_action.id, "codes": cand_decision.reason_codes},
                )

        # 4. Dimensión: Bucles y Ciclos de Ejecución (LoopDetector)
        schema_steps: List[Step] = []
        for s in branch.steps:
            tool_name = s.action.tool_call.tool_name if s.action.tool_call else None
            tool_args = s.action.tool_call.arguments if s.action.tool_call else None
            schema_steps.append(
                Step(
                    id=s.step_id,
                    step_type=StepType.TOOL_CALL if tool_name else StepType.THOUGHT,
                    content=s.action.description or "",
                    tool_name=tool_name,
                    tool_args=tool_args,
                )
            )

        if schema_steps or candidate_action:
            available_claims = {ev.claim for ev in state.evidence}
            loop_rep = self.loop_detector.analyze_trajectory(
                steps=schema_steps,
                candidate=candidate_action,
                available_evidence_claims=available_claims,
            )
            # Solo podar por LOOP_DETECTED si hay una anomalía real de convergencia cíclica
            if loop_rep.loop_detected and loop_rep.convergence != ConvergenceAnomaly.NONE and loop_rep.severity >= 3:
                return PruningDecision(
                    should_prune=True,
                    category=PruningCategory.LOOP_DETECTED,
                    reason=f"Ciclo o estancamiento detectado: {loop_rep.explanation}",
                    severity=loop_rep.severity,
                    details={"loop_type": loop_rep.loop_type.value if loop_rep.loop_type else "unknown"},
                )

        # 4. Dimensión 4: Evidencias y Premisas Requeridas
        if candidate_action and candidate_action.requires_evidence:
            existing_evidence_ids = {ev.id for ev in state.evidence}
            missing_ev = [req for req in candidate_action.requires_evidence if req not in existing_evidence_ids]
            # Si requiere evidencias críticas no obtenidas y es una operación no reversible
            if missing_ev:
                action_name = candidate_action.tool_call.tool_name if candidate_action.tool_call else ""
                if action_name in ("run_command", "write_file", "edit_file", "modify_state"):
                    return PruningDecision(
                        should_prune=True,
                        category=PruningCategory.EVIDENCE_CONTRADICTION,
                        reason=f"Operación con efectos colaterales carece de evidencias previas requeridas: {missing_ev}",
                        severity=3,
                        details={"missing_evidence": missing_ev},
                    )

        # 5. Dimensión 5: Cota Presupuestaria de Profundidad (Budget Exhaustion)
        if branch.depth >= self.max_branch_depth:
            return PruningDecision(
                should_prune=True,
                category=PruningCategory.BUDGET_EXHAUSTION,
                reason=f"Profundidad máxima de rama alcanzada ({branch.depth}/{self.max_branch_depth})",
                severity=2,
                details={"depth": branch.depth, "max_depth": self.max_branch_depth},
            )

        # 6. Dimensión 6: Degradación de Puntuación (Score Degradation)
        comp_score = branch.score.composite_score
        if comp_score < self.min_composite_score and branch.depth >= 2:
            return PruningDecision(
                should_prune=True,
                category=PruningCategory.SCORE_DEGRADATION,
                reason=f"Puntuación compuesta ({comp_score}) inferior al umbral mínimo ({self.min_composite_score})",
                severity=2,
                details={"score": comp_score, "min_score": self.min_composite_score},
            )

        if current_best_score is not None and branch.depth >= 2:
            gap = current_best_score - comp_score
            if gap > self.max_relative_gap:
                return PruningDecision(
                    should_prune=True,
                    category=PruningCategory.SCORE_DEGRADATION,
                    reason=f"Brecha relativa excesiva con la mejor rama ({gap:.3f} > {self.max_relative_gap})",
                    severity=2,
                    details={"score": comp_score, "best_score": current_best_score, "gap": gap},
                )

        # No hay causal de poda
        return PruningDecision(should_prune=False)

    def apply_pruning(
        self,
        branch: BranchPath,
        state: "SessionState",
        decision: PruningDecision,
    ) -> bool:
        """Aplica la decisión de poda marcando la rama y sincronizando en state.metadata."""
        if not decision.should_prune:
            return False

        reason_str = f"[{decision.category.value if decision.category else 'PRUNED'}] {decision.reason or 'Descartada por poda temprana'}"
        branch.mark_pruned(reason_str)

        # Sincronizar en metadata para DAGContextSelector (Fase 2)
        pruned_list = state.metadata.setdefault("pruned_branches", [])
        if branch.branch_id not in pruned_list:
            pruned_list.append(branch.branch_id)

        return True
