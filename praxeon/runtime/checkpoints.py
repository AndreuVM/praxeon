"""Gestor de Checkpoints y Rollback formal para JEV Reasoning Navigator v0.2.

Permite restaurar el estado canónico anterior ante ramas degenerativas,
invalidando descendientes y prohibiendo transiciones fallidas.
"""

from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from praxeon.runtime.state import SessionState


class Checkpoint(BaseModel):
    """Snapshot inmutable de un estado recuperable."""
    model_config = ConfigDict(frozen=True)

    id: str
    step_index: int
    state_hash: str
    reason: str
    snapshot_data: Dict[str, Any]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))



class CheckpointManager:
    """Gestiona la creación, almacenamiento y restauración de checkpoints."""

    def __init__(self):
        self._checkpoints: Dict[str, Checkpoint] = {}
        self.rollback_history: List[Dict[str, Any]] = []

    def create_checkpoint(self, state: SessionState, reason: str = "Punto de restauración") -> Checkpoint:
        """Crea un snapshot inmutable del estado actual."""
        chk_id = f"chk_{len(self._checkpoints)}_{uuid.uuid4().hex[:6]}"
        checkpoint = Checkpoint(
            id=chk_id,
            step_index=len(state.steps),
            state_hash=state.compute_hash(),
            reason=reason,
            snapshot_data=state.to_snapshot(),
        )
        self._checkpoints[chk_id] = checkpoint
        state.checkpoint_ids.append(chk_id)
        return checkpoint

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """Recupera un checkpoint específico por ID."""
        return self._checkpoints.get(checkpoint_id)

    def get_latest_checkpoint(self) -> Optional[Checkpoint]:
        """Devuelve el checkpoint más reciente creado."""
        if not self._checkpoints:
            return None
        return list(self._checkpoints.values())[-1]

    def get_genesis_checkpoint(self) -> Optional[Checkpoint]:
        """Devuelve el checkpoint inicial (génesis) de la sesión."""
        if not self._checkpoints:
            return None
        return list(self._checkpoints.values())[0]

    def restore_checkpoint(
        self,
        checkpoint_id: str,
        current_state: SessionState,
        culprit_tool: Optional[str] = None,
        reason: str = "Restauración de checkpoint",
    ) -> SessionState:
        """Restaura el estado a partir de un checkpoint registrado."""
        return self.rollback(checkpoint_id, current_state, culprit_tool=culprit_tool, reason=reason)

    def rollback(
        self,
        checkpoint_id: str,
        current_state: SessionState,

        culprit_tool: Optional[str] = None,
        reason: str = "Backtracking por degradación de trayectoria",
    ) -> SessionState:
        """Restaura el estado al checkpoint indicado, invalidando descendientes y prohibiendo la transición fallida.

        Flujo formal:
        restore_checkpoint -> invalidate_descendants -> forbid_failed_transition -> replan
        """
        checkpoint = self._checkpoints.get(checkpoint_id)
        if not checkpoint:
            raise KeyError(f"Checkpoint '{checkpoint_id}' no encontrado en el almacén.")

        # 1. Reconstruir estado desde el snapshot
        restored_state = SessionState.from_snapshot(checkpoint.snapshot_data)

        # 2. Bloquear físicamente la herramienta causante del bucle para no repetir la transición
        if culprit_tool:
            restored_state.forbid_tool(culprit_tool)

        # 3. Registrar auditoría de rollback
        self.rollback_history.append({
            "timestamp": datetime.now(timezone.utc),
            "checkpoint_id": checkpoint_id,

            "target_step_index": checkpoint.step_index,
            "discarded_steps_count": len(current_state.steps) - checkpoint.step_index,
            "culprit_tool": culprit_tool,
            "reason": reason,
        })

        return restored_state

    def list_checkpoints(self) -> List[Checkpoint]:
        """Devuelve todos los checkpoints ordenados cronológicamente."""
        return list(self._checkpoints.values())
