"""Runtime aislado por sesión (praxeon/runtime/session_runtime.py).

Garantiza que cada sesión o misión mantenga su propio modelo de decisión (DecisionRuntime),
su propio LLM runtime, sus perfiles de política y su telemetría aislada,
eliminando toda referencia a providers globales mutables en el servicio central.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from praxeon.runtime.decision_runtime import DecisionRuntime


@dataclass
class SessionRuntime:
    """Encapsula los componentes de ejecución y supervisión exclusivos de una sesión."""
    session_id: str
    decision_provider: Any = None
    decision_runtime: Optional[DecisionRuntime] = None
    llm_runtime: Optional[Any] = None
    context_manager: Optional[Any] = None
    policy_profile: str = "default"
    execution_profile: str = "local_restricted"
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        # Sincronizar decision_runtime y decision_provider
        if self.decision_runtime is not None and self.decision_provider is None:
            self.decision_provider = self.decision_runtime.provider
        elif self.decision_provider is not None and self.decision_runtime is None:
            # Si sólo se pasó decision_provider, crear un DecisionRuntime envolvente
            from praxeon.domain.decision_provider import DecisionModelConfig
            prov_id = getattr(self.decision_provider, "provider_id", getattr(self.decision_provider, "name", "custom"))
            cfg = DecisionModelConfig(provider=prov_id, model_id="default")
            self.decision_runtime = DecisionRuntime(config=cfg, provider=self.decision_provider)

    def evaluate_step(self, state: Any, action: Any) -> Any:
        """Evalúa una acción con el provider o DecisionRuntime exclusivo de esta sesión."""
        if self.decision_runtime is not None:
            return self.decision_runtime.evaluate_step(state, action)
        return self.decision_provider.evaluate(state, [action])

    def evaluate_actions(self, state: Any, actions: Any) -> Any:
        """Evalúa un lote de acciones con el provider o DecisionRuntime exclusivo de esta sesión."""
        if self.decision_runtime is not None:
            return self.decision_runtime.evaluate(state, actions)
        return self.decision_provider.evaluate(state, actions)
