"""Modelo de Datos, Estructura de Grafo y Validación de Workflows Visuales (F6-01).

Define las abstracciones fundamentales para orquestación de flujos de trabajo en PRAXEON:
- NodeType & NodeStatus: Taxonomía y ciclo de vida de nodos.
- EdgeCondition: Condiciones lógicas de transición entre nodos evaluadas de forma segura.
- WorkflowEdge: Conexiones dirigidas y cableado entre nodos.
- WorkflowNode: Nodos de cómputo, agentes, tareas, decisiones lógicas y paralelismo.
- WorkflowDefinition: Especificación formal del grafo, validación topológica (DAG),
  detección de ciclos, ordenación topológica y portabilidad JSON/YAML.
"""

from collections import defaultdict, deque
from datetime import datetime, timezone
from enum import Enum
import json
import re
from typing import Any, Callable, Dict, List, Optional, Set, Union
import uuid
from pydantic import BaseModel, ConfigDict, Field, field_validator
import yaml


class NodeType(str, Enum):
    """Tipos de nodos en el grafo de flujo de trabajo."""
    START = "START"                    # Nodo inicial de disparo del flujo
    END = "END"                        # Nodo terminal de finalización
    TASK = "TASK"                      # Tarea operativa o herramienta estándar
    AGENT = "AGENT"                    # Tarea delegada a un agente especializado
    DECISION = "DECISION"              # Bifurcación condicional basada en reglas
    IF = "IF"                          # Bifurcación condicional booleana (alias semántico de DECISION)
    WHILE = "WHILE"                    # Bucle estructurado acotado con salida garantizada
    DELEGATE = "DELEGATE"              # Delegación a agente por router/política
    HUMAN_APPROVAL = "HUMAN_APPROVAL"  # Pausa supervisada para aprobación humana
    PARALLEL_FORK = "PARALLEL_FORK"    # División concurrente en múltiples ramas
    PARALLEL_JOIN = "PARALLEL_JOIN"    # Sincronización y unificación de ramas concurrentes


class NodeStatus(str, Enum):
    """Estados del ciclo de vida de un nodo en ejecución."""
    PENDING = "PENDING"                # En espera de que se cumplan dependencias
    READY = "READY"                    # Dependencias satisfechas, listo para ejecutar
    RUNNING = "RUNNING"                # En ejecución activa
    COMPLETED = "COMPLETED"            # Finalizado exitosamente con outputs
    FAILED = "FAILED"                  # Falló tras agotar reintentos
    SKIPPED = "SKIPPED"                # Omitido por bifurcación condicional
    CANCELLED = "CANCELLED"            # Cancelado por aborto de workflow
    WAITING_APPROVAL = "WAITING_APPROVAL"  # Pausado esperando confirmación humana
    WAITING_RESULT = "WAITING_RESULT"      # En espera asíncrona de resultado (p. ej. respuesta de agente o servicio externo)
    TIMEOUT = "TIMEOUT"                # Expiró el tiempo límite asignado
    RETRYING = "RETRYING"              # En espera programada por política de reintentos y backoff


class WorkflowStatus(str, Enum):
    """Estados del ciclo de vida de la ejecución de un flujo de trabajo."""
    IDLE = "IDLE"                      # Instanciado, aún no iniciado
    RUNNING = "RUNNING"                # Ejecutándose activamente
    PAUSED = "PAUSED"                  # Detenido temporalmente
    COMPLETED = "COMPLETED"            # Todos los nodos terminales alcanzados
    FAILED = "FAILED"                  # Detenido por fallo no recuperable en un nodo crítico
    CANCELLED = "CANCELLED"            # Abortado explícitamente


class UIPosition(BaseModel):
    """Coordenadas visuales bidimensionales para el lienzo drag & drop."""
    model_config = ConfigDict(frozen=True)

    x: float = 0.0
    y: float = 0.0


class RetryPolicy(BaseModel):
    """Política de reintentos para fallos transitorios en nodos de ejecución."""
    model_config = ConfigDict(frozen=True)

    max_retries: int = Field(default=0, ge=0)
    delay_seconds: float = Field(default=1.0, ge=0.0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0)


