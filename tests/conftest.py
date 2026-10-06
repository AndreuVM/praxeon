"""Configuración global de Pytest y categorización automática por niveles (unit, integration, e2e, security, providers, benchmark)."""

import pytest
from pathlib import Path


def pytest_collection_modifyitems(config, items):
    """Asigna markers canónicos a cada prueba según su ubicación y naturaleza."""
    unit_marker = pytest.mark.unit
    integration_marker = pytest.mark.integration
    e2e_marker = pytest.mark.e2e
    security_marker = pytest.mark.security
    providers_marker = pytest.mark.providers
    benchmark_marker = pytest.mark.benchmark

    # Patrones por defecto para clasificación de archivos raíz
    unit_root_prefixes = (
        "test_domain",
        "test_jev_scoring",
        "test_completion",
        "test_evidence",
        "test_risk_engine",
        "test_policy_v02",
        "test_properties",
        "test_state",
        "test_trajectory",
        "test_evaluation",
        "test_calibration",
    )

    for item in items:
        file_path = Path(item.fspath).resolve()
        path_str = str(file_path).replace("\\", "/").lower()
        file_name = file_path.name.lower()

        # 1. Security
        if "/tests/security/" in path_str or "security" in file_name or file_name.startswith("test_receipt_tampering") or file_name.startswith("test_concurrent_replay"):
            item.add_marker(security_marker)

        # 2. E2E
        if "/tests/e2e/" in path_str or file_name.startswith("test_e2e") or "crash_recovery" in file_name:
            item.add_marker(e2e_marker)

        # 3. Providers
        if "/tests/providers/" in path_str or file_name.startswith("test_provider") or "mock_and_replay" in file_name or file_name.startswith("test_typesafe_adapter"):
            item.add_marker(providers_marker)

        # 4. Benchmarks
        if "/tests/benchmarks/" in path_str or "/tests/benchmark/" in path_str or file_name.startswith("test_benchmark"):
            item.add_marker(benchmark_marker)

        # 5. Unit
        if "/tests/unit/" in path_str or "/tests/domain/" in path_str or "/tests/reasoning/" in path_str:
            item.add_marker(unit_marker)
        elif any(file_name.startswith(p) for p in unit_root_prefixes):
            item.add_marker(unit_marker)
            if file_name.startswith("test_properties"):
                item.add_marker(security_marker)

        # 6. Integration
        if (
            "/tests/integration/" in path_str
            or "/tests/agents/" in path_str
            or "/tests/routing/" in path_str
            or "/tests/workflows/" in path_str
            or "/tests/persistence/" in path_str
            or "/tests/context/" in path_str
            or "/tests/runtime/" in path_str
            or "/tests/live_agent/" in path_str
            or "/tests/release/" in path_str
        ):
            item.add_marker(integration_marker)
        elif not any(m.name in ("unit", "security", "e2e", "providers", "benchmark") for m in item.iter_markers()):
            # Fallback seguro para tests de servidor, CLI o auditoría
            item.add_marker(integration_marker)
