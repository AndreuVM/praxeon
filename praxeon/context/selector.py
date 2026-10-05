"""Selector determinista de contexto consciente del grafo de dependencias (DAGContextSelector).

Selecciona fragmentos relevantes a partir del estado de la sesión, árbol de decisiones (DAG)
y evidencias contrastadas sin requerir bases vectoriales ni modelos de embeddings opacos.
"""

from typing import Any, Dict, List, Optional, Set
from praxeon.context.fragments import (
    ContextFragment,
    GoalFragment,
    EvidenceFragment,
    ObservationFragment,
    ConstraintFragment,
    DecisionFragment,
    TaskFragment,
    EnvironmentFragment,
)
from praxeon.domain.action import ActionCandidate


class DAGContextSelector:
    """Selector estructural de fragmentos de contexto a partir de SessionState, dependencias y ramas."""

    def __init__(
        self,
        max_recent_observations: int = 10,
        max_obs_chars: int = 300,
        max_evidence_items: int = 8,
        include_environment: bool = True,
        include_key_decisions: bool = True,
        filter_pruned_branches: bool = True,
    ):
        self.max_recent_observations = max_recent_observations
        self.max_obs_chars = max_obs_chars
        self.max_evidence_items = max_evidence_items
        self.include_environment = include_environment
        self.include_key_decisions = include_key_decisions
        self.filter_pruned_branches = filter_pruned_branches

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

        # 4. Fragmentos de Evidencia Empírica Contrastada (EVIDENCE) con Priorización Relevante (F2-03)
        raw_evidence = getattr(state, "evidence", [])
        req_evidence_ids = (
            set(candidate_action.requires_evidence)
            if candidate_action and candidate_action.requires_evidence
            else set()
        )

        prioritized_ev = []
        other_ev = []
        for ev in raw_evidence:
            ev_id = str(getattr(ev, "id", ""))
            if ev_id in req_evidence_ids:
                prioritized_ev.append(ev)
            else:
                other_ev.append(ev)

        ordered_ev = (prioritized_ev + other_ev)[:self.max_evidence_items]
        for ev in ordered_ev:
            ev_id = getattr(ev, "id", f"ev_{hash(str(ev)) % 10000}")
            claim = getattr(ev, "claim", str(ev))
            fragments.append(EvidenceFragment(evidence_id=str(ev_id), claim=claim))

        # 5. Filtrado de Ramas Podadas y Selección de Pasos Pertinentes (F2-03 y F2-04)
        raw_steps = getattr(state, "steps", [])
        pruned_branches: Set[str] = set(metadata.get("pruned_branches", []))
        pruned_nodes: Set[str] = set(metadata.get("pruned_nodes", []))

        admissible_steps = []
        key_decisions: List[ContextFragment] = []

        for step in raw_steps:
            step_action = getattr(step, "action", None)
            step_meta = getattr(step_action, "metadata", {}) or {}
            step_node = step_meta.get("node_id") or getattr(step, "node_id", None)
            step_branch = step_meta.get("branch_id") or getattr(step, "branch_id", None)

            # Exclusión estricta de ramas podadas/descartadas (F2-04)
            if self.filter_pruned_branches:
                if step_node and step_node in pruned_nodes:
                    continue
                if step_branch and step_branch in pruned_branches:
                    continue

            admissible_steps.append(step)

            # Extracción de decisiones clave de supervisión (BLOCK / REPLAN) (F2-03)
            if self.include_key_decisions:
                decision = getattr(step, "decision", None)
                if decision:
                    status_val = getattr(decision, "status", None)
                    status_str = status_val.value if hasattr(status_val, "value") else str(status_val)
                    if status_str in ("block", "replan", "abstain"):
                        r_codes = getattr(decision, "reason_codes", []) or []
                        explanation = ", ".join(r_codes) if r_codes else "Acción denegada por política de seguridad"
                        act_id = getattr(step_action, "id", "unknown_action") if step_action else None
                        key_decisions.append(
                            DecisionFragment(
                                decision_id=f"dec_{len(key_decisions)+1}",
                                status=status_str,
                                explanation=explanation,
                                action_id=act_id,
                            )
                        )

        # Añadir decisiones clave seleccionadas
        fragments.extend(key_decisions)

        # 6. Fragmentos de Observaciones Previas Recientes
        recent_steps = (
            admissible_steps[-self.max_recent_observations:]
            if len(admissible_steps) > self.max_recent_observations
            else admissible_steps
        )

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

        # 7. Fragmento de Entorno (ENVIRONMENT)
        if self.include_environment:
            env_info = metadata.get("environment_info")
            if not env_info:
                cwd = metadata.get("cwd", ".")
                mode = metadata.get("execution_mode", "local_restricted")
                env_info = f"cwd={cwd}, mode={mode}"
            fragments.append(EnvironmentFragment(environment_info=str(env_info)))

        return fragments
