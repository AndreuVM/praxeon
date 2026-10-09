"""Semántica de Nodos, Control de Flujo y Evaluación de Condiciones (Fase 0 y Fase 1).

Define los contratos y estructuras tipadas para ejecución determinista y acotada:
- EvaluationContext: Contexto estructurado con namespaces explícitos (variables, outputs, loop, budget, agent_state).
- AtomicCondition & CompoundCondition: Árbol lógico tipado (AND, OR, NOT) sin ejecución arbitraria (eval() prohibido).
- ConditionResult & NodeExecutionResult: Resultados observables y auditables de control de flujo.
- ControlConfig: Configuración declarativa para IF/DECISION, WHILE, DELEGATE, PARALLEL_JOIN y HUMAN_APPROVAL.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field

from praxeon.workflows.models import NodeStatus


class LogicalOperator(str, Enum):
    """Operadores lógicos para condiciones compuestas."""
    AND = "AND"
    OR = "OR"
    NOT = "NOT"


class EvaluationContext(BaseModel):
    """Contexto formal de evaluación con namespaces explícitos para evitar colisiones."""
    model_config = ConfigDict(frozen=False)

    variables: Dict[str, Any] = Field(default_factory=dict, description="Variables globales del flujo")
    outputs: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Resultados indexados por node_id")
    last_result: Optional[Dict[str, Any]] = Field(default=None, description="Último resultado emitido por el nodo upstream")
    loop: Dict[str, Any] = Field(default_factory=dict, description="Métricas de bucle activo (iteration, max_iterations, elapsed_seconds)")
    budget: Dict[str, Any] = Field(default_factory=dict, description="Presupuesto de ejecución (remaining_tokens, calls_count)")
    agent_state: Dict[str, Any] = Field(default_factory=dict, description="Estado reportado por agentes")
    semantic_assessment: Optional[Dict[str, Any]] = Field(default=None, description="Juicio semántico del System-1")

    def resolve_field(self, field_path: str) -> Any:
        """Resuelve el valor de una ruta con puntos navegando los namespaces correspondientes.
        
        Admite:
        - 'variables.<key>'
        - 'outputs.<node_id>.<key>'
        - 'loop.<key>'
        - 'budget.<key>'
        - 'agent_state.<key>'
        - 'last_result.<key>'
        - 'semantic_assessment.<key>'
        
        Retrocompatibilidad: Si no coincide con un namespace explícito, busca en
        last_result, variables y outputs.
        """
        parts = field_path.strip().split(".")
        if not parts or not parts[0]:
            return None

        ns = parts[0]
        remaining = parts[1:]

        # 1. Namespace de variables
        if ns == "variables":
            return self._navigate(self.variables, remaining)

        # 2. Namespace de outputs por nodo
        if ns == "outputs":
            if not remaining:
                return self.outputs
            node_id = remaining[0]
            node_out = self.outputs.get(node_id, {})
            return self._navigate(node_out, remaining[1:])

        # 3. Namespace de bucle
        if ns == "loop":
            return self._navigate(self.loop, remaining)

        # 4. Namespace de presupuesto
        if ns == "budget":
            return self._navigate(self.budget, remaining)

        # 5. Namespace de estado de agente
        if ns == "agent_state":
            return self._navigate(self.agent_state, remaining)

        # 6. Namespace de último resultado
        if ns == "last_result":
            return self._navigate(self.last_result or {}, remaining)

        # 7. Namespace de evaluación semántica System-1
        if ns == "semantic_assessment":
            return self._navigate(self.semantic_assessment or {}, remaining)

        # Fallback de retrocompatibilidad:
        # Si field_path es ej. 'status' o 'output.status' o 'task.completed'
        if ns == "output" and self.last_result is not None:
            return self._navigate(self.last_result, remaining)

        # Buscar en last_result primero
        if self.last_result is not None:
            val = self._navigate(self.last_result, parts)
            if val is not None:
                return val

        # Buscar en variables globales
        val = self._navigate(self.variables, parts)
        if val is not None:
            return val

        # Buscar en outputs de cualquier nodo (primer match)
        for node_id, node_out in self.outputs.items():
            if parts[0] == node_id:
                return self._navigate(node_out, remaining)
            val = self._navigate(node_out, parts)
            if val is not None:
                return val

        return None

    @staticmethod
    def _navigate(data: Any, parts: List[str]) -> Any:
        """Navega recursivamente un objeto o diccionario usando partes de clave."""
        curr = data
        for part in parts:
            if isinstance(curr, dict) and part in curr:
                curr = curr[part]
            elif hasattr(curr, part):
                curr = getattr(curr, part)
            else:
                return None
        return curr

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Genera un diccionario aplanado para retrocompatibilidad con evaluadores legados."""
        legacy: Dict[str, Any] = dict(self.variables)
        if self.last_result:
            legacy["output"] = dict(self.last_result)
            legacy.update(self.last_result)
        for nid, nout in self.outputs.items():
            legacy[f"output_{nid}"] = nout
        legacy["loop"] = dict(self.loop)
        legacy["budget"] = dict(self.budget)
        return legacy


