"""Control Adaptativo de Trayectorias (Trajectory Controller) (F8-03).

Supervisa en tiempo real la ejecución de trayectorias y emite directivas adaptativas:
- CONTINUE: Paso exitoso y progresivo; avanzar en la trayectoria.
- PRUNE: Detección de bucle o rama redundante/estéril; podar rama y excluir contexto.
- RECOVER: Error de ejecución o fallo de aserción recuperable; retroceso determinista a checkpoint.
- ESCALATE: Incertidumbre epistémica elevada o capacidad insuficiente; invocar DynamicEscalationEngine.
- SUSPEND_HITL: Violación de invariante de contención o superación de reintentos; freno humano.
- TERMINATE: Meta de la tarea completamente satisfecha y verificada.
"""

from typing import Any, Dict, List, Optional, Tuple

from praxeon.routing.escalation import DynamicEscalationEngine, EscalationTriggerType
from praxeon.routing.models import TaskRequirement
from praxeon.runtime.adaptive.models import TrajectoryDirective, TrajectoryStepRecord


class TrajectoryController:
    """Controlador y supervisor de trayectorias en vivo para el Adaptive Runtime."""

    def __init__(
        self,
        escalation_engine: Optional[DynamicEscalationEngine] = None,
        uncertainty_threshold: float = 0.70,
        max_consecutive_errors: int = 2,
    ):
        self.escalation_engine = escalation_engine
        self.uncertainty_threshold = uncertainty_threshold
        self.max_consecutive_errors = max_consecutive_errors

    def evaluate_step(
        self,
        step_record: TrajectoryStepRecord,
        history: List[TrajectoryStepRecord],
        task: TaskRequirement,
    ) -> Tuple[TrajectoryDirective, str]:
        """Evalúa el resultado de un paso ejecutado y determina la directiva adaptativa de control."""
        # 1. Verificar si la acción fue denegada por política de seguridad
        if step_record.policy_decision == "DENY":
            return (
                TrajectoryDirective.SUSPEND_HITL,
                f"Paso {step_record.step_index} bloqueado por política de seguridad: violación de contención."
            )

        obs = (step_record.observation or "").lower()

        # 2. Verificar condición de completitud exitosa
        completion_markers = ["task_completed", "goal_achieved", "mission_accomplished", "all tests passed"]
        if any(marker in obs for marker in completion_markers) or step_record.metadata.get("is_complete", False):
            return TrajectoryDirective.TERMINATE, "Meta de la tarea verificada y completada con éxito."

        # 3. Detección de bucles y estancamiento (Loop Detection)
        # Comprobar si las últimas 3 acciones son idénticas
        if len(history) >= 2:
            prev_steps = history[-2:] + [step_record]
            action_signatures = [
                f"{s.action_proposed.get('tool_name')}:{s.action_proposed.get('arguments')}"
                for s in prev_steps
            ]
            if len(set(action_signatures)) == 1 and action_signatures[0] != "None:None":
                return (
                    TrajectoryDirective.PRUNE,
                    f"Bucle de herramientas detectado en paso {step_record.step_index} "
                    f"({action_signatures[0]}). Se descarta rama estéril."
                )

        # 4. Detección de fallos de ejecución, sintaxis o aserción (Error Recovery)
        error_indicators = ["error:", "traceback", "exception:", "assertionerror", "failed with code"]
        has_error = any(ind in obs for ind in error_indicators)

        if has_error:
            # Contar errores consecutivos recientes
            consecutive_errs = 1
            for prev in reversed(history):
                prev_obs = (prev.observation or "").lower()
                if any(ind in prev_obs for ind in error_indicators):
                    consecutive_errs += 1
                else:
                    break

            if consecutive_errs >= self.max_consecutive_errors:
                # Si los errores persisten tras varios intentos -> ESCALATE
                return (
                    TrajectoryDirective.ESCALATE,
                    f"Errores de ejecución persistentes ({consecutive_errs} consecutivos). "
                    f"Se requiere escalado de modelo o rol especialista."
                )
            else:
                # Error puntual -> RECOVER mediante retroceso a checkpoint previo
                return (
                    TrajectoryDirective.RECOVER,
                    f"Error de ejecución en paso {step_record.step_index}. "
                    f"Activando retroceso determinista a checkpoint previo para replanificar."
                )

        # 5. Detección de incertidumbre epistémica elevada
        epistemic_uncertainty = step_record.metadata.get("uncertainty_score", 0.0)
        if epistemic_uncertainty >= self.uncertainty_threshold:
            return (
                TrajectoryDirective.ESCALATE,
                f"Incertidumbre epistémica excesiva ({epistemic_uncertainty:.2f} >= {self.uncertainty_threshold}). "
                f"Escalando hacia agente de mayor capacidad."
            )

        # 6. Ejecución nominal segura -> CONTINUE
        return TrajectoryDirective.CONTINUE, f"Paso {step_record.step_index} ejecutado nominalmente. Avanzando."
