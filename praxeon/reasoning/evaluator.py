"""Evaluador cognitivo analítico y derivación de señales JEV (Sección 3.1).

Evita el colapso de dimensiones ortogonales en un único escalar decisorio.
JEV se calcula como una señal analítica estructurada dentro de ProviderAssessment.
"""

from typing import List, Optional
from praxeon.domain.models import ProviderAssessment


class CognitiveEvaluator:
    """Calcula métricas analíticas sin fusionar indebidamente dimensiones ortogonales."""

    @staticmethod
    def compute_analytical_jev(
        progress_prob: float,
        grounded_prob: float,
        loop_penalty: float = 0.0,
        novelty_prob: float = 0.5,
    ) -> float:
        """Deriva la señal escalar JEV para telemetría sin emplearla como árbitro ejecutivo exclusivo."""
        raw = ((progress_prob + grounded_prob + novelty_prob) / 3.0) - loop_penalty
        return max(-1.0, min(1.0, round(raw, 4)))

    @staticmethod
    def build_assessment(
        provider_name: str,
        available: bool,
        progress_prob: Optional[float] = None,
        grounded_prob: Optional[float] = None,
        loop_prob: Optional[float] = None,
        novelty_prob: Optional[float] = None,
        confidence: float = 1.0,
        failure_reason: Optional[str] = None,
        reason_codes: Optional[List[str]] = None,
    ) -> ProviderAssessment:
        """Sintetiza un ProviderAssessment estructurado preservando dimensiones independientes."""
        analytical_jev = None
        if available and progress_prob is not None and grounded_prob is not None:
            analytical_jev = CognitiveEvaluator.compute_analytical_jev(
                progress_prob=progress_prob,
                grounded_prob=grounded_prob,
                loop_penalty=(loop_prob or 0.0),
                novelty_prob=(novelty_prob or 0.5),
            )

        return ProviderAssessment(
            provider=provider_name,
            available=available,
            confidence=confidence,
            progress_probability=progress_prob,
            grounded_probability=grounded_prob,
            loop_probability=loop_prob,
            novelty_probability=novelty_prob,
            analytical_jev=analytical_jev,
            failure_reason=failure_reason,
            reason_codes=reason_codes or [],
        )
