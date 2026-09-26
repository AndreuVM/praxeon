"""Rutas REST para inspección, confirmación y ejecución de decisiones (praxeon/server/routes/decisions.py)."""

from typing import Any, Dict, Optional
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
)

router = APIRouter(prefix="/v1/decisions", tags=["Decisions"])


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
        )
        return APIResponse(data=res)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Decisión '{decision_id}' no encontrada.",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error procesando confirmación: {str(e)}",
        )


@router.post("/{decision_id}/execute", response_model=APIResponse[ExecuteDecisionResponse])
def execute_decision(
    decision_id: str,
    req: Optional[ExecuteDecisionRequest] = None,
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Solicita la ejecución física de la acción autorizada dentro del sandbox."""
    cap_token = req.capability_token if req else None
    try:
        exec_res = service.execute_decision(decision_id, capability_token=cap_token)
        return APIResponse(data=exec_res)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Decisión '{decision_id}' no encontrada.",
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
