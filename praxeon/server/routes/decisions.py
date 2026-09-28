"""Rutas REST para inspección, confirmación y ejecución de decisiones (praxeon/server/routes/decisions.py)."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status

from praxeon.runtime.executor import PolicyViolation
from praxeon.server.dependencies import RuntimeApplicationService, get_runtime_service
from praxeon.server.schemas.common import APIResponse
from praxeon.server.schemas.decision import (
    ConfirmDecisionRequest,
    ConfirmDecisionResponse,
    DecisionDetailResponse,
    ExecuteDecisionRequest,
    ExecuteDecisionResponse,
    RejectDecisionRequest,
)

router = APIRouter(prefix="/v1/decisions", tags=["Decisions"])


@router.get("", response_model=APIResponse[List[Dict[str, Any]]])
def list_decisions(
    session_id: Optional[str] = None,
    limit: int = 150,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Lista las decisiones emitidas (filtradas opcionalmente por sesión)."""
    decisions = service.list_all_decisions(session_id=session_id, limit=limit)
    return APIResponse(data=decisions)


@router.get("/{decision_id}", response_model=APIResponse[DecisionDetailResponse])
def get_decision_detail(
    decision_id: str,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Obtiene el detalle contextualizado en 4 pestañas (Decision, Evidence, Policy, Receipt)."""
    detail = service.get_decision_detail(decision_id)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Decisión '{decision_id}' no encontrada.",
        )
    return APIResponse(data=detail)


@router.post("/{decision_id}/confirm", response_model=APIResponse[ConfirmDecisionResponse])
def confirm_decision(
    decision_id: str,
    req: ConfirmDecisionRequest,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Aprueba o deniega una decisión humana en estado REVIEW."""
    try:
        res = service.confirm_decision(
            decision_id=decision_id,
            approved=req.approved,
            reason=req.reason,
            actor=req.actor or "human_operator",
            operator_id=req.operator_id or "operator_admin",
            role=req.role or "operator",
        )
        return APIResponse(data=res)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Decisión '{decision_id}' no encontrada.",
        )
    except PermissionError as pe:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(pe),
        )
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error procesando confirmación: {str(e)}",
        )


@router.post("/{decision_id}/reject", response_model=APIResponse[ConfirmDecisionResponse])
def reject_decision(
    decision_id: str,
    req: RejectDecisionRequest,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Rechaza explícitamente una decisión en estado REVIEW con justificación obligatoria del operador."""
    try:
        res = service.reject_decision(
            decision_id=decision_id,
            reason=req.reason,
            actor=req.actor or "human_operator",
            operator_id=req.operator_id or "operator_admin",
            role=req.role or "operator",
        )
        return APIResponse(data=res)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Decisión '{decision_id}' no encontrada.",
        )
    except PermissionError as pe:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(pe),
        )
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error procesando rechazo: {str(e)}",
        )


@router.post("/{decision_id}/execute", response_model=APIResponse[ExecuteDecisionResponse])
def execute_decision(
    decision_id: str,
    req: Optional[ExecuteDecisionRequest] = None,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Solicita la ejecución física de la acción autorizada dentro del sandbox."""
    cap_token = req.capability_token if req else None
    operator_id = req.operator_id if req else None
    role = req.role if req and req.role else "operator"
    try:
        exec_res = service.execute_decision(
            decision_id,
            capability_token=cap_token,
            operator_id=operator_id,
            role=role,
        )
        return APIResponse(data=exec_res)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Decisión '{decision_id}' no encontrada.",
        )
    except PermissionError as pe:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(pe),
        )
    except PolicyViolation as pv:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Ejecución física denegada por política de runtime: {str(pv)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Fallo durante la ejecución en sandbox: {str(e)}",
        )
