"""Tests para el exportador de telemetría y métricas Prometheus / OpenTelemetry (tests/test_prometheus_metrics.py)."""

import pytest
from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.telemetry.prometheus import PrometheusMetricsExporter, get_prometheus_exporter


def test_prometheus_metrics_exporter_unit():
    """Valida la generación de formato texto Prometheus 0.0.4 puro."""
    exporter = PrometheusMetricsExporter(start_time=100.0)

    # Registrar contadores y gauges personalizados
    exporter.record_counter("test_counter_total", value=5, labels={"source": "agent", "type": "tool"})
    exporter.set_gauge("test_active_workers", value=3, labels={"cluster": "local"})

    metrics_text = exporter.generate_metrics(service=None)

    assert "# HELP praxeon_build_info" in metrics_text
    assert "# TYPE praxeon_build_info gauge" in metrics_text
    assert 'praxeon_build_info{version="1.1.0"' in metrics_text

    assert "# HELP praxeon_uptime_seconds" in metrics_text
    assert "# TYPE praxeon_uptime_seconds gauge" in metrics_text
    assert "praxeon_uptime_seconds" in metrics_text

    assert '# TYPE test_counter_total counter' in metrics_text
    assert 'test_counter_total{source="agent",type="tool"} 5.0' in metrics_text

    assert '# TYPE test_active_workers gauge' in metrics_text
    assert 'test_active_workers{cluster="local"} 3' in metrics_text


def test_prometheus_metrics_with_mock_service():
    """Valida el cálculo de buckets, evaluaciones de políticas y cache hit ratio."""
    exporter = PrometheusMetricsExporter()

    class MockContextManager:
        def get_metrics(self):
            return {
                "l1_hits": 10,
                "l1_misses": 2,
                "l2_hits": 8,
                "l2_misses": 4,
                "hit_ratio": 0.75,
            }

    class MockService:
        def __init__(self):
            self.context_manager = MockContextManager()
            self.decision_repository = None

        def list_sessions(self):
            return [
                {
                    "id": "s-1",
                    "status": "Active",
                    "execution_mode": "local_restricted",
                    "allowed_count": 8,
                    "blocked_count": 2,
                    "review_count": 1,
                    "event_count": 15,
                },
                {
                    "id": "s-2",
                    "status": "Completed",
                    "execution_mode": "container",
                    "allowed_count": 4,
                    "blocked_count": 0,
                    "review_count": 0,
                    "event_count": 6,
                },
            ]

    srv = MockService()
    out = exporter.generate_metrics(service=srv)

    assert "praxeon_sessions_total 2" in out
    assert "praxeon_active_sessions 1" in out
    assert 'praxeon_sessions_by_isolation{mode="local_restricted"} 1' in out
    assert 'praxeon_sessions_by_isolation{mode="container"} 1' in out

    assert 'praxeon_policy_evaluations_total{status="allow"} 12' in out
    assert 'praxeon_policy_evaluations_total{status="block"} 2' in out
    assert 'praxeon_policy_evaluations_total{status="review"} 1' in out

    assert 'praxeon_context_cache_requests_total{level="l1",result="hit"} 10' in out
    assert 'praxeon_context_cache_requests_total{level="l1",result="miss"} 2' in out
    assert "praxeon_context_cache_hit_ratio 0.75" in out

    assert "praxeon_decision_latency_seconds_bucket" in out
    assert 'praxeon_decision_latency_seconds_bucket{le="+Inf"}' in out
    assert "praxeon_events_total 21" in out


def test_metrics_http_endpoints_integration():
    """Valida los endpoints HTTP raíz y versión 1 en FastAPI."""
    app = create_app()
    client = TestClient(app)

    # 1. Endpoint raíz /metrics (Scraping Prometheus por defecto)
    res_root = client.get("/metrics")
    assert res_root.status_code == 200
    assert "text/plain" in res_root.headers.get("content-type", "")
    assert "# HELP praxeon_build_info" in res_root.text
    assert "praxeon_uptime_seconds" in res_root.text

    # 2. Endpoint /v1/metrics en formato JSON (por defecto para REST clients)
    res_v1_json = client.get("/v1/metrics")
    assert res_v1_json.status_code == 200
    assert "application/json" in res_v1_json.headers.get("content-type", "")
    data = res_v1_json.json()
    assert "data" in data
    assert "total_sessions" in data["data"]
    assert "decisions_by_status" in data["data"]

    # 3. Endpoint /v1/metrics con query ?format=prometheus
    res_v1_prom_query = client.get("/v1/metrics?format=prometheus")
    assert res_v1_prom_query.status_code == 200
    assert "text/plain" in res_v1_prom_query.headers.get("content-type", "")
    assert "praxeon_build_info" in res_v1_prom_query.text

    # 4. Endpoint dedicado /v1/metrics/prometheus
    res_v1_prom_path = client.get("/v1/metrics/prometheus")
    assert res_v1_prom_path.status_code == 200
    assert "text/plain" in res_v1_prom_path.headers.get("content-type", "")
    assert "praxeon_build_info" in res_v1_prom_path.text

    # 5. Content negotiation con cabecera Accept: text/plain
    res_accept = client.get("/v1/metrics", headers={"Accept": "text/plain"})
    assert res_accept.status_code == 200
    assert "text/plain" in res_accept.headers.get("content-type", "")
    assert "praxeon_build_info" in res_accept.text
