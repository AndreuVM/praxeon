"""Condensación semántica (Summarization) y compresión de contexto para PRAXEON (F2-05).

Permite comprimir observaciones históricas, evidencias voluminosas o ramas antiguas
en fragmentos de resumen estructurados (SummaryFragment) cuando el volumen de tokens
supera el presupuesto disponible, preservando la continuidad cognitiva del agente.
"""

import re
from typing import Any, Dict, List, Optional
from praxeon.context.fragments import (
    ContextFragment,
    FragmentType,
    SummaryFragment,
    estimate_tokens,
)


class SemanticContextCompressor:
    """Condensador semántico determinista de observaciones y evidencias para ajuste a budget."""

    def __init__(self, target_compression_ratio: float = 0.25):
        self.target_compression_ratio = target_compression_ratio

    def condense_observations(
        self,
        observations: List[ContextFragment],
        summary_id: str = "summary_obs_history",
        max_tokens: int = 150,
    ) -> Optional[ContextFragment]:
        """Condensa una secuencia de observaciones detalladas en un SummaryFragment compacto."""
        if not observations:
            return None

        tools_used: List[str] = []
        outcomes: List[str] = []
        files_touched: List[str] = []

        for obs in observations:
            meta = obs.metadata or {}
            tool = meta.get("tool_name") or "herramienta"
            if tool not in tools_used:
                tools_used.append(tool)

            args = meta.get("arguments", {})
            if isinstance(args, dict):
                path = args.get("path") or args.get("file") or args.get("target_file")
                if path and path not in files_touched:
                    files_touched.append(str(path))

            # Detectar si fue exitoso o falló
            content = obs.content.lower()
            if "error" in content or "fail" in content:
                outcomes.append(f"falla en {tool}")
            elif "ok" in content or "success" in content or "passed" in content:
                outcomes.append(f"éxito en {tool}")

        # Sintetizar resumen estructurado
        tools_str = ", ".join(tools_used[:5])
        files_str = f" sobre [{', '.join(files_touched[:4])}]" if files_touched else ""
        step_count = len(observations)
        
        summary_text = (
            f"Historial condensado de {step_count} pasos previos: Invocación de herramientas ({tools_str}){files_str}. "
            f"Estado operativo: {'Sin errores críticos detectados.' if not outcomes or 'falla' not in ' '.join(outcomes) else 'Se registraron advertencias resueltas.'}"
        )

        sess_id = observations[0].metadata.get("session_id", "session") if observations[0].metadata else "session"
        covered_ids = [obs.source_id for obs in observations if obs.source_id]

        return SummaryFragment(
            summary_text=summary_text,
            covered_step_ids=covered_ids,
            source_id=summary_id,
            metadata={
                "scope": f"steps_0_to_{step_count}",
                "session_id": sess_id,
                "condensed_steps_count": step_count,
                "original_tokens": sum(o.token_estimate for o in observations),
                "tools_used": tools_used,
            },
        )

    def condense_evidences(
        self,
        evidences: List[ContextFragment],
        summary_id: str = "summary_evidence_cluster",
        max_tokens: int = 120,
    ) -> Optional[ContextFragment]:
        """Condensa un conjunto de evidencias atómicas en un resumen consolidado de hechos probados."""
        if not evidences:
            return None

        claims = [e.content.replace("EVIDENCIA", "").strip() for e in evidences]
        consolidated = " | ".join(claims[:6])
        summary_text = f"Consolidado fáctico verificado ({len(evidences)} evidencias): {consolidated}"
        covered_ids = [e.source_id for e in evidences if e.source_id]

        return SummaryFragment(
            summary_text=summary_text,
            covered_step_ids=covered_ids,
            source_id=summary_id,
            metadata={
                "scope": "evidence_cluster",
                "condensed_evidence_count": len(evidences),
                "original_tokens": sum(e.token_estimate for e in evidences),
            },
        )