class AtomicCondition(BaseModel):
    """Condición atómica que compara un campo resuelto contra un valor esperado."""
    model_config = ConfigDict(frozen=True)

    field: str = Field(description="Ruta o namespace del campo (ej. 'outputs.review.approved' o 'loop.iteration')")
    operator: str = Field(default="==", description="Operador: ==, !=, >, >=, <, <=, in, contains, is_true, is_false, is_null, not_null")
    expected_value: Any = Field(default=None, description="Valor esperado de referencia")

    def evaluate(self, context: Union[EvaluationContext, Dict[str, Any]]) -> bool:
        """Evalúa deterministamente la condición sin eval() arbitrario."""
        if isinstance(context, EvaluationContext):
            val = context.resolve_field(self.field)
        elif isinstance(context, dict):
            # Enfoque legado
            keys = self.field.split(".")
            val = context
            for k in keys:
                if isinstance(val, dict) and k in val:
                    val = val[k]
                else:
                    val = None
                    break
        else:
            return False

        op = self.operator.strip().lower()
        expected = self.expected_value

        if op in ("==", "eq"):
            return val == expected
        elif op in ("!=", "neq"):
            return val != expected
        elif op in (">", "gt"):
            return val is not None and expected is not None and val > expected
        elif op in (">=", "gte"):
            return val is not None and expected is not None and val >= expected
        elif op in ("<", "lt"):
            return val is not None and expected is not None and val < expected
        elif op in ("<=", "lte"):
            return val is not None and expected is not None and val <= expected
        elif op in ("in", "is_in"):
            if isinstance(expected, (list, tuple, set)):
                return val in expected
            return False
        elif op == "contains":
            if isinstance(val, (list, tuple, set, str)):
                return expected in val
            return False
        elif op in ("is_true", "true"):
            return bool(val) is True
        elif op in ("is_false", "false"):
            return bool(val) is False
        elif op in ("is_null", "null"):
            return val is None
        elif op in ("not_null", "is_not_null"):
            return val is not None
        return False


class CompoundCondition(BaseModel):
    """Árbol lógico de condiciones compuestas (AND, OR, NOT)."""
    model_config = ConfigDict(frozen=True)

    logical_op: LogicalOperator = Field(default=LogicalOperator.AND)
    conditions: List[Union["CompoundCondition", AtomicCondition]] = Field(default_factory=list)

    def evaluate(self, context: Union[EvaluationContext, Dict[str, Any]]) -> bool:
        """Evalúa el árbol de condiciones con lógica booleana estricta (fail-closed ante listas vacías si NOT)."""
        if not self.conditions:
            return True

        if self.logical_op == LogicalOperator.AND:
            return all(c.evaluate(context) for c in self.conditions)
        elif self.logical_op == LogicalOperator.OR:
            return any(c.evaluate(context) for c in self.conditions)
        elif self.logical_op == LogicalOperator.NOT:
            # NOT invierte el resultado del primer elemento (o de todos unidos por AND)
            return not all(c.evaluate(context) for c in self.conditions)
        return False


# Reconstrucción recursiva del modelo para referencias hacia adelante
CompoundCondition.model_rebuild()


