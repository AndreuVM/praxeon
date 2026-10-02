"""Selector determinista de contexto consciente del grafo de dependencias (DAGContextSelector).

Selecciona fragmentos relevantes a partir del estado de la sesión, árbol de decisiones (DAG)
y evidencias contrastadas sin requerir bases vectoriales ni modelos de embeddings opacos.
"""

from typing import Any, Dict, List, Optional
from praxeon.context.fragments import (
    ContextFragment,
    GoalFragment,
    EvidenceFragment,
    ObservationFragment,
    ConstraintFragment,
    TaskFragment,
    EnvironmentFragment,
)
from praxeon.domain.action import ActionCandidate


class DAGContextSelector:
    """Selector estructural de fragmentos de contexto a partir de SessionState y dependencias."""

    def __init__(
        self,
        max_recent_observations: int = 10,
        max_obs_chars: int = 300,
        max_evidence_items: int = 8,
        include_environment: bool = True,
    ):
        self.max_recent_observations = max_recent_observations
        self.max_obs_chars = max_obs_chars
        self.max_evidence_items = max_evidence_items
        self.include_environment = include_environment

    def select(
        self,
        state: Any,
        candidate_action: Optional[ActionCandidate] = None,
        session_context: Optional[Any] = None,
        active_node_id: Optional[str] = None,
    ) -> List[ContextFragment]:
        """Extrae y estructura todos los fragmentos pertinentes para el nodo actual de la trayectoria."""
        fragments: List[ContextFragment] = []

        # 1. Fragmento de Meta (GOAL) y criterios de éxito
        session_id = getattr(state, "session_id", "session_default")
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

        fragments.append(
            GoalFragment(goal_text=goal_text, criteria=criteria, session_id=session_id)
        )

        # 2. Fragmentos de Restricciones Operacionales (CONSTRAINT)
        metadata = getattr(state, "metadata", {}) or {}
        constraints = metadata.get("constraints", [])
        if isinstance(constraints, list):
            for idx, c in enumerate(constraints):
                c_text = str(c)
                fragments.append(ConstraintFragment(constraint_text=c_text, rule_id=f"rule_{idx+1}"))

        # 3. Fragmentos de Memoria de Tareas Previas (TASK)
        if session_context and hasattr(session_context, "task_records"):
            for rec in getattr(session_context, "task_records", []):
                t_id = getattr(rec, "task_id", 1)
                t_goal = getattr(rec, "goal", "")
                t_summary = getattr(rec, "summary", getattr(rec, "final_answer", ""))
                fragments.append(TaskFragment(task_id=t_id, goal=t_goal, summary=t_summary))

        # 4. Fragmentos de Evidencia Empírica Contrastada (EVIDENCE)
        raw_evidence = getattr(state, "evidence", [])
        ev_items = raw_evidence[:self.max_evidence_items] if len(raw_evidence) > self.max_evidence_items else raw_evidence
        for ev in ev_items:
            ev_id = getattr(ev, "id", f"ev_{hash(str(ev)) % 10000}")
            claim = getattr(ev, "claim", str(ev))
            fragments.append(EvidenceFragment(evidence_id=str(ev_id), claim=claim))

        # 5. Fragmentos de Observaciones Previas (OBSERVATION)
        raw_steps = getattr(state, "steps", [])
        recent_steps = raw_steps[-self.max_recent_observations:] if len(raw_steps) > self.max_recent_observations else raw_steps

        for step in recent_steps:
            step_action = getattr(step, "action", None)
            tool_name = "unknown"
            step_args = {}
            if step_action and getattr(step_action, "tool_call", None):
                tool_name = step_action.tool_call.tool_name
                step_args = getattr(step_action.tool_call, "arguments", {}) or {}

            raw_obs = getattr(step, "observation", "") or ""
            obs_str = str(raw_obs)
            step_truncated = False
            if len(obs_str) > self.max_obs_chars:
                obs_str = obs_str[:self.max_obs_chars] + "... [truncado]"
                step_truncated = True

            step_id = str(getattr(step, "step_id", getattr(step_action, "id", "step")))

            meta = dict(step_args) if step_args else {}
            if step_truncated:
                meta["truncated"] = True

            fragments.append(
                ObservationFragment(
                    step_id=step_id,
                    tool_name=tool_name,
                    observation=obs_str,
                    arguments=step_args,
                    metadata=meta,
                )
            )

        # 6. Fragmento de Entorno (ENVIRONMENT)
        if self.include_environment:
            env_info = metadata.get("environment_info")
            if not env_info:
                cwd = metadata.get("cwd", ".")
                mode = metadata.get("execution_mode", "local_restricted")
                env_info = f"cwd={cwd}, mode={mode}"
            fragments.append(EnvironmentFragment(environment_info=str(env_info)))

        return fragments
