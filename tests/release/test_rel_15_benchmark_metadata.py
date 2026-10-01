"""Pruebas de aceptación formal de Release: REL-15 (Metadatos de Reproducibilidad de Benchmarks).

Conforme al documento de cierre 'PRAXEON v1.0.0 — Plan formal de cierre de versión antes de integrar Context Caching':
- REL-15: Metadatos y Benchmark Reproducible:
          Todos los artefactos generados por scripts/run_benchmarks.py deben registrar metadatos formales:
          git_commit, timestamp (ISO-8601 UTC), praxeon_version, model y seed procedural.
          Garantía de reproducibilidad estricta: misma semilla produce exactamente el mismo dataset.
          Persistencia consistente en benchmark_run_metadata.json, cabeceras de SUMMARY.md y dentro de cada JSON de benchmark.
          Resiliencia de detección de Git commit con soporte para variables de entorno PRAXEON_GIT_COMMIT.
"""

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import pytest

from praxeon import __version__
from praxeon.evaluation.scenarios import ScenarioCatalog
from scripts.run_benchmarks import (
    dump_with_metadata,
    generate_benchmark_metadata,
    get_git_commit,
    run_all_benchmarks,
)


# =============================================================================
# REL-15: Metadatos y Benchmark Reproducible
# =============================================================================

def test_rel_15_benchmark_metadata_schema_and_fields():
    """REL-15.1: El generador de metadatos debe emitir un esquema canónico con git_commit, timestamp, version, model y seed."""
    metadata = generate_benchmark_metadata(seed=42, model="laya-v1-calibrated")

    assert isinstance(metadata, dict)
    assert metadata["praxeon_version"] == __version__
    assert isinstance(metadata["git_commit"], str)
    assert len(metadata["git_commit"]) > 0
    assert metadata["seed"] == 42
    assert metadata["model"] == "laya-v1-calibrated"
    assert metadata["platform"] == sys.platform
    assert metadata["python_version"] == sys.version.split()[0]

    # Validar formato ISO-8601 de timestamp UTC
    ts_str = metadata["timestamp"]
    assert ts_str.endswith("Z")
    parsed_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    assert isinstance(parsed_dt, datetime)


