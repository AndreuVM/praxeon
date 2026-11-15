"""El comprobador de cobertura por módulo (``scripts/check_module_coverage.py``, auditoría Q-03)."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_module_coverage.py"
spec = importlib.util.spec_from_file_location("check_module_coverage", SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def summary(statements, covered, branches=0, covered_branches=0):
    return {"summary": {"num_statements": statements, "covered_lines": covered,
                        "num_branches": branches, "covered_branches": covered_branches}}


REPORT = {"files": {
    "praxeon\\runtime\\executor.py": summary(100, 95, 20, 15),          # (95+15)/(100+20) = 91,7 %
    "praxeon/policy/engine.py": summary(80, 80),                          # 100 %
    "praxeon/policy/permissions.py": summary(80, 40, 20, 0),              # 40 %
}}


def test_the_percentage_counts_lines_and_branches_like_coverage_py():
    assert checker.percent([REPORT["files"]["praxeon\\runtime\\executor.py"]["summary"]]) == pytest.approx(91.666, abs=0.01)
    assert checker.percent([]) == 100.0


def test_windows_separators_are_normalized():
    assert "praxeon/runtime/executor.py" in checker.measured(REPORT)


def test_a_module_at_or_above_its_floor_passes():
    lines, failures = checker.check(REPORT, {"praxeon/runtime/executor.py": 90})
    assert failures == [] and lines[0].startswith("OK")


def test_a_module_below_its_floor_fails_even_if_the_rest_compensates():
    _lines, failures = checker.check(REPORT, {"praxeon/policy/permissions.py": 90})
    assert failures == ["praxeon/policy/permissions.py: 40.0% < 90%"]


def test_a_key_ending_in_a_slash_aggregates_the_whole_package():
    # (80 + 40) / (80 + 80 + 20) = 66,7 %: el paquete suma sus ficheros, no promedia sus porcentajes
    lines, failures = checker.check(REPORT, {"praxeon/policy/": 60})
    assert failures == [] and "66.7%" in lines[0]
    assert checker.check(REPORT, {"praxeon/policy/": 70})[1]


def test_a_floor_that_matches_no_file_is_an_error_not_a_pass():
    _lines, failures = checker.check(REPORT, {"praxeon/runtime/renombrado.py": 90})
    assert len(failures) == 1 and "ningún fichero medido" in failures[0]


def test_the_command_line_exit_code(tmp_path, capsys):
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps(REPORT), encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.praxeon-coverage]\n"praxeon/runtime/executor.py" = 90\n', encoding="utf-8")
    assert checker.main(["x", str(report), str(pyproject)]) == 0
    pyproject.write_text('[tool.praxeon-coverage]\n"praxeon/policy/permissions.py" = 90\n', encoding="utf-8")
    assert checker.main(["x", str(report), str(pyproject)]) == 1
    assert "por debajo del mínimo" in capsys.readouterr().err
    pyproject.write_text("[tool.otra]\nx = 1\n", encoding="utf-8")
    assert checker.main(["x", str(report), str(pyproject)]) == 1                   # sin mínimos configurados
