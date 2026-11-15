"""Comprueba los mínimos de cobertura por módulo de ``[tool.praxeon-coverage]`` en pyproject.toml (auditoría Q-03).

``--cov-fail-under`` solo mide el total: un módulo crítico puede caer sin que se note mientras el resto lo compense. Aquí cada
módulo (o paquete, si la clave termina en ``/``) tiene su propio mínimo.

Uso, tras ejecutar pytest con ``--cov=praxeon --cov-report=json``::

    python scripts/check_module_coverage.py [coverage.json] [pyproject.toml]

La métrica es la de coverage.py (líneas más ramas), la misma que usa ``--cov-fail-under``. Sale con 1 si algún módulo está por
debajo de su mínimo o si una clave no corresponde a ningún fichero medido (un renombrado no puede dejar un mínimo vacío).
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

TABLE = "praxeon-coverage"


def load_floors(pyproject: Path) -> dict[str, float]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return {str(key): float(value) for key, value in data.get("tool", {}).get(TABLE, {}).items()}


def measured(report: dict) -> dict[str, dict]:
    """Resumen por fichero, con rutas ``/`` aunque coverage las haya escrito con ``\\`` (Windows)."""
    return {path.replace("\\", "/"): entry["summary"] for path, entry in report["files"].items()}


def percent(summaries: list[dict]) -> float:
    """Líneas + ramas cubiertas sobre líneas + ramas totales, como el porcentaje de coverage.py."""
    total = sum(s["num_statements"] + s.get("num_branches", 0) for s in summaries)
    covered = sum(s["covered_lines"] + s.get("covered_branches", 0) for s in summaries)
    return 100.0 if total == 0 else 100.0 * covered / total


def check(report: dict, floors: dict[str, float]) -> tuple[list[str], list[str]]:
    """Devuelve (líneas del informe, fallos)."""
    files = measured(report)
    lines: list[str] = []
    failures: list[str] = []
    for key, floor in sorted(floors.items()):
        wanted = [s for path, s in files.items() if (path.startswith(key) if key.endswith("/") else path == key)]
        if not wanted:
            failures.append(f"{key}: no corresponde a ningún fichero medido (¿renombrado o fuera de --cov?)")
            continue
        value = percent(wanted)
        ok = value >= floor
        lines.append(f"{'OK  ' if ok else 'FAIL'} {value:5.1f}% (mínimo {floor:g}%)  {key}")
        if not ok:
            failures.append(f"{key}: {value:.1f}% < {floor:g}%")
    return lines, failures


def main(argv: list[str]) -> int:
    report_path = Path(argv[1] if len(argv) > 1 else "coverage.json")
    pyproject = Path(argv[2] if len(argv) > 2 else "pyproject.toml")
    floors = load_floors(pyproject)
    if not floors:
        print(f"No hay mínimos en [tool.{TABLE}] de {pyproject}.", file=sys.stderr)
        return 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    lines, failures = check(report, floors)
    print("\n".join(lines))
    if failures:
        print("\nCobertura por debajo del mínimo:", *failures, sep="\n  - ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
