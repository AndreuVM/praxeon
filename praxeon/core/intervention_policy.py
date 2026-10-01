"""Políticas de intervención cognitiva desacopladas: Decisión Formal vs Síntesis Discursiva (Sección 3.7).

Desacopla formalmente dos capas:
1. PolicyEngine: Emite el estado tipado PolicyDecision (status, reason_codes, forbidden_tools).
2. InterventionPlanner: Formula las directivas discursivas, inyecciones de contexto XML y sugerencias de rescate.
"""

from typing import List, Optional
from praxeon.config import PraxeonConfig, default_config
from praxeon.core.state_graph import StateGraph
from praxeon.domain.models import DecisionStatus, PolicyDecision
from praxeon.models.schema import (
    InterventionDirective,
    InterventionLevel,
    JEVScore,
    LoopReport,
    LoopType,
    Step,
)


class InterventionPlanner:
    """Planificador contextual: formula directivas discursivas e inyecciones de contexto XML."""

    @staticmethod
    def plan_intervention(
        decision: Optional[PolicyDecision],
        loop_report: Optional[LoopReport],
        current_step: Optional[Step],
        target_step_id: Optional[str] = None,
        target_desc: str = "Estado previo validado",
        goal: Optional[str] = None,
    ) -> Optional[InterventionDirective]:
        """Sintetiza la directiva discursiva formal a partir de la decisión y diagnóstico."""
        # Si no hay loop ni rechazo/replanificación, no se requiere directiva
        if (
            (loop_report is None or not loop_report.loop_detected)
            and (decision is None or decision.status == DecisionStatus.ALLOW)
        ):
            return None

        # Determinar nivel de severidad
        severity = loop_report.severity if loop_report else 2
        loop_type = loop_report.loop_type if loop_report else LoopType.NONE
        culprit = loop_report.culprit_tool if loop_report else (current_step.tool_name if current_step else None)
        is_jev_degradation = decision and any("CRITICAL_JEV_DEGRADATION" in r for r in decision.reason_codes)

        extra_attrs = ""
        if is_jev_degradation:
            level = InterventionLevel.LEVEL_1_META_FEEDBACK
            explanation = loop_report.explanation if loop_report else "Joint Expected Value cayó bajo el umbral crítico."
            message = (
                f"[JEV-MONITOR | NIVEL 1 - META-FEEDBACK]\n"
                f"Alerta: {explanation}\n"
                "INSTRUCCIÓN OBLIGATORIA: Antes de invocar cualquier otra herramienta o tomar una decisión táctica, "
                "describe en exactamente 1 frase por qué tu premisa anterior no produjo el resultado esperado."
            )
            forbidden = []
            suggested = "Reflexionar brevemente sobre el fallo antes de continuar"

        elif loop_type == LoopType.HALLUCINATION or (decision and any("HALLUCINATION" in r for r in decision.reason_codes)):
            level = InterventionLevel.LEVEL_2_FORCED_BACKTRACKING
            extra_attrs = " type='anti_hallucination'"
            message = (
                f"[JEV-SUPERVISOR | DIRECTIVA ANTI-ALUCINACIÓN]\n"
                f"Diagnóstico: {loop_report.explanation if loop_report else 'Asunción de hechos o archivos no verificados empíricamente.'}\n"
                f"ESTADO DE RECUPERACIÓN: Se anula el paso actual. Debes reanudar desde: '{target_step_id}' ({target_desc}).\n"
                f"DIRECTIVA: Queda PROHIBIDO asumir la existencia de rutas o variables sin inspección real previa."
            )
            forbidden = [culprit] if culprit else ["finish"]
            suggested = "Ejecutar comando de lectura/inspección real para comprobar el entorno"

        elif loop_type == LoopType.UNGROUNDED_PREMISE or (decision and any("MISSING_REQUIRED_EVIDENCE" in r for r in decision.reason_codes)):
            level = InterventionLevel.LEVEL_1_META_FEEDBACK
            message = (
                f"[JEV-SUPERVISOR | PREMISA NO FUNDAMENTADA]\n"
                f"Alerta: {loop_report.explanation if loop_report else 'Faltan evidencias empíricas requeridas para esta acción.'}\n"
                f"INSTRUCCIÓN: Obtén primero las evidencias requeridas mediante herramientas de inspección antes de reintentar."
            )
            forbidden = [culprit] if culprit else []
            suggested = "Consultar o leer los datos empíricos necesarios"

        elif loop_type == LoopType.ENTROPIC_STAGNATION or severity >= 4:
            level = InterventionLevel.LEVEL_3_ABSTRACTION_SHIFT
            extra_attrs = " type='devils_advocate'"
            message = (
                f"[JEV-SUPERVISOR | NIVEL 3 - ABOGADO DEL DIABLO / RUPTURA ESTRATÉGICA]\n"
                f"Alerta: Estancamiento cognitivo o ciclo degenerativo severo ({loop_report.explanation if loop_report else 'sin progreso'}).\n"
                f"INSTRUCCIÓN OBLIGATORIA: Detén inmediatamente este enfoque táctico. Cambia de estrategia completamente o retrocede."
            )
            forbidden = [culprit] if culprit else []
            suggested = "Replantear la hipótesis central y explorar un camino alternativo"

        else:
            level = InterventionLevel.LEVEL_2_FORCED_BACKTRACKING
            if culprit in ("run_command", "read_file", "view_file", "list_dir"):
                veto_text = f"Reintentar '{culprit}' con idénticos argumentos o de forma reiterativa sin variación."
            else:
                veto_text = f"Herramienta '{culprit}' vetada temporalmente para evitar fijación."
            goal_reminder = f" Recuerda que tu objetivo a resolver es: '{goal}'." if goal else ""
            message = (
                f"[JEV-SUPERVISOR | NIVEL 2 - RETROCESO Y PODA]\n"
                f"Alerta: {loop_report.explanation if loop_report else 'Bucle o repetición detectada'}.\n"
                f"ACCIONES PROHIBIDAS: {veto_text}\n"
                f"INSTRUCCIÓN: Si necesitas datos del entorno, varía el enfoque o argumentos; si ya recopilaste información suficiente, invoca 'finish' con tu informe.{goal_reminder} "
                "Responde a la tarea del usuario; NO menciones diagnósticos ni advertencias del supervisor en 'finish'."
            )
            forbidden = [culprit] if culprit else []
            suggested = "Continuar con un método o herramienta diferente o formular la solución final"

        injection = (
            f"<system_intervention level='{level.value}'{extra_attrs}>\n"
            f"{message}\n"
            f"</system_intervention>"
        )

        return InterventionDirective(
            level=level,
            target_step_id=target_step_id,
            message=message,
            forbidden_actions=forbidden,
            suggested_action=suggested,
            context_injection=injection,
        )


