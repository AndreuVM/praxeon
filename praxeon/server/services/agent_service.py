from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from praxeon.agents.bus import AgentMessageBus
from praxeon.agents.definition import AgentDefinition, AgentStatus
from praxeon.agents.registry import AgentRegistry
from praxeon.agents.templates import AgentTemplateCatalog
from praxeon.routing.router import AgentRouter


class AgentService:
    """Gestiona el catálogo, enrutamiento, mensajería y evolución de agentes dinámicos."""

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        router: Optional[AgentRouter] = None,
        bus: Optional[AgentMessageBus] = None,
    ):
        self.registry = registry or AgentRegistry()
        self.bus = bus or AgentMessageBus()
        self.router = router or AgentRouter(registry=self.registry, message_bus=self.bus)

        # Sembrar plantillas canónicas si el catálogo está vacío
        if not self.registry.list_all():
            for tpl in ["developer", "security_auditor", "researcher", "writer", "code_reviewer"]:
                try:
                    self.registry.register(AgentTemplateCatalog.instantiate(tpl, f"ag_{tpl}"))
                except Exception:
                    pass

    def get_registry(self) -> AgentRegistry:
        return self.registry

    def get_router(self) -> AgentRouter:
        return self.router

    def get_bus(self) -> AgentMessageBus:
        return self.bus

    def register_agent(self, agent: AgentDefinition) -> AgentDefinition:
        return self.registry.register(agent)

    def get_agent(self, agent_id: str) -> Optional[AgentDefinition]:
        return self.registry.get(agent_id)

    def list_agents(
        self,
        status: Optional[AgentStatus] = None,
        role: Optional[str] = None,
    ) -> List[AgentDefinition]:
        return self.registry.list_all(status=status, role=role)

    def update_agent(self, agent_id: str, updates: Dict[str, Any]) -> AgentDefinition:
        return self.registry.update(agent_id, updates)

    def delete_agent(self, agent_id: str, hard_delete: bool = False) -> bool:
        return self.registry.delete(agent_id, hard_delete=hard_delete)

    def get_agent_history(self, agent_id: str) -> List[Dict[str, Any]]:
        """Recupera el historial inmutable de versiones del agente."""
        return self.registry.get_agent_history(agent_id)

    def get_agent_version(self, agent_id: str, version: int) -> Optional[AgentDefinition]:
        """Obtiene una versión histórica inmutable específica de un agente."""
        return self.registry.get_agent_version(agent_id, version)

    def evolve_agent(
        self,
        agent_id: str,
        feedback: str,
        new_capabilities: Optional[List[str]] = None,
        refined_prompt: Optional[str] = None,
        performance_score: Optional[float] = None,
    ) -> AgentDefinition:
        """Evoluciona un agente aplicando refinamiento de prompts, capacidades y métricas históricas."""
        current = self.get_agent(agent_id)
        if not current:
            raise KeyError(f"Agente con ID '{agent_id}' no encontrado.")

        updates: Dict[str, Any] = {}
        if refined_prompt:
            updates["system_prompt"] = refined_prompt.strip()

        if new_capabilities:
            caps = list(dict.fromkeys(current.capabilities + new_capabilities))
            updates["capabilities"] = caps

        # Registrar historial de evolución en metadata
        meta = dict(current.metadata or {})
        history = list(meta.get("evolution_history", []))
        history.append({
            "evolved_at": datetime.now(timezone.utc).isoformat(),
            "feedback": feedback,
            "performance_score": performance_score,
            "previous_version": current.version,
            "new_version": current.version + 1,
        })
        meta["evolution_history"] = history
        if performance_score is not None:
            meta["last_performance_score"] = performance_score
        updates["metadata"] = meta

        return self.update_agent(agent_id, updates)
