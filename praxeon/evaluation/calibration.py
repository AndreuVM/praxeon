"""Módulo formal de calibración y riesgo selectivo (evaluation/calibration.py).

Implementa las métricas de calibración probabilística y clasificación selectiva
exigidas por la hoja de ruta técnica (Fase 3 / v0.4) y el paper:
'JEV-as-a-Judge: Accept When Confident, Escalate When Unsure' (arXiv:2609.26550):
- Expected Calibration Error (ECE)
- Maximum Calibration Error (MCE)
- Brier Score
- Curva de Cobertura vs Riesgo Selectivo (Coverage vs Selective Risk / Selective Classification)
- Área bajo la curva de riesgo-cobertura (AURC)
- Datos para Diagramas de Fiabilidad (Reliability Diagrams)
"""

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field


class CalibrationBin(BaseModel):
    """Intervalo discreto para el cálculo de calibración y diagramas de fiabilidad."""
    model_config = ConfigDict(frozen=True)

    bin_index: int
    bin_lower: float
    bin_upper: float
    count: int
    accuracy: float
    confidence: float
    gap: float  # abs(accuracy - confidence)


class CalibrationMetrics(BaseModel):
    """Métricas cuantitativas de calibración probabilística."""
    model_config = ConfigDict(frozen=True)

    total_samples: int
    ece: float  # Expected Calibration Error (0.0..1.0)
    mce: float  # Maximum Calibration Error (0.0..1.0)
    brier_score: float  # Brier Score (0.0..1.0)
    bins: List[CalibrationBin] = Field(default_factory=list)

    def to_summary_dict(self) -> Dict[str, Any]:
        """Resumen formateado para reportes y tablas de benchmark."""
        return {
            "Total Muestras": self.total_samples,
            "ECE (Expected Calibration Error)": f"{self.ece:.4f}",
            "MCE (Max Calibration Error)": f"{self.mce:.4f}",
            "Brier Score": f"{self.brier_score:.4f}",
            "Calibración General": "Excelente" if self.ece < 0.05 else ("Aceptable" if self.ece < 0.15 else "Descalibrado"),
        }


class SelectiveRiskPoint(BaseModel):
    """Punto operativo en la curva de clasificación selectiva (Cobertura vs Riesgo)."""
    model_config = ConfigDict(frozen=True)

    threshold: float
    coverage: float  # Proporción de predicciones aceptadas (0.0..1.0)
    selective_risk: float  # Tasa de error en las predicciones aceptadas (0.0..1.0)
    accepted_count: int
    abstained_count: int


class SelectiveRiskCurve(BaseModel):
    """Curva completa de Cobertura frente a Riesgo Selectivo con AURC."""
    model_config = ConfigDict(frozen=True)

    points: List[SelectiveRiskPoint] = Field(default_factory=list)
    aurc: float  # Area Under the Risk-Coverage Curve
    full_coverage_risk: float  # Riesgo cuando la cobertura es 100% (sin abstención)
    min_risk: float  # Menor riesgo alcanzable aumentando el umbral

    def to_summary_dict(self) -> Dict[str, Any]:
        return {
            "AURC (Area Under Risk-Coverage)": f"{self.aurc:.4f}",
            "Riesgo Cobertura Total (100%)": f"{self.full_coverage_risk * 100:.1f}%",
            "Riesgo Mínimo Alcanzable": f"{self.min_risk * 100:.1f}%",
            "Puntos Evaluados": len(self.points),
        }


