"""Cliente de integración con la API de TypeSafe AI y el modelo Jev (System One)."""

import logging
import os
import time
from typing import Any, Dict, List, Optional

from praxeon.config import PraxeonConfig, default_config
from praxeon.models.schema import ActionCandidate, LoopType, Step, StepType

logger = logging.getLogger("praxeon.typesafe")


class TypeSafeJEVClient:
    """Cliente para evaluar razonamiento y decisiones usando el modelo Jev de TypeSafe AI."""

    def __init__(self, config: Optional[PraxeonConfig] = None):
        self.config = config or default_config
        if self.config.typesafe_api_key is not None:
            self.api_key = self.config.typesafe_api_key
        elif self.config.use_typesafe_api:
            self.api_key = os.getenv("TYPESAFE_API_KEY")
        else:
            self.api_key = None
        self._client = None
        if self.api_key and self.config.use_typesafe_api:
            self._init_client()

    def _init_client(self) -> None:
        """Inicializa TypeSafeClient desde typesafe-sdk."""
        try:
            from typesafe_sdk import TypeSafeClient
            self._client = TypeSafeClient(api_key=self.api_key)
        except Exception as e:
            logger.warning(f"No se pudo inicializar TypeSafeClient: {e}")
            self._client = None

    def is_available(self) -> bool:
        """Verifica si el cliente y la clave API están listos para operar."""
        if not self.config.use_typesafe_api:
            return False
        return bool(self.api_key and self._client is not None)

    def _invoke_system_one_with_retry(
        self,
        state: Dict[str, Any],
        questions: Dict[str, Any],
        max_retries: int = 2,
    ) -> Any:
        """Invoca system_one con reintentos exponenciales ante errores de conexión o timeouts transitorios."""
        last_err = None
        for attempt in range(max_retries + 1):
            try:
                return self._client.system_one(
                    state=state,
                    questions=questions,
                    model=self.config.typesafe_model,
                )
            except Exception as e:
                last_err = e
                err_msg = str(e).lower()
                is_transient = any(kw in err_msg for kw in ("timeout", "connection", "503", "502", "504", "reset", "retry", "econnreset"))
                if attempt < max_retries and is_transient:
                    backoff = 0.5 * (2 ** attempt)
                    logger.info(f"Fallo transitorio en TypeSafe AI ({e}). Reintentando en {backoff:.1f}s...")
                    time.sleep(backoff)
                else:
                    break
        raise last_err

    def evaluate_step_chunk(
        self,
        goal: str,
        history: List[Step],
        candidates: List[ActionCandidate],
    ) -> Optional[Dict[str, Any]]:
        """Invoca el modelo Jev vía TypeSafe AI para evaluar un bloque agrupado de pasos en una sola llamada.
        
        Permite ahorrar peticiones a la API agrupando la supervisión de múltiples pasos
        y verificando activamente la fundamentación empírica para prevenir alucinaciones.
        """
        if not candidates:
            return None

        if not self.is_available():
            import json
            flagged_idx = None
            for i in range(1, len(candidates)):
                c_prev = candidates[i - 1]
                c_curr = candidates[i]
                if c_curr.tool_name and c_curr.tool_name == c_prev.tool_name and (c_curr.tool_args or {}) == (c_prev.tool_args or {}):
                    flagged_idx = i
                    break
            if flagged_idx is None and history and candidates:
                last_hist = None
                for s in reversed(history):
                    if s.tool_name:
                        last_hist = (s.tool_name, s.tool_args or {})
                        break
                if last_hist and candidates[0].tool_name == last_hist[0] and (candidates[0].tool_args or {}) == last_hist[1]:
                    flagged_idx = 0

            is_loop = (flagged_idx is not None)
            return {
                "source": "typesafe_offline_heuristic",
                "all_safe": not is_loop,
                "is_loop": is_loop,
                "loop_type": LoopType.ONE_HOP_TOOL_REPEAT if is_loop else LoopType.NONE,
                "is_hallucination": False,
                "hallucination_type": None,
                "flagged_index": flagged_idx,
                "noul_prob": 0.9 if is_loop else 0.05,
                "p_progress": 0.1 if is_loop else 0.8,
                "delta_u": 0.0 if is_loop else 0.8,
                "loop_penalty": 1.5 if is_loop else 0.0,
                "confidence": 0.9,
                "model": "offline-heuristic",
                "total_candidates": len(candidates),
            }

        try:
            from typesafe_sdk import Choice, Noul, Score

            recent_steps = [
                {
                    "step_id": s.id,
                    "type": s.step_type.value,
                    "tool": s.tool_name,
                    "args": s.tool_args,
                    "content": s.content[:2000],
                }
                for s in history[-8:]
            ]

            chunk_steps = [
                {
                    "index": idx,
                    "id": c.id,
                    "tool": c.tool_name,
                    "args": c.tool_args,
                    "description": c.description,
                    "rationale": c.rationale,
                }
                for idx, c in enumerate(candidates)
            ]

            state = {
                "goal": goal,
                "recent_trajectory": recent_steps,
                "candidate_steps_chunk": chunk_steps,
            }

            # Configurar preguntas agrupadas para TypeSafe System One
            divergence_criteria = {"none": "All candidate steps in the chunk are grounded and constructive"}
            for idx in range(len(candidates)):
                divergence_criteria[f"step_{idx}"] = f"Candidate step at index {idx} contains hallucination, loop or invalid premise"

            questions = {
                "has_hallucination_or_loop": Noul(
                    instructions=(
                        "Does any candidate step in candidate_steps_chunk contain hallucinations (inventing nonexistent files/paths, "
                        "claiming ungrounded facts, calling invalid tools) or unconstructive cyclic loops? "
                        "Note: A 'finish' action delivering the final task summary or solution to the user is valid and should NOT be flagged as hallucination or loop."
                    )
                ),
                "divergence_step": Choice(
                    instructions="Identify which candidate step index first diverges, hallucinates, or loops (or 'none' if all steps are sound)",
                    criteria=divergence_criteria,
                ),
                "hallucination_type": Choice(
                    instructions="Classify the specific flaw or hallucination pattern if present in the chunk",
                    criteria={
                        "none": "No hallucination or loop detected, or valid finish action",
                        "invented_fact_or_file": "Mentions or accesses nonexistent files, variables, or ungrounded facts",
                        "unverified_assumption": "Assumes a bug or error is resolved without empirical verification",
                        "tool_hallucination": "Invokes nonexistent tools or invalid parameters",
                        "loop_repetition": "Repeats previous tool call or cyclic actions",
                        "semantic_fixation": "Fixates on an already refuted hypothesis",
                    },
                ),
                "progress": Score(
                    instructions="Rate the projected progress of this step chunk toward achieving the goal",
                    criteria=[
                        "counterproductive",
                        "irrelevant",
                        "minor_progress",
                        "significant_progress",
                        "direct_solution",
                    ],
                ),
                "novelty": Score(
                    instructions="Rate the novelty and information gain of this chunk compared to previously explored states",
                    criteria=["redundant", "minor_variation", "novel_action"],
                ),
            }

            response = self._invoke_system_one_with_retry(
                state=state,
                questions=questions,
            )

            # Extraer Noul (soporta has_hallucination_or_loop e is_loop)
            noul_obj = response.nouls.get("has_hallucination_or_loop") or response.nouls.get("is_loop")
            raw_noul = getattr(noul_obj, "noul", 0.0) if noul_obj else 0.0
            if isinstance(raw_noul, bool):
                noul_val = 1.0 if raw_noul else 0.0
            else:
                try:
                    noul_val = float(raw_noul)
                except Exception:
                    noul_val = 1.0 if bool(raw_noul) else 0.0

            is_divergent = bool(noul_val >= 0.5)

            # Extraer Choice de paso divergente
            div_choice_obj = response.choices.get("divergence_step") or response.choices.get("loop_type")
            div_step_str = str(getattr(div_choice_obj, "choice", "none")) if div_choice_obj else "none"

            # Determinar índice del paso con fallo
            flagged_index: Optional[int] = None
            if is_divergent:
                if div_step_str.startswith("step_"):
                    try:
                        flagged_index = int(div_step_str.split("_")[1])
                    except Exception:
                        flagged_index = 0
                else:
                    flagged_index = 0

            # Extraer tipo de alucinación / fallo
            hallucination_choice_obj = response.choices.get("hallucination_type") or response.choices.get("loop_type")
            hallucination_type_str = str(getattr(hallucination_choice_obj, "choice", "none")) if hallucination_choice_obj else "none"

            # Mapeo a LoopType
            if not is_divergent:
                detected_loop_type = LoopType.NONE
                is_hallucination = False
                loop_penalty = 0.0
            else:
                if hallucination_type_str in ("invented_fact_or_file", "tool_hallucination"):
                    detected_loop_type = LoopType.HALLUCINATION
                    is_hallucination = True
                    loop_penalty = 1.8
                elif hallucination_type_str == "unverified_assumption":
                    detected_loop_type = LoopType.UNGROUNDED_PREMISE
                    is_hallucination = True
                    loop_penalty = 1.6
                elif hallucination_type_str in LoopType._value2member_map_:
                    detected_loop_type = LoopType(hallucination_type_str)
                    is_hallucination = False
                    loop_penalty = 1.5
                else:
                    detected_loop_type = LoopType.ONE_HOP_TOOL_REPEAT
                    is_hallucination = False
                    loop_penalty = 1.5

            # Mapeo de scores
            progress_map = {
                "counterproductive": 0.05,
                "irrelevant": 0.20,
                "minor_progress": 0.50,
                "significant_progress": 0.80,
                "direct_solution": 0.95,
            }
            novelty_map = {
                "redundant": 0.0,
                "minor_variation": 0.35,
                "novel_action": 0.90,
            }

            prog_obj = response.scores.get("progress")
            raw_prog = getattr(prog_obj, "score", 2.0) if prog_obj else 2.0
            if isinstance(raw_prog, (int, float)):
                p_progress = 0.05 + (float(raw_prog) / 4.0) * (0.95 - 0.05)
            elif str(raw_prog) in progress_map:
                p_progress = progress_map[str(raw_prog)]
            else:
                p_progress = 0.5

            nov_obj = response.scores.get("novelty")
            raw_nov = getattr(nov_obj, "score", 1.0) if nov_obj else 1.0
            if isinstance(raw_nov, (int, float)):
                delta_u = 0.0 + (float(raw_nov) / 2.0) * (0.90 - 0.0)
            elif str(raw_nov) in novelty_map:
                delta_u = novelty_map[str(raw_nov)]
            else:
                delta_u = 0.5

            p_progress = max(0.0, min(1.0, p_progress))
            delta_u = max(0.0, min(1.0, delta_u))

            return {
                "source": "typesafe_ai_jev",
                "all_safe": not is_divergent,
                "is_loop": is_divergent,
                "loop_type": detected_loop_type,
                "is_hallucination": is_hallucination,
                "hallucination_type": hallucination_type_str,
                "flagged_index": flagged_index,
                "noul_prob": noul_val,
                "p_progress": p_progress,
                "delta_u": delta_u,
                "loop_penalty": loop_penalty,
                "confidence": getattr(div_choice_obj, "confidence", 0.9) if div_choice_obj else 0.9,
                "model": getattr(response, "model", self.config.typesafe_model),
                "total_candidates": len(candidates),
            }

        except Exception as e:
            logger.warning(f"Error al invocar TypeSafe AI Jev evaluate_step_chunk: {e}. Activando fallback local.")
            return None

    def evaluate_candidate(
        self,
        goal: str,
        history: List[Step],
        candidate: ActionCandidate,
    ) -> Optional[Dict[str, Any]]:
        """Invoca el modelo Jev vía TypeSafe AI para clasificar bucles y calcular JEV para un candidato individual.
        
        Retorna un diccionario con métricas tipadas o None si la API no está disponible o falla.
        """
        if not self.is_available():
            return None

        try:
            from typesafe_sdk import Choice, Noul, Score

            # Construir estado estructurado del programa para el modelo Jev
            recent_steps = [
                {
                    "step_id": s.id,
                    "type": s.step_type.value,
                    "tool": s.tool_name,
                    "args": s.tool_args,
                    "content": s.content[:2000],
                }
                for s in history[-8:]
            ]

            state = {
                "goal": goal,
                "recent_trajectory": recent_steps,
                "candidate_step": {
                    "tool": candidate.tool_name,
                    "args": candidate.tool_args,
                    "description": candidate.description,
                    "rationale": candidate.rationale,
                },
            }

            questions = {
                "is_loop": Noul(
                    instructions=(
                        "Does this proposed candidate_step represent an unconstructive loop, "
                        "hallucination, identical tool repetition, or degenerating failure pattern based on the trajectory?"
                    )
                ),
                "loop_type": Choice(
                    instructions="Identify if this proposed candidate_step introduces a loop or if it is constructive",
                    criteria={
                        "none": "No loop, candidate_step is constructive or breaks stagnation",
                        "one_hop_tool_repeat": "Candidate repeats previous tool with identical arguments",
                        "n_hop_cycle": "Candidate closes a cyclic sequence",
                        "semantic_fixation": "Candidate remains fixated on a refuted hypothesis",
                        "entropic_stagnation": "Candidate exhibits verbal rumination without action",
                        "hallucination": "Candidate hallucinates nonexistent files, state or facts",
                    },
                ),
                "progress": Score(
                    instructions="Rate the projected progress of this action toward achieving the goal",
                    criteria=[
                        "counterproductive",
                        "irrelevant",
                        "minor_progress",
                        "significant_progress",
                        "direct_solution",
                    ],
                ),
                "novelty": Score(
                    instructions="Rate the novelty of this action compared to previously explored states",
                    criteria=["redundant", "minor_variation", "novel_action"],
                ),
            }

            # Llamada al modelo Jev en una única pasada con reintentos
            response = self._invoke_system_one_with_retry(
                state=state,
                questions=questions,
            )

            # Extraer resultados tipados (soporta tanto float continuo como strings categóricos)
            noul_obj = response.nouls.get("is_loop") or response.nouls.get("has_hallucination_or_loop")
            raw_noul = getattr(noul_obj, "noul", 0.0) if noul_obj else 0.0
            if isinstance(raw_noul, bool):
                noul_val = 1.0 if raw_noul else 0.0
            else:
                try:
                    noul_val = float(raw_noul)
                except Exception:
                    noul_val = 1.0 if bool(raw_noul) else 0.0

            loop_choice_obj = response.choices.get("loop_type") or response.choices.get("divergence_step")
            loop_type_str = str(getattr(loop_choice_obj, "choice", "none")) if loop_choice_obj else "none"
            # La decisión primaria de bucle se rige por la probabilidad del Noul
            is_loop = bool(noul_val >= 0.5)

            progress_map = {
                "counterproductive": 0.05,
                "irrelevant": 0.20,
                "minor_progress": 0.50,
                "significant_progress": 0.80,
                "direct_solution": 0.95,
            }
            novelty_map = {
                "redundant": 0.0,
                "minor_variation": 0.35,
                "novel_action": 0.90,
            }

            prog_obj = response.scores.get("progress")
            raw_prog = getattr(prog_obj, "score", 2.0) if prog_obj else 2.0
            if isinstance(raw_prog, (int, float)):
                p_progress = 0.05 + (float(raw_prog) / 4.0) * (0.95 - 0.05)
            elif str(raw_prog) in progress_map:
                p_progress = progress_map[str(raw_prog)]
            else:
                try:
                    p_progress = float(raw_prog) / 4.0
                except Exception:
                    p_progress = 0.5

            nov_obj = response.scores.get("novelty")
            raw_nov = getattr(nov_obj, "score", 1.0) if nov_obj else 1.0
            if isinstance(raw_nov, (int, float)):
                delta_u = 0.0 + (float(raw_nov) / 2.0) * (0.90 - 0.0)
            elif str(raw_nov) in novelty_map:
                delta_u = novelty_map[str(raw_nov)]
            else:
                try:
                    delta_u = float(raw_nov) / 2.0
                except Exception:
                    delta_u = 0.5

            p_progress = max(0.0, min(1.0, p_progress))
            delta_u = max(0.0, min(1.0, delta_u))

            # Mapear a Enum LoopType y penalización
            if not is_loop:
                loop_type = LoopType.NONE
                loop_penalty = 0.0
            else:
                if loop_type_str in LoopType._value2member_map_:
                    loop_type = LoopType(loop_type_str)
                elif loop_type_str == "hallucination":
                    loop_type = LoopType.HALLUCINATION
                else:
                    loop_type = LoopType.ONE_HOP_TOOL_REPEAT
                loop_penalty = 1.5

            return {
                "source": "typesafe_ai_jev",
                "is_loop": is_loop,
                "loop_type": loop_type,
                "noul_prob": noul_val,
                "p_progress": p_progress,
                "delta_u": delta_u,
                "loop_penalty": loop_penalty,
                "confidence": getattr(loop_choice_obj, "confidence", 0.9) if loop_choice_obj else 0.9,
                "model": getattr(response, "model", self.config.typesafe_model),
            }

        except Exception as e:
            logger.warning(f"Error al invocar TypeSafe AI Jev API: {e}.")
            return None

    def diagnose_trajectory(self, trajectory: Any) -> Any:
        """Diagnostica una trayectoria completa usando TypeSafe AI System One."""
        from praxeon.models.schema import LoopReport, LoopType, StepType

        steps = getattr(trajectory, "steps", [])
        goal = getattr(trajectory, "goal", "")

        if not steps:
            return LoopReport(
                loop_detected=False,
                loop_type=LoopType.NONE,
                severity=0,
                confidence=1.0,
                explanation="Trayectoria vacía.",
            )

        if not self.is_available():
            import json
            tool_calls = [
                (s.tool_name, json.dumps(s.tool_args or {}, sort_keys=True))
                for s in steps if s.tool_name
            ]
            if len(tool_calls) >= 2:
                # 1. Chequear repetición 1-hop inmediata: A -> A
                for i in range(1, len(tool_calls)):
                    if tool_calls[i] == tool_calls[i - 1]:
                        return LoopReport(
                            loop_detected=True,
                            loop_type=LoopType.ONE_HOP_TOOL_REPEAT,
                            severity=3,
                            confidence=0.9,
                            explanation=f"Reintento idéntico inmediato de herramienta '{tool_calls[i][0]}'",
                            culprit_tool=tool_calls[i][0],
                        )
                # 2. Chequear ciclo n-hop (periodo 2 o 3, ej: A -> B -> A -> B)
                for p in (2, 3):
                    if len(tool_calls) >= 2 * p:
                        pattern = tool_calls[-2 * p : -p]
                        recent = tool_calls[-p:]
                        if pattern == recent:
                            return LoopReport(
                                loop_detected=True,
                                loop_type=LoopType.N_HOP_CYCLE,
                                severity=4,
                                confidence=0.95,
                                explanation=f"Ciclo cerrado de acciones de periodo {p} detectado: {[t[0] for t in pattern]}",
                                culprit_tool=recent[-1][0],
                            )
            return LoopReport(
                loop_detected=False,
                loop_type=LoopType.NONE,
                severity=0,
                confidence=1.0,
                explanation="Trayectoria analizada localmente sin bucles detectados.",
            )

        try:
            from typesafe_sdk import Choice, Noul, Score

            steps_data = [
                {
                    "step_id": s.id,
                    "type": s.step_type.value,
                    "tool": s.tool_name,
                    "args": s.tool_args,
                    "content": s.content[:200],
                }
                for s in steps[-12:]
            ]

            state = {
                "goal": goal,
                "steps_history": steps_data,
            }

            questions = {
                "has_loop_or_flaw": Noul(
                    instructions="Does this trajectory exhibit an unconstructive loop, cyclic trap, or hallucination pattern?"
                ),
                "loop_type": Choice(
                    instructions="Classify the anomaly pattern if present",
                    criteria={
                        "none": "Trajectory is sound, progressing constructively toward the goal",
                        "one_hop_tool_repeat": "Identical tool repetition with same arguments without state change",
                        "n_hop_cycle": "Periodic cycle of actions returning to previous states",
                        "semantic_fixation": "Cognitive fixation on a refuted hypothesis",
                        "entropic_stagnation": "Verbal rumination without action or progress",
                        "hallucination": "Fabrication of non-existent files, variables or facts",
                        "ungrounded_premise": "Unverified assumption of success without testing",
                    },
                ),
                "severity": Score(
                    instructions="Rate the severity of the divergence or loop",
                    criteria=["none", "minor_stagnation", "moderate_loop", "severe_loop", "critical_deadlock"],
                ),
            }

            response = self._invoke_system_one_with_retry(
                state=state,
                questions=questions,
            )

            noul_obj = response.nouls.get("has_loop_or_flaw")
            raw_noul = getattr(noul_obj, "noul", 0.0) if noul_obj else 0.0
            is_loop = bool(raw_noul >= 0.5) if not isinstance(raw_noul, bool) else raw_noul

            loop_choice = response.choices.get("loop_type")
            loop_type_str = str(getattr(loop_choice, "choice", "none")) if loop_choice else "none"

            sev_obj = response.scores.get("severity")
            raw_sev = getattr(sev_obj, "score", 0) if sev_obj else 0
            severity = int(raw_sev) if isinstance(raw_sev, (int, float)) else (3 if is_loop else 0)

            if not is_loop:
                loop_type = LoopType.NONE
            elif loop_type_str in LoopType._value2member_map_:
                loop_type = LoopType(loop_type_str)
            elif loop_type_str == "hallucination":
                loop_type = LoopType.HALLUCINATION
            else:
                loop_type = LoopType.ONE_HOP_TOOL_REPEAT

            culprit_tool = None
            tool_steps = [s for s in steps if s.step_type == StepType.TOOL_CALL]
            if is_loop and tool_steps:
                culprit_tool = tool_steps[-1].tool_name

            explanation = (
                f"TypeSafe AI detectó anomalía '{loop_type.value}' en la trayectoria."
                if is_loop
                else "Trayectoria saludable sin patrones degenerativos detectados por TypeSafe AI."
            )

            return LoopReport(
                loop_detected=is_loop,
                loop_type=loop_type,
                severity=severity,
                cycle_nodes=[s.id for s in steps[-3:]] if is_loop else [],
                confidence=getattr(loop_choice, "confidence", 0.9) if loop_choice else 0.9,
                explanation=explanation,
                culprit_tool=culprit_tool,
            )
        except Exception as e:
            logger.warning(f"Error al diagnosticar trayectoria con TypeSafe: {e}")
            return LoopReport(
                loop_detected=False,
                loop_type=LoopType.NONE,
                severity=0,
                explanation=f"Error en diagnóstico TypeSafe: {e}",
            )


