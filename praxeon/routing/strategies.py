"""Estrategias Híbridas de Enrutamiento de Agentes (F7-03).

Implementa las estrategias canónicas de selección multicriterio:
1. CostAwareRoutingStrategy: Minimización de consumo de tokens y costes de inferencia
   sin sacrificar el umbral mínimo de capacidades ni tolerancias de riesgo.
2. SemanticRoutingStrategy: Máxima correspondencia semántica entre directiva, skills,
   descripción de rol y system prompt de los agentes.
3. AdaptiveRoutingStrategy: Ponderación multicriterio balanceada (semántica 45%,
   coste 30%, cobertura de capacidades 25%).
"""

from datetime import datetime, timezone
import math
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from praxeon.agents.definition import AgentDefinition
from praxeon.domain.assessment import RiskLevel
from praxeon.routing.models import (
    RoutingDecision,
    RoutingStrategyType,
    TaskComplexity,
    TaskRequirement,
)


class CostAwareRoutingStrategy:
    """Estrategia de ruteo orientada al coste de inferencia y optimización presupuestaria."""

    # Tarifas estimadas por millón de tokens (promedio blended entrada/salida)
    MODEL_RATES_PER_1M: Dict[str, float] = {
        "flash": 0.25,        # e.g., gemini-1.5-flash (~$0.00000025/token)
        "haiku": 0.25,        # e.g., claude-3-haiku
        "-mini": 0.35,        # e.g., gpt-4o-mini
        "_mini": 0.35,
        "pro": 2.50,          # e.g., gemini-1.5-pro (~$0.0000025/token)
        "sonnet": 3.00,       # e.g., claude-3.5-sonnet
        "ultra": 10.00,       # e.g., gemini-ultra / opus
        "opus": 15.00,
        "default": 1.00,
    }

    @classmethod
    def estimate_agent_task_cost(cls, agent: AgentDefinition, estimated_tokens: int) -> float:
        """Calcula el coste proyectado en USD para ejecutar la tarea con un agente dado."""
        model_name = agent.model.model_name.lower()
        rate = cls.MODEL_RATES_PER_1M["default"]
        for key, val in cls.MODEL_RATES_PER_1M.items():
            if key in model_name:
                rate = val
                break

        cost_per_token = rate / 1_000_000.0
        return round(estimated_tokens * cost_per_token, 6)

    def route(self, task: TaskRequirement, eligible: List[AgentDefinition]) -> RoutingDecision:
        """Selecciona el agente más económico que satisface los requerimientos técnicos y de calidad."""
        if not eligible:
            raise ValueError(f"No hay agentes elegibles para la tarea '{task.task_id}'.")

        tokens = task.metadata.get("classification", {}).get("estimated_tokens", 1500)
        scored_candidates: List[Tuple[float, float, str, AgentDefinition]] = []

        for ag in eligible:
            cost = self.estimate_agent_task_cost(ag, tokens)
            model_name = ag.model.model_name.lower()

            # Identificar modelos ligeros sin falso positivo con 'gemini'
            is_lightweight = any(
                lt in model_name for lt in ["flash", "haiku", "-mini", "_mini", "small"]
            )

            # Para tareas CRITICAL o HIGH, penalizar modelos excesivamente ligeros si hay modelos Pro/Ultra disponibles
            quality_penalty = 0.0
            if task.complexity in [TaskComplexity.HIGH, TaskComplexity.CRITICAL]:
                if is_lightweight:
                    quality_penalty = 100.0

            # Penalización severa si supera el presupuesto máximo asignado
            budget_penalty = 0.0
            if task.max_acceptable_cost is not None and cost > task.max_acceptable_cost:
                budget_penalty = 50.0

            # Desempate suave por afinidad de rol/capabilities para agentes con igual coste
            affinity_bonus = 0.0
            matched_caps = [c for c in task.required_capabilities if c in ag.capabilities]
            affinity_bonus += len(matched_caps) * 0.00001
            role_terms = ag.role.lower().split()
            if any(term in task.prompt.lower() for term in role_terms):
                affinity_bonus += 0.00005

            effective_score = cost + quality_penalty + budget_penalty - affinity_bonus
            scored_candidates.append((effective_score, cost, ag.agent_id, ag))

        # Ordenar ascendentemente por score efectivo
        scored_candidates.sort(key=lambda item: item[0])
        best_effective, best_cost, _, best_agent = scored_candidates[0]

        alts = [a.agent_id for _, _, _, a in scored_candidates[1:4]]
        matched_caps = [c for c in task.required_capabilities if c in best_agent.capabilities]

        return RoutingDecision(
            decision_id=f"rd_cost_{uuid.uuid4().hex[:12]}",
            task_id=task.task_id,
            selected_agent_id=best_agent.agent_id,
            confidence=0.88,
            strategy_used=RoutingStrategyType.COST_AWARE,
            alternative_agent_ids=alts,
            rationale=(
                f"Optimización de coste: seleccionado '{best_agent.name}' ({best_agent.model.model_name}) "
                f"con coste proyectado de ${best_cost:.6f} para {tokens} tokens."
            ),
            matched_capabilities=matched_caps,
            estimated_cost=best_cost,
        )


