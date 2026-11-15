"""Contrato común de los pipelines de pasos de PRAXEON (auditoría A-01).

Un pipeline es una secuencia fija de pasos sobre un contexto mutable. Cada paso implementa ``Step.run(ctx) -> Outcome``:

- ``Outcome.CONTINUE``: el paso no cierra la pasada; sigue el siguiente paso.
- ``Outcome.END``: la pasada por el pipeline termina aquí (en un bucle, el turno; el siguiente empieza después).
- ``Outcome.STOP``: el proceso que repite el pipeline se detiene.

Lo usan el bucle del agente (``praxeon.server.runtime_service.agent_loop``), el pipeline de propuestas
(``praxeon.server.runtime_service.proposal_pipeline``) y la ejecución de nodos de workflow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import ClassVar, Generic, TypeVar
from collections.abc import Iterable

C = TypeVar("C")


class Outcome(Enum):
    """Qué hace el pipeline tras un paso."""

    CONTINUE = "continue"
    END = "end"
    STOP = "stop"


class Step(ABC, Generic[C]):
    """Un paso del pipeline: actúa sobre el contexto y dice si la pasada sigue, termina o el proceso se detiene."""

    name: ClassVar[str]

    @abstractmethod
    def run(self, ctx: C) -> Outcome:
        """Ejecuta el paso."""

    def __repr__(self) -> str:
        return f"<Step {self.name}>"


def run_steps(steps: Iterable[Step[C]], ctx: C) -> Outcome:
    """Ejecuta los pasos en orden hasta que uno cierra la pasada o detiene el proceso."""
    for step in steps:
        outcome = step.run(ctx)
        if outcome is not Outcome.CONTINUE:
            return outcome
    return Outcome.CONTINUE
