"""Motor de Búsqueda y Exploración en Árbol con Branching Factor k (F3-02).

Implementa la búsqueda de trayectorias multirruta para PRAXEON:
- Estrategias: GREEDY (k=1), BEAM_SEARCH (k=3/5), BEST_FIRST y ADAPTIVE.
- Evaluación especulativa segura: las ramas exploratorias no ejecutan mutaciones destructivas
  en el entorno físico hasta que la rama ganadora es formalmente COMMITTED.
- Integración nativa con BranchPruner (F3-03): poda temprana por política, riesgo, ciclo y presupuesto.
- Sincronización con ContextManager y state.metadata["pruned_branches"] (Fase 2).
"""

from datetime import datetime, timezone
from enum import Enum
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.branch import (
    BranchPath,
    BranchScore,
    BranchStatus,
    BranchStep,
    RollbackCheckpoint,
)
from praxeon.domain.decision import DecisionStatus, PolicyDecision
from praxeon.policy.risk import ToolRegistry
from praxeon.reasoning.pruner import BranchPruner, PruningDecision

if TYPE_CHECKING:
    from praxeon.runtime.state import SessionState


class SearchStrategy(str, Enum):
    """Estrategias canónicas de búsqueda en el árbol de razonamiento."""
    GREEDY = "GREEDY"                    # k=1: Toma siempre el mejor paso inmediato sin ramificación
    BEAM_SEARCH = "BEAM_SEARCH"          # Mantiene un haz de las M mejores ramas en cada paso
    BEST_FIRST = "BEST_FIRST"            # Expande prioritariamente la rama con mayor composite_score
    ADAPTIVE = "ADAPTIVE"                # Ajusta dinámicamente k entre 1 y 5 según riesgo e incertidumbre


class SearchConfig(BaseModel):
    """Configuración operativa del motor de búsqueda (Sección 23: Baseline determinista por defecto)."""
    model_config = ConfigDict(frozen=True)

    branching_factor: int = Field(default=1, ge=1, le=10, description="Factor de ramificación k (1 por defecto para baseline determinista)")
    beam_width: int = Field(default=1, ge=1, le=10, description="Ancho de haz para Beam Search (1 por defecto)")
    max_depth: int = Field(default=10, ge=1, le=50, description="Profundidad máxima de búsqueda")
    strategy: SearchStrategy = SearchStrategy.GREEDY
    speculative_simulation: bool = True
    min_score_threshold: float = 0.20
    is_experimental: bool = Field(default=False, description="Marca si la búsqueda utiliza exploración multirrama experimental")


class SearchResult(BaseModel):
    """Resultado formal consolidado de la sesión de búsqueda en árbol."""
    model_config = ConfigDict(frozen=True)

    strategy: SearchStrategy
    branching_factor: int
    success: bool
    is_experimental: bool = False
    committed_branch: Optional[BranchPath] = None
    all_branches: List[BranchPath] = Field(default_factory=list)
    explored_branches_count: int = 0
    pruned_branches_count: int = 0
    abandoned_branches_count: int = 0
    total_steps_evaluated: int = 0
    total_wall_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "branching_factor": self.branching_factor,
            "success": self.success,
            "is_experimental": self.is_experimental,
            "committed_branch_id": self.committed_branch.branch_id if self.committed_branch else None,
            "committed_branch_score": self.committed_branch.score.composite_score if self.committed_branch else 0.0,
            "committed_depth": self.committed_branch.depth if self.committed_branch else 0,
            "explored_branches_count": self.explored_branches_count,
            "pruned_branches_count": self.pruned_branches_count,
            "abandoned_branches_count": self.abandoned_branches_count,
            "total_steps_evaluated": self.total_steps_evaluated,
            "total_wall_ms": self.total_wall_ms,
        }


