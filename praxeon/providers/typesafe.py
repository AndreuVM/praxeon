"""Adaptador de TypeSafe AI System One encapsulado para JEV Reasoning Navigator v0.2.

Aisla por completo la dependencia de typesafe-sdk en este módulo.
Traduce las respuestas de TypeSafe a ProviderAssessment estructurados.
Garantiza el comportamiento fail-safe ante caídas, timeouts y errores de autenticación.
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional
from praxeon.domain.models import ActionCandidate, ProviderAssessment
from praxeon.providers.base import BaseReasoningProvider
from praxeon.providers.resilience import (
    CircuitBreaker,
    calculate_jittered_backoff,
    parse_retry_after,
)

logger = logging.getLogger("praxeon.providers.typesafe")

# Fallback limpio si typesafe-sdk no está instalado en el entorno
try:
    from typesafe_sdk import Choice, Noul, Score, TypeSafeClient
except ImportError:
    class Choice:  # type: ignore
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs

    class Noul:  # type: ignore
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs

    class Score:  # type: ignore
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs

    TypeSafeClient = None  # type: ignore


# Fallos transitorios del API que merecen reintento (red, sobrecarga, límite de peticiones)
TRANSIENT_ERROR_MARKERS = ("timeout", "connection", "502", "503", "504", "reset", "429", "rate", "retry")


def create_sdk_client(api_key: Optional[str]) -> Any:
    """Cliente de typesafe-sdk, o None si no hay clave, falta el SDK o no se puede crear (único punto de acceso al SDK)."""
    if not api_key:
        return None
    try:
        if TypeSafeClient is not None:
            return TypeSafeClient(api_key=api_key)
        from typesafe_sdk import TypeSafeClient as _TSC
        return _TSC(api_key=api_key)
    except Exception as e:
        logger.warning(f"No se pudo inicializar TypeSafeClient: {e}")
        return None


def invoke_system_one(
    client: Any,
    state: Dict[str, Any],
    questions: Dict[str, Any],
    model: str,
    max_retries: int = 2,
    circuit_breaker: Optional[CircuitBreaker] = None,
) -> Any:
    """Llama a ``system_one`` reintentando los fallos transitorios con espera exponencial con jitter (y Retry-After)."""
    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            res = client.system_one(state=state, questions=questions, model=model)
            if circuit_breaker is not None:
                circuit_breaker.record_success()
            return res
        except Exception as e:
            last_error = e
            if circuit_breaker is not None:
                circuit_breaker.record_failure(e)
            err_lower = str(e).lower()
            if attempt < max_retries and any(kw in err_lower for kw in TRANSIENT_ERROR_MARKERS):
                time.sleep(calculate_jittered_backoff(attempt=attempt, base_backoff=0.4, retry_after=parse_retry_after(err_lower)))
            else:
                break
    raise last_error if last_error is not None else RuntimeError("system_one no llegó a invocarse")

class TypeSafeAdapter(BaseReasoningProvider):
    """Adaptador que implementa ReasoningProvider utilizando TypeSafe AI (System One)."""

    name: str = "typesafe"
    backend: str = "api"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "jev-v1",
        max_retries: int = 2,
        timeout: float = 10.0,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY")
        self.model_name = model_name
        self.max_retries = max_retries
        self.timeout = timeout
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self._client = None

        if self.api_key:
            self._init_sdk_client()

    def _init_sdk_client(self) -> None:
        """Inicializa TypeSafeClient desde typesafe-sdk."""
        self._client = create_sdk_client(self.api_key)

    def is_available(self) -> bool:
        """Verifica si el cliente y credenciales están disponibles para realizar inferencias."""
        return bool(self.api_key and self._client is not None)

    def evaluate(
        self,
        state: Any,
        actions: List[ActionCandidate],
    ) -> List[ProviderAssessment]:
        """Evalúa semánticamente una lista de acciones candidatas mediante TypeSafe System One."""
        if not actions:
            return []

        # 1. Verificación de disponibilidad de credenciales y CircuitBreaker
        if not self.is_available():
            return [
                ProviderAssessment(
                    provider="typesafe",
                    model=self.model_name,
                    available=False,
                    confidence=0.0,
                    failure_reason="Clave de API de TypeSafe no configurada o cliente no inicializado",
                    reason_codes=["API_KEY_MISSING"],
                )
                for _ in actions
            ]

        if not self.circuit_breaker.allow_request():
            return [
                ProviderAssessment(
                    provider="typesafe",
                    model=self.model_name,
                    available=False,
                    confidence=0.0,
                    failure_reason="Circuit breaker OPEN tras fallos reiterados del proveedor",
                    reason_codes=["CIRCUIT_BREAKER_OPEN"],
                )
                for _ in actions
            ]

        # 2. Formulación de preguntas tipadas para TypeSafe System One
        try:
            chunk_steps = [
                {
                    "index": idx,
                    "id": a.id,
                    "tool": a.tool_call.tool_name if a.tool_call else None,
                    "args": a.tool_call.arguments if a.tool_call else {},
                    "description": a.description,
                    "rationale": a.rationale,
                    "requires_evidence": a.requires_evidence,
                }
                for idx, a in enumerate(actions)
            ]

            divergence_criteria = {"none": "Todas las acciones son fundamentadas, seguras y constructivas"}
            for idx in range(len(actions)):
                divergence_criteria[f"step_{idx}"] = f"La acción candidata en el índice {idx} contiene alucinación, bucle o premisa inválida"

            eval_state = {
                "system_state": state if isinstance(state, dict) else {},
                "candidate_actions": chunk_steps,
            }

            questions = {
                "has_divergence_or_loop": Noul(
                    instructions=(
                        "¿Contiene alguna de las acciones candidatas alucinaciones (archivos inventados, datos no respaldados) "
                        "o bucles de acción repetitivos y estériles?"
                    )
                ),
                "flagged_index": Choice(
                    instructions="Identifica qué índice diverge o alucina primero (o 'none' si todas son válidas):",
                    criteria=divergence_criteria,
                ),
                "progress_score": Score(
                    instructions="Evalúa el progreso semántico estimado hacia el objetivo:",
                    criteria=[
                        "counterproductive",
                        "irrelevant",
                        "minor_progress",
                        "significant_progress",
                        "direct_solution",
                    ],
                ),
            }

            # 3. Invocación con reintentos exponenciales
            res = self._invoke_with_retry(eval_state, questions)

            # 4. Decodificación de resultados calibrados
            raw_prob = getattr(res.get("has_divergence_or_loop"), "prob", 0.15) if isinstance(res, dict) else 0.15
            flagged_choice = getattr(res.get("flagged_index"), "choice", "none") if isinstance(res, dict) else "none"
            raw_score = getattr(res.get("progress_score"), "score", 3.0) if isinstance(res, dict) else 3.0

            if isinstance(raw_score, (int, float)):
                progress_prob = max(0.0, min(1.0, float(raw_score) / 4.0 if float(raw_score) <= 4.0 else float(raw_score) / 10.0))
            elif str(raw_score) in ("significant_progress", "direct_solution"):
                progress_prob = 0.85
            elif str(raw_score) == "minor_progress":
                progress_prob = 0.50
            else:
                progress_prob = 0.20
            is_flagged = bool(flagged_choice != "none" or raw_prob > 0.60)

            assessments: List[ProviderAssessment] = []
            for idx, _act in enumerate(actions):
                is_this_step_flagged = (flagged_choice == f"step_{idx}") or (is_flagged and len(actions) == 1)
                loop_p = raw_prob if is_this_step_flagged else min(raw_prob, 0.10)
                grounded_p = 0.20 if is_this_step_flagged else max(0.10, 1.0 - raw_prob)

                reasons = []
                if is_this_step_flagged:
                    reasons.append("FLAGGED_BY_TYPESAFE_SYSTEM_ONE")
                else:
                    reasons.append("GROUNDED_PROGRESS")

                assessments.append(
                    ProviderAssessment(
                        provider="typesafe",
                        model=self.model_name,
                        available=True,
                        confidence=0.92,
                        loop_probability=round(loop_p, 3),
                        grounded_probability=round(grounded_p, 3),
                        progress_probability=round(progress_prob, 3),
                        novelty_probability=round(1.0 - loop_p, 3),
                        reason_codes=reasons,
                    )
                )

            return assessments

        except Exception as e:
            err_msg = str(e)
            logger.error(f"Error durante evaluación en TypeSafe AI: {err_msg}")
            reason_code = "PROVIDER_ERROR"
            lower_err = err_msg.lower()
            if "timeout" in lower_err:
                reason_code = "TIMEOUT"
            elif "429" in lower_err or "rate" in lower_err:
                reason_code = "RATE_LIMIT"
            elif "401" in lower_err or "auth" in lower_err or "key" in lower_err:
                reason_code = "AUTH_ERROR"

            return [
                ProviderAssessment(
                    provider="typesafe",
                    model=self.model_name,
                    available=False,
                    confidence=0.0,
                    failure_reason=err_msg,
                    reason_codes=[reason_code],
                )
                for _ in actions
            ]

    def _invoke_with_retry(self, state: Dict[str, Any], questions: Dict[str, Any]) -> Any:
        """Ejecuta system_one con reintentos para fallos transitorios y control de CircuitBreaker."""
        return invoke_system_one(self._client, state, questions, self.model_name, self.max_retries, self.circuit_breaker)


# Alias canónico
TypeSafeProvider = TypeSafeAdapter

__all__ = ["TypeSafeAdapter", "TypeSafeProvider", "create_sdk_client", "invoke_system_one"]
