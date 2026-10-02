"""Constructor de contexto específico para proveedores de razonamiento (ProviderContextBuilder).

Convierte el SessionState completo y la ActionCandidate en un ProviderContext
estructurado y acotado, aplicando control estricto de presupuesto de tokens,
detección de truncado y extracción de evidencias para evitar desbordar modelos locales
o incurrir en costos excesivos en APIs de inferencia.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from praxeon.domain.action import ActionCandidate


class ProviderContext(BaseModel):
    """Contexto estructurado, normalizado y acotado para evaluación en un ReasoningProvider."""
    model_config = ConfigDict(frozen=True)

    session_id: str
    goal: str
    success_criteria: List[str] = Field(default_factory=list)
    history_window: List[Dict[str, Any]] = Field(default_factory=list)
    active_evidence: List[str] = Field(default_factory=list)
    candidate_action: Dict[str, Any] = Field(default_factory=dict)
    token_estimate: int = 0
    truncated: bool = False
    max_context_tokens: int = 2048
    formatted_prompt: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa el contexto a un diccionario canónico."""
        return self.model_dump()

    def to_laya_payload(self) -> Dict[str, Any]:
        """Genera el payload estructurado optimizado para el motor de inferencia LAYA."""
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "success_criteria": self.success_criteria,
            "history": self.history_window,
            "evidence": self.active_evidence,
            "candidate": self.candidate_action,
            "prompt": self.formatted_prompt,
            "token_estimate": self.token_estimate,
            "truncated": self.truncated,
        }

    def to_typesafe_payload(self) -> Dict[str, Any]:
        """Genera el payload estructurado compatible con TypeSafe System One."""
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "history": [
                {
                    "step_id": h.get("id"),
                    "tool": h.get("tool"),
                    "output": h.get("observation"),
                }
                for h in self.history_window
            ],
            "proposed_action": self.candidate_action,
        }


from praxeon.context.manager import ContextManager
from praxeon.context.selector import DAGContextSelector
from praxeon.context.budget import TokenBudget
from praxeon.context.fragments import FragmentType


