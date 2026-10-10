"""Tests de validación para la exportación de OpenAPI y consistencia del SDK (tests/test_openapi_export.py)."""

import json
from pathlib import Path
from scripts.export_openapi import export_openapi


def test_export_openapi_schema(tmp_path: Path):
    """Valida que el generador de OpenAPI genera un esquema OpenAPI 3.1 válido con endpoints clave."""
    target_json = tmp_path / "openapi_test.json"
    schema = export_openapi(output_paths=[target_json])

    assert target_json.exists()
    assert schema.get("openapi", "").startswith("3.")
    assert schema.get("info", {}).get("version") == "1.1.0"
    assert "PRAXEON" in schema.get("info", {}).get("title", "")

    paths = schema.get("paths", {})

    # Endpoints clave del servidor REST
    assert "/v1/sessions" in paths
    assert "/v1/sessions/run" in paths
    assert "/v1/sessions/{session_id}/actions" in paths
    assert "/v1/decisions/{decision_id}/confirm" in paths
    assert "/v1/decisions/{decision_id}/execute" in paths
    assert "/v1/workflows" in paths
    assert "/metrics" in paths
    assert "/v1/metrics" in paths
    assert "/health" in paths


def test_packages_sdk_files_exist():
    """Valida la presencia del paquete TypeScript @praxeon/sdk."""
    sdk_dir = Path("packages") / "sdk"
    assert (sdk_dir / "package.json").is_file()
    assert (sdk_dir / "tsconfig.json").is_file()
    assert (sdk_dir / "src" / "types.ts").is_file()
    assert (sdk_dir / "src" / "client.ts").is_file()
    assert (sdk_dir / "src" / "index.ts").is_file()
    assert (sdk_dir / "README.md").is_file()

    # Validar que package.json sea un JSON válido con versión 1.1.0
    with open(sdk_dir / "package.json", "r", encoding="utf-8") as f:
        pkg = json.load(f)
    assert pkg.get("name") == "@praxeon/sdk"
    assert pkg.get("version") == "1.1.0"
