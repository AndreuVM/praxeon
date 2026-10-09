"""Dominio formal e interfaces agnósticas de DecisionProvider (PRAXEON 1.1).

Especificación de independencia del modelo System-1:
- El protocolo DecisionProvider abstrae cualquier modelo de juicio semántico (LAYA, TypeSafe, KEV, Replay, Mock).
- Ningún SDK externo debe ser importado en esta capa del dominio.
- DecisionModelConfig desacopla la configuración del supervisor System-1 de la del LLM que razona.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Protocol, Set, runtime_checkable
from pydantic import BaseModel, ConfigDict, Field, field_validator

from praxeon.domain.assessment import ProviderAssessment
from praxeon.domain.models import ActionCandidate


class DecisionProviderError(Exception):
    """Excepción base para fallos en proveedores de decisión semántica."""
    pass


class DecisionProviderUnavailableError(DecisionProviderError):
    """Lanzada cuando un proveedor requerido no está instalado o no está operativo."""
    pass


class DecisionProviderTimeoutError(DecisionProviderError):
    """Lanzada cuando el proveedor excede la ventana de tiempo configurada."""
    pass


class DecisionModelConfig(BaseModel):
    """Configuración tipada del modelo supervisor System-1 (Sección 6 del informe de auditoría).

    Permite parametrizar de forma exhaustiva e independiente el proveedor, modelo, backend,
    dispositivo, política de fallback y timeouts sin acoplamiento a SDKs específicos.
    """
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    provider: str = Field(
        ...,
        description="Identificador del proveedor (ej. 'laya', 'typesafe', 'replay', 'mock', 'kev')",
    )
    model_id: str = Field(
        default="default",
        description="Identificador del modelo específico (ej. 'laya-v1', 'jev-v1', 'trace-01')",
    )
    model_version: Optional[str] = Field(
        default=None,
        description="Versión del modelo o pesos (ej. '0.3', '1.0.0')",
    )
    backend: Optional[str] = Field(
        default="local",
        description="Backend de ejecución (ej. 'local', 'api', 'onnx', 'torch')",
    )
    endpoint: Optional[str] = Field(
        default=None,
        description="URL o endpoint en caso de proveedores remotos",
    )
    allow_custom_endpoints: bool = Field(
        default=False,
        description="Permitir endpoints remotos personalizados fuera de la allowlist canónica (SEC-01)",
    )
    allowed_hosts: Set[str] = Field(
        default_factory=set,
        description="Allowlist explícita de dominios de endpoints autorizados",
    )

    @field_validator("endpoint")
    @classmethod
    def _validate_endpoint(cls, v: Optional[str], info: Any) -> Optional[str]:
        if not v:
            return None
        from praxeon.policy.egress import validate_provider_endpoint
        data = info.data if hasattr(info, "data") else {}
        allow_custom = bool(data.get("allow_custom_endpoints", False))
        allowed = data.get("allowed_hosts")
        profile = (os.environ.get("PRAXEON_PROFILE") or os.environ.get("PRAXEON_ENV") or "dev").lower().strip()
        return validate_provider_endpoint(
            url=v,
            allow_custom=allow_custom,
            profile=profile,
            allowed_hosts=allowed,
        )
    model_path: Optional[str] = Field(
        default=None,
        description="Ruta en sistema de archivos hacia pesos o checkpoint local",
    )
    device: Optional[str] = Field(
        default="auto",
        description="Dispositivo de aceleración (ej. 'cpu', 'cuda', 'mps', 'auto')",
    )
    timeout_seconds: float = Field(
        default=10.0,
        description="Tiempo límite de evaluación en segundos",
    )
    max_retries: int = Field(
        default=1,
        description="Número máximo de reintentos ante errores transitorios",
    )
    calibration_profile: Optional[str] = Field(
        default=None,
        description="Perfil de calibración de probabilidades o temperatura (ej. 'conservative', 'strict')",
    )
    fallback_policy: str = Field(
        default="none",
        description="Estrategia ante indisponibilidad ('none', 'mock', 'replay', o nombre de provider)",
    )
    fallback_provider: Optional[str] = Field(
        default=None,
        description="Proveedor alternativo explícito a utilizar si fallback_policy es activa",
    )
    custom_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parámetros arbitrarios específicos del proveedor o SDK subyacente",
    )


class DecisionProviderMetadata(BaseModel):
    """Metadatos estáticos y dinámicos del proveedor de decisión para auditoría y observabilidad."""
    model_config = ConfigDict(frozen=True)

    provider_id: str
    model_id: str
    model_version: Optional[str] = None
    backend: Optional[str] = None
    device: Optional[str] = None
    is_available: bool = True
    capabilities: List[str] = Field(default_factory=list)
    calibration_profile: Optional[str] = None
    extra_info: Dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class DecisionProvider(Protocol):
    """Protocolo formal y canónico para cualquier evaluador de decisión System-1 en PRAXEON.

    Garantiza que el runtime interactúe de manera agnóstica con cualquier motor de juicio semántico.
    """

    @property
    def provider_id(self) -> str:
        """Identificador canónico del proveedor (ej. 'laya', 'typesafe', 'mock')."""
        ...

    def evaluate(
        self,
        state: Any,
        actions: List[ActionCandidate],
    ) -> List[ProviderAssessment]:
        """Evalúa semánticamente una lista de acciones candidatas bajo el estado provisto."""
        ...

    def is_available(self) -> bool:
        """Indica si el proveedor está instalado, configurado y listo para inferir."""
        ...

    def metadata(self) -> DecisionProviderMetadata:
        """Devuelve los metadatos completos y reproducibles del proveedor y modelo activo."""
        ...
