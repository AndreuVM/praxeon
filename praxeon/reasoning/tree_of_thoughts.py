"""Motor de Búsqueda Heurística Top-K y Branching Adaptativo (Tree of Thoughts).

Implementa la Fase 6 del Roadmap de PRAXEON:
- Exploración de múltiples alternativas concurrentes (Tree of Thoughts) en el espacio de acciones.
- Ponderación heurística mediante System-1 Fast Reflex Inference (LAYA / TypeSafe):
  puntuaciones de progreso (progress_probability), fundamentación (grounded_probability)
  y penalización de repetición/loop (loop_probability).
- Poda inteligente multidimensional:
  * Poda temprana por bucle cognitivo (loop_probability >= threshold).
  * Poda por estancamiento heurístico sin ganancia de progreso entre pasos (STAGNATION).
  * Poda normativa por política (BLOCK / RISK_CRITICAL).
- Selección y consolidación formal de la rama de pensamiento ganadora (COMMITTED).
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.branch import BranchPath, BranchScore, BranchStatus, BranchStep
from praxeon.domain.decision import DecisionStatus, PolicyDecision
from praxeon.domain.interfaces import ReasoningProvider
from praxeon.domain.models import ProviderAssessment
from praxeon.policy.engine import PolicyEngine
from praxeon.policy.risk import ToolRegistry
from praxeon.providers.laya import LayaProvider
from praxeon.providers.replay import ReplayProvider
from praxeon.reasoning.pruner import BranchPruner, PruningCategory, PruningDecision
from praxeon.runtime.state import SessionState


class ThoughtNode(BaseModel):
    """Nodo individual dentro del árbol de razonamiento Tree of Thoughts."""
    model_config = ConfigDict(frozen=False)

    thought_id: str
    parent_id: Optional[str] = None
    depth: int = 0
    action: ActionCandidate
    heuristic_score: float = 0.5
    progress_score: float = 0.5
    grounded_score: float = 0.8
    loop_probability: float = 0.0
    status: BranchStatus = BranchStatus.EXPLORING
    pruning_reason: Optional[str] = None
    pruning_category: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TreeOfThoughtsConfig(BaseModel):
    """Configuración para el motor de exploración Tree of Thoughts Top-K."""
    model_config = ConfigDict(frozen=True)

    top_k: int = Field(default=3, ge=1, le=10, description="Número de alternativas heurísticas concurrentes a explorar por nivel")
    beam_width: int = Field(default=2, ge=1, le=10, description="Número de ramas activas mantenidas en la frontera")
    max_depth: int = Field(default=6, ge=1, le=30, description="Profundidad máxima del árbol de pensamientos")
    stagnation_patience: int = Field(default=2, ge=1, le=5, description="Pasos consecutivos sin progreso permitidos antes de podar por estancamiento")
    stagnation_min_delta: float = Field(default=0.04, ge=0.0, le=0.5, description="Incremento mínimo de progreso requerido para evitar estancamiento")
    loop_threshold: float = Field(default=0.65, ge=0.1, le=1.0, description="Umbral de loop_probability para podar por bucle cognitivo")
    min_heuristic_threshold: float = Field(default=0.20, ge=0.0, le=1.0, description="Umbral mínimo heurístico admisible")
    weight_progress: float = 0.45
    weight_grounded: float = 0.30
    weight_novelty: float = 0.15
    weight_loop_penalty: float = 0.80
    is_experimental: bool = True


class TreeOfThoughtsResult(BaseModel):
    """Resultado estructurado de la ejecución de Tree of Thoughts."""
    model_config = ConfigDict(frozen=True)

    success: bool
    root_thought_id: str
    committed_node: Optional[ThoughtNode] = None
    committed_path: List[ActionCandidate] = Field(default_factory=list)
    all_nodes: List[ThoughtNode] = Field(default_factory=list)
    total_thoughts_evaluated: int = 0
    pruned_stagnant_count: int = 0
    pruned_loop_count: int = 0
    pruned_policy_count: int = 0
    pruned_total_count: int = 0
    top_k_used: int = 3
    average_progress_score: float = 0.0
    wall_time_ms: float = 0.0
    is_experimental: bool = True


class TreeOfThoughtsEngine:
    """Motor de búsqueda en árbol de pensamientos guiado por heurísticas System-1 y poda adaptativa."""

    def __init__(
        self,
        provider: Optional[ReasoningProvider] = None,
        policy_engine: Optional[PolicyEngine] = None,
        pruner: Optional[BranchPruner] = None,
        config: Optional[TreeOfThoughtsConfig] = None,
    ):
        self.provider = provider or LayaProvider(backend="simulated")
        self.policy_engine = policy_engine or PolicyEngine()
        self.pruner = pruner or BranchPruner(policy_engine=self.policy_engine)
        self.config = config or TreeOfThoughtsConfig()

    def compute_heuristic_score(
        self,
        assessment: ProviderAssessment,
        action: ActionCandidate,
    ) -> Tuple[float, float, float, float]:
        """Calcula la puntuación heurística combinada de System-1 (progress, grounded, loop, composite)."""
        prog = float(assessment.progress_probability if assessment.progress_probability is not None else 0.5)
        ground = float(assessment.grounded_probability if assessment.grounded_probability is not None else 0.8)
        nov = float(assessment.novelty_probability if assessment.novelty_probability is not None else 0.5)
        loop = float(assessment.loop_probability if assessment.loop_probability is not None else 0.0)

        # Si el proveedor emite score intrínseco (ej. LAYA Fast Reflex)
        choice_score = None
        if assessment.metadata and isinstance(assessment.metadata, dict):
            choice = assessment.metadata.get("choice", {})
            if isinstance(choice, dict) and "score" in choice:
                choice_score = float(choice["score"])

        if choice_score is not None:
            # Ponderar la decisión reflexiva neuronal con la probabilidad de progreso
            composite = (choice_score * 0.5) + (prog * 0.3) + (ground * 0.2) - (loop * self.config.weight_loop_penalty)
        else:
            composite = (
                (prog * self.config.weight_progress)
                + (ground * self.config.weight_grounded)
                + (nov * self.config.weight_novelty)
                - (loop * self.config.weight_loop_penalty)
            )

        # Normalizar entre 0.0 y 1.0
        clamped_score = max(0.0, min(1.0, round(composite, 4)))
        return clamped_score, prog, ground, loop

    def evaluate_candidates_top_k(
        self,
        state: SessionState,
        candidates: List[ActionCandidate],
        k: Optional[int] = None,
    ) -> List[Tuple[ActionCandidate, ProviderAssessment, float]]:
        """Evalúa un lote de candidatos con System-1 en una pasada y devuelve los Top-K ordenados."""
        if not candidates:
            return []

        limit_k = k or self.config.top_k
        # Evaluación en lote por System-1
        assessments = self.provider.evaluate(state, candidates)
        scored: List[Tuple[ActionCandidate, ProviderAssessment, float]] = []

        for cand, assess in zip(candidates, assessments):
            h_score, _, _, _ = self.compute_heuristic_score(assess, cand)
            scored.append((cand, assess, h_score))

        # Ordenar de mayor a menor puntuación heurística
        scored.sort(key=lambda item: item[2], reverse=True)
        return scored[:limit_k]

    def run_tree_search(
        self,
        initial_state: SessionState,
        candidate_generator: Callable[[ThoughtNode, SessionState, int], List[ActionCandidate]],
        goal_checker: Optional[Callable[[ThoughtNode, SessionState], bool]] = None,
    ) -> TreeOfThoughtsResult:
        """Ejecuta la búsqueda multirruta Tree of Thoughts con selección Top-K y poda de ramas estancadas."""
        t_start = time.perf_counter()

        root_node = ThoughtNode(
            thought_id=f"tot_root_{uuid.uuid4().hex[:8]}",
            parent_id=None,
            depth=0,
            action=ActionCandidate(id="act_genesis", description="Pensamiento raíz inicial"),
            heuristic_score=0.75,
            progress_score=0.5,
            status=BranchStatus.ACTIVE,
        )

        all_nodes: List[ThoughtNode] = [root_node]
        frontier: List[ThoughtNode] = [root_node]
        committed_node: Optional[ThoughtNode] = None

        pruned_stagnant = 0
        pruned_loop = 0
        pruned_policy = 0
        total_eval = 0

        depth = 0
        while frontier and depth < self.config.max_depth:
            depth += 1
            next_frontier_candidates: List[ThoughtNode] = []

            for current_node in frontier:
                # Comprobar si el pensamiento actual ya cumple los criterios de éxito del objetivo
                if goal_checker and goal_checker(current_node, initial_state):
                    current_node.status = BranchStatus.SUCCEEDED
                    committed_node = current_node
                    break

                # 1. Generar pensamientos/acciones alternativas para este nodo
                raw_candidates = candidate_generator(current_node, initial_state, self.config.top_k * 2)
                total_eval += len(raw_candidates)
                if not raw_candidates:
                    continue

                # 2. Filtrar y ordenar los Top-K candidatos según la heurística System-1
                top_k_evaluated = self.evaluate_candidates_top_k(
                    state=initial_state,
                    candidates=raw_candidates,
                    k=self.config.top_k,
                )

                for cand, assess, h_score in top_k_evaluated:
                    _, prog, ground, loop = self.compute_heuristic_score(assess, cand)
                    child_id = f"{current_node.thought_id}_d{depth}_{cand.id}"

                    child_node = ThoughtNode(
                        thought_id=child_id,
                        parent_id=current_node.thought_id,
                        depth=depth,
                        action=cand,
                        heuristic_score=h_score,
                        progress_score=prog,
                        grounded_score=ground,
                        loop_probability=loop,
                        status=BranchStatus.EXPLORING,
                        metadata={
                            "provider": assess.provider,
                            "assessment_confidence": assess.confidence,
                        },
                    )
                    all_nodes.append(child_node)

                    # 3. Poda 1: Detección temprana de bucle cognitivo (LOOP_DETECTED)
                    if loop >= self.config.loop_threshold:
                        child_node.status = BranchStatus.PRUNED
                        child_node.pruning_category = PruningCategory.LOOP_DETECTED.value
                        child_node.pruning_reason = f"Bucle cognitivo detectado por System-1 (loop_prob={loop:.2f})"
                        pruned_loop += 1
                        continue

                    # 4. Poda 2: Seguridad y Normativa (SECURITY_VIOLATION / BLOCK)
                    policy_dec, _ = self.policy_engine.evaluate_action(action=cand, state=initial_state)
                    if policy_dec.status == DecisionStatus.BLOCK:
                        child_node.status = BranchStatus.PRUNED
                        child_node.pruning_category = PruningCategory.SECURITY_VIOLATION.value
                        child_node.pruning_reason = f"Acción rechazada por política formal: {policy_dec.reason_codes}"
                        pruned_policy += 1
                        continue

                    # 5. Poda 3: Estancamiento Heurístico (STAGNATION)
                    # Si el nuevo paso no aporta un delta de progreso positivo respecto a su padre
                    progress_delta = prog - current_node.progress_score
                    if depth >= 2 and progress_delta < self.config.stagnation_min_delta and prog < 0.85:
                        child_node.status = BranchStatus.PRUNED
                        child_node.pruning_category = "STAGNATION"
                        child_node.pruning_reason = (
                            f"Rama estancada sin incremento de progreso suficiente "
                            f"(delta={progress_delta:.3f} < {self.config.stagnation_min_delta})"
                        )
                        pruned_stagnant += 1
                        continue

                    # 6. Poda 4: Puntuación mínima absoluta
                    if h_score < self.config.min_heuristic_threshold:
                        child_node.status = BranchStatus.PRUNED
                        child_node.pruning_category = PruningCategory.SCORE_DEGRADATION.value
                        child_node.pruning_reason = f"Puntuación heurística {h_score:.3f} inferior al umbral mínimo"
                        continue

                    next_frontier_candidates.append(child_node)

            if committed_node:
                break

            if not next_frontier_candidates:
                break

            # Ordenar candidatos de la siguiente generación por score heurístico compuesto
            next_frontier_candidates.sort(key=lambda n: n.heuristic_score, reverse=True)

            # Conservar los mejores beam_width y abandonar el resto
            frontier = next_frontier_candidates[: self.config.beam_width]
            for discarded in next_frontier_candidates[self.config.beam_width:]:
                discarded.status = BranchStatus.ABANDONED
                discarded.pruning_reason = "Descartada por poda competitiva (límite de beam_width)"

        # Selección de la rama ganadora
        if not committed_node:
            viable = [n for n in frontier if n.status == BranchStatus.EXPLORING]
            if viable:
                viable.sort(key=lambda n: n.heuristic_score, reverse=True)
                committed_node = viable[0]
            else:
                # Si la frontera quedó vacía, buscar el pensamiento no podado con mayor progreso
                candidates_pool = [n for n in all_nodes if n.status in (BranchStatus.EXPLORING, BranchStatus.ACTIVE) and n.depth > 0]
                if candidates_pool:
                    candidates_pool.sort(key=lambda n: (n.depth, n.heuristic_score), reverse=True)
                    committed_node = candidates_pool[0]
                else:
                    committed_node = root_node

        if committed_node:
            committed_node.status = BranchStatus.COMMITTED

        # Reconstruir la ruta de pensamientos desde el nodo committed hacia la raíz
        path: List[ActionCandidate] = []
        curr = committed_node
        node_map = {n.thought_id: n for n in all_nodes}
        while curr and curr.parent_id:
            if curr.action and curr.action.id != "act_genesis":
                path.append(curr.action)
            curr = node_map.get(curr.parent_id)
        path.reverse()

        wall_ms = (time.perf_counter() - t_start) * 1000.0
        pruned_total = sum(1 for n in all_nodes if n.status == BranchStatus.PRUNED)
        avg_progress = (
            sum(n.progress_score for n in all_nodes if n.status != BranchStatus.PRUNED)
            / max(1, sum(1 for n in all_nodes if n.status != BranchStatus.PRUNED))
        )

        return TreeOfThoughtsResult(
            success=committed_node is not None and committed_node.status in (BranchStatus.COMMITTED, BranchStatus.SUCCEEDED),
            root_thought_id=root_node.thought_id,
            committed_node=committed_node,
            committed_path=path,
            all_nodes=all_nodes,
            total_thoughts_evaluated=total_eval,
            pruned_stagnant_count=pruned_stagnant,
            pruned_loop_count=pruned_loop,
            pruned_policy_count=pruned_policy,
            pruned_total_count=pruned_total,
            top_k_used=self.config.top_k,
            average_progress_score=round(avg_progress, 4),
            wall_time_ms=round(wall_ms, 3),
            is_experimental=self.config.is_experimental,
        )
