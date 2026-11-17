"""Una observación de herramienta es un FALLO solo si lo es: leer un documento que dice «error» o «no existen» no es un fallo.

Regresión de una misión real de edición de documentación con agy: el supervisor buscaba «no existe»/«error» en cualquier parte de la observación,
así que (1) una lectura correcta no contaba como evidencia (las ediciones y el `finish` posteriores se vetaban por «falta de evidencia») y (2) el
cortacircuitos anunciaba «fallos consecutivos intentando acceder a rutas que no existen» sobre archivos que existían.
"""

from types import SimpleNamespace

import pytest

from praxeon.policy.loop_detector import _is_failure
from praxeon.reasoning.grounding import EvidenceEngine
from praxeon.reasoning.observations import is_failure_observation, reports_missing_path
from praxeon.server.runtime_service.agent_loop import RecordResultStep

# contenido de un documento real: largo, con frases que contienen las palabras que antes se tomaban por un fallo
DOC = (
    "# Plan de trabajo\n\nEstado: pendiente\n\n"
    + "Contenido: resumen con el contexto del proyecto, el planteamiento del problema y los objetivos generales. " * 6
    + "\nLos diagramas aún no existen (hoy son marcadores de contenido embebido).\nSi hay un error de cálculo se corrige en la siguiente revisión.\n"
    + "El test de integración failed en la primera ejecución y se arregló después.\n"
)


@pytest.mark.parametrize("obs", [
    DOC,
    "[FULL_ACCESS] Contenido de 'docs/memoria/02-resumen.md':\n" + DOC,
    "x" * 400 + " no existe " + "y" * 400,
    "Los diagramas aún no existen " + "z" * 500,
    "salida normal sin ninguna palabra clave",
    "",
])
def test_long_content_that_mentions_failure_words_is_not_a_failure(obs):
    assert is_failure_observation(obs) is False
    assert reports_missing_path(obs) is False
    assert _is_failure(obs) is False


@pytest.mark.parametrize("obs", [
    "Error: no existe el archivo 'a.txt'",
    "La ruta 'docs/x.md' NO EXISTE. Comprueba el nombre con list_dir sobre la carpeta superior.",
    "[FULL_ACCESS] Archivo o ruta 'f.md' (C:\\p\\f.md) no existe en el sistema.",
    "Command failed with exit code 1",
    "Python was not found; run with error",
    "File not found: x",
    "Traceback (most recent call last):\n" + "  File x\n" * 80 + "ValueError: boom",   # largo, pero EMPIEZA como un fallo
    "Error: " + "detalle " * 200,
    "❌ La ruta 'a' NO EXISTE en el sistema de archivos",
])
def test_short_or_error_shaped_messages_are_failures(obs):
    assert is_failure_observation(obs) is True


def test_only_a_failure_that_says_the_path_is_missing_counts_as_a_missing_path():
    assert reports_missing_path("Error: no existe el archivo 'a.txt'") is True
    assert reports_missing_path("Archivo o ruta 'f.md' no existe en el sistema.") is True
    assert reports_missing_path("Error: permiso denegado") is False                   # fallo, pero no de ruta
    assert reports_missing_path(DOC) is False


def test_reading_a_document_that_mentions_errors_is_evidence():
    engine = EvidenceEngine()
    added = engine.ingest_from_observation("read_file", {"path": "docs/memoria/02-resumen.md"}, DOC, step_id="s1")
    assert {e.claim for e in added} == {"file_exists:docs/memoria/02-resumen.md", "file_read:docs/memoria/02-resumen.md"}


def test_a_real_read_failure_is_still_not_evidence():
    engine = EvidenceEngine()
    assert engine.ingest_from_observation("read_file", {"path": "nada.md"}, "Error: no existe el archivo 'nada.md'", step_id="s1") == []
    assert engine.ingest_from_observation("read_file", {"path": "nada.md"}, "[FULL_ACCESS] Archivo o ruta 'nada.md' no existe en el sistema.", step_id="s2") == []


def _ctx(tool="run_command", args=None, reasons=""):
    return SimpleNamespace(step_reasons=reasons, tool=tool, args=args or {"command": "Get-Content docs/memoria/*.md"}, workspace_root=None)


def test_the_circuit_breaker_does_not_blame_missing_paths_for_a_command_whose_output_is_a_document():
    # Get-Content docs/memoria/*.md; git log … → salió con código 1 por `git`, y su salida era el texto de los documentos
    assert RecordResultStep._missing_paths(_ctx(), DOC) is False
    assert RecordResultStep._missing_paths(_ctx(), "Los diagramas aún no existen " + "z" * 400) is False


def test_the_circuit_breaker_still_recognises_real_missing_paths():
    assert RecordResultStep._missing_paths(_ctx(), "Error: no existe la carpeta 'docs/zzz'") is True
    assert RecordResultStep._missing_paths(_ctx(reasons="UNGROUNDED_FILE_NOT_FOUND"), DOC) is True
    assert RecordResultStep._missing_paths(_ctx(tool="read_file", args={"path": "esto/no/esta/aqui.md"}), "x") is True


# ----------------------------------------------------------------------- de extremo a extremo: una misión que lee un documento largo
from tests.test_vision_providers import FakeServer, api  # noqa: E402,F401,F811  (fixture `api`: servidor real con workspace temporal)


def test_a_mission_that_reads_a_document_mentioning_errors_finishes_without_interventions(api, monkeypatch):  # noqa: F811
    """Guarda de extremo a extremo: una misión que lee un documento largo con «error»/«no existen» termina sin vetos ni cortacircuitos.

    Aviso honesto: por sí sola NO discrimina (pasa también con el comprobador ingenuo, porque en este camino el veto no depende de ese detalle);
    lo que fija los comprobadores son las pruebas unitarias de arriba.
    """
    import time

    client, service, root = api
    (root / "guia.md").write_text(DOC, encoding="utf-8")
    replies = iter([
        'Thought: leo la guía\nAction: read_file({"path": "guia.md"})',
        'Thought: ya la he leído\nAction: finish("Guía leída y resumida")',
    ])
    srv = FakeServer(lambda path, body: {"choices": [{"message": {"content": next(replies, 'Thought: fin\nAction: finish("Guía leída y resumida")')}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}})
    try:
        monkeypatch.setattr("praxeon.policy.egress.validate_provider_endpoint", lambda url, **kw: url)
        res = client.post("/v1/sessions/run", json={
            "goal": "lee guia.md y resúmela", "execution_mode": "local_restricted", "max_steps": 6, "step_delay_ms": 100, "workspace_root": str(root),
            "llm_provider": "openai", "llm_model": "gpt-4o", "api_key": "k", "base_url": f"{srv.url}/v1", "session_id": "docread",
        })
        assert res.status_code == 201, res.text
        deadline = time.time() + 40
        events = []
        while time.time() < deadline:
            events = client.get("/v1/sessions/docread/events", params={"limit": 1000}).json()["data"]["events"]
            if any(e["type"] == "session.completed" for e in events):
                break
            time.sleep(0.3)
        done = next(e["payload"] for e in events if e["type"] == "session.completed")
        assert done["summary"] == "Guía leída y resumida", done
        interventions = [e["payload"].get("intervention") for e in events if e["type"] == "intervention.applied"]
        assert not interventions, interventions                                   # ni vetos, ni retrocesos, ni cortacircuitos
    finally:
        srv.close()
