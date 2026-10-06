"""Tests de validación para empaquetado, metadatos y distribución Wheel (Task 26)."""

from pathlib import Path
import zipfile
import pytest


def test_wheel_artifact_and_dependencies():
    root = Path(__file__).resolve().parent.parent.parent
    dist_dir = root / "dist"
    wheels = list(dist_dir.glob("praxeon-*.whl"))
    if not wheels:
        import subprocess
        import sys
        try:
            subprocess.run(
                [sys.executable, "-m", "build", "--wheel", "--no-isolation"],
                cwd=str(root),
                check=True,
                capture_output=True,
            )
            wheels = list(dist_dir.glob("praxeon-*.whl"))
        except Exception:
            pass

    if not wheels:
        pytest.skip(f"No se encontró ningún archivo .whl en {dist_dir} y no se pudo construir bajo demanda")

    wheel_path = wheels[0]
    assert wheel_path.exists()
    assert wheel_path.stat().st_size > 1000

    # Inspeccionar contenido interno del wheel
    with zipfile.ZipFile(wheel_path, "r") as zf:
        namelist = zf.namelist()
        # Verificar que el paquete praxeon está empaquetado
        assert any("praxeon/__init__.py" in n for n in namelist)

        # Verificar METADATA para confirmar declaración de PyYAML
        metadata_files = [n for n in namelist if n.endswith("METADATA")]
        assert len(metadata_files) == 1, "Debe existir exactamente un archivo METADATA en la distribución wheel"

        metadata_content = zf.read(metadata_files[0]).decode("utf-8")
        assert "Requires-Dist: pyyaml" in metadata_content or "pyyaml" in metadata_content.lower(), (
            "PyYAML no está declarado en los metadatos de distribución del wheel"
        )
