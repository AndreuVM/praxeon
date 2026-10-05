"""Pruebas unitarias para el Clasificador Multidimensional de Tareas (Fase 7 - F7-02).

Valida:
- Inferencia de complejidad (TRIVIAL, LOW, MEDIUM, HIGH, CRITICAL).
- Inferencia de nivel de riesgo (LOW, MEDIUM, HIGH, CRITICAL).
- Extracción de capabilities y herramientas canónicas desde lenguaje natural.
- Estimación heurística de tokens y coste proyectado.
- Integración fluida con AgentRouter para enrutamiento end-to-end.
"""

import pytest

from praxeon.agents import AgentRegistry, AgentTemplateCatalog
from praxeon.domain.assessment import RiskLevel
from praxeon.routing import (
    AgentRouter,
    RoutingStrategyType,
    TaskClassifier,
    TaskComplexity,
    TaskRequirement,
)


@pytest.fixture
def classifier():
    return TaskClassifier()


def test_classify_trivial_read_task(classifier):
    """Valida la clasificación de consultas y solicitudes informativas triviales."""
    prompt = "What is the project name?"
    req = classifier.classify(prompt)

    assert req.complexity == TaskComplexity.TRIVIAL
    assert req.inferred_risk == RiskLevel.LOW
    assert req.prompt == prompt
    assert req.task_id.startswith("task_")
    assert req.metadata["classification"]["estimated_tokens"] > 0
    assert req.metadata["classification"]["estimated_cost"] > 0.0


def test_classify_critical_destructive_task(classifier):
    """Valida la detección de patrones críticos destructivos (DROP TABLE / rm -rf)."""
    prompt = "Execute maintenance script to drop database and rm -rf old cache"
    req = classifier.classify(prompt)

    assert req.inferred_risk == RiskLevel.CRITICAL
    assert any("crítico" in r.lower() for r in req.metadata["classification"]["risk_factors"])
    assert req.complexity in [TaskComplexity.HIGH, TaskComplexity.CRITICAL]


def test_classify_security_audit_task(classifier):
    """Valida la detección de riesgos de seguridad y extracción de capabilities de auditoría."""
    prompt = "Perform static security audit and check authentication middleware for token vulnerabilities"
    req = classifier.classify(prompt, target_files=["src/auth/jwt_service.py"])

    assert req.inferred_risk == RiskLevel.HIGH
    assert "security_audit" in req.required_capabilities
    assert req.complexity in [TaskComplexity.HIGH, TaskComplexity.CRITICAL]
    assert any("jwt_service.py" in str(r) or "seguridad" in str(r).lower() for r in req.metadata["classification"]["risk_factors"])


def test_classify_dev_and_testing_task(classifier):
    """Valida la inferencia de capabilities de desarrollo, testing y herramientas asociadas."""
    prompt = "Implement new user profile endpoint and write unit tests with pytest"
    req = classifier.classify(
        prompt=prompt,
        target_files=["src/routes/profile.py", "tests/test_profile.py"],
    )

    assert "code_authoring" in req.required_capabilities
    assert "testing" in req.required_capabilities
    assert "write_file" in req.required_tools
    assert "run_command" in req.required_tools
    assert req.complexity in [TaskComplexity.MEDIUM, TaskComplexity.HIGH]


def test_classify_cost_estimation_monotonicity(classifier):
    """Valida que la estimación de tokens y coste crezca proporcionalmente con archivos y longitud."""
    req_small = classifier.classify("Hello world")
    req_large = classifier.classify(
        prompt="Refactor entire distributed messaging layer, redesign state consensus protocol, " * 5,
        target_files=["file1.py", "file2.py", "file3.py", "file4.py"],
    )

    tokens_small = req_small.metadata["classification"]["estimated_tokens"]
    tokens_large = req_large.metadata["classification"]["estimated_tokens"]
    cost_small = req_small.metadata["classification"]["estimated_cost"]
    cost_large = req_large.metadata["classification"]["estimated_cost"]

    assert tokens_large > tokens_small
    assert cost_large > cost_small


def test_classify_and_route_integration(classifier):
    """Valida la integración fluida entre TaskClassifier y AgentRouter."""
    registry = AgentRegistry()
    registry.register(AgentTemplateCatalog.instantiate("developer", "ag_dev"))
    registry.register(AgentTemplateCatalog.instantiate("security_auditor", "ag_sec"))
    registry.register(AgentTemplateCatalog.instantiate("researcher", "ag_res"))

    router = AgentRouter(registry=registry)

    # 1. Tarea de investigación clasificada y enrutada
    research_prompt = "Search academic literature and arxiv papers on LLM self-reflection"
    task_res = classifier.classify(research_prompt)
    assert "literature_search" in task_res.required_capabilities

    dec_res = router.route(task_res, strategy=RoutingStrategyType.RULE_BASED)
    assert dec_res.selected_agent_id == "ag_res"
    assert dec_res.confidence >= 0.7

    # 2. Tarea de seguridad clasificada y enrutada
    sec_prompt = "Audit authorization bypass vulnerability in admin handler"
    task_sec = classifier.classify(sec_prompt)
    assert "security_audit" in task_sec.required_capabilities

    dec_sec = router.route(task_sec, strategy=RoutingStrategyType.RULE_BASED)
    assert dec_sec.selected_agent_id == "ag_sec"