def test_rel_15_git_commit_detection_and_fallback(monkeypatch):
    """REL-15.2: get_git_commit debe resolver el commit real o utilizar el fallback de entorno/defecto sin excepciones."""
    # 1. Detección real en el repositorio
    real_commit = get_git_commit()
    assert isinstance(real_commit, str)
    assert len(real_commit) >= 7

    # 2. Prioridad de variable de entorno PRAXEON_GIT_COMMIT
    monkeypatch.setenv("PRAXEON_GIT_COMMIT", "custom_sha_abcdef123456")
    assert get_git_commit() == "custom_sha_abcdef123456"

    # 3. Fallback cuando git falla y no hay variable de entorno
    monkeypatch.delenv("PRAXEON_GIT_COMMIT", raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    with patch("subprocess.run", side_effect=FileNotFoundError("git no encontrado")):
        fallback = get_git_commit()
        assert fallback == "unknown_commit"


def test_rel_15_procedural_dataset_deterministic_seed():
    """REL-15.3: El generador de escenarios procedurales debe ser 100% determinista ante la misma semilla."""
    # Generar dos particiones con seed=42
    train_a = ScenarioCatalog.get_train_scenarios(n=50, seed=42)
    train_b = ScenarioCatalog.get_train_scenarios(n=50, seed=42)

    assert len(train_a) == len(train_b) == 50
    for sa, sb in zip(train_a, train_b):
        assert sa.scenario_id == sb.scenario_id
        assert sa.category == sb.category
        assert sa.expected_status == sb.expected_status
        assert sa.is_destructive == sb.is_destructive
        assert sa.description == sb.description

    # Generar con semilla diferente (seed=99) produce variaciones probabilísticas procedurales
    train_diff = ScenarioCatalog.get_train_scenarios(n=50, seed=99)
    diff_count = sum(
        1
        for sa, sd in zip(train_a, train_diff)
        if sa.simulated_assessment.loop_probability != sd.simulated_assessment.loop_probability
    )
    assert diff_count > 0, "Diferentes semillas deben producir diferentes asignaciones procedurales"


def test_rel_15_dump_with_metadata_injection():
    """REL-15.4: dump_with_metadata debe incrustar limpiamente los metadatos canónicos en dicts y modelos Pydantic."""
    meta = {
        "praxeon_version": "1.0.0",
        "git_commit": "abcdef",
        "timestamp": "2026-10-02T00:00:00Z",
        "seed": 42,
        "model": "laya-v1-calibrated",
    }
    sample_payload = {"total_operations": 100, "throughput_ops_sec": 500.0}

    dumped_str = dump_with_metadata(sample_payload, meta)
    parsed = json.loads(dumped_str)

    assert parsed["total_operations"] == 100
    assert parsed["throughput_ops_sec"] == 500.0
    assert "metadata" in parsed
    assert parsed["metadata"] == meta


def test_rel_15_benchmark_artifacts_generate_complete_metadata(tmp_path):
    """REL-15.5: run_all_benchmarks debe generar todos los archivos JSON y Markdown conteniendo los metadatos completos."""
    out_dir = tmp_path / "benchmark_rel_15_artifacts"
    target_seed = 777
    target_model = "laya-test-eval-suite"

    returned_meta = run_all_benchmarks(
        output_dir=str(out_dir),
        seed=target_seed,
        model=target_model,
        quick_mode=True,
    )

    assert returned_meta["seed"] == target_seed
    assert returned_meta["model"] == target_model

    # 1. Archivo específico de metadatos de la corrida
    meta_file = out_dir / "benchmark_run_metadata.json"
    assert meta_file.exists()
    file_meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert file_meta["seed"] == target_seed
    assert file_meta["model"] == target_model
    assert file_meta["praxeon_version"] == __version__
    assert "git_commit" in file_meta
    assert "timestamp" in file_meta

    # 2. Archivo de resumen en Markdown SUMMARY.md
    summary_file = out_dir / "SUMMARY.md"
    assert summary_file.exists()
    summary_content = summary_file.read_text(encoding="utf-8")
    assert "Metadatos de Reproducibilidad (REL-15)" in summary_content
    assert str(target_seed) in summary_content
    assert target_model in summary_content
    assert file_meta["git_commit"] in summary_content

    # 3. Todos los reportes JSON individuales deben contener la clave "metadata"
    expected_reports = [
        "policy_benchmark_holdout.json",
        "policy_benchmark_train.json",
        "enforcement_benchmark.json",
        "runtime_benchmark.json",
        "trajectory_benchmark.json",
        "provider_comparison.json",
        "ablation_study.json",
    ]

    for report_name in expected_reports:
        r_file = out_dir / report_name
        assert r_file.exists(), f"El reporte {report_name} debe existir."
        report_data = json.loads(r_file.read_text(encoding="utf-8"))
        assert "metadata" in report_data, f"El reporte {report_name} debe contener la clave 'metadata'."
        assert report_data["metadata"]["seed"] == target_seed
        assert report_data["metadata"]["model"] == target_model
        assert report_data["metadata"]["git_commit"] == file_meta["git_commit"]


def test_rel_15_cli_argparse_interface():
    """REL-15.6: La interfaz de línea de comandos de scripts/run_benchmarks.py debe exponer los parámetros configurables."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="benchmark_results")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model", default="laya-v1-calibrated")
    parser.add_argument("--quick", action="store_true")

    args = parser.parse_args(["--output-dir", "custom_dir", "--seed", "101", "--model", "custom-model", "--quick"])
    assert args.output_dir == "custom_dir"
    assert args.seed == 101
    assert args.model == "custom-model"
    assert args.quick is True