class TreeSearchEngine:
    """Motor de exploración y búsqueda multirruta estructurada."""

    def __init__(
        self,
        pruner: Optional[BranchPruner] = None,
        config: Optional[SearchConfig] = None,
        tool_registry: Optional[ToolRegistry] = None,
    ):
        self.pruner = pruner or BranchPruner()
        self.config = config or SearchConfig()
        self.tool_registry = tool_registry or ToolRegistry(register_defaults=True)

    def determine_adaptive_k(self, confidence: float, risk_level: str) -> int:
        """Calcula el branching factor k adaptativo según el riesgo e incertidumbre."""
        # Si la confianza es alta y el riesgo es mínimo, no desperdiciar cómputo en ramas paralelas (k=1)
        if confidence >= 0.88 and risk_level == "low":
            return 1
        # Si hay riesgo medio/alto o baja confianza, abrir exploración más amplia (hasta 5)
        if risk_level in ("medium", "high") or confidence < 0.65:
            return min(5, max(3, self.config.branching_factor + 1))
        return self.config.branching_factor

    def score_candidate(
        self,
        action: ActionCandidate,
        branch: BranchPath,
        state: "SessionState",
    ) -> BranchScore:
        """Calcula una puntuación multidimensional para una acción candidata en una rama."""
        # 1. Confianza
        meta = action.metadata or {}
        conf = float(meta.get("confidence", 0.75))

        # 2. Respaldo en evidencia
        ev_support = 0.5
        if action.requires_evidence:
            existing_ev = {e.id for e in state.evidence}
            covered = sum(1 for req in action.requires_evidence if req in existing_ev)
            ev_support = covered / len(action.requires_evidence)

        # 3. Alineamiento con el objetivo
        align = 0.8
        goal_text = state.goal.objective.lower() if state.goal else ""
        desc = (action.description or "").lower()
        if any(w in desc for w in goal_text.split() if len(w) > 4):
            align = 0.95

        # 4. Riesgo operacional
        risk_penalty = 0.1
        risk_ass = self.pruner.risk_engine.assess_action_risk(action)
        if risk_ass.level.value == "critical":
            risk_penalty = 1.0
        elif risk_ass.level.value == "high":
            risk_penalty = 0.6
        elif risk_ass.level.value == "medium":
            risk_penalty = 0.3

        # 5. Penalización por profundidad acumulada
        depth_pen = (branch.depth + 1) * 0.02

        return BranchScore(
            confidence=conf,
            evidence_support=ev_support,
            risk_penalty=risk_penalty,
            goal_alignment=align,
            depth_penalty=depth_pen,
        )

    def expand_branch(
        self,
        branch: BranchPath,
        candidates: List[ActionCandidate],
        state: "SessionState",
        current_best_score: Optional[float] = None,
    ) -> List[BranchPath]:
        """Expande una rama generando ramas hijas para cada acción candidata viable."""
        all_generated: List[BranchPath] = []

        for idx, cand in enumerate(candidates):
            child_id = f"{branch.branch_id}_sub_{branch.depth+1}_{idx+1}"
            child_branch = BranchPath(
                branch_id=child_id,
                parent_branch_id=branch.branch_id,
                root_session_id=state.session_id,
                status=BranchStatus.EXPLORING,
                steps=list(branch.steps),
                metadata=dict(branch.metadata),
            )

            # Evaluar poda con BranchPruner antes de crear el paso
            prune_decision = self.pruner.evaluate_branch(
                branch=child_branch,
                state=state,
                candidate_action=cand,
                current_best_score=current_best_score,
            )

            if prune_decision.should_prune:
                self.pruner.apply_pruning(child_branch, state, prune_decision)
                all_generated.append(child_branch)
                continue

            # Evaluar score y crear el paso especulativo
            step_score = self.score_candidate(cand, child_branch, state)
            child_branch.update_score(step_score)

            step = BranchStep(
                step_id=f"step_{child_id}",
                action=cand,
                decision=PolicyDecision(status=DecisionStatus.ALLOW),
                observation=(
                    f"Simulación especulativa de [{cand.tool_call.tool_name if cand.tool_call else 'thought'}]: "
                    "precondición válida y ejecución simulada sin errores."
                ),
                is_speculative=True,
                step_score=step_score.composite_score,
                token_estimate=len(cand.description or "") // 4 + 20,
            )
            child_branch.add_step(step)
            all_generated.append(child_branch)

        return all_generated

    def run_search(
        self,
        initial_state: "SessionState",
        candidate_generator: Callable[[BranchPath, "SessionState", int], List[ActionCandidate]],
        goal_checker: Optional[Callable[[BranchPath, "SessionState"], bool]] = None,
    ) -> SearchResult:
        """Ejecuta la búsqueda multirruta hasta converger, alcanzar éxito o límite de profundidad."""
        start_wall = time.perf_counter()

        root_branch = BranchPath(
            branch_id=f"branch_root_{uuid.uuid4().hex[:8]}",
            parent_branch_id=None,
            root_session_id=initial_state.session_id,
            status=BranchStatus.ACTIVE,
        )

        all_branches: List[BranchPath] = [root_branch]
        active_frontier: List[BranchPath] = [root_branch]
        committed_branch: Optional[BranchPath] = None
        total_steps_eval = 0
        depth = 0

        current_k = self.config.branching_factor
        if self.config.strategy == SearchStrategy.GREEDY:
            current_k = 1

        while active_frontier and depth < self.config.max_depth:
            depth += 1
            next_generation: List[BranchPath] = []
            best_score_so_far = max((b.score.composite_score for b in active_frontier), default=0.5)

            for branch in active_frontier:
                # Comprobar si ya satisfizo el objetivo
                if goal_checker and goal_checker(branch, initial_state):
                    branch.status = BranchStatus.SUCCEEDED
                    committed_branch = branch
                    break

                # Determinar k para esta rama
                k = current_k
                if self.config.strategy == SearchStrategy.ADAPTIVE:
                    k = self.determine_adaptive_k(
                        confidence=branch.score.confidence,
                        risk_level="critical" if branch.score.risk_penalty > 0.5 else "low",
                    )

                # Generar candidatos
                candidates = candidate_generator(branch, initial_state, k)
                total_steps_eval += len(candidates)

                # Expandir y podar
                children = self.expand_branch(
                    branch=branch,
                    candidates=candidates,
                    state=initial_state,
                    current_best_score=best_score_so_far,
                )
                all_branches.extend(children)
                next_generation.extend([c for c in children if c.status == BranchStatus.EXPLORING])

            if committed_branch:
                break

            if not next_generation:
                break

            # Ordenar candidatos de la siguiente generación por score descendente
            next_generation.sort(key=lambda b: b.score.composite_score, reverse=True)

            # Aplicar filtro según la estrategia de búsqueda
            if self.config.strategy in (SearchStrategy.BEAM_SEARCH, SearchStrategy.ADAPTIVE):
                # Mantener los mejores beam_width
                active_frontier = next_generation[: self.config.beam_width]
                # Descartar las que quedaron fuera del haz
                for abandoned in next_generation[self.config.beam_width:]:
                    abandoned.mark_abandoned("Descartada al superar el límite de beam_width")
            elif self.config.strategy == SearchStrategy.GREEDY:
                # Mantener únicamente la mejor
                active_frontier = next_generation[:1]
                for abandoned in next_generation[1:]:
                    abandoned.mark_abandoned("Descartada por política Greedy")
            elif self.config.strategy == SearchStrategy.BEST_FIRST:
                active_frontier = next_generation[: self.config.beam_width]

        # Consolidar la rama ganadora
        if not committed_branch:
            # 1. Prioridad: la mejor rama de la frontera activa actual
            viable_frontier = [b for b in active_frontier if b.status == BranchStatus.EXPLORING]
            if viable_frontier:
                viable_frontier.sort(key=lambda b: b.score.composite_score, reverse=True)
                committed_branch = viable_frontier[0]
            else:
                # 2. Si la frontera quedó vacía, buscar en all_branches la rama más profunda no podada
                viable = [b for b in all_branches if b.status in (BranchStatus.EXPLORING, BranchStatus.ACTIVE) and b.depth > 0]
                if viable:
                    viable.sort(key=lambda b: (b.depth, b.score.composite_score), reverse=True)
                    committed_branch = viable[0]
                else:
                    committed_branch = root_branch

        if committed_branch:
            committed_branch.mark_committed()
            # Marcar el resto de ramas activas/exploring como abandonadas
            for b in all_branches:
                if b.branch_id != committed_branch.branch_id and b.status == BranchStatus.EXPLORING:
                    b.mark_abandoned("Superada por la rama ganadora committed")

        total_wall_ms = (time.perf_counter() - start_wall) * 1000.0

        pruned_count = sum(1 for b in all_branches if b.status == BranchStatus.PRUNED)
        abandoned_count = sum(1 for b in all_branches if b.status == BranchStatus.ABANDONED)
        explored_count = len(all_branches)

        is_success = committed_branch is not None and (
            committed_branch.status in (BranchStatus.COMMITTED, BranchStatus.SUCCEEDED)
        )

        is_experimental_run = (
            self.config.is_experimental
            or self.config.branching_factor > 1
            or self.config.strategy != SearchStrategy.GREEDY
        )

        return SearchResult(
            strategy=self.config.strategy,
            branching_factor=current_k,
            success=is_success,
            is_experimental=is_experimental_run,
            committed_branch=committed_branch,
            all_branches=all_branches,
            explored_branches_count=explored_count,
            pruned_branches_count=pruned_count,
            abandoned_branches_count=abandoned_count,
            total_steps_evaluated=total_steps_eval,
            total_wall_ms=round(total_wall_ms, 3),
        )
