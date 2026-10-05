"""Despacho Adaptativo a Nivel de Paso (Step-level Adaptive Dispatch) (F8-02).

Implementa el principio 'Semántica != Autoridad':
- Para cada paso de la trayectoria, genera dinámicamente la tupla de autorización:
  (agente idóneo, contexto mínimo suficiente, herramientas autorizadas, límites de riesgo).
- Aplica el principio de menor privilegio aislando herramientas y acotando el contexto
  mediante ContextManager para evitar alucinaciones y contaminación contextual.
- Valida la conformidad de la acción física antes de permitir su ejecución por el runtime.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from praxeon.agents.definition import AgentDefinition
from praxeon.context.fingerprint import ContextFingerprint
from praxeon.context.fragments import ContextFragment, FragmentType
from praxeon.context.manager import ContextManager
from praxeon.domain.assessment import RiskLevel
from praxeon.routing.models import TaskRequirement
from praxeon.runtime.adaptive.models import StepDispatchSpec


class StepLevelAdaptiveDispatcher:
    """Despachador y guardián de autorización a nivel de paso atómico."""

    # Herramientas globales de riesgo crítico que siempre exigen confirmación o sandbox estricto
    CRITICAL_SYSTEM_TOOLS = {"run_destructive_command", "drop_table", "drop_database", "format_disk", "kill_process"}

    def __init__(self, context_manager: Optional[ContextManager] = None):
        self.context_manager = context_manager or ContextManager()

    def create_dispatch_spec(
        self,
        task: TaskRequirement,
        agent: AgentDefinition,
        step_index: int,
        session_id: str,
        sandbox_tier: str = "local",
    ) -> StepDispatchSpec:
        """Calcula dinámicamente la especificación de autorización y contexto para el paso actual."""
        # 1. Determinar herramientas estrictamente autorizadas para este paso
        authorized_tools = self._resolve_step_authorized_tools(task, agent)

        # 2. Obtener contexto mínimo suficiente desde ContextManager
        # Selecciona únicamente los fragmentos registrados pertinentes para esta sesión
        items = [item for item in self.context_manager.get_items() if item.source_id == session_id]
        fragments = [item.to_fragment() for item in items]
        if not fragments:
            goal_frag = ContextFragment.create(
                fragment_id=f"step_{step_index}_goal",
                fragment_type=FragmentType.GOAL,
                source_id=session_id,
                content=f"OBJETIVO: {task.prompt}",
            )
            fragments = [goal_frag]

        goal_hash = fragments[0].content_hash
        frag_hashes = [f.content_hash for f in fragments]
        fingerprint_obj = ContextFingerprint.generate(
            session_id=session_id,
            goal_hash=goal_hash,
            relevant_node_ids=[f"step_{step_index}"],
            fragment_hashes=frag_hashes,
        )
        context_fingerprint = fingerprint_obj.value

        scoped_snapshot = self.context_manager.builder.build_snapshot(
            fingerprint=context_fingerprint,
            session_id=session_id,
            fragments=fragments,
            extra_metadata={"step_index": step_index, "agent_id": agent.agent_id},
        )

        # 3. Determinar techo de riesgo y exigencia de confirmación humana
        step_risk = agent.risk_profile.max_risk_level
        require_human = (
            agent.risk_profile.require_human_confirmation
            or task.inferred_risk == RiskLevel.CRITICAL
            or any(t in self.CRITICAL_SYSTEM_TOOLS for t in authorized_tools)
        )

        return StepDispatchSpec(
            step_index=step_index,
            agent_id=agent.agent_id,
            assigned_role=agent.role,
            model_name=agent.model.model_name,
            minimal_context_fingerprint=context_fingerprint,
            authorized_tools=authorized_tools,
            max_risk_level=step_risk,
            require_human_confirmation=require_human,
            sandbox_tier=sandbox_tier,
            metadata={
                "task_id": task.task_id,
                "complexity": task.complexity.value,
                "context_item_count": len(scoped_snapshot.fragments),
            },
        )

    def _resolve_step_authorized_tools(
        self,
        task: TaskRequirement,
        agent: AgentDefinition,
    ) -> List[str]:
        """Calcula la intersección segura de herramientas del agente y la tarea."""
        if "*" in agent.allowed_tools:
            # Si el agente tiene comodín '*', filtrar explícitamente sus herramientas prohibidas
            base_tools = set(task.required_tools) if task.required_tools else {
                "read_file", "view_file", "list_dir", "grep_search", "write_file", "replace_file_content", "run_command"
            }
            # Restar cualquier herramienta prohibida
            safe_tools = [t for t in base_tools if agent.is_tool_allowed(t)]
            return sorted(safe_tools)

        # Si el agente tiene lista blanca explícita, usarla respetando las prohibiciones
        allowed = [t for t in agent.allowed_tools if agent.is_tool_allowed(t)]
        return sorted(allowed)

    def validate_action_authorization(
        self,
        spec: StepDispatchSpec,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Verifica en tiempo de ejecución previa si la acción propuesta está autorizada en la tupla de paso."""
        # 1. Verificar presencia en lista blanca del paso
        if tool_name not in spec.authorized_tools:
            return False, (
                f"Acción denegada: La herramienta '{tool_name}' no está en la lista autorizada "
                f"para el agente '{spec.agent_id}' en el paso {spec.step_index}. "
                f"Herramientas autorizadas: {spec.authorized_tools}"
            )

        # 2. Verificar parámetros contra patrones de alto riesgo destructivo
        args_str = str(arguments or {}).lower()
        destructive_patterns = ["rm -rf", "drop table", "drop database", "truncate", "format c:"]
        for pat in destructive_patterns:
            if pat in args_str:
                return False, (
                    f"Acción bloqueada: Detección de patrón destructivo '{pat}' "
                    f"no autorizado para el paso {spec.step_index}."
                )

        return True, None
