"""Entidad inmutable Goal para la especificación formal de objetivos del agente."""

from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class CriterionType(str, Enum):
    """Tipos formales de criterios de éxito verificables."""
    FILE_EXISTS = "file_exists"          # El archivo debe existir físicamente en el workspace
    TESTS_PASS = "tests_pass"            # La suite de tests debe haber pasado con exit code 0
    EXIT_CODE_ZERO = "exit_code_zero"    # Un comando debe haber finalizado con código de salida 0
    STATE_VALUE = "state_value"          # Un valor específico debe constar en las evidencias o estado
    API_STATUS = "api_status"            # Un endpoint debe responder con estatus exitoso
    CUSTOM = "custom"                    # Criterio general fundamentado en evidencia empírica


class SuccessCriterion(BaseModel):
    """Criterio formal tipado de éxito con claim verificable."""
    model_config = ConfigDict(frozen=True)

    id: str
    description: str
    criterion_type: CriterionType = CriterionType.CUSTOM
    target: str | None = None         # Ej: ruta de archivo, nombre de test suite, comando
    expected_value: str | None = None # Ej: 'exit_code=0', 'passed', '200'
    mandatory: bool = True               # Si es obligatorio para autorizar finish


class Goal(BaseModel):
    """Objetivo formal del agente con criterios de éxito verificables."""
    model_config = ConfigDict(frozen=True)

    objective: str
    success_criteria: list[str] = Field(default_factory=list)
    criteria: list[SuccessCriterion] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    forbidden_outcomes: list[str] = Field(default_factory=list)
    budget: float | None = None
    deadline: datetime | None = None

    def get_all_criteria(self) -> list[SuccessCriterion]:
        """Devuelve todos los criterios formalizados, autocompletando con success_criteria si aplica."""
        all_crit = list(self.criteria)
        for i, sc in enumerate(self.success_criteria):
            if not any(c.description == sc for c in all_crit):
                all_crit.append(SuccessCriterion(id=f"crit_{i}", description=sc, criterion_type=CriterionType.CUSTOM))
        return all_crit
