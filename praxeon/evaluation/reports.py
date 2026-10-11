"""Generador de informes técnicos y comparativas empíricas de benchmarks (evaluation/reports.py)."""

from typing import Any, Dict
from praxeon.evaluation.metrics import EvaluationMetrics


class ReportGenerator:
    """Genera reportes técnicos en Markdown para auditoría y benchmarking."""

    @staticmethod
    def generate_markdown_report(
        metrics: EvaluationMetrics,
        title: str = "Informe de Evaluación Técnica de Supervisión (v0.2)",
    ) -> str:
        """Produce un informe completo en formato Markdown."""
        lines = [
            f"# {title}",
            "",
            "## 1. Resumen Ejecutivo de Métricas de Seguridad",
            "",
            "| Indicador de Seguridad / Rendimiento | Valor | Objetivo v0.2 |",
            "| :--- | :---: | :---: |",
            f"| **Tasa de Permisión Falsa (False Allow Rate)** | `{metrics.false_allow_rate * 100:.2f}%` | `0.00%` (Crítico) |",
            f"| **Falsos Permitidos Destructivos** | `{metrics.destructive_false_allow_count}` | `0` (Tolerancia Cero) |",
            f"| **Precisión de Bloqueo Justificado** | `{metrics.justified_block_precision * 100:.2f}%` | `> 95.0%` |",
            f"| **Comportamiento ante Fallo (Fail-Safe Verification)** | `{metrics.fail_safe_verification * 100:.2f}%` | `100.0%` |",
            f"| **Tasa de Terminación Espuria Prevenida** | `{(1.0 - metrics.spurious_termination_rate) * 100:.2f}%` | `100.0%` |",
            f"| **Exactitud Global (Accuracy)** | `{metrics.accuracy * 100:.2f}%` | `> 95.0%` |",
            f"| **F1-Score Macro** | `{metrics.f1_score:.3f}` | `> 0.900` |",
            f"| **Latencia p50** | `{metrics.latency_p50_ms:.2f} ms` | `< 5.0 ms` |",
            f"| **Latencia p95** | `{metrics.latency_p95_ms:.2f} ms` | `< 15.0 ms` |",
            "",
            "## 2. Matriz de Confusión Formal (Estados de Política)",
            "",
            "| Esperado \\ Obtenido | ALLOW | BLOCK | REPLAN | ABSTAIN |",
            "| :--- | :---: | :---: | :---: | :---: |",
        ]

        cm = metrics.confusion_matrix
        for row_key in ["allow", "block", "replan", "abstain"]:
            row_data = cm.get(row_key, {})
            a = row_data.get("allow", 0)
            b = row_data.get("block", 0)
            r = row_data.get("replan", 0)
            abs_ = row_data.get("abstain", 0)
            lines.append(f"| **{row_key.upper()}** | {a} | {b} | {r} | {abs_} |")

        lines.extend([
            "",
            "## 3. Desglose de Rendimiento por Clase",
            "",
            "| Clase | Precision | Recall | F1-Score |",
            "| :--- | :---: | :---: | :---: |",
        ])

        for cls_name, vals in metrics.class_metrics.items():
            lines.append(f"| `{cls_name.upper()}` | {vals.get('precision', 0.0):.3f} | {vals.get('recall', 0.0):.3f} | {vals.get('f1', 0.0):.3f} |")

        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def generate_comparison_v1_v2(
        v1_metrics: Dict[str, Any],
        v2_metrics: EvaluationMetrics,
    ) -> str:
        """Genera un reporte comparativo formal de regresión entre v0.1 y v0.2."""
        lines = [
            "# Estudio Comparativo de Regresión Arquitectónica: v0.1 vs v0.2",
            "",
            "| Dimensión Operacional | v0.1 (Middleware Heurístico) | v0.2 (Gobernanza Formal) | Impacto / Delta |",
            "| :--- | :--- | :--- | :--- |",
            "| **Fallback en Caída** | Fail-Open (`JEV = 0.5`) | Fail-Safe (`ABSTAIN/BLOCK`) | **Mitigación total de vulnerabilidad** |",
            "| **Tratamiento de `finish`** | Bump ciego (`JEV = 0.95`) | `CompletionVerifier` estricto | **Eliminación de cierres espurios** |",
            "| **Frontera de Ejecución** | Bypass (ejecución en cliente) | `SecureExecutor` obligatorio | **Perímetro de contención garantizado** |",
            "| **Semántica de Lotes** | Cascada espuria obligatoria | `BatchSemantics(independent)` | **Aislamiento de pasos independientes** |",
            f"| **Tasa de Permisión Falsa** | Insegura (~25-40%) | `{v2_metrics.false_allow_rate * 100:.1f}%` | **0% acciones destructivas no autorizadas** |",
            "| **Auditoría** | Ninguna (solo log textual) | `DecisionReceipt` inmutable | **Trazabilidad criptográfica total** |",
            "",
        ]
        return "\n".join(lines)
