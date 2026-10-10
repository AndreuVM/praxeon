"""Script de exportación de esquema OpenAPI para PRAXEON (scripts/export_openapi.py).

Extrae el esquema JSON canónico de FastAPI generado por Pydantic v2 y lo persiste
en 'openapi.json' y en 'packages/sdk/openapi.json'.
"""

import json
import os
import sys
from pathlib import Path


def export_openapi(output_paths: list[str | Path] | None = None) -> dict:
    """Exporta el esquema OpenAPI de la aplicación PRAXEON."""
    # Asegurar que el directorio raíz del proyecto esté en sys.path
    root_dir = Path(__file__).resolve().parent.parent
    if str(root_dir) not in sys.path:
        sys.path.insert(0, str(root_dir))

    from praxeon.server.app import create_app

    app = create_app()
    openapi_schema = app.openapi()

    if output_paths is None:
        output_paths = [
            root_dir / "openapi.json",
            root_dir / "packages" / "sdk" / "openapi.json",
        ]

    for p in output_paths:
        path_obj = Path(p)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        with open(path_obj, "w", encoding="utf-8") as f:
            json.dump(openapi_schema, f, indent=2, ensure_ascii=False)
        print(f"[OK] OpenAPI schema exportado a: {path_obj}")

    return openapi_schema


if __name__ == "__main__":
    schema = export_openapi()
    print(f"Esquema OpenAPI generado con éxito. Título: {schema.get('info', {}).get('title')} v{schema.get('info', {}).get('version')}")
