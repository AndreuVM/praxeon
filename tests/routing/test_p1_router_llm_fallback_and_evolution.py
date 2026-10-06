"""Pruebas de Certificación para Fallback a LLM en Enrutador y Evolución de Agentes (DEUDA-ROUT-01 y DEUDA-EV-01).

Verifica:
1. AgentRouter dispara automáticamente fallback a LLM / provider semántico ante baja confianza heurística (< 0.65).
2. Se genera un rationale explicativo y se emite evento de auditoría en el event_bus.
3. AgentService.evolve_agent aplica refinamiento de prompt y capacidades, incrementando versión inmutable y registrando métricas.
4. Consulta de versiones históricas en SQLite.
"""

from datetime import datetime, timezone
import pytest

from praxeon.agents.definition import AgentDefinition, AgentStatus, ModelConfig
from praxeon.agents.registry import AgentRegistry
from praxeon.agents.templates import AgentTemplateCatalog
from praxeon.domain.assessment import RiskLevel
from praxeon.domain.events import EventType
from praxeon.domain.models import ActionCandidate, ProviderAssessment
from praxeon.persistence.sqlite_store import SqlitePersistenceStore
from praxeon.providers.base import BaseReasoningProvider
from praxeon.routing.models import (
    RoutingDecision,
    RoutingStrategyType,
    TaskComplexity,
    TaskRequirement,
)
from praxeon.routing.router import AgentRouter
from praxeon.runtime.event_bus import EventBus
from praxeon.server.services.agent_service import AgentService


class MockLLMRoutingProvider(BaseReasoningProvider):
    """Proveedor mock para simular respuestas de inferencia LLM en enrutamiento."""
    name: str = "mock_gemini_routing"

    def __init__(self, preferred_agent_id: str = "ag_security_auditor", confidence: float = 0.92):
        self.preferred_agent_id = preferred_agent_id
        self.confidence_score = confidence

    def evaluate(self, state, actions):
        results = []
        for a in actions:
            ag_id = a.tool_call.arguments.get("agent_id")
            if ag_id == self.preferred_agent_id:
                results.append(
                    ProviderAssessment(
                        provider=self.name,
                        available=True,
                        confidence=self.confidence_score,
                        metadata={"rationale": f"Evaluación LLM: El agente '{ag_id}' posee la especialización semántica óptima."},
                    )
                )
            else:
                results.append(
                    ProviderAssessment(
                        provider=self.name,
                        available=True,
                        confidence=0.45,
                        metadata={"rationale": "Afinidad menor según el análisis contextual del modelo."},
                    )
                )
        return results


def test_agent_router_llm_fallback_on_low_confidence(tmp_path):
    """DEUDA-ROUT-01: AgentRouter activa fallback a LLM ante ambigüedad y baja confianza."""
    db_file = str(tmp_path / "router_fallback.db")
    store = SqlitePersistenceStore(db_path=db_file)
    registry = AgentRegistry(store=store)

    # Registrar agentes
    dev = AgentTemplateCatalog.instantiate("developer", "ag_dev")
    sec = AgentTemplateCatalog.instantiate("security_auditor", "ag_sec")
    registry.register(dev)
    registry.register(sec)

    bus = EventBus()
    emitted_events = []
    bus.subscribe(lambda evt: emitted_events.append(evt))

    llm = MockLLMRoutingProvider(preferred_agent_id="ag_sec", confidence=0.88)

    router = AgentRouter(
        registry=registry,
        llm_provider=llm,
        event_bus=bus,
    )

    # Tarea ambigua sin keywords claras ni capabilities específicas -> confianza de regla baja (< 0.65)
    task = TaskRequirement(
        task_id="task_ambiguous_01",
        prompt="Revisar minuciosamente los aspectos críticos y validar que todo esté en orden.",
        complexity=TaskComplexity.MEDIUM,
        required_capabilities=[],
        inferred_risk=RiskLevel.MEDIUM,
    )

    decision = router.route(task)

    # Debe haberse activado el fallback a LLM
    assert decision.selected_agent_id == "ag_sec"
    assert decision.confidence >= 0.80
    assert decision.metadata.get("llm_fallback_triggered") is True
    assert "LLM FALLBACK" in decision.rationale
    assert decision.metadata.get("provider") == "mock_gemini_routing"

    # Verificar emisión del evento de intervención
    fallback_events = [e for e in emitted_events if e.type == EventType.INTERVENTION_APPLIED]
    assert len(fallback_events) >= 1
    assert fallback_events[0].payload.get("intervention_type") == "routing_llm_fallback"
    assert fallback_events[0].payload.get("selected_agent_id") == "ag_sec"


def test_agent_service_evolution_and_versioning(tmp_path):
    """DEUDA-EV-01: AgentService evoluciona agentes, actualiza prompts y versiona en SQLite."""
    db_file = str(tmp_path / "agent_evolution.db")
    store = SqlitePersistenceStore(db_path=db_file)
    registry = AgentRegistry(store=store)
    service = AgentService(registry=registry)

    # Registrar agente inicial
    agent = AgentDefinition(
        agent_id="ag_evolve_01",
        name="Initial Developer",
        role="Developer",
        system_prompt="Eres un programador junior.",
        capabilities=["code_editing"],
        skills=["python"],
    )
    service.register_agent(agent)
    assert agent.version == 1

    # Evolucionar agente a partir de métricas de sesión
    evolved = service.evolve_agent(
        agent_id="ag_evolve_01",
        feedback="El agente requiere capacidades de testing avanzado y mejor directiva de código.",
        new_capabilities=["unit_testing", "code_review"],
        refined_prompt="Eres un ingeniero de software senior enfocado en calidad y tests.",
        performance_score=0.95,
    )

    assert evolved.version == 2
    assert "unit_testing" in evolved.capabilities
    assert "code_editing" in evolved.capabilities
    assert "ingeniero de software senior" in evolved.system_prompt
    assert evolved.metadata["last_performance_score"] == 0.95
    assert len(evolved.metadata["evolution_history"]) == 1

    # Comprobar recuperación histórica desde el registro y persistencia SQLite
    history = service.get_agent_history("ag_evolve_01")
    assert len(history) == 2  # v1 y v2

    v1_rec = service.get_agent_version("ag_evolve_01", 1)
    assert v1_rec is not None
    assert v1_rec.version == 1
    assert "programador junior" in v1_rec.system_prompt

    v2_rec = service.get_agent_version("ag_evolve_01", 2)
    assert v2_rec is not None
    assert v2_rec.version == 2
    assert "ingeniero de software senior" in v2_rec.system_prompt
