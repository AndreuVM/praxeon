"""Configuraciones desacopladas y parametrización modular del runtime JEV Reasoning Navigator.

Implementa los requisitos de la Sección 23 de la Auditoría Técnica:
- Segregación modular en sub-configuraciones Pydantic especializadas
- Sin efectos secundarios en la importación de la biblioteca (load_dotenv() opcional y explícito)
- Carga declarativa con PraxeonConfig.from_env()
- Compatibilidad retroactiva total con la interfaz existente
"""

import os
import warnings
from typing import Any, List, Literal, Optional, Set
from pydantic import BaseModel, Field, model_validator


class ProviderConfig(BaseModel):
    """Configuración del proveedor de inferencia semántica (TypeSafe / LAYA / Replay)."""
    name: str = Field(default="typesafe", description="Identificador del proveedor supervisor ('typesafe', 'jev', 'laya')")
    api_key: Optional[str] = Field(default=None, description="Clave de API para TypeSafe AI")
    use_api: bool = Field(default=False, description="Activar invocación real del proveedor")
    model: str = Field(default="jev-latest", description="Identificador del modelo de inferencia")
    base_url: Optional[str] = Field(default=None, description="URL base alternativa para el proveedor")
    laya_backend: str = Field(default="auto", description="Backend para LAYA (auto, local, simulated, hosted)")
    llm_failure_policy: Literal["fail_closed", "synthetic_fallback"] = Field(
        default="synthetic_fallback",
        description="Política ante fallos críticos del LLM: 'fail_closed' o 'synthetic_fallback'",
    )


class DecisionConfig(BaseModel):
    """Parámetros y umbrales para el motor de políticas de decisión."""
    critical_jev_threshold: float = Field(default=0.0, description="Umbral crítico de degradación JEV")
    min_grounded_threshold: float = Field(default=0.35, description="Probabilidad mínima de fundamentación")
    loop_threshold: float = Field(default=0.65, description="Umbral de probabilidad para clasificar bucle")


class ChunkConfig(BaseModel):
    """Supervisión por bloques (chunking) y control de invocaciones."""
    chunk_size: int = Field(default=3, description="Tamaño por defecto de bloque de candidatos")
    enabled: bool = Field(default=True, description="Habilitar evaluación agrupada de candidatos")
    hallucination_detection: bool = Field(default=True, description="Supervisión activa de groundedness")
    call_llm_only_on_intervention_or_chunk_end: bool = Field(
        default=True,
        description="Invocar al modelo de generación solo ante intervención o fin de bloque",
    )


class RiskConfig(BaseModel):
    """Políticas de categorización y contención de riesgo operacional."""
    protected_files: Set[str] = Field(
        default_factory=lambda: {".env", "id_rsa", "id_ed25519", "credentials.json", ".git"},
        description="Archivos sensibles protegidos contra mutaciones no confirmadas",
    )
    destructive_commands: List[str] = Field(
        default_factory=lambda: ["rm -rf", "del /f", "drop table", "mkfs", "dd if="],
        description="Patrones de comandos shell intrínsecamente destructivos",
    )
    require_confirmation_for_external: bool = Field(
        default=True,
        description="Exigir confirmación para herramientas con efectos colaterales externos",
    )


class RetryConfig(BaseModel):
    """Configuración de reintentos, Circuit Breaker y presupuestos de tiempo."""
    max_retries: int = Field(default=2, description="Número máximo de reintentos transitorios")
    timeout_seconds: float = Field(default=10.0, description="Timeout global por petición de evaluación")
    circuit_breaker_failures: int = Field(default=3, description="Fallos técnicos consecutivos para abrir el circuito técnico")
    semantic_loop_threshold: int = Field(default=2, description="Repeticiones semánticas consecutivas para abrir circuito semántico y podar rama")
    circuit_recovery_seconds: float = Field(default=30.0, description="Tiempo de enfriamiento antes de probar recuperación")


