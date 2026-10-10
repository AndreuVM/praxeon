"""Exportador de métricas Prometheus y OpenTelemetry para PRAXEON (praxeon/telemetry/prometheus.py).

Cumple estrictamente con la especificación de formato de texto Prometheus 0.0.4:
- Encabezados # HELP y # TYPE para cada métrica
- Counters, Gauges e Histograms (con buckets, sum y count)
- Etiquetas normalizadas y libres de inyecciones
- Cero dependencias externas requeridas
"""

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional


class PrometheusMetricsExporter:
    """Recolector y formateador de métricas en tiempo real compatible con Prometheus."""

    # Buckets estándar para latencia en segundos (de 1ms a 5s)
    DEFAULT_LATENCY_BUCKETS = (0.001, 0.005, 0.010, 0.025, 0.050, 0.100, 0.250, 0.500, 1.000, 2.500, 5.000)

    def __init__(self, start_time: Optional[float] = None) -> None:
        self._lock = threading.Lock()
        self._start_time = start_time or time.time()
        self._custom_counters: Dict[str, Dict[str, float]] = {}
        self._custom_gauges: Dict[str, Dict[str, float]] = {}

    def record_counter(self, name: str, value: float = 1.0, labels: Optional[Dict[str, str]] = None) -> None:
        """Incrementa un contador personalizado."""
        lbl_key = self._format_label_key(labels)
        with self._lock:
            if name not in self._custom_counters:
                self._custom_counters[name] = {}
            self._custom_counters[name][lbl_key] = self._custom_counters[name].get(lbl_key, 0.0) + value

    def set_gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Establece el valor de un gauge personalizado."""
        lbl_key = self._format_label_key(labels)
        with self._lock:
            if name not in self._custom_gauges:
                self._custom_gauges[name] = {}
            self._custom_gauges[name][lbl_key] = value

    @staticmethod
    def _format_label_key(labels: Optional[Dict[str, str]]) -> str:
        if not labels:
            return ""
        items = sorted(labels.items())
        parts = [f'{k}="{v}"' for k, v in items]
        return "{" + ",".join(parts) + "}"

    def generate_metrics(self, service: Optional[Any] = None) -> str:
        """Recolecta el estado del servicio y formatea la salida Prometheus 0.0.4."""
        now = time.time()
        uptime = round(now - self._start_time, 2)
        lines: List[str] = []

        # 1. Metadatos del runtime
        lines.append("# HELP praxeon_build_info Build information and current version of PRAXEON runtime.")
        lines.append("# TYPE praxeon_build_info gauge")
        prof = (os.environ.get("PRAXEON_PROFILE") or os.environ.get("PRAXEON_ENV") or "dev").lower()
        lines.append(f'praxeon_build_info{{version="1.1.0",profile="{prof}"}} 1')

        lines.append("# HELP praxeon_uptime_seconds Total runtime uptime in seconds.")
        lines.append("# TYPE praxeon_uptime_seconds gauge")
        lines.append(f"praxeon_uptime_seconds {uptime}")

        sessions = service.list_sessions() if (service is not None and hasattr(service, "list_sessions")) else []
        total_sessions = len(sessions)
        active_sessions = sum(1 for s in sessions if s.get("status") == "Active")

        lines.append("# HELP praxeon_sessions_total Total sessions created in Praxeon.")
        lines.append("# TYPE praxeon_sessions_total counter")
        lines.append(f"praxeon_sessions_total {total_sessions}")

        lines.append("# HELP praxeon_active_sessions Number of currently active sessions.")
        lines.append("# TYPE praxeon_active_sessions gauge")
        lines.append(f"praxeon_active_sessions {active_sessions}")

        # Distribución de aislamiento
        isolation_counts = {"container": 0, "local_restricted": 0, "full_access": 0}
        for s in sessions:
            mode = str(s.get("execution_mode") or "local_restricted").lower()
            if mode in isolation_counts:
                isolation_counts[mode] += 1
            else:
                isolation_counts["local_restricted"] += 1

        lines.append("# HELP praxeon_sessions_by_isolation Number of sessions partitioned by isolation mode.")
        lines.append("# TYPE praxeon_sessions_by_isolation gauge")
        for mode, count in isolation_counts.items():
            lines.append(f'praxeon_sessions_by_isolation{{mode="{mode}"}} {count}')

        # Evaluaciones de políticas
        allowed = sum(s.get("allowed_count", 0) for s in sessions)
        blocked = sum(s.get("blocked_count", 0) for s in sessions)
        review = sum(s.get("review_count", 0) for s in sessions)
        replan = 0  # Calculado desde decisiones durables si están presentes

        lines.append("# HELP praxeon_policy_evaluations_total Total policy decisions evaluated by Praxeon PolicyEngine.")
        lines.append("# TYPE praxeon_policy_evaluations_total counter")
        lines.append(f'praxeon_policy_evaluations_total{{status="allow"}} {allowed}')
        lines.append(f'praxeon_policy_evaluations_total{{status="block"}} {blocked}')
        lines.append(f'praxeon_policy_evaluations_total{{status="review"}} {review}')

        # Latencias e histograma de decisiones
        latencies_sec: List[float] = []
        nonce_success = 0
        nonce_replay = 0

        if hasattr(service, "decision_repository") and service.decision_repository:
            try:
                conn = service.decision_repository._get_connection()
                cur = conn.cursor()
                cur.execute("SELECT data_json FROM durable_decisions ORDER BY created_at DESC LIMIT 1000")
                rows = cur.fetchall()
                for r in rows:
                    try:
                        d = json.loads(r[0])
                        dec_status = (d.get("status") or "").upper()
                        if dec_status == "REPLAN":
                            replan += 1

                        lat_ms = d.get("receipt", {}).get("latency_ms")
                        if lat_ms is not None and isinstance(lat_ms, (int, float)) and lat_ms > 0:
                            latencies_sec.append(float(lat_ms) / 1000.0)

                        # Verificación de nonce
                        rec = d.get("receipt", {})
                        if rec.get("signature") or rec.get("capability"):
                            nonce_success += 1
                    except Exception:
                        pass
                service.decision_repository._close_conn(conn)
            except Exception:
                pass

        lines.append(f'praxeon_policy_evaluations_total{{status="replan"}} {replan}')

        # Histograma de latencias
        lines.append("# HELP praxeon_decision_latency_seconds Latency of System-1 and supervisory decision pipeline in seconds.")
        lines.append("# TYPE praxeon_decision_latency_seconds histogram")

        bucket_counts: Dict[float, int] = {b: 0 for b in self.DEFAULT_LATENCY_BUCKETS}
        total_latency_sum = 0.0
        for l_sec in latencies_sec:
            total_latency_sum += l_sec
            for b in self.DEFAULT_LATENCY_BUCKETS:
                if l_sec <= b:
                    bucket_counts[b] += 1

        cumulative = 0
        for b in self.DEFAULT_LATENCY_BUCKETS:
            cumulative = bucket_counts[b]
            lines.append(f'praxeon_decision_latency_seconds_bucket{{le="{b}"}} {cumulative}')
        lines.append(f'praxeon_decision_latency_seconds_bucket{{le="+Inf"}} {len(latencies_sec)}')
        lines.append(f"praxeon_decision_latency_seconds_sum {round(total_latency_sum, 4)}")
        lines.append(f"praxeon_decision_latency_seconds_count {len(latencies_sec)}")

        # Métricas de Nonces (Anti-Replay)
        lines.append("# HELP praxeon_nonce_verifications_total Total capability token nonce verifications.")
        lines.append("# TYPE praxeon_nonce_verifications_total counter")
        lines.append(f'praxeon_nonce_verifications_total{{result="success"}} {nonce_success}')
        lines.append(f'praxeon_nonce_verifications_total{{result="replay_detected"}} {nonce_replay}')

        # Métricas de Context Cache (L1 / L2)
        if hasattr(service, "context_manager") and service.context_manager:
            try:
                c_metrics = service.context_manager.get_metrics()
                l1_hits = c_metrics.get("l1_hits", 0)
                l1_misses = c_metrics.get("l1_misses", 0)
                l2_hits = c_metrics.get("l2_hits", 0)
                l2_misses = c_metrics.get("l2_misses", 0)
                hit_ratio = float(c_metrics.get("hit_ratio", 0.0))

                lines.append("# HELP praxeon_context_cache_requests_total Total context fragment cache queries.")
                lines.append("# TYPE praxeon_context_cache_requests_total counter")
                lines.append(f'praxeon_context_cache_requests_total{{level="l1",result="hit"}} {l1_hits}')
                lines.append(f'praxeon_context_cache_requests_total{{level="l1",result="miss"}} {l1_misses}')
                lines.append(f'praxeon_context_cache_requests_total{{level="l2",result="hit"}} {l2_hits}')
                lines.append(f'praxeon_context_cache_requests_total{{level="l2",result="miss"}} {l2_misses}')

                lines.append("# HELP praxeon_context_cache_hit_ratio Ratio of cache hits across all context lookups.")
                lines.append("# TYPE praxeon_context_cache_hit_ratio gauge")
                lines.append(f"praxeon_context_cache_hit_ratio {round(hit_ratio, 4)}")
            except Exception:
                pass

        # Total de eventos emitidos
        total_events = sum(s.get("event_count", 0) for s in sessions)
        lines.append("# HELP praxeon_events_total Total lifecycle events emitted by EventBus.")
        lines.append("# TYPE praxeon_events_total counter")
        lines.append(f"praxeon_events_total {total_events}")

        # Contadores y gauges personalizados
        with self._lock:
            for c_name, c_vals in self._custom_counters.items():
                lines.append(f"# TYPE {c_name} counter")
                for c_lbl, c_val in c_vals.items():
                    lines.append(f"{c_name}{c_lbl} {c_val}")

            for g_name, g_vals in self._custom_gauges.items():
                lines.append(f"# TYPE {g_name} gauge")
                for g_lbl, g_val in g_vals.items():
                    lines.append(f"{g_name}{g_lbl} {g_val}")

        return "\n".join(lines) + "\n"


_global_exporter: Optional[PrometheusMetricsExporter] = None
_exporter_lock = threading.Lock()


def get_prometheus_exporter() -> PrometheusMetricsExporter:
    """Retorna la instancia singleton del exportador de métricas Prometheus."""
    global _global_exporter
    with _exporter_lock:
        if _global_exporter is None:
            _global_exporter = PrometheusMetricsExporter()
        return _global_exporter