class ProviderContextBuilder:
    """Constructor y podador inteligente de contexto para ReasoningProviders (JEV, LAYA, Replay).
    
    Integra de forma transparente el ContextManager de PRAXEON para proveer caching L1,
    fingerprints deterministas y presupuestado jerárquico de tokens.
    """

    def __init__(
        self,
        default_max_tokens: int = 2048,
        max_history_steps: int = 10,
        max_obs_chars: int = 300,
        enable_caching: bool = True,
        context_manager: Optional[ContextManager] = None,
    ):
        self.default_max_tokens = default_max_tokens
        self.max_history_steps = max_history_steps
        self.max_obs_chars = max_obs_chars
        self.enable_caching = enable_caching

        if context_manager is not None:
            self.context_manager = context_manager
        elif enable_caching:
            selector = DAGContextSelector(
                max_recent_observations=max_history_steps,
                max_obs_chars=max_obs_chars,
            )
            budget = TokenBudget(default_max_tokens=default_max_tokens)
            self.context_manager = ContextManager(selector=selector, budget=budget)
        else:
            self.context_manager = None

    def _estimate_tokens(self, text: str) -> int:
        """Aproximación heurística de tokens (~4 caracteres por token en inglés/español técnico)."""
        return max(1, len(text) // 4)

    def build(
        self,
        state: Any,
        action: ActionCandidate,
        max_tokens: Optional[int] = None,
    ) -> ProviderContext:
        """Construye un ProviderContext acotado y normalizado a partir de SessionState y ActionCandidate."""
        budget_tokens = max_tokens or self.default_max_tokens
        max_chars = budget_tokens * 4
        truncated = False

        state_meta = getattr(state, "metadata", {}) or (state.get("metadata", {}) if isinstance(state, dict) else {})

        # Ruta optimizada con ContextManager (Context Caching L1)
        if self.enable_caching and self.context_manager is not None:
            snapshot, is_hit = self.context_manager.build(
                state=state,
                candidate_action=action,
                max_tokens=budget_tokens,
            )

            # Extraer campos estructurados directamente de los fragmentos compilados
            goal_text = "Objetivo no especificado"
            criteria: List[str] = []
            evidence_claims: List[str] = []
            history_steps: List[Dict[str, Any]] = []

            for frag in snapshot.fragments:
                if frag.fragment_type == FragmentType.GOAL:
                    goal_text = frag.content.split("\nCriterios de éxito:")[0].replace("OBJETIVO: ", "").strip()
                    criteria = frag.metadata.get("criteria", [])
                elif frag.fragment_type == FragmentType.EVIDENCE:
                    # Extraer claim limpio
                    claim = frag.content.split("]: ", 1)[-1] if "]: " in frag.content else frag.content
                    evidence_claims.append(claim)
                elif frag.fragment_type == FragmentType.OBSERVATION:
                    history_steps.append({
                        "id": frag.source_id,
                        "tool": frag.metadata.get("tool_name"),
                        "arguments": frag.metadata.get("arguments", {}),
                        "operation": "",
                        "observation": frag.metadata.get("observation", frag.content.split("]: ", 1)[-1] if "]: " in frag.content else frag.content),
                    })

            candidate_dict = {
                "id": action.id,
                "description": action.description,
                "tool_name": action.tool_call.tool_name if action.tool_call else None,
                "arguments": action.tool_call.arguments if action.tool_call else {},
                "requires_evidence": list(action.requires_evidence),
            }

            meta_merged = dict(state_meta) if isinstance(state_meta, dict) else {}
            meta_merged["cache_hit"] = is_hit
            meta_merged["fingerprint"] = snapshot.fingerprint

            # Verificar si se truncó el historial o el prompt
            is_trunc = snapshot.truncated
            raw_steps_len = len(getattr(state, "steps", []))
            if raw_steps_len > len(history_steps) or len(snapshot.formatted_prompt) > max_chars:
                is_trunc = True

            return ProviderContext(
                session_id=snapshot.session_id,
                goal=goal_text,
                success_criteria=criteria,
                history_window=history_steps,
                active_evidence=evidence_claims,
                candidate_action=candidate_dict,
                token_estimate=snapshot.total_tokens,
                truncated=is_trunc,
                max_context_tokens=budget_tokens,
                formatted_prompt=snapshot.formatted_prompt,
                metadata=meta_merged,
            )

        # Ruta estándar (fallback sin caching)

        # 1. Extraer meta y criterios de éxito
        session_id = getattr(state, "session_id", "session_unknown")
        goal_obj = getattr(state, "goal", None)
        if goal_obj is None:
            goal_text = "Objetivo no especificado"
            criteria: List[str] = []
        elif isinstance(goal_obj, str):
            goal_text = goal_obj
            criteria = []
        else:
            goal_text = getattr(goal_obj, "objective", str(goal_obj))
            criteria = [
                c.description if hasattr(c, "description") else str(c)
                for c in getattr(goal_obj, "get_all_criteria", lambda: getattr(goal_obj, "success_criteria", []))()
            ]

        # 2. Extraer candidato propuesto
        candidate_dict = {
            "id": action.id,
            "description": action.description,
            "tool_name": action.tool_call.tool_name if action.tool_call else None,
            "arguments": action.tool_call.arguments if action.tool_call else {},
            "requires_evidence": list(action.requires_evidence),
        }

        # 3. Extraer evidencias activas
        raw_evidence = getattr(state, "evidence", [])
        evidence_claims: List[str] = [
            getattr(ev, "claim", str(ev)) for ev in raw_evidence
        ]

        # 4. Ventana de historial (últimos N pasos preservados)
        raw_steps = getattr(state, "steps", [])
        history_steps: List[Dict[str, Any]] = []

        # Tomamos los últimos N pasos para no desbordar el contexto
        window_steps = raw_steps[-self.max_history_steps:] if len(raw_steps) > self.max_history_steps else raw_steps
        if len(raw_steps) > len(window_steps):
            truncated = True

        for step in window_steps:
            step_action = getattr(step, "action", None)
            tool_name = None
            step_args = {}
            if step_action and getattr(step_action, "tool_call", None):
                tool_name = step_action.tool_call.tool_name
                step_args = getattr(step_action.tool_call, "arguments", {}) or {}

            raw_obs = getattr(step, "observation", "") or ""
            obs_str = str(raw_obs)
            if len(obs_str) > self.max_obs_chars:
                obs_str = obs_str[:self.max_obs_chars] + "... [truncado]"
                truncated = True

            step_entry = {
                "id": getattr(step, "step_id", getattr(step_action, "id", "step")),
                "tool": tool_name,
                "arguments": step_args,
                "operation": getattr(step_action, "description", "") or "",
                "observation": obs_str,
            }
            history_steps.append(step_entry)

        # 5. Formatear prompt canónico
        lines = [
            f"GOAL: {goal_text}",
        ]
        if criteria:
            lines.append("CRITERIA: " + " | ".join(criteria[:5]))
        if evidence_claims:
            lines.append("CONFIRMED EVIDENCE: " + ", ".join(evidence_claims[:8]))

        if history_steps:
            lines.append("RECENT STEPS:")
            for s in history_steps:
                tool_info = f" [{s['tool']}]" if s.get("tool") else ""
                lines.append(f" - {s['id']}{tool_info} => {s['observation']}")

        tool_call_str = f"{candidate_dict['tool_name']}({candidate_dict['arguments']})" if candidate_dict['tool_name'] else "thought"
        lines.append(f"CANDIDATE ACTION: {candidate_dict['description']} -> {tool_call_str}")

        prompt_text = "\n".join(lines)

        # 6. Comprobar límite de caracteres / tokens
        if len(prompt_text) > max_chars:
            prompt_text = prompt_text[:max_chars - 30] + "\n... [contexto podado por presupuesto]"
            truncated = True

        token_est = self._estimate_tokens(prompt_text)
        state_meta = getattr(state, "metadata", {}) or (state.get("metadata", {}) if isinstance(state, dict) else {})

        return ProviderContext(
            session_id=session_id,
            goal=goal_text,
            success_criteria=criteria,
            history_window=history_steps,
            active_evidence=evidence_claims,
            candidate_action=candidate_dict,
            token_estimate=token_est,
            truncated=truncated,
            max_context_tokens=budget_tokens,
            formatted_prompt=prompt_text,
            metadata=dict(state_meta) if isinstance(state_meta, dict) else {},
        )