class CalibrationCalculator:
    """Calculador matemático de métricas de calibración y análisis de incertidumbre."""

    @classmethod
    def compute_brier_score(cls, probabilities: List[float], labels: List[bool]) -> float:
        """Calcula el Brier Score medio sobre las predicciones probabilísticas.
        
        Brier = (1/N) * sum((p_i - y_i)^2)
        donde y_i in {0, 1} y p_i in [0.0, 1.0].
        """
        if not probabilities or not labels:
            return 0.0
        n = min(len(probabilities), len(labels))
        if n == 0:
            return 0.0

        sq_errors = [
            (probabilities[i] - (1.0 if labels[i] else 0.0)) ** 2
            for i in range(n)
        ]
        return sum(sq_errors) / n

    @classmethod
    def compute_ece(
        cls,
        confidences: List[float],
        labels: List[bool],
        n_bins: int = 10,
    ) -> CalibrationMetrics:
        """Calcula el Expected Calibration Error (ECE) y Maximum Calibration Error (MCE).
        
        ECE = sum_m (|B_m|/N) * |acc(B_m) - conf(B_m)|
        MCE = max_m |acc(B_m) - conf(B_m)|
        """
        total = min(len(confidences), len(labels))
        if total == 0 or n_bins <= 0:
            return CalibrationMetrics(
                total_samples=0,
                ece=0.0,
                mce=0.0,
                brier_score=0.0,
                bins=[],
            )

        brier = cls.compute_brier_score(confidences[:total], labels[:total])

        bin_size = 1.0 / n_bins
        bins: List[CalibrationBin] = []

        ece = 0.0
        mce = 0.0

        for b in range(n_bins):
            lower = b * bin_size
            upper = (b + 1) * bin_size

            # Filtrar muestras en este bin (el último incluye el extremo superior 1.0)
            in_bin_indices = []
            for i in range(total):
                conf = max(0.0, min(1.0, confidences[i]))
                if b == n_bins - 1:
                    if lower <= conf <= upper:
                        in_bin_indices.append(i)
                else:
                    if lower <= conf < upper:
                        in_bin_indices.append(i)

            count = len(in_bin_indices)
            if count > 0:
                bin_acc = sum(1.0 for idx in in_bin_indices if labels[idx]) / count
                bin_conf = sum(confidences[idx] for idx in in_bin_indices) / count
                gap = abs(bin_acc - bin_conf)

                weight = count / total
                ece += weight * gap
                if gap > mce:
                    mce = gap
            else:
                bin_acc = 0.0
                bin_conf = (lower + upper) / 2.0
                gap = 0.0

            bins.append(
                CalibrationBin(
                    bin_index=b,
                    bin_lower=round(lower, 3),
                    bin_upper=round(upper, 3),
                    count=count,
                    accuracy=round(bin_acc, 4),
                    confidence=round(bin_conf, 4),
                    gap=round(gap, 4),
                )
            )

        return CalibrationMetrics(
            total_samples=total,
            ece=round(ece, 4),
            mce=round(mce, 4),
            brier_score=round(brier, 4),
            bins=bins,
        )

    @classmethod
    def compute_selective_risk_curve(
        cls,
        confidences: List[float],
        labels: List[bool],
        num_thresholds: int = 21,
    ) -> SelectiveRiskCurve:
        """Genera la curva de Riesgo Selectivo vs Cobertura para clasificación con opción de abstención.
        
        Para cada umbral tau:
        - Cobertura: % de instancias donde confianza >= tau.
        - Riesgo Selectivo: Tasa de error exclusivamente entre las instancias aceptadas.
        """
        total = min(len(confidences), len(labels))
        if total == 0:
            return SelectiveRiskCurve(
                points=[],
                aurc=0.0,
                full_coverage_risk=0.0,
                min_risk=0.0,
            )

        thresholds = [i / (num_thresholds - 1) for i in range(num_thresholds)]
        points: List[SelectiveRiskPoint] = []

        full_coverage_risk = 0.0
        min_risk = 1.0

        for tau in thresholds:
            accepted_indices = [
                i for i in range(total)
                if confidences[i] >= tau
            ]
            accepted_count = len(accepted_indices)
            abstained_count = total - accepted_count

            coverage = accepted_count / total

            if accepted_count > 0:
                # Errores entre los aceptados
                errors = sum(1.0 for idx in accepted_indices if not labels[idx])
                selective_risk = errors / accepted_count
            else:
                selective_risk = 0.0

            if tau == 0.0:
                full_coverage_risk = selective_risk

            if accepted_count > 0 and selective_risk < min_risk:
                min_risk = selective_risk

            points.append(
                SelectiveRiskPoint(
                    threshold=round(tau, 3),
                    coverage=round(coverage, 4),
                    selective_risk=round(selective_risk, 4),
                    accepted_count=accepted_count,
                    abstained_count=abstained_count,
                )
            )

        if min_risk == 1.0 and total > 0:
            min_risk = full_coverage_risk

        # Calcular AURC (Área bajo la curva Riesgo-Cobertura) mediante integración trapezoidal
        # Ordenamos los puntos de menor a mayor cobertura
        sorted_by_cov = sorted(points, key=lambda p: p.coverage)
        aurc = 0.0
        for i in range(len(sorted_by_cov) - 1):
            p1 = sorted_by_cov[i]
            p2 = sorted_by_cov[i + 1]
            delta_cov = p2.coverage - p1.coverage
            if delta_cov > 0:
                avg_risk = (p1.selective_risk + p2.selective_risk) / 2.0
                aurc += avg_risk * delta_cov

        return SelectiveRiskCurve(
            points=points,
            aurc=round(aurc, 4),
            full_coverage_risk=round(full_coverage_risk, 4),
            min_risk=round(min_risk, 4),
        )
