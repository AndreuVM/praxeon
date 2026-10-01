"""Verificador de completitud (CompletionVerifier) para JEV Reasoning Navigator v0.2.2.

Valida de manera formal y rigurosa si una acción de finalización (finish/complete_task)
está genuinamente respaldada por evidencias empíricas observables frente a Goal.criteria y Goal.success_criteria,
evitando el antipatrón de finalización prematura sin pruebas estructuradas de éxito.
"""

from enum import Enum
import os
import re
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING
from pydantic import BaseModel, ConfigDict, Field
from praxeon.domain.goal import CriterionType, Goal, SuccessCriterion
from praxeon.domain.models import ActionCandidate

if TYPE_CHECKING:
    from praxeon.runtime.state import SessionState


EXIT_CODE_ZERO_PATTERN = re.compile(
    r"\b(?:exit_code|exit code|rc|status|returncode)\s*[:=]?\s*0\b|"
    r"código de salida\s*[:=]?\s*0\b|"
    r"\b0\s+errors\b|\b0\s+failed\b",
    re.IGNORECASE,
)

EXIT_CODE_NONZERO_PATTERN = re.compile(
    r"\b(?:exit_code|exit code|rc|status|returncode)\s*[:=]?\s*[1-9]\d*\b|"
    r"código de salida\s*[:=]?\s*[1-9]\d*\b|"
    r"\b[1-9]\d*\s+failed\b|\b[1-9]\d*\s+errors\b",
    re.IGNORECASE,
)

TESTS_PASS_PATTERN = re.compile(
    r"\b[1-9]\d*\s+passed\b",
    re.IGNORECASE,
)




class CriterionStatus(str, Enum):
    """Estados canónicos de verificación de un criterio de éxito."""
    VERIFIED = "verified"      # Claim empíricamente demostrado y validado
    UNVERIFIED = "unverified"  # Pendiente de evidencia observable
    FAILED = "failed"          # Comprobado pero con resultado fallido o erróneo


class CriterionEvaluation(BaseModel):
    """Evaluación formal individual de un criterio de éxito."""
    model_config = ConfigDict(frozen=True)

    criterion_id: str
    description: str
    criterion_type: CriterionType
    status: CriterionStatus
    evidence_ids: List[str] = Field(default_factory=list)
    rationale: str = ""


class CompletionAssessment(BaseModel):
    """Evaluación formal consolidada del cumplimiento de criterios de un objetivo."""
    model_config = ConfigDict(frozen=True)

    is_complete: bool
    satisfied_criteria: List[str] = Field(default_factory=list)
    missing_criteria: List[str] = Field(default_factory=list)
    unverified_claims: List[str] = Field(default_factory=list)
    evaluations: List[CriterionEvaluation] = Field(default_factory=list)
    confidence: float = 1.0
    rationale: str = ""


