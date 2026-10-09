"""Rutas REST para gestión de sesiones y propuestas de acción (praxeon/server/routes/sessions.py)."""

import os
import secrets
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
        execution_mode=req.execution_mode or "local_restricted",
        workspace_root=req.workspace_root,
        llm_provider=req.llm_provider or "simulator",
        llm_model=req.llm_model,
        api_key=req.api_key,
        base_url=req.base_url,
        supervisor=req.supervisor or "laya",
        decision_model=req.decision_model,
        max_steps=req.max_steps or 25,
        step_delay_ms=req.step_delay_ms or 900,
        autonomous=bool(req.autonomous or req.allow_unattended_execution),
        allow_unattended_execution=bool(req.autonomous or req.allow_unattended_execution),
        llm_failure_policy=req.llm_failure_policy or "synthetic_fallback",
        chat_history=req.chat_history,
        metadata={"created_by": "api_client", "full_access_authorized_by_operator": False},
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


@router.post("/{session_id}/stop", response_model=APIResponse[Dict[str, Any]])
def stop_mission(
    session_id: str,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Detiene definitivamente una misión en ejecución."""
    ok = service.stop_mission(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Misión activa para sesión '{session_id}' no encontrada.")
    return APIResponse(data={"session_id": session_id, "status": "Stopped"})


@router.post("/{session_id}/rollback", response_model=APIResponse[Dict[str, Any]])
def rollback_session(
    session_id: str,
    req: Optional[Dict[str, Any]] = None,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Revierte la sesión a un checkpoint de estado seguro."""
    data = req or {}
    checkpoint_id = data.get("checkpoint_id")
    culprit_tool = data.get("culprit_tool")
    reason = data.get("reason", "Rollback formal por degradación de trayectoria")
    try:
        res = service.rollback_session(
            session_id,
            checkpoint_id=checkpoint_id,
            culprit_tool=culprit_tool,
            reason=reason,
        )
        return APIResponse(data=res)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e).strip("'"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))



@router.post("", response_model=APIResponse[SessionSummaryResponse], status_code=status.HTTP_201_CREATED)
def create_session(
    req: CreateSessionRequest,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Crea una nueva sesión de supervisión para un agente autónomo."""
    metadata = dict(req.metadata or {})
    metadata["created_by"] = "api_client"
    # Un llamante HTTP untrusted no puede auto-aprobarse full_access autónomo sin verificar credencial de operador
    operator_token = metadata.get("operator_token") or metadata.get("operator_approval_token")
    expected_secret = os.environ.get("PRAXEON_SECRET_KEY") or os.environ.get("PRAXEON_API_KEY")
    if operator_token and expected_secret and secrets.compare_digest(str(operator_token), str(expected_secret)):
        metadata["full_access_authorized_by_operator"] = True
    else:
        metadata["full_access_authorized_by_operator"] = False

    if req.confirmation_required_for_full_access is not None:
        metadata["confirmation_required_for_full_access"] = req.confirmation_required_for_full_access
    if req.allow_unattended_execution is not None:
        metadata["allow_unattended_execution"] = req.allow_unattended_execution
    if req.autonomous is not None:
        metadata["autonomous"] = req.autonomous
    if req.workspace_root is not None:
        metadata["working_directory"] = req.workspace_root
        metadata["workspace_root"] = req.workspace_root
    if req.network_policy is not None:
        metadata["network_policy"] = req.network_policy
    if req.decision_model is not None:
        metadata["decision_model"] = req.decision_model
    if req.llm_config is not None:
        metadata["llm_config"] = req.llm_config

    meta = service.create_session(
        goal=req.goal,
        session_id=req.session_id,
        agent_name=req.agent_name or "CodingAgent",
        execution_mode=req.execution_mode or "local_restricted",
        metadata=metadata,
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
@router.get("/{session_id}/snapshot", response_model=APIResponse[SessionSnapshotResponse])
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


@router.delete("/{session_id}", response_model=APIResponse[Dict[str, Any]])
@router.delete("/{session_id}/", response_model=APIResponse[Dict[str, Any]])
@router.post("/{session_id}/delete", response_model=APIResponse[Dict[str, Any]])
def delete_session(
    session_id: str,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Elimina permanentemente una sesión y todos sus registros asociados."""
    ok = service.delete_session(session_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sesión '{session_id}' no encontrada.",
        )
    return APIResponse(data={"session_id": session_id, "deleted": True})


@router.delete("", response_model=APIResponse[Dict[str, Any]])
@router.delete("/", response_model=APIResponse[Dict[str, Any]])
@router.post("/clear", response_model=APIResponse[Dict[str, Any]])
def clear_old_sessions(
    only_completed: bool = True,
    exclude_session_id: Optional[str] = None,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Purga sesiones antiguas para liberar espacio acumulado."""
    deleted_count = service.clear_old_sessions(
        only_completed=only_completed,
        exclude_session_id=exclude_session_id,
    )
    return APIResponse(data={"deleted_count": deleted_count})


@router.get("/{session_id}/context-stats", response_model=APIResponse[Dict[str, Any]])
def get_session_context_stats(
    session_id: str,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Retorna las métricas cuantitativas de context caching e invalidación para esta sesión."""
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sesión '{session_id}' no encontrada.",
        )
    metrics = service.context_manager.get_metrics()
    return APIResponse(data={
        "session_id": session_id,
        "context_metrics": metrics,
    })