def parse_condition(data: Any) -> Optional[Union[CompoundCondition, AtomicCondition]]:
    """Parsea una condición estructurada desde diccionario o modelo existente."""
    if data is None:
        return None
    if isinstance(data, (AtomicCondition, CompoundCondition)):
        return data

    if isinstance(data, dict):
        # Soporte para formato compacto: {all: [...]} o {any: [...]} o {not: [...]}
        if "all" in data:
            sub = [parse_condition(c) for c in data["all"] if c is not None]
            return CompoundCondition(logical_op=LogicalOperator.AND, conditions=[c for c in sub if c is not None])
        if "any" in data:
            sub = [parse_condition(c) for c in data["any"] if c is not None]
            return CompoundCondition(logical_op=LogicalOperator.OR, conditions=[c for c in sub if c is not None])
        if "not" in data:
            raw_not = data["not"]
            items = raw_not if isinstance(raw_not, list) else [raw_not]
            sub = [parse_condition(c) for c in items if c is not None]
            return CompoundCondition(logical_op=LogicalOperator.NOT, conditions=[c for c in sub if c is not None])

        # Formato explícito CompoundCondition
        if "logical_op" in data and "conditions" in data:
            sub = [parse_condition(c) for c in data["conditions"] if c is not None]
            return CompoundCondition(
                logical_op=LogicalOperator(data["logical_op"].upper()),
                conditions=[c for c in sub if c is not None],
            )

        # Formato atómico: {field: "...", operator: "...", expected_value: ...}
        if "field" in data:
            return AtomicCondition(
                field=data["field"],
                operator=data.get("operator", data.get("op", "==")),
                expected_value=data.get("expected_value", data.get("value", None)),
            )

    return None


class ConditionResult(BaseModel):
    """Resultado auditable y observable tras la evaluación de una condición de control."""
    model_config = ConfigDict(frozen=True)

    evaluated: bool = Field(default=True, description="Indica si la condición fue evaluada con éxito")
    matched: bool = Field(default=False, description="Resultado booleano de la evaluación")
    branch_taken: Optional[str] = Field(default=None, description="Rama seleccionada: 'true', 'false', 'default' o ID de arista")
    reason: Optional[str] = Field(default=None, description="Motivo auditable de la selección de rama")
    details: Dict[str, Any] = Field(default_factory=dict, description="Entradas y contexto relevantes para telemetría")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class NodeExecutionResult(BaseModel):
    """Resultado formal del intento de ejecución de un nodo."""
    model_config = ConfigDict(frozen=True)

    node_id: str
    status: NodeStatus
    outputs: Dict[str, Any] = Field(default_factory=dict)
    condition_result: Optional[ConditionResult] = None
    error: Optional[str] = None
    execution_time_seconds: float = 0.0


class ControlConfig(BaseModel):
    """Configuración declarativa para control de flujo estructurado."""
    model_config = ConfigDict(frozen=True)

    # 1. Configuración para IF / DECISION
    condition: Optional[Union[CompoundCondition, AtomicCondition, Dict[str, Any]]] = None
    true_edge: Optional[str] = Field(default=None, description="ID o destino de arista cuando condition == True")
    false_edge: Optional[str] = Field(default=None, description="ID o destino de arista cuando condition == False")
    default_edge: Optional[str] = Field(default=None, description="ID o destino de arista por defecto si no coincide")
    on_error: Optional[str] = Field(default=None, description="ID o destino de arista ante error o campo ausente")

    # 2. Configuración para WHILE / LOOP acotado
    body_entry: Optional[str] = Field(default=None, description="Nodo inicial del cuerpo del bucle")
    exit_target: Optional[str] = Field(default=None, description="Nodo objetivo al salir del bucle")
    max_iterations: Optional[int] = Field(default=None, ge=1, description="Límite estricto obligatorio de iteraciones")
    on_limit: Optional[str] = Field(default="ABORT", description="Acción al agotar iteraciones: 'ABORT' o 'ESCALATE'")

    # 3. Configuración para DELEGATE / ROUTER
    routing_mode: Optional[str] = Field(default="MANUAL", description="'MANUAL' a agente fijo o 'AUTOMATIC' según capacidades")
    candidate_agents: Optional[List[str]] = Field(default=None, description="Lista de agentes candidatos para selección automática")

    # 4. Configuración para PARALLEL_JOIN
    join_policy: Optional[str] = Field(default="all", description="Política de sincronización: 'all', 'any', 'quorum'")
    quorum_count: Optional[int] = Field(default=None, ge=1, description="Número de ramas exitosas requeridas si join_policy == 'quorum'")
    cancel_remaining: bool = Field(default=False, description="Si es True, cancela las ramas en progreso tras cumplir condición de join")
    merge_policy: Optional[str] = Field(default="shallow", description="Estrategia de combinación de outputs: 'shallow', 'namespace', 'reduce'")

    # 5. Configuración para HUMAN_APPROVAL
    prompt: Optional[str] = Field(default=None, description="Mensaje explicativo para el supervisor humano")
    approver_role: Optional[str] = Field(default=None, description="Rol requerido para autorizar la continuación")
    reject_target: Optional[str] = Field(default=None, description="Nodo de destino en caso de rechazo")