class SecurityConfig(BaseModel):
    """Políticas de privacidad, enmascaramiento de secretos y delimitación de confianza."""
    redact_secrets: bool = Field(default=True, description="Enmascarar tokens, credenciales y API keys")
    redact_pii: bool = Field(default=True, description="Enmascarar emails y direcciones IP sensibles")
    max_payload_bytes: int = Field(default=100_000, description="Límite máximo de bytes por payload de herramienta")
    max_history_steps: int = Field(default=50, description="Número máximo de pasos retenidos en contexto")


class TelemetryConfig(BaseModel):
    """Configuración de observabilidad y registro estructurado."""
    enabled: bool = Field(default=True, description="Habilitar emisión de eventos y telemetría")
    log_to_file: bool = Field(default=False, description="Persistir eventos en archivo JSONL")
    log_file_path: Optional[str] = Field(default=None, description="Ruta para el archivo de logs de eventos")


class PraxeonConfig(BaseModel):
    """Configuración global compuesta para PRAXEON Runtime Platform."""

    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    decision: DecisionConfig = Field(default_factory=DecisionConfig)
    chunk: ChunkConfig = Field(default_factory=ChunkConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            provider_data = dict(data.get("provider", {}) if isinstance(data.get("provider"), dict) else (data.get("provider").model_dump() if isinstance(data.get("provider"), BaseModel) else {}))
            decision_data = dict(data.get("decision", {}) if isinstance(data.get("decision"), dict) else (data.get("decision").model_dump() if isinstance(data.get("decision"), BaseModel) else {}))
            chunk_data = dict(data.get("chunk", {}) if isinstance(data.get("chunk"), dict) else (data.get("chunk").model_dump() if isinstance(data.get("chunk"), BaseModel) else {}))
            retry_data = dict(data.get("retry", {}) if isinstance(data.get("retry"), dict) else (data.get("retry").model_dump() if isinstance(data.get("retry"), BaseModel) else {}))
            security_data = dict(data.get("security", {}) if isinstance(data.get("security"), dict) else (data.get("security").model_dump() if isinstance(data.get("security"), BaseModel) else {}))

            if "typesafe_api_key" in data:
                provider_data["api_key"] = data.pop("typesafe_api_key")
            if "use_typesafe_api" in data:
                provider_data["use_api"] = data.pop("use_typesafe_api")
            if "typesafe_model" in data:
                provider_data["model"] = data.pop("typesafe_model")

            if "critical_jev_threshold" in data:
                decision_data["critical_jev_threshold"] = data.pop("critical_jev_threshold")

            if "evaluation_chunk_size" in data:
                chunk_data["chunk_size"] = data.pop("evaluation_chunk_size")
            if "enable_chunk_evaluation" in data:
                chunk_data["enabled"] = data.pop("enable_chunk_evaluation")
            if "hallucination_detection" in data:
                chunk_data["hallucination_detection"] = data.pop("hallucination_detection")
            if "call_llm_only_on_intervention_or_chunk_end" in data:
                chunk_data["call_llm_only_on_intervention_or_chunk_end"] = data.pop("call_llm_only_on_intervention_or_chunk_end")

            if "max_history_steps" in data:
                security_data["max_history_steps"] = data.pop("max_history_steps")

            if "max_retries" in data:
                retry_data["max_retries"] = data.pop("max_retries")
            if "timeout_seconds" in data:
                retry_data["timeout_seconds"] = data.pop("timeout_seconds")

            data["provider"] = provider_data
            data["decision"] = decision_data
            data["chunk"] = chunk_data
            data["retry"] = retry_data
            data["security"] = security_data
        return data

    # --- Propiedades de conveniencia y compatibilidad con versiones previas ---
    @property
    def typesafe_api_key(self) -> Optional[str]:
        return self.provider.api_key

    @typesafe_api_key.setter
    def typesafe_api_key(self, value: Optional[str]) -> None:
        self.provider.api_key = value

    @property
    def use_typesafe_api(self) -> bool:
        return self.provider.use_api

    @use_typesafe_api.setter
    def use_typesafe_api(self, value: bool) -> None:
        self.provider.use_api = value

    @property
    def typesafe_model(self) -> str:
        return self.provider.model

    @typesafe_model.setter
    def typesafe_model(self, value: str) -> None:
        self.provider.model = value

    @property
    def critical_jev_threshold(self) -> float:
        return self.decision.critical_jev_threshold

    @critical_jev_threshold.setter
    def critical_jev_threshold(self, value: float) -> None:
        self.decision.critical_jev_threshold = value

    @property
    def max_history_steps(self) -> int:
        return self.security.max_history_steps

    @max_history_steps.setter
    def max_history_steps(self, value: int) -> None:
        self.security.max_history_steps = value

    @property
    def evaluation_chunk_size(self) -> int:
        return self.chunk.chunk_size

    @evaluation_chunk_size.setter
    def evaluation_chunk_size(self, value: int) -> None:
        self.chunk.chunk_size = value

    @property
    def enable_chunk_evaluation(self) -> bool:
        return self.chunk.enabled

    @enable_chunk_evaluation.setter
    def enable_chunk_evaluation(self, value: bool) -> None:
        self.chunk.enabled = value

    @property
    def hallucination_detection(self) -> bool:
        return self.chunk.hallucination_detection

    @hallucination_detection.setter
    def hallucination_detection(self, value: bool) -> None:
        self.chunk.hallucination_detection = value

    @property
    def call_llm_only_on_intervention_or_chunk_end(self) -> bool:
        return self.chunk.call_llm_only_on_intervention_or_chunk_end

    @call_llm_only_on_intervention_or_chunk_end.setter
    def call_llm_only_on_intervention_or_chunk_end(self, value: bool) -> None:
        self.chunk.call_llm_only_on_intervention_or_chunk_end = value

    @property
    def supervisor(self) -> str:
        return self.provider.name

    @supervisor.setter
    def supervisor(self, value: str) -> None:
        self.provider.name = value

    @property
    def laya_backend(self) -> str:
        return self.provider.laya_backend

    @laya_backend.setter
    def laya_backend(self, value: str) -> None:
        self.provider.laya_backend = value

    @classmethod
    def from_env(cls, load_env_file: bool = False) -> "PraxeonConfig":
        """Instancia la configuración leyendo variables de entorno de forma explícita."""
        if load_env_file:
            try:
                from dotenv import load_dotenv
                load_dotenv()
            except ImportError:
                pass

        supervisor_name = os.getenv("PRAXEON_SUPERVISOR", os.getenv("SUPERVISOR_PROVIDER", "typesafe")).lower()
        api_key = os.getenv("TYPESAFE_API_KEY")
        model = os.getenv("TYPESAFE_MODEL", "jev-latest")
        laya_backend = os.getenv("LAYA_BACKEND", "auto")

        if supervisor_name in ("laya", "laya-system1", "laya-v1"):
            model = os.getenv("LAYA_MODEL", "laya-v1-calibrated")

        use_api = bool(api_key and os.getenv("USE_TYPESAFE_API", "true").lower() in ("true", "1", "yes"))

        return cls(
            provider=ProviderConfig(
                name=supervisor_name,
                api_key=api_key,
                use_api=use_api,
                model=model,
                laya_backend=laya_backend,
            ),
            decision=DecisionConfig(
                critical_jev_threshold=float(os.getenv("JEV_CRITICAL_THRESHOLD", "0.0")),
            ),
            chunk=ChunkConfig(
                chunk_size=int(os.getenv("JEV_CHUNK_SIZE", "3")),
            ),
            retry=RetryConfig(
                max_retries=int(os.getenv("JEV_MAX_RETRIES", "2")),
                timeout_seconds=float(os.getenv("JEV_TIMEOUT_SECONDS", "10.0")),
            ),
            security=SecurityConfig(
                max_history_steps=int(os.getenv("JEV_MAX_HISTORY_STEPS", "50")),
            ),
        )


class JEVConfig(PraxeonConfig):
    """Alias retrocompatible con advertencia de obsolescencia (DeprecationWarning)."""

    def __init__(self, **data: Any):
        warnings.warn(
            "JEVConfig está en desuso a partir de PRAXEON v1.0. Utilice PraxeonConfig.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(**data)


# Instancia canónica por defecto cargando entorno local
default_config = PraxeonConfig.from_env(load_env_file=True)