class EdgeCondition(BaseModel):
    """Condición lógica de evaluación para transiciones condicionales."""
    model_config = ConfigDict(frozen=True)

    field: str = Field(description="Ruta o clave del campo en el contexto/output del nodo origen")
    operator: str = Field(default="==", description="Operador de comparación: ==, !=, >, >=, <, <=, in, contains, is_true, is_false")
    expected_value: Any = Field(default=None, description="Valor de referencia para la comparación")

    def evaluate(self, context: Union[Dict[str, Any], Any]) -> bool:
        """Evalúa la condición de manera segura contra un diccionario de contexto o EvaluationContext."""
        if hasattr(context, "resolve_field"):
            val = context.resolve_field(self.field)
        elif isinstance(context, dict):
            # Extraer valor por clave (soporta puntos anidados ej. 'output.status')
            keys = self.field.split(".")
            val = context
            for k in keys:
                if isinstance(val, dict) and k in val:
                    val = val[k]
                else:
                    val = None
                    break
        else:
            val = None

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
        else:
            return False


class WorkflowEdge(BaseModel):
    """Conexión dirigida (arista) entre dos nodos del flujo de trabajo."""
    model_config = ConfigDict(frozen=True)

    edge_id: str = Field(description="Identificador único de la arista")
    from_node: str = Field(description="Identificador del nodo origen")
    to_node: str = Field(description="Identificador del nodo destino")
    condition: Optional[EdgeCondition] = Field(default=None, description="Condición lógica opcional")
    label: str = Field(default="", description="Etiqueta visual de la conexión")


class WorkflowNode(BaseModel):
    """Definición y estado de un nodo dentro del flujo de trabajo."""
    model_config = ConfigDict(frozen=True)

    node_id: str = Field(description="Identificador único del nodo")
    name: str = Field(description="Nombre descriptivo legible")
    node_type: NodeType = Field(default=NodeType.TASK)
    agent_id: Optional[str] = Field(default=None, description="ID del agente asignado si node_type == AGENT")
    tool_name: Optional[str] = Field(default=None, description="Herramienta a invocar si node_type == TASK")
    inputs: Dict[str, Any] = Field(default_factory=dict, description="Parámetros de entrada o mapeos de contexto")
    outputs: Dict[str, Any] = Field(default_factory=dict, description="Resultados producidos tras la ejecución")
    status: NodeStatus = Field(default=NodeStatus.PENDING)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_seconds: Optional[int] = Field(default=120, ge=1)
    position: UIPosition = Field(default_factory=UIPosition)
    control_config: Optional[Dict[str, Any]] = Field(default=None, description="Configuración de control de flujo declarativa")
    input_mapping: Optional[Dict[str, str]] = Field(default=None, description="Mapeo explícito de entradas desde namespaces")
    output_mapping: Optional[Dict[str, str]] = Field(default=None, description="Mapeo explícito de salidas hacia namespaces")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def with_status(self, new_status: NodeStatus, outputs: Optional[Dict[str, Any]] = None) -> "WorkflowNode":
        """Genera una copia inmutable con estado y outputs actualizados."""
        dump = self.model_dump()
        dump["status"] = new_status
        if outputs is not None:
            dump["outputs"] = outputs
        return WorkflowNode(**dump)


