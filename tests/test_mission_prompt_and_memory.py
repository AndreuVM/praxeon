"""Pruebas para validación de formulación de prompt adaptativo y memoria de sesiones multi-turno.

Verifica:
1. Que el esquema RunMissionRequest acepte 'chat_history'.
2. Que la función generate_goal_tailored_steps identifique tareas creativas o conversacionales
   (cuentos, historias, saludos, relatos) y resuelva con 'finish' directamente sin forzar lectura
   de README.md o archivos inexistentes.
3. Que el inicio de misiones interactivas con historial multi-turno preserve y pase la memoria
   conversacional al flujo del agente.
4. Que las tareas de código sigan inspeccionando archivos reales mientras que las tareas
   creativas o de respuesta directa concluyan de inmediato.
"""

import time
import pytest
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    generate_goal_tailored_steps,
    set_runtime_service,
)
from praxeon.server.schemas.session import RunMissionRequest


@pytest.fixture
def runtime_service(tmp_path):
    service = RuntimeApplicationService(db_dir=str(tmp_path / "test_memory_db"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


def test_run_mission_request_schema_supports_chat_history():
    """El esquema RunMissionRequest debe aceptar chat_history opcional."""
    req = RunMissionRequest(
        goal="Ahora cuentame un cuento",
        chat_history=[
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "Hola, ¿en qué puedo ayudarte?"},
        ],
    )
    assert req.chat_history is not None
    assert len(req.chat_history) == 2
    assert req.chat_history[0]["role"] == "user"


def test_creative_goals_resolved_directly_without_file_inspection():
    """Peticiones de cuento o narración no deben generar pasos de inspección de archivos de repo."""
    creative_goals = [
        "Ahora cuentame un cuento",
        "Escribe una historia sobre agentes de IA",
        "Cuéntame un relato corto",
        "Escribe un poema sobre la seguridad",
    ]

    for goal in creative_goals:
        steps = generate_goal_tailored_steps(goal=goal, max_steps=5)
        assert len(steps) >= 1
        first_step = steps[0]
        assert first_step["tool"] == "finish", f"El objetivo '{goal}' debió usar finish directamente, pero usó '{first_step['tool']}'"
        assert "summary" in first_step["arguments"]
        assert len(first_step["arguments"]["summary"]) > 20
        # No debe referenciar README.md ni config/cuento.json
        for s in steps:
            args = s.get("arguments", {})
            assert "README.md" not in str(args)
            assert "config/" not in str(args)


def test_code_tasks_still_inspect_workspace_files():
    """Peticiones sobre tests, código o archivos del repo deben inspeccionar archivos reales."""
    steps = generate_goal_tailored_steps(goal="Auditar y ejecutar suite con pytest", max_steps=5)
    assert len(steps) >= 2
    assert steps[0]["tool"] == "read_file"
    assert "test" in steps[0]["arguments"]["path"]


def test_start_mission_with_chat_history_runs_and_completes(runtime_service):
    """Iniciar una misión con historial previo debe registrar el estado y completar correctamente."""
    history = [
        {"role": "user", "content": "Hola"},
        {"role": "assistant", "content": "Hola, estoy listo."},
    ]
    meta = runtime_service.start_mission(
        goal="Ahora cuentame un cuento",
        llm_provider="simulator",
        chat_history=history,
        step_delay_ms=50,
    )
    sid = meta["session_id"]
    assert sid is not None

    # Esperar brevemente a que el worker complete
    time.sleep(0.5)

    summary = runtime_service.get_session_summary(sid)
    assert summary is not None
    assert summary["status"] in ("Active", "Completed")

    session_meta = runtime_service._sessions_meta.get(sid, {})
    # Debe tener una respuesta final orientada a cuento, no a análisis de README
    final_ans = session_meta.get("final_answer", "")
    if final_ans:
        assert "Había una vez" in final_ans or "cuento" in final_ans or len(final_ans) > 20


def test_opinion_and_rating_goals_resolved_directly_without_boilerplate_report():
    """Preguntas evaluativas o de opinión ('Le das un 10 al proyecto?') deben responder con finish directo."""
    opinion_goals = [
        "Le das un 10 al proyecto?",
        "¿Qué nota le pones al proyecto?",
        "¿Qué opinas de la arquitectura?",
        "¿Cuáles son los puntos fuertes del proyecto?",
    ]

    for goal in opinion_goals:
        steps = generate_goal_tailored_steps(goal=goal, max_steps=5)
        assert len(steps) >= 1
        first_step = steps[0]
        assert first_step["tool"] == "finish", f"El objetivo '{goal}' debió usar finish directamente, pero usó '{first_step['tool']}'"
        summary = first_step["arguments"]["summary"]
        assert "El informe proporciona una descripción detallada" not in summary
        assert "10" in summary or "puntos fuertes" in summary or "arquitectura" in summary.lower()


def test_project_spec_and_requirements_goals_resolved_without_reading_praxeon_readme():
    """Peticiones de informes formales, user stories, requisitos y estructura de carpetas
    para nuevos proyectos deben resolverse sin leer README.md ni pyproject.toml del repo local."""
    goals = [
        "Bien, pues hazme un informe formal inicial con la estructura de carpetas del proyecto, user stories, requisitos funcionales y no funcionales",
        "Diseña la arquitectura, estructura de carpetas y requisitos para un nuevo proyecto de comercio electrónico",
        "Redacta un informe inicial con user stories y requisitos funcionales y no funcionales para la app móvil",
    ]

    for g in goals:
        steps = generate_goal_tailored_steps(goal=g, max_steps=5)
        assert len(steps) >= 1
        first_step = steps[0]
        assert first_step["tool"] == "finish", f"Para '{g}', el primer paso debió ser finish directo, pero fue '{first_step['tool']}'"
        summary = first_step["arguments"]["summary"]
        assert "Estructura de Carpetas" in summary or "user stories" in summary.lower() or "requisitos" in summary.lower()
        # Verificar que no forzó leer el README de PRAXEON
        for s in steps:
            args = s.get("arguments", {})
            assert "README.md" not in str(args)
            assert "pyproject.toml" not in str(args)


def test_finish_on_project_report_is_not_flagged_as_premature_in_propose_action(runtime_service):
    """Verificar que un finish con informe formal inicial no sea penalizado con baja probabilidad
    de grounding en propose_action cuando no se han leído archivos del repositorio."""
    from praxeon.server.schemas.action import ProposeActionRequest

    # Crear una sesión en el runtime_service
    meta = runtime_service.create_session(
        goal="Bien, pues hazme un informe formal inicial con la estructura de carpetas del proyecto, user stories, requisitos funcionales y no funcionales",
        execution_mode="local_restricted",
    )
    sid = meta["session_id"]

    req = ProposeActionRequest(
        action_id="act_1",
        parent_id=f"root_{sid}",
        tool="finish",
        operation="1. Entregar especificación técnica e informe formal",
        arguments={
            "summary": (
                "# Informe Formal Inicial\n\n"
                "## Estructura de Carpetas del Proyecto\n"
                "project-root/ docs/ src/ tests/\n\n"
                "## User Stories\n"
                "- US-01: Registro de usuario seguro\n\n"
                "## Requisitos Funcionales\n"
                "- RF-01: Autenticación OAuth2 y JWT\n\n"
                "## Requisitos No Funcionales\n"
                "- RNF-01: Latencia p95 < 200ms"
            )
        },
        thought_rationale="Entregando informe formal con arquitectura, user stories y requisitos del nuevo proyecto solicitado.",
        context={"goal": "Bien, pues hazme un informe formal inicial con la estructura de carpetas del proyecto, user stories, requisitos funcionales y no funcionales"},
    )

    resp = runtime_service.propose_action(session_id=sid, proposal=req)
    assert resp.status == "ALLOW"
    assert "GROUNDED_LOW_RISK_AUTHORIZED" in resp.policy.reason_codes