class CompletionVerifier:
    """Verifica si una acción candidata de tipo finish cumple formalmente las condiciones de éxito."""

    def __init__(self, stop_words: Optional[Set[str]] = None, workspace_root: Optional[str] = None):
        self.stop_words = stop_words or {
            "el", "la", "los", "las", "un", "una", "de", "en", "para", "por", "con", "que", "y", "o", "a",
            "the", "a", "an", "in", "on", "for", "with", "that", "and", "or", "to", "of", "is"
        }
        self.workspace_root = os.path.realpath(workspace_root or os.getcwd())

    def is_finish_action(self, action: ActionCandidate) -> bool:
        """Determina si la acción propuesta expresa la intención de finalizar la tarea."""
        if action.tool_call and action.tool_call.tool_name.lower() in (
            "finish", "complete_task", "done", "complete", "task_completed"
        ):
            return True
        if action.id.lower() in ("finish", "complete", "done"):
            return True
        desc = action.description.lower()
        finish_keywords = (
            "finalizar tarea", "completar tarea", "tarea terminada", "tarea completada",
            "finish task", "complete task", "task complete", "task completed"
        )
        return any(kw in desc for kw in finish_keywords)

    def _extract_keywords(self, text: str) -> Set[str]:
        """Extrae términos significativos de un texto para emparejamiento."""
        words = text.lower().replace(",", " ").replace(".", " ").replace(":", " ").split()
        return {w for w in words if len(w) > 2 and w not in self.stop_words}

    def _evaluate_criterion(
        self,
        crit: SuccessCriterion,
        state: Any,
        evidence_claims: List[str],
        corpus: str,
    ) -> CriterionEvaluation:
        """Aplica el verificador específico correspondiente al tipo formal de criterio."""
        c_type = crit.criterion_type
        target = crit.target
        desc = crit.description
        c_id = crit.id

        # 1. Verificador específico: FILE_EXISTS
        if c_type == CriterionType.FILE_EXISTS:
            file_to_check = target
            if not file_to_check:
                candidates = [w.strip("`'\",") for w in desc.split() if "." in w]
                file_to_check = candidates[0] if candidates else None

            if file_to_check:
                target_path = os.path.join(self.workspace_root, file_to_check) if not os.path.isabs(file_to_check) else file_to_check
                if os.path.exists(target_path):
                    return CriterionEvaluation(
                        criterion_id=c_id,
                        description=desc,
                        criterion_type=c_type,
                        status=CriterionStatus.VERIFIED,
                        rationale=f"Archivo '{file_to_check}' verificado físicamente en disco.",
                    )
                # Comprobar si se creó en pasos
                created_in_step = any(
                    s.action.tool_call and s.action.tool_call.arguments.get("path") == file_to_check and s.observation and "error" not in s.observation.lower()
                    for s in state.steps
                )
                if created_in_step:
                    return CriterionEvaluation(
                        criterion_id=c_id,
                        description=desc,
                        criterion_type=c_type,
                        status=CriterionStatus.VERIFIED,
                        rationale=f"Creación de '{file_to_check}' confirmada en observaciones de ejecución.",
                    )
                return CriterionEvaluation(
                    criterion_id=c_id,
                    description=desc,
                    criterion_type=c_type,
                    status=CriterionStatus.UNVERIFIED,
                    rationale=f"Archivo '{file_to_check}' no encontrado en workspace ni en observaciones.",
                )

        # 2. Verificador específico: TESTS_PASS
        if c_type == CriterionType.TESTS_PASS or any(w in desc.lower() for w in ("test", "tests", "pytest")):
            has_failed_tests = False
            has_passed_tests = False
            for s in state.steps:
                tool_name = (s.action.tool_call.tool_name if s.action.tool_call else "").lower()
                # Finding 18: La verificación de tests exige procedencia de ejecución activa (run_command / terminal)
                # y no lectura pasiva de archivos (read_file / view_file) que contengan 'tests'.
                if tool_name not in ("run_command", "execute_command", "bash", "terminal", "exec", "subprocess"):
                    continue

                obs_text = (s.observation or "").lower()
                cmd_text = ""
                if s.action.tool_call and s.action.tool_call.arguments:
                    cmd_text = str(s.action.tool_call.arguments.get("cmd") or s.action.tool_call.arguments.get("command") or "").lower()
                
                # Debe corresponder a una invocación de tests real
                if "pytest" in cmd_text or "test" in cmd_text or "check" in cmd_text or "unittest" in cmd_text:
                    if EXIT_CODE_NONZERO_PATTERN.search(obs_text) or any(re.search(rf"\b{re.escape(w)}\b", obs_text) for w in ("failed", "failure", "fail", "failures", "errors")):
                        has_failed_tests = True
                    if TESTS_PASS_PATTERN.search(obs_text) or EXIT_CODE_ZERO_PATTERN.search(obs_text) or any(re.search(rf"\b{re.escape(w)}\b", obs_text) for w in ("ok", "100%")):
                        if not re.search(r"\bfailed to (?:verify|achieve|reach|find|run|pass)\b", obs_text):
                            has_passed_tests = True


            if has_failed_tests and not has_passed_tests:
                return CriterionEvaluation(
                    criterion_id=c_id,
                    description=desc,
                    criterion_type=c_type,
                    status=CriterionStatus.FAILED,
                    rationale="Fallos de test detectados en la observación de ejecución.",
                )
            if has_passed_tests and not has_failed_tests:
                return CriterionEvaluation(
                    criterion_id=c_id,
                    description=desc,
                    criterion_type=c_type,
                    status=CriterionStatus.VERIFIED,
                    rationale="Ejecución de tests verificada con resultado exitoso (0 fallos).",
                )

        # 3. Verificador específico: EXIT_CODE_ZERO
        if c_type == CriterionType.EXIT_CODE_ZERO:
            last_cmd_step = next((s for s in reversed(state.steps) if s.action.tool_call and s.action.tool_call.tool_name == "run_command"), None)
            if last_cmd_step and last_cmd_step.observation:
                obs = last_cmd_step.observation.lower()
                if EXIT_CODE_ZERO_PATTERN.search(obs):
                    return CriterionEvaluation(
                        criterion_id=c_id,
                        description=desc,
                        criterion_type=c_type,
                        status=CriterionStatus.VERIFIED,
                        rationale="Comando finalizado con código de salida 0 verificado por salida estructurada.",
                    )
                if EXIT_CODE_NONZERO_PATTERN.search(obs) or "exit code" in obs or "exit_code" in obs:
                    return CriterionEvaluation(
                        criterion_id=c_id,
                        description=desc,
                        criterion_type=c_type,
                        status=CriterionStatus.FAILED,
                        rationale="Comando finalizado con código de error distinto de 0.",
                    )
                if "exitoso" in obs and not any(w in obs for w in ("fallo", "error", "no exitoso", "failed")):
                    return CriterionEvaluation(
                        criterion_id=c_id,
                        description=desc,
                        criterion_type=c_type,
                        status=CriterionStatus.VERIFIED,
                        rationale="Comando verificado como exitoso.",
                    )


        # 4. Verificador CUSTOM / Coincidencia por claim empírico
        criterion_lower = desc.lower().strip()
        direct_match = any(criterion_lower in claim or claim in criterion_lower for claim in evidence_claims)
        
        criterion_keywords = self._extract_keywords(criterion_lower)
        keyword_match = False
        if criterion_keywords:
            matched_keywords = {kw for kw in criterion_keywords if kw in corpus}
            if len(matched_keywords) / len(criterion_keywords) >= 0.6:
                keyword_match = True

        if direct_match or keyword_match:
            return CriterionEvaluation(
                criterion_id=c_id,
                description=desc,
                criterion_type=c_type,
                status=CriterionStatus.VERIFIED,
                rationale="Criterio respaldado por evidencias empíricas y observaciones en la sesión.",
            )

        return CriterionEvaluation(
            criterion_id=c_id,
            description=desc,
            criterion_type=c_type,
            status=CriterionStatus.UNVERIFIED,
            rationale="No se hallaron evidencias suficientes que respalden este criterio.",
        )

    def verify(
        self,
        goal: Goal,
        state: Any,
        action: ActionCandidate,
    ) -> CompletionAssessment:
        """Evalúa si la acción de finalización está justificada formalmente por las evidencias del estado."""
        # 1. Si no es una acción de finalización, no aplica verificación de completitud
        if not self.is_finish_action(action):
            return CompletionAssessment(
                is_complete=False,
                rationale="La acción propuesta no es de finalización.",
            )

        # 2. Verificación de ejecución mínima (no se puede finalizar en frío sin ningún paso)
        if len(state.steps) == 0:
            return CompletionAssessment(
                is_complete=False,
                missing_criteria=[c.description for c in goal.get_all_criteria()] or ["ejecución_iniciada"],
                confidence=0.0,
                rationale="Finalización prematura: no se ha ejecutado ningún paso en la sesión.",
            )

        # 3. Recopilar texto de evidencias y observaciones del estado
        evidence_claims = [ev.claim.lower() for ev in state.evidence]
        step_observations = [s.observation.lower() for s in state.steps if s.observation]
        corpus = " ".join(evidence_claims + step_observations)

        # 4. Evaluar cada criterio tipado
        all_criteria = goal.get_all_criteria()
        evaluations: List[CriterionEvaluation] = []
        satisfied: List[str] = []
        missing: List[str] = []

        for crit in all_criteria:
            eval_res = self._evaluate_criterion(crit, state, evidence_claims, corpus)
            evaluations.append(eval_res)

            if eval_res.status == CriterionStatus.VERIFIED:
                satisfied.append(crit.description)
            else:
                missing.append(crit.description)

        # 5. Comprobar que no se hayan producido resultados prohibidos (forbidden_outcomes)
        forbidden_violations: List[str] = []
        for forbidden in goal.forbidden_outcomes:
            forbidden_lower = forbidden.lower()
            forbidden_keywords = self._extract_keywords(forbidden_lower)
            if forbidden_keywords and all(kw in corpus for kw in forbidden_keywords):
                forbidden_violations.append(forbidden)

        if forbidden_violations:
            return CompletionAssessment(
                is_complete=False,
                satisfied_criteria=satisfied,
                missing_criteria=missing,
                unverified_claims=[f"Violación de resultado prohibido: {f}" for f in forbidden_violations],
                evaluations=evaluations,
                confidence=0.0,
                rationale=f"Resultado prohibido detectado en la trayectoria: {', '.join(forbidden_violations)}",
            )

        # 6. Regla formal estricta: todos los criterios obligatorios deben estar en estado VERIFIED
        unverified_mandatory = [
            e for e in evaluations
            if e.status != CriterionStatus.VERIFIED and any(c.id == e.criterion_id and c.mandatory for c in all_criteria)
        ]

        if unverified_mandatory:
            failed_count = sum(1 for e in unverified_mandatory if e.status == CriterionStatus.FAILED)
            status_desc = "fallidos o no demostrados" if failed_count > 0 else "pendientes de verificación empírica"
            return CompletionAssessment(
                is_complete=False,
                satisfied_criteria=satisfied,
                missing_criteria=[e.description for e in unverified_mandatory],
                evaluations=evaluations,
                confidence=len(satisfied) / max(1, len(all_criteria)),
                rationale=f"Criterios obligatorios {status_desc}: {', '.join(e.description for e in unverified_mandatory)}",
            )

        # 7. Finalización formalmente autorizada con evidencias demostradas
        confidence = 1.0
        if state.evidence:
            confidence = min(1.0, sum(ev.confidence for ev in state.evidence) / len(state.evidence))

        return CompletionAssessment(
            is_complete=True,
            satisfied_criteria=satisfied if all_criteria else ["pasos_ejecutados_con_exito"],
            missing_criteria=[],
            evaluations=evaluations,
            confidence=confidence,
            rationale="Todos los criterios de éxito verificados formalmente con estado VERIFIED.",
        )
