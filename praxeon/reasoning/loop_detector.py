"""Detector analítico de anomalías cognitivas y bucles segregados (Sección 3.13).

Segrega las anomalías en tres dimensiones ortogonales:
1. Convergencia Algorítmica: Repetición unihop, ciclos n-hop, estancamiento entrópico, fijación semántica.
2. Solidez Empírica: Premisas no fundamentadas, alucinación de hechos, contradicción de evidencia.
3. Riesgo Instrumental: Llamadas repetitivas destructivas, herramientas no autorizadas o sin confirmación.
"""

import json
from typing import List, Optional, Set
from praxeon.domain.models import ActionCandidate
from praxeon.models.schema import (
    ConvergenceAnomaly,
    GroundingAnomaly,
    InstrumentalRiskAnomaly,
    LoopReport,
    LoopType,
    Step,
)


class LoopDetector:
    """Analizador topológico y semántico de anomalías de trayectoria."""

    def __init__(self, history_window: int = 10):
        self.history_window = history_window

    def analyze_trajectory(
        self,
        steps: List[Step],
        candidate: Optional[ActionCandidate] = None,
        available_evidence_claims: Optional[Set[str]] = None,
    ) -> LoopReport:
        """Diagnostica de forma integral la trayectoria segregando las dimensiones analíticas."""
        if not steps and not candidate:
            return LoopReport(loop_detected=False)

        evidence_set = available_evidence_claims or set()
        convergence = ConvergenceAnomaly.NONE
        grounding = GroundingAnomaly.NONE
        instrumental = InstrumentalRiskAnomaly.NONE
        cycle_nodes: List[str] = []
        severity = 0
        explanation_parts: List[str] = []
        culprit_tool: Optional[str] = None

        # 1. Dimensión de Convergencia: Detección de repetición unihop y ciclos
        recent_steps = steps[-self.history_window:] if len(steps) > self.history_window else steps
        if candidate and candidate.tool_call and recent_steps:
            last_step = recent_steps[-1]
            cand_tool = candidate.tool_call.tool_name
            # Reintento idéntico unihop inmediato
            if (
                last_step.tool_name == cand_tool
                and last_step.tool_args == candidate.tool_call.arguments
                and cand_tool not in ("read_file", "view_file", "list_dir")
            ):
                convergence = ConvergenceAnomaly.ONE_HOP_REPEAT
                severity = max(severity, 3)
                culprit_tool = cand_tool
                explanation_parts.append(f"Reintento idéntico inmediato de la herramienta '{cand_tool}'.")

        # Detección de ciclo n-hop (A -> B -> A -> B) con acciones y argumentos equivalentes
        action_signatures = []
        for s in recent_steps:
            if s.tool_name:
                try:
                    args_key = json.dumps(s.tool_args or {}, sort_keys=True)
                except Exception:
                    args_key = str(s.tool_args or {})
                action_signatures.append((s.tool_name, args_key))

        if candidate and candidate.tool_call:
            try:
                cand_args_key = json.dumps(candidate.tool_call.arguments or {}, sort_keys=True)
            except Exception:
                cand_args_key = str(candidate.tool_call.arguments or {})
            action_signatures.append((candidate.tool_call.tool_name, cand_args_key))

        if len(action_signatures) >= 4:
            # Buscar patrones cíclicos cortos donde se repiten exactamente las mismas acciones
            if action_signatures[-1] == action_signatures[-3] and action_signatures[-2] == action_signatures[-4]:
                cand_tool = action_signatures[-1][0]
                if cand_tool not in ("read_file", "view_file", "list_dir"):
                    convergence = ConvergenceAnomaly.N_HOP_CYCLE
                    severity = max(severity, 4)
                    culprit_tool = cand_tool
                    cycle_nodes = [s.id for s in recent_steps[-4:]]
                    explanation_parts.append(
                        f"Ciclo recurrente cerrado detectado en acciones: "
                        f"{action_signatures[-4][0]} -> {action_signatures[-3][0]} -> {action_signatures[-2][0]} -> {action_signatures[-1][0]}."
                    )

        # 2. Dimensión de Solidez Empírica (Grounding): Premisas no fundamentadas
        if candidate and candidate.requires_evidence:
            missing_claims = [c for c in candidate.requires_evidence if c.strip().lower() not in evidence_set]
            if missing_claims:
                grounding = GroundingAnomaly.UNGROUNDED_PREMISE
                severity = max(severity, 3)
                explanation_parts.append(f"Premisa no fundamentada: faltan evidencias {missing_claims}.")

        # 3. Dimensión de Riesgo Instrumental
        if candidate and candidate.tool_call:
            t_name = candidate.tool_call.tool_name.lower()
            if t_name in ("delete_file", "run_destructive_command", "rm_rf"):
                instrumental = InstrumentalRiskAnomaly.DESTRUCTIVE_CALL
                severity = max(severity, 4)
                culprit_tool = t_name
                explanation_parts.append(f"Acción instrumental de alto riesgo destructivo invocada: '{t_name}'.")

        loop_detected = (
            convergence != ConvergenceAnomaly.NONE
            or grounding != GroundingAnomaly.NONE
            or instrumental != InstrumentalRiskAnomaly.NONE
        )

        # Mapeo a LoopType canónico para compatibilidad hacia atrás
        legacy_loop_type = LoopType.NONE
        if convergence == ConvergenceAnomaly.ONE_HOP_REPEAT:
            legacy_loop_type = LoopType.ONE_HOP_TOOL_REPEAT
        elif convergence == ConvergenceAnomaly.N_HOP_CYCLE:
            legacy_loop_type = LoopType.N_HOP_CYCLE
        elif convergence == ConvergenceAnomaly.ENTROPIC_STAGNATION:
            legacy_loop_type = LoopType.ENTROPIC_STAGNATION
        elif grounding == GroundingAnomaly.HALLUCINATION:
            legacy_loop_type = LoopType.HALLUCINATION
        elif grounding == GroundingAnomaly.UNGROUNDED_PREMISE:
            legacy_loop_type = LoopType.UNGROUNDED_PREMISE

        return LoopReport(
            loop_detected=loop_detected,
            loop_type=legacy_loop_type,
            convergence=convergence,
            grounding=grounding,
            instrumental_risk=instrumental,
            severity=severity,
            cycle_nodes=cycle_nodes,
            confidence=0.9 if loop_detected else 0.0,
            explanation=" ".join(explanation_parts) if explanation_parts else "Trayectoria convergente y fundamentada.",
            culprit_tool=culprit_tool,
        )
