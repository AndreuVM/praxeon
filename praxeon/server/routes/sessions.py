"""Rutas REST para gestión de sesiones y propuestas de acción (praxeon/server/routes/sessions.py)."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status

from praxeon.server.dependencies import RuntimeApplicationService, get_runtime_service
from praxeon.server.schemas.action import ProposeActionRequest
from praxeon.server.schemas.common import APIResponse
from praxeon.server.schemas.decision import DecisionResponse
from praxeon.server.schemas.session import (
    CreateSessionRequest,
    RunMissionRequest,
    SessionSnapshotResponse,
    SessionSummaryResponse,
)

router = APIRouter(prefix="/v1/sessions", tags=["Sessions"])


@router.post("/run", response_model=APIResponse[SessionSummaryResponse], status_code=status.HTTP_201_CREATED)
def run_mission(
    req: RunMissionRequest,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Inicia una nueva misión interactiva con ejecución y supervisión en tiempo real."""
    meta = service.start_mission(
        goal=req.goal,
        session_id=req.session_id,
        agent_name=req.agent_name or "CodingAgent",
        llm_provider=req.llm_provider or "simulator",
        llm_model=req.llm_model,
        api_key=req.api_key,
        base_url=req.base_url,
        supervisor=req.supervisor or "laya",
        max_steps=req.max_steps or 6,
        step_delay_ms=req.step_delay_ms or 900,
    )
    summary = service.get_session_summary(meta["session_id"])
    if not summary:
        raise HTTPException(status_code=500, detail="Error inicializando el resumen de la misión.")
    return APIResponse(data=SessionSummaryResponse(**summary))


@router.post("/{session_id}/pause", response_model=APIResponse[Dict[str, Any]])
def pause_mission(
    session_id: str,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Pausa temporalmente una misión en ejecución."""
    ok = service.pause_mission(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Misión activa para sesión '{session_id}' no encontrada.")
    return APIResponse(data={"session_id": session_id, "status": "Paused"})


@router.post("/{session_id}/resume", response_model=APIResponse[Dict[str, Any]])
def resume_mission(
    session_id: str,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Reanuda una misión pausada."""
    ok = service.resume_mission(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Misión activa para sesión '{session_id}' no encontrada.")
    return APIResponse(data={"session_id": session_id, "status": "Resumed"})


@router.post("", response_model=APIResponse[SessionSummaryResponse], status_code=status.HTTP_201_CREATED)
def create_session(
    req: CreateSessionRequest,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Crea una nueva sesión de supervisión para un agente autónomo."""
    meta = service.create_session(
        goal=req.goal,
        session_id=req.session_id,
        agent_name=req.agent_name or "CodingAgent",
        metadata=req.metadata,
    )
    summary = service.get_session_summary(meta["session_id"])
    if not summary:
        raise HTTPException(status_code=500, detail="Error inicializando el resumen de la sesión.")
    return APIResponse(data=SessionSummaryResponse(**summary))


@router.get("", response_model=APIResponse[List[SessionSummaryResponse]])
def list_sessions(
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Devuelve la lista de todas las sesiones registradas con sus contadores de decisión."""
    summaries = service.list_sessions()
    return APIResponse(data=[SessionSummaryResponse(**s) for s in summaries])


@router.get("/{session_id}", response_model=APIResponse[SessionSnapshotResponse])
def get_session_snapshot(
    session_id: str,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Obtiene el snapshot completo de una sesión junto con el árbol de decisiones derivado."""
    snapshot = service.get_session_snapshot(session_id)
    if not snapshot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Sesión '{session_id}' no encontrada.")
    return APIResponse(data=SessionSnapshotResponse(**snapshot))


@router.post("/{session_id}/actions", response_model=APIResponse[DecisionResponse])
def propose_action(
    session_id: str,
    proposal: ProposeActionRequest,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Propone una acción para su evaluación formal a través del pipeline de supervisión."""
    try:
        decision_resp = service.propose_action(session_id, proposal)
        return APIResponse(data=decision_resp)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error en supervisión de la propuesta: {str(e)}",
        )


@router.get("/{session_id}/decisions", response_model=APIResponse[List[Dict[str, Any]]])
def list_session_decisions(
    session_id: str,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Lista las decisiones emitidas en el contexto de una sesión."""
    decisions = service.list_decisions_for_session(session_id)
    return APIResponse(data=decisions)