class InterventionPolicy:
    """Coordinador de intervención que articula evaluación de políticas y síntesis discursiva."""

    def __init__(self, state_graph: StateGraph, config: Optional[PraxeonConfig] = None):
        self.graph = state_graph
        self.config = config or state_graph.config or default_config
        self.planner = InterventionPlanner()

    def evaluate_and_intervene(
        self,
        current_step: Optional[Step] = None,
        jev_score: Optional[JEVScore] = None,
        loop_report: Optional[LoopReport] = None,
    ) -> Optional[InterventionDirective]:
        """Evalúa las condiciones y formula directiva discursiva delegando en InterventionPlanner."""
        if loop_report is None or not loop_report.loop_detected:
            # Si no hay loop pero el JEV es crítico (< 0.0)
            if jev_score and jev_score.total_jev < self.config.critical_jev_threshold:
                empty_rep = LoopReport(
                    loop_detected=True,
                    severity=1,
                    explanation=f"Joint Expected Value ({jev_score.total_jev:.2f}) cayó bajo el umbral crítico ({self.config.critical_jev_threshold:.2f}).",
                )
                return self.planner.plan_intervention(
                    decision=PolicyDecision(status=DecisionStatus.REPLAN, reason_codes=["CRITICAL_JEV_DEGRADATION"]),
                    loop_report=empty_rep,
                    current_step=current_step,
                    target_step_id=current_step.id if current_step else None,
                )
            return None

        # Localizar el mejor estado previo para retroceder
        highest_step = self.graph.get_highest_jev_step()
        target_id = highest_step.id if highest_step else (
            self.graph.get_chronological_nodes()[0] if self.graph.get_chronological_nodes() else "root"
        )
        target_desc = highest_step.content if highest_step else "Estado inicial de la tarea"

        decision_status = DecisionStatus.BLOCK if loop_report.severity >= 4 else DecisionStatus.REPLAN
        decision = PolicyDecision(status=decision_status, reason_codes=[loop_report.loop_type.value])

        return self.planner.plan_intervention(
            decision=decision,
            loop_report=loop_report,
            current_step=current_step,
            target_step_id=target_id,
            target_desc=target_desc,
            goal=getattr(self.graph, "goal", None),
        )