class SemanticRoutingStrategy:
    """Estrategia de ruteo por afinidad semántica y especialización técnica."""

    @staticmethod
    def _tokenize(text: str) -> Set[str]:
        """Tokeniza y normaliza un texto a un conjunto de términos limpios."""
        cleaned = re.sub(r"[^\w\s-]", " ", text.lower())
        tokens = {t.strip() for t in cleaned.split() if len(t.strip()) > 2}
        return tokens

    @classmethod
    def compute_affinity(cls, task: TaskRequirement, agent: AgentDefinition) -> Tuple[float, List[str]]:
        """Calcula el score de afinidad semántica y las coincidencias clave."""
        prompt_tokens = cls._tokenize(task.prompt)
        matches: List[str] = []
        score = 0.0

        # 1. Correspondencia con Rol (peso 3.5)
        role_tokens = cls._tokenize(agent.role)
        role_hits = prompt_tokens.intersection(role_tokens)
        if role_hits:
            score += len(role_hits) * 3.5
            matches.append(f"Rol: {','.join(role_hits)}")

        # 2. Correspondencia con Skills específicas (peso 3.0)
        for skill in agent.skills:
            skill_tokens = cls._tokenize(skill.replace("-", " "))
            if skill_tokens.intersection(prompt_tokens):
                score += 3.0
                matches.append(f"Skill: {skill}")

        # 3. Correspondencia con Capabilities (peso 2.5)
        for cap in agent.capabilities:
            cap_tokens = cls._tokenize(cap.replace("_", " "))
            if cap_tokens.intersection(prompt_tokens):
                score += 2.5
                matches.append(f"Cap: {cap}")

        # 4. Correspondencia con Descripción de perfil (peso 1.5)
        desc_tokens = cls._tokenize(agent.description)
        desc_hits = prompt_tokens.intersection(desc_tokens)
        if desc_hits:
            score += min(len(desc_hits) * 1.5, 4.5)
            matches.append(f"Desc: {len(desc_hits)} matches")

        # 5. Correspondencia con System Prompt (peso 0.5)
        sys_tokens = cls._tokenize(agent.system_prompt)
        sys_hits = prompt_tokens.intersection(sys_tokens)
        if sys_hits:
            score += min(len(sys_hits) * 0.5, 3.0)

        return score, matches

    def route(self, task: TaskRequirement, eligible: List[AgentDefinition]) -> RoutingDecision:
        """Selecciona el agente con mayor afinidad semántica hacia la directiva."""
        if not eligible:
            raise ValueError(f"No hay agentes elegibles para la tarea '{task.task_id}'.")

        scored: List[Tuple[float, List[str], AgentDefinition]] = []
        for ag in eligible:
            aff_score, matches = self.compute_affinity(task, ag)
            scored.append((aff_score, matches, ag))

        # Ordenar descendentemente por afinidad semántica
        scored.sort(key=lambda item: item[0], reverse=True)
        top_score, top_matches, top_agent = scored[0]

        # Normalizar confianza semántica [0.65, 0.98]
        norm_conf = min(0.98, max(0.65, round(top_score / (top_score + 4.0), 3)))
        alts = [a.agent_id for _, _, a in scored[1:4]]
        matched_caps = [c for c in task.required_capabilities if c in top_agent.capabilities]

        return RoutingDecision(
            decision_id=f"rd_sem_{uuid.uuid4().hex[:12]}",
            task_id=task.task_id,
            selected_agent_id=top_agent.agent_id,
            confidence=norm_conf,
            strategy_used=RoutingStrategyType.SEMANTIC,
            alternative_agent_ids=alts,
            rationale=(
                f"Máxima afinidad semántica ({top_score:.1f}) con '{top_agent.name}': "
                f"[{'; '.join(top_matches[:3])}]."
            ),
            matched_capabilities=matched_caps,
        )


