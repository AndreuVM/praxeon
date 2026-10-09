"""Tests unitarios para BENCH-01 y BENCH-02: Matriz científica y reproducibilidad."""

import json
from pathlib import Path
import pytest

from praxeon.evaluation.matrix import (
    BenchmarkMetadata,
    HardwareMetadata,
    ScientificMatrixBenchmark,
    collect_benchmark_metadata,
    collect_hardware_metadata,
    organize_historical_benchmarks,
)
from praxeon.evaluation.scenarios import BenchmarkScenario, ScenarioCatalog
from praxeon.domain.decision import DecisionStatus
from praxeon.cli import run_benchmark_cli


def test_collect_hardware_and_benchmark_metadata():
    hw = collect_hardware_metadata()
    assert isinstance(hw, HardwareMetadata)
    assert hw.cpu_cores >= 1
    assert hw.ram_gb > 0
    assert hw.os_name != ""

    meta = collect_benchmark_metadata(seed=123)
    assert isinstance(meta, BenchmarkMetadata)
    assert len(meta.commit_sha) >= 7
    assert len(meta.config_hash) == 16
    assert meta.seed == 123
    assert meta.dataset_version == "eval-matrix-v1.1"


def test_organize_historical_benchmarks(tmp_path: Path):
    bench_dir = tmp_path / "benchmark_results"
    bench_dir.mkdir(parents=True)

    # Crear archivos históricos simulados
    old_file_1 = bench_dir / "benchmark_run_20261001.json"
    old_file_1.write_text(json.dumps({"old_run": 1}), encoding="utf-8")
    
    current_file = bench_dir / "matrix_evaluation.json"
    current_file.write_text(json.dumps({"current": True}), encoding="utf-8")

    archived = organize_historical_benchmarks(str(bench_dir))
    assert len(archived) == 1
    assert (bench_dir / "historical" / "benchmark_run_20261001.json").exists()
    assert (bench_dir / "matrix_evaluation.json").exists()
    assert not (bench_dir / "historical" / "matrix_evaluation.json").exists()


def test_scientific_matrix_execution(tmp_path: Path):
    bench = ScientificMatrixBenchmark(
        llms=["qwen2.5-coder:7b", "simulator"],
        supervisors=["sin_supervisor", "replay", "mock"],
        seed=42,
    )

    # Usar un subset de escenarios
    scenarios = ScenarioCatalog.get_extended_scenarios()[:4]
    out_dir = tmp_path / "results"

    report = bench.run_matrix(
        scenarios=scenarios,
        output_dir=str(out_dir),
        archive_historical=False,
    )

    assert report.suite_name == "LLM_x_System1_Scientific_Matrix"
    assert len(report.matrix_cells) == 6  # 2 LLMs x 3 supervisores
    assert (out_dir / "matrix_evaluation.json").exists()
    assert (out_dir / "MATRIX_REPORT.md").exists()

    # Comprobar que en supervisados el False Allow sea 0.0
    for cell in report.matrix_cells:
        if cell.system1_name != "sin_supervisor":
            assert cell.false_allow_rate == 0.0
            assert cell.critical_violations_count == 0
        else:
            # Sin supervisor debe registrar violaciones críticas
            assert cell.critical_violations_count > 0

    # Comprobar contenido del markdown
    md_content = (out_dir / "MATRIX_REPORT.md").read_text(encoding="utf-8")
    assert "# Informe de Evaluación Científica LLM × System-1" in md_content
    assert "Metadatos de Reproducibilidad" in md_content
    assert "False Allow (Crítico)" in md_content


def test_cli_benchmark_matrix_integration(tmp_path: Path):
    out_json = tmp_path / "custom_matrix.json"
    # Ejecutar vía cli
    run_benchmark_cli(
        matrix=True,
        archive_historical=True,
        output_file=str(tmp_path),
    )

    assert (tmp_path / "matrix_evaluation.json").exists()
    assert (tmp_path / "MATRIX_REPORT.md").exists()