class WorkflowDefinition(BaseModel):
    """Definición canónica y formal del grafo de workflow en PRAXEON."""
    model_config = ConfigDict(frozen=True)

    workflow_id: str = Field(description="Identificador único del flujo")
    name: str = Field(description="Nombre del flujo de trabajo")
    description: str = Field(default="")
    version: int = Field(default=1, ge=1)
    nodes: Dict[str, WorkflowNode] = Field(default_factory=dict, description="Catálogo de nodos indexados por node_id")
    edges: List[WorkflowEdge] = Field(default_factory=list, description="Lista de conexiones dirigidas")
    variables: Dict[str, Any] = Field(default_factory=dict, description="Variables globales del flujo")
    timeout_seconds: Optional[float] = Field(default=None, description="Timeout global máximo de ejecución en segundos")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def get_start_node(self) -> Optional[WorkflowNode]:
        """Obtiene el nodo de inicio principal del flujo."""
        for node in self.nodes.values():
            if node.node_type == NodeType.START:
                return node
        return None

    def get_end_nodes(self) -> List[WorkflowNode]:
        """Obtiene todos los nodos terminales del flujo."""
        return [node for node in self.nodes.values() if node.node_type == NodeType.END]

    def get_outgoing_edges(self, node_id: str) -> List[WorkflowEdge]:
        """Retorna todas las aristas que parten del nodo especificado."""
        return [e for e in self.edges if e.from_node == node_id]

    def get_incoming_edges(self, node_id: str) -> List[WorkflowEdge]:
        """Retorna todas las aristas que llegan al nodo especificado."""
        return [e for e in self.edges if e.to_node == node_id]

    def get_upstream_node_ids(self, node_id: str) -> List[str]:
        """Obtiene los IDs de los nodos predecesores inmediatos."""
        return [e.from_node for e in self.get_incoming_edges(node_id)]

    def get_downstream_node_ids(self, node_id: str) -> List[str]:
        """Obtiene los IDs de los nodos sucesores inmediatos."""
        return [e.to_node for e in self.get_outgoing_edges(node_id)]

    def validate_graph(self) -> List[str]:
        """Valida rigurosamente la coherencia estructural y topológica del grafo.
        
        Retorna:
            Lista de errores encontrados. Si la lista está vacía, el grafo es válido.
        """
        errors: List[str] = []

        # 1. Validar existencia del nodo START
        start_nodes = [n for n in self.nodes.values() if n.node_type == NodeType.START]
        if not start_nodes:
            errors.append("El flujo de trabajo carece de nodo de inicio (START).")
        elif len(start_nodes) > 1:
            errors.append(f"El flujo de trabajo contiene {len(start_nodes)} nodos START; debe haber exactamente uno.")

        # 2. Validar existencia de al menos un nodo END
        end_nodes = [n for n in self.nodes.values() if n.node_type == NodeType.END]
        if not end_nodes:
            errors.append("El flujo de trabajo carece de al menos un nodo terminal (END).")

        # 3. Validar consistencia referencial de las aristas
        node_ids = set(self.nodes.keys())
        edge_ids: Set[str] = set()

        for edge in self.edges:
            if edge.edge_id in edge_ids:
                errors.append(f"ID de arista duplicado: '{edge.edge_id}'.")
            edge_ids.add(edge.edge_id)

            if edge.from_node not in node_ids:
                errors.append(f"Arista '{edge.edge_id}' referencia un nodo origen inexistente: '{edge.from_node}'.")
            if edge.to_node not in node_ids:
                errors.append(f"Arista '{edge.edge_id}' referencia un nodo destino inexistente: '{edge.to_node}'.")

        if errors:
            return errors

        # 4. Validar que el nodo START no tenga aristas entrantes
        start_id = start_nodes[0].node_id
        if self.get_incoming_edges(start_id):
            errors.append(f"El nodo START '{start_id}' no puede tener aristas entrantes.")

        # 5. Validar que los nodos END no tengan aristas salientes
        for end_node in end_nodes:
            if self.get_outgoing_edges(end_node.node_id):
                errors.append(f"El nodo END '{end_node.node_id}' no puede tener aristas salientes.")

        # 6. Validar alcanzabilidad desde START (detección de nodos aislados o huérfanos)
        visited_forward: Set[str] = set()
        queue = deque([start_id])
        while queue:
            curr = queue.popleft()
            if curr not in visited_forward:
                visited_forward.add(curr)
                for next_id in self.get_downstream_node_ids(curr):
                    if next_id not in visited_forward:
                        queue.append(next_id)

        unreachable = node_ids - visited_forward
        if unreachable:
            errors.append(f"Nodos inalcanzables desde START: {sorted(unreachable)}.")

        # 7. Detección de ciclos estructurados vs dependencias circulares no controladas
        while_nodes = {nid: n for nid, n in self.nodes.items() if n.node_type == NodeType.WHILE}

        # Validar configuración obligatoria de nodos WHILE
        for wn_id, wn in while_nodes.items():
            ctrl = wn.control_config or wn.metadata.get("control_config", {})
            max_iter = ctrl.get("max_iterations") or wn.metadata.get("max_iterations")
            if max_iter is None or not isinstance(max_iter, int) or max_iter < 1:
                errors.append(f"El nodo WHILE '{wn_id}' debe definir un límite obligatorio de iteraciones ('max_iterations' >= 1).")
            exit_target = ctrl.get("exit_target") or wn.metadata.get("exit_target")
            outgoing = self.get_outgoing_edges(wn_id)
            if not exit_target and not outgoing:
                errors.append(f"El nodo WHILE '{wn_id}' carece de ruta de salida ('exit_target' o aristas salientes).")

        # Separar candidatas a aristas de retorno estructuradas hacia nodos WHILE
        loop_back_edge_ids: Set[str] = {
            edge.edge_id for edge in self.edges if edge.to_node in while_nodes
        }

        # Validación DAG inicial con todas las aristas
        in_degree = {nid: len(self.get_incoming_edges(nid)) for nid in node_ids}
        kahn_queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        processed_count = 0

        while kahn_queue:
            curr = kahn_queue.popleft()
            processed_count += 1
            for nxt in self.get_downstream_node_ids(curr):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    kahn_queue.append(nxt)

        if processed_count != len(node_ids):
            # Probar si los ciclos corresponden exclusivamente a aristas de bucle estructurado a nodos WHILE válidos
            if loop_back_edge_ids:
                in_degree_dag = {
                    nid: len([e for e in self.get_incoming_edges(nid) if e.edge_id not in loop_back_edge_ids])
                    for nid in node_ids
                }
                kahn_queue_dag = deque([nid for nid, deg in in_degree_dag.items() if deg == 0])
                processed_dag = 0
                while kahn_queue_dag:
                    curr = kahn_queue_dag.popleft()
                    processed_dag += 1
                    for edge in self.get_outgoing_edges(curr):
                        if edge.edge_id not in loop_back_edge_ids:
                            nxt = edge.to_node
                            in_degree_dag[nxt] -= 1
                            if in_degree_dag[nxt] == 0:
                                kahn_queue_dag.append(nxt)

                if processed_dag != len(node_ids):
                    errors.append("El grafo contiene dependencias circulares (ciclos) no estructuradas.")
            else:
                errors.append("El grafo contiene dependencias circulares (ciclos) no resolubles.")

        return errors

    def topological_sort(self) -> List[str]:
        """Calcula el orden topológico de ejecución respetando dependencias."""
        errors = self.validate_graph()
        if errors:
            raise ValueError(f"No se puede ordenar un grafo inválido: {'; '.join(errors)}")

        node_ids = set(self.nodes.keys())
        while_nodes = {nid for nid, n in self.nodes.items() if n.node_type == NodeType.WHILE}
        loop_back_edge_ids: Set[str] = {
            edge.edge_id for edge in self.edges if edge.to_node in while_nodes
        }

        in_degree = {
            nid: len([e for e in self.get_incoming_edges(nid) if e.edge_id not in loop_back_edge_ids])
            for nid in node_ids
        }
        kahn_queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        order: List[str] = []

        while kahn_queue:
            curr = kahn_queue.popleft()
            order.append(curr)
            for edge in self.get_outgoing_edges(curr):
                if edge.edge_id not in loop_back_edge_ids:
                    nxt = edge.to_node
                    in_degree[nxt] -= 1
                    if in_degree[nxt] == 0:
                        kahn_queue.append(nxt)

        return order

    def create_execution(
        self,
        execution_id: Optional[str] = None,
        initial_variables: Optional[Dict[str, Any]] = None,
    ) -> "WorkflowExecution":
        """Crea una nueva instancia independiente y aislada de ejecución para este workflow."""
        exec_id = execution_id or f"exec_{uuid.uuid4().hex[:12]}"
        vars_copy = dict(self.variables)
        if initial_variables:
            vars_copy.update(initial_variables)
        return WorkflowExecution(
            execution_id=exec_id,
            workflow_id=self.workflow_id,
            status=WorkflowStatus.IDLE,
            node_states={nid: NodeStatus.PENDING for nid in self.nodes},
            variables=vars_copy,
            node_retries={nid: 0 for nid in self.nodes},
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa la definición a diccionario estructurado."""
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowDefinition":
        """Deserializa un diccionario en una instancia de WorkflowDefinition."""
        clean = dict(data)
        if isinstance(clean.get("created_at"), str):
            clean["created_at"] = datetime.fromisoformat(clean["created_at"])
        if isinstance(clean.get("updated_at"), str):
            clean["updated_at"] = datetime.fromisoformat(clean["updated_at"])
        return cls(**clean)

    def to_yaml(self) -> str:
        """Serializa el workflow a formato YAML estándar."""
        return yaml.dump(self.to_dict(), sort_keys=False, allow_unicode=True)

    @classmethod
    def from_yaml(cls, yaml_content: str) -> "WorkflowDefinition":
        """Instancia un workflow a partir de una cadena YAML."""
        parsed = yaml.safe_load(yaml_content)
        if not isinstance(parsed, dict):
            raise ValueError("El contenido YAML no representa un objeto válido de workflow.")
        return cls.from_dict(parsed)


class WorkflowExecution(BaseModel):
    """Entidad formal e independiente que representa una instancia única de ejecución de un workflow."""
    model_config = ConfigDict(frozen=False)

    execution_id: str = Field(description="Identificador único de la instancia de ejecución")
    workflow_id: str = Field(description="ID del WorkflowDefinition asociado")
    status: WorkflowStatus = Field(default=WorkflowStatus.IDLE)
    node_states: Dict[str, NodeStatus] = Field(default_factory=dict, description="Estado de cada nodo en esta ejecución")
    node_outputs: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Outputs producidos por cada nodo")
    node_retries: Dict[str, int] = Field(default_factory=dict, description="Reintentos por nodo")
    node_started_at: Dict[str, datetime] = Field(default_factory=dict, description="Momento de inicio de ejecución por nodo")
    node_retry_after: Dict[str, datetime] = Field(default_factory=dict, description="Momento a partir del cual el nodo puede reintentarse")
    variables: Dict[str, Any] = Field(default_factory=dict, description="Variables dinámicas de ejecución")
    execution_history: List[str] = Field(default_factory=list, description="Secuencia de nodos ejecutados")
    checkpoints: List[Dict[str, Any]] = Field(default_factory=list, description="Instantáneas de estado para rollback")
    error_message: Optional[str] = Field(default=None)
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def is_active(self) -> bool:
        """Determina si la ejecución está en progreso (RUNNING o PAUSED)."""
        return self.status in (WorkflowStatus.RUNNING, WorkflowStatus.PAUSED)

    def is_terminal(self) -> bool:
        """Determina si la ejecución ha concluido (COMPLETED, FAILED o CANCELLED)."""
        return self.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED)

    def to_dict(self) -> Dict[str, Any]:
        """Serialización canónica a diccionario."""
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowExecution":
        """Deserialización desde diccionario estructurado."""
        clean = dict(data)
        if isinstance(clean.get("started_at"), str):
            clean["started_at"] = datetime.fromisoformat(clean["started_at"])
        if isinstance(clean.get("finished_at"), str):
            clean["finished_at"] = datetime.fromisoformat(clean["finished_at"])
        if isinstance(clean.get("created_at"), str):
            clean["created_at"] = datetime.fromisoformat(clean["created_at"])
        if isinstance(clean.get("node_started_at"), dict):
            clean["node_started_at"] = {
                k: datetime.fromisoformat(v) if isinstance(v, str) else v
                for k, v in clean["node_started_at"].items()
            }
        if isinstance(clean.get("node_retry_after"), dict):
            clean["node_retry_after"] = {
                k: datetime.fromisoformat(v) if isinstance(v, str) else v
                for k, v in clean["node_retry_after"].items()
            }
        return cls(**clean)