class AdaptiveRoutingStrategy:
    """Estrategia de ruteo adaptativa y multicriterio (Semántica, Coste y Capacidades)."""

    def __init__(
        self,
        weight_semantic: float = 0.45,
        weight_cost: float = 0.30,
        weight_capabilities: float = 0.25,
    ):
        total = weight_semantic + weight_cost + weight_capabilities
        self.w_sem = weight_semantic / total
        self.w_cost = weight_cost / total
        self.w_cap = weight_capabilities / total

    def route(self, task: TaskRequirement, eligible: List[AgentDefinition]) -> RoutingDecision:
        """Pondera armónicamente afinidad semántica, contención de costes y cobertura técnica."""
        if not eligible:
            raise ValueError(f"No hay agentes elegibles para la tarea '{task.task_id}'.")

        tokens = task.metadata.get("classification", {}).get("estimated_tokens", 1500)
        evaluated: List[Dict[str, Any]] = []

        # 1. Obtener métricas primarias de cada agente
        for ag in eligible:
            sem_score, _ = SemanticRoutingStrategy.compute_affinity(task, ag)
            cost = CostAwareRoutingStrategy.estimate_agent_task_cost(ag, tokens)

            req_caps = set(task.required_capabilities)
            ag_caps = set(ag.capabilities)
            cap_ratio = (len(req_caps.intersection(ag_caps)) / len(req_caps)) if req_caps else 1.0

            evaluated.append({
                "agent": ag,
                "sem_raw": sem_score,
                "cost_raw": cost,
                "cap_ratio": cap_ratio,
            })

        # 2. Normalizar métricas al rango [0.0, 1.0]
        max_sem = max((e["sem_raw"] for e in evaluated), default=1.0)
        min_cost = min((e["cost_raw"] for e in evaluated), default=0.0001)
        max_cost = max((e["cost_raw"] for e in evaluated), default=0.001)

        for e in evaluated:
            e["sem_norm"] = (e["sem_raw"] / max_sem) if max_sem > 0 else 0.5
            # Coste inverso (a menor coste, mayor score de eficiencia)
            cost_range = max_cost - min_cost
            if cost_range > 1e-9:
                e["cost_norm"] = 1.0 - ((e["cost_raw"] - min_cost) / cost_range)
            else:
                e["cost_norm"] = 1.0

            # Utilidad combinada
            e["utility"] = (
                (self.w_sem * e["sem_norm"]) +
                (self.w_cost * e["cost_norm"]) +
                (self.w_cap * e["cap_ratio"])
            )

        # 3. Ordenar descendentemente por utilidad combinada
        evaluated.sort(key=lambda item: item["utility"], reverse=True)
        top = evaluated[0]
        top_agent: AgentDefinition = top["agent"]

        alts = [e["agent"].agent_id for e in evaluated[1:4]]
        matched_caps = [c for c in task.required_capabilities if c in top_agent.capabilities]

        conf = min(0.99, max(0.68, round(top["utility"], 3)))

        return RoutingDecision(
            decision_id=f"rd_adapt_{uuid.uuid4().hex[:12]}",
            task_id=task.task_id,
            selected_agent_id=top_agent.agent_id,
            confidence=conf,
            strategy_used=RoutingStrategyType.ADAPTIVE,
            alternative_agent_ids=alts,
            rationale=(
                f"[EXPERIMENTAL] Selección adaptativa balanceada (Utilidad: {top['utility']:.2f}): "
                f"Semántica={top['sem_norm']:.2f}, Eficiencia Coste={top['cost_norm']:.2f}, "
                f"Cobertura Capabilities={top['cap_ratio']:.2f} con '{top_agent.name}'."
            ),
            matched_capabilities=matched_caps,
            estimated_cost=top["cost_raw"],
            metadata={
                "experimental": True,
                "is_experimental": True,
                "baseline_mode": "adaptive_experimental",
                "adaptive_breakdown": {
                    "utility": round(top["utility"], 4),
                    "semantic_normalized": round(top["sem_norm"], 4),
                    "cost_normalized": round(top["cost_norm"], 4),
                    "capability_ratio": round(top["cap_ratio"], 4),
                },
            },
        )
