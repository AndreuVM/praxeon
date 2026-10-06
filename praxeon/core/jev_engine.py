"""Motor de evaluación de acciones y trayectorias basado en TypeSafe AI (Saneado v0.2).

Corrige los fallos basales identificados en la auditoría:
- Erradica el fallback permisivo (fail-open) asignando disponibilidad False ante caídas.
- Parametriza BatchSemantics(independent=True/False) para evitar penalizaciones espurias en cascada.
- Emite tanto JEVScore analítico como ProviderAssessment multidimensional estructurado.
"""

from typing import Any, Dict, List, Optional, Tuple

from praxeon.config import PraxeonConfig, default_config
from praxeon.core.state_graph import StateGraph
from praxeon.core.typesafe_client import TypeSafeJEVClient
from praxeon.domain.models import ProviderAssessment
from praxeon.models.schema import (
    ActionCandidate,
    BatchSemantics,
    JEVScore,
    LoopReport,
    LoopType,
    Step,
    StepType,
    Trajectory,
)


class JEVEngine:
    """Motor de evaluación cognitiva sin supuestos permisivos en caídas."""

    def __init__(self, state_graph: StateGraph, config: Optional[PraxeonConfig] = None):
        self.graph = state_graph
        self.config = config or state_graph.config or default_config
        self.supervisor_name = getattr(self.config.provider, "name", "typesafe").lower()

        if self.supervisor_name in ("laya", "laya-system1", "laya-v1") or (
            self.config.provider.model and self.config.provider.model.lower().startswith("laya")
        ):
            from praxeon.providers.laya import LayaProvider
            backend = getattr(self.config.provider, "laya_backend", "auto")
            model_name = (
                self.config.provider.model
                if (self.config.provider.model and self.config.provider.model.lower().startswith("laya"))
                else "laya-v1-calibrated"
            )
            self.laya_provider = LayaProvider(backend=backend, model_name=model_name)
            self.typesafe_client = None
        else:
            self.laya_provider = None
            self.typesafe_client = TypeSafeJEVClient(self.config)

    def evaluate_candidate(self, candidate: ActionCandidate) -> JEVScore:
        """Evalúa un candidato individual mediante TypeSafe AI o LAYA System-1 y retorna su puntuación JEV estructurada."""
        typesafe_eval = None
        history_steps = [s for s in self.graph.get_all_steps() if s.id != candidate.id]

        if self.laya_provider is not None:
            from praxeon.domain.models import ActionCandidate as DomainActionCandidate, ToolCall
            domain_cand = DomainActionCandidate(
                id=candidate.id,
                description=candidate.description,
                tool_call=ToolCall(tool_name=candidate.tool_name, arguments=candidate.tool_args or {}) if candidate.tool_name else None,
                rationale=candidate.rationale,
            )
            class MockState:
                def __init__(self, goal, history):
                    self.session_id = "laya_eval_session"
                    self.goal = goal
                    self.evidence = []
                    self.steps = [
                        type("MockStep", (), {
                            "step_id": s.id,
                            "action": type("MockAction", (), {
                                "id": s.id,
                                "tool_call": type("MockTC", (), {"tool_name": s.tool_name}) if s.tool_name else None
                            })(),
                            "observation": s.content if s.step_type.value == "observation" else ""
                        })()
                        for s in history
                    ]

            assess = self.laya_provider.evaluate_action(MockState(self.graph.goal, history_steps), domain_cand)
            p_progress = assess.progress_probability
            delta_u = assess.novelty_probability
            loop_penalty = 1.6 if assess.loop_probability >= 0.65 else 0.0
            raw_jev = assess.analytical_jev
            total_jev = max(-1.0, min(1.0, float(raw_jev)))
            details = {
                "source": f"laya_{self.laya_provider.backend}",
                "goal": self.graph.goal,
                "raw_score": raw_jev,
                "laya_eval": assess.model_dump() if hasattr(assess, "model_dump") else assess.__dict__,
                "provider_available": True,
            }
            return JEVScore(
                candidate_id=candidate.id,
                p_progress=round(p_progress, 4),
                delta_u=round(delta_u, 4),
                loop_penalty=round(loop_penalty, 4),
                total_jev=round(total_jev, 4),
                details=details,
            )

        is_provider_up = self.typesafe_client is not None and self.typesafe_client.is_available()

        if is_provider_up:
            typesafe_eval = self.typesafe_client.evaluate_candidate(
                goal=self.graph.goal,
                history=history_steps,
                candidate=candidate,
            )

        # Hallazgo 3.2: Erradicación de Fail-Open
        if typesafe_eval is not None:
            p_progress = float(typesafe_eval.get("p_progress", 0.5))
            delta_u = float(typesafe_eval.get("delta_u", 0.5))
            loop_penalty = float(typesafe_eval.get("loop_penalty", 0.0))
            is_available = True
            raw_jev = ((p_progress + delta_u) / 2.0) - loop_penalty
            total_jev = max(-1.0, min(1.0, float(raw_jev)))
        else:
            # Proveedor indisponible o con fallo: NO asignar puntuación neutral permisiva
            p_progress = 0.0
            delta_u = 0.0
            loop_penalty = 0.0
            total_jev = -0.5  # Penalización preventiva para indicar indisponibilidad/incertidumbre
            raw_jev = -0.5
            is_available = False

        details = {
            "source": "typesafe_ai",
            "goal": self.graph.goal,
            "raw_score": raw_jev,
            "typesafe_eval": typesafe_eval,
            "provider_available": is_available,
        }

        return JEVScore(
            candidate_id=candidate.id,
            p_progress=round(p_progress, 4),
            delta_u=round(delta_u, 4),
            loop_penalty=round(loop_penalty, 4),
            total_jev=round(total_jev, 4),
            details=details,
        )

    def evaluate_candidate_assessment(self, candidate: ActionCandidate) -> ProviderAssessment:
        """Emite el juicio semántico formal desacoplado ProviderAssessment (Hallazgo 3.1)."""
        score = self.evaluate_candidate(candidate)
        ts_eval = score.details.get("typesafe_eval") or {}
        available = bool(score.details.get("provider_available", False))

        if not available:
            return ProviderAssessment(
                provider="typesafe",
                available=False,
                confidence=0.0,
                failure_reason="Proveedor TypeSafe AI no disponible o sin respuesta válida",
                reason_codes=["PROVIDER_OFFLINE"],
            )

        loop_prob = float(ts_eval.get("loop_penalty", score.loop_penalty))
        return ProviderAssessment(
            provider="typesafe",
            model="system-one-v1",
            available=True,
            confidence=float(ts_eval.get("confidence", 0.9)),
            loop_probability=loop_prob,
            grounded_probability=float(ts_eval.get("grounded_prob", 0.95 if loop_prob < 0.3 else 0.2)),
            progress_probability=score.p_progress,
            novelty_probability=score.delta_u,
            analytical_jev=score.total_jev,
            reason_codes=["EVALUATED_BY_TYPESAFE"],
        )

    def evaluate_candidate_chunk(
        self,
        candidates: List[ActionCandidate],
        batch_semantics: Optional[BatchSemantics] = None,
    ) -> List[Tuple[ActionCandidate, JEVScore, Optional[Dict[str, Any]]]]:
        """Evalúa un bloque agrupado de candidatos respetando BatchSemantics (Hallazgo 3.3)."""
        if not candidates:
            return []

        chunk_eval = None
        candidate_ids = {c.id for c in candidates}
        history_steps = [s for s in self.graph.get_all_steps() if s.id not in candidate_ids]

        if self.laya_provider is not None:
            from praxeon.domain.models import ActionCandidate as DomainActionCandidate, ToolCall
            class MockState:
                def __init__(self, goal, history):
                    self.session_id = "laya_chunk_session"
                    self.goal = goal
                    self.evidence = []
                    self.steps = [
                        type("MockStep", (), {
                            "step_id": s.id,
                            "action": type("MockAction", (), {
                                "id": s.id,
                                "tool_call": type("MockTC", (), {"tool_name": s.tool_name}) if s.tool_name else None
                            })(),
                            "observation": s.content if s.step_type.value == "observation" else ""
                        })()
                        for s in history
                    ]

            state = MockState(self.graph.goal, history_steps)
            flagged_idx = None
            is_hallucination = False
            hallucination_type = "none"
            sum_prog = 0.0
            sum_nov = 0.0
            sum_noul = 0.0
            assessments = []

            for idx, cand in enumerate(candidates):
                domain_cand = DomainActionCandidate(
                    id=cand.id,
                    description=cand.description,
                    tool_call=ToolCall(tool_name=cand.tool_name, arguments=cand.tool_args or {}) if cand.tool_name else None,
                    rationale=cand.rationale,
                )
                assess = self.laya_provider.evaluate_action(state, domain_cand)
                assessments.append(assess)
                sum_prog += assess.progress_probability
                sum_nov += assess.novelty_probability
                sum_noul += assess.loop_probability

                # Detectar intervención si el paso excede umbrales
                if flagged_idx is None:
                    if assess.loop_probability >= 0.65:
                        flagged_idx = idx
                        hallucination_type = "loop_repetition"
                    elif assess.grounded_probability < 0.35:
                        flagged_idx = idx
                        is_hallucination = True
                        hallucination_type = "unverified_assumption"

            n = len(candidates) or 1
            chunk_eval = {
                "source": f"laya_{self.laya_provider.backend}",
                "all_safe": (flagged_idx is None),
                "is_loop": (flagged_idx is not None and not is_hallucination),
                "is_hallucination": is_hallucination,
                "hallucination_type": hallucination_type,
                "flagged_index": flagged_idx,
                "noul_prob": sum_noul / n if flagged_idx is None else assessments[flagged_idx].loop_probability,
                "p_progress": sum_prog / n,
                "delta_u": sum_nov / n,
                "loop_penalty": 1.6 if flagged_idx is not None else 0.0,
                "confidence": 0.90,
                "model": self.laya_provider.model_name,
                "total_candidates": len(candidates),
                "explanation": f"Evaluado por LAYA System-1 ({self.laya_provider.backend})",
            }
        else:
            if self.typesafe_client is not None:
                chunk_eval = self.typesafe_client.evaluate_step_chunk(
                    goal=self.graph.goal,
                    history=history_steps,
                    candidates=candidates,
                )

        results: List[Tuple[ActionCandidate, JEVScore, Optional[Dict[str, Any]]]] = []
        flagged_idx = chunk_eval.get("flagged_index") if chunk_eval else None
        is_independent = batch_semantics.independent if batch_semantics else False

        for idx, cand in enumerate(candidates):
            if chunk_eval is not None:
                # Comprobar si este paso diverge
                if flagged_idx is not None and idx >= flagged_idx:
                    is_exact_flagged = (idx == flagged_idx)
                    # Si el lote es independiente, pasos posteriores al índice flaggeado no se penalizan en cascada
                    if is_independent and not is_exact_flagged:
                        p_progress = float(chunk_eval.get("p_progress", 0.7))
                        delta_u = float(chunk_eval.get("delta_u", 0.7))
                        loop_penalty = 0.0
                    else:
                        p_progress = 0.05 if is_exact_flagged else 0.01
                        delta_u = 0.0
                        loop_penalty = float(chunk_eval.get("loop_penalty", 1.5))
                else:
                    p_progress = float(chunk_eval.get("p_progress", 0.8))
                    delta_u = float(chunk_eval.get("delta_u", 0.8))
                    loop_penalty = 0.0

                raw_jev = ((p_progress + delta_u) / 2.0) - loop_penalty
                total_jev = max(-1.0, min(1.0, float(raw_jev)))
                is_available = True
            else:
                # Hallazgo 3.2: Proveedor offline en evaluación de chunk
                p_progress = 0.0
                delta_u = 0.0
                loop_penalty = 0.0
                total_jev = -0.5
                raw_jev = -0.5
                is_available = False

            details = {
                "source": "typesafe_ai_chunk",
                "goal": self.graph.goal,
                "raw_score": raw_jev,
                "chunk_eval": chunk_eval,
                "provider_available": is_available,
                "is_divergent": bool(flagged_idx is not None and (idx == flagged_idx or (not is_independent and idx > flagged_idx))),
            }

            score = JEVScore(
                candidate_id=cand.id,
                p_progress=round(p_progress, 4),
                delta_u=round(delta_u, 4),
                loop_penalty=round(loop_penalty, 4),
                total_jev=round(total_jev, 4),
                details=details,
            )
            results.append((cand, score, chunk_eval))

        return results

    def evaluate_step_chunk(
        self,
        steps: List[Step],
        batch_semantics: Optional[BatchSemantics] = None,
    ) -> List[Tuple[Step, JEVScore, Optional[Dict[str, Any]]]]:
        """Convierte y evalúa un bloque de pasos existentes o propuestos mediante TypeSafe AI."""
        candidates = [
            ActionCandidate(
                id=step.id,
                description=step.content,
                tool_name=step.tool_name,
                tool_args=step.tool_args,
            )
            for step in steps
        ]
        cand_results = self.evaluate_candidate_chunk(candidates, batch_semantics=batch_semantics)
        step_results: List[Tuple[Step, JEVScore, Optional[Dict[str, Any]]]] = []
        for step, (_, sc, metadata) in zip(steps, cand_results):
            step_results.append((step, sc, metadata))
        return step_results

    def evaluate_step(self, step: Step) -> JEVScore:
        """Evalúa un paso de razonamiento existente en el grafo mediante TypeSafe AI."""
        candidate = ActionCandidate(
            id=step.id,
            description=step.content,
            tool_name=step.tool_name,
            tool_args=step.tool_args,
        )
        return self.evaluate_candidate(candidate)

    def rank_candidates(
        self, candidates: List[ActionCandidate]
    ) -> List[Tuple[ActionCandidate, JEVScore]]:
        """Evalúa un lote de candidatos y los devuelve ordenados de mayor a menor JEV según TypeSafe AI."""
        scored: List[Tuple[ActionCandidate, JEVScore]] = []
        for cand in candidates:
            score = self.evaluate_candidate(cand)
            scored.append((cand, score))

        scored.sort(key=lambda item: item[1].total_jev, reverse=True)
        return scored

    def diagnose_trajectory(self, trajectory: Optional[Trajectory] = None) -> LoopReport:
        """Diagnostica la trayectoria actual o proporcionada usando TypeSafe AI."""
        traj = trajectory or Trajectory(
            session_id=self.graph.session_id,
            goal=self.graph.goal,
            steps=self.graph.get_all_steps(),
        )
        if self.typesafe_client is not None:
            return self.typesafe_client.diagnose_trajectory(traj)
        from praxeon.core.typesafe_client import TypeSafeJEVClient
        return TypeSafeJEVClient(self.config).diagnose_trajectory(traj)


# Nomenclatura canónica PRAXEON 1.0 (JEVEngine conservado para retrocompatibilidad)
PraxeonEngine = JEVEngine

