"""Misiones gobernadas por un workflow.

Cuando el usuario lanza una tarea con un workflow activo, el grafo manda: el `WorkflowEngine` decide qué nodo toca,
los bucles, las bifurcaciones y las aprobaciones, y cada nodo de agente/tarea se ejecuta como una *mini-misión*
supervisada (el mismo bucle ReAct + política que una misión normal) con un briefing completo:

- objetivo global de la tarea,
- la misión concreta del nodo y el rol del agente,
- el resultado íntegro de los nodos de los que depende (quién lo produjo y con qué misión),
- su intento anterior y el veredicto recibido cuando el nodo se repite dentro de un bucle,
- quién recibirá su resultado después.

El estado de los nodos se persiste tras cada paso, de modo que la UI sigue mostrando el avance por nodos.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set

from praxeon.workflows.engine import WorkflowEngine
from praxeon.workflows.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)

AGENT_NODE_TYPES = (NodeType.AGENT, NodeType.DELEGATE, NodeType.TASK)
TERMINAL = (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED)
RESERVED_OUTPUT_KEYS = {"agent_id", "agent_name", "agent_role", "node_mission", "response", "status", "steps", "iteration", "simulated"}
DEFAULT_NODE_STEPS = 30
DEFAULT_AUTO_NODE_RETRIES = 2  # reintentos automáticos de un nodo fallido en misiones autónomas
MAX_NODE_STEPS = 500
DEFAULT_TOTAL_NODE_RUNS = 200


def node_mission(node: WorkflowNode) -> str:
    """Misión textual del nodo: metadata.mission > inputs.mission/instruction/task/prompt > nombre."""
    for source in (node.metadata, node.inputs):
        for key in ("mission", "instruction", "task", "prompt"):
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, str) and value.strip():
                return value.strip()
    return node.name


def _json_objects(text: str) -> List[Dict[str, Any]]:
    """Todos los objetos JSON que aparecen en el texto, estén donde estén (entre comillas inversas, en prosa, en bloques)."""
    decoder = json.JSONDecoder()
    found: List[Dict[str, Any]] = []
    i = 0
    while True:
        i = text.find("{", i)
        if i == -1:
            break
        try:
            data, end = decoder.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(data, dict):
            found.append(data)
            i = end
        else:
            i += 1
    return found


def parse_structured_output(text: str, prefer_keys: Optional[Set[str]] = None) -> Dict[str, Any]:
    """Extrae el resultado estructurado de la respuesta final para que las condiciones del grafo lo lean.

    Acepta el JSON entero, en un bloque ```json```, entre comillas inversas o suelto dentro de un informe en prosa.
    Con `prefer_keys` se elige el objeto que trae esos campos (p. ej. `approved`); si el texto solo escribe
    «approved: false» sin JSON, se recupera ese valor booleano.
    """
    if not text:
        return {}
    objects = [o for o in _json_objects(text) if o]
    wanted = {k for k in (prefer_keys or set()) if k}
    if wanted:
        with_keys = [o for o in objects if wanted & set(o)]
        if with_keys:
            return with_keys[-1]
        loose: Dict[str, Any] = {}
        for key in wanted:
            m = re.findall(r"""["'`*_]*%s["'`*_]*\s*[:=]\s*["'`*_]*(true|false)\b""" % re.escape(key), text, flags=re.IGNORECASE)
            if m:
                loose[key] = m[-1].lower() == "true"
        if loose:
            return loose
    stripped = text.strip()
    try:
        whole = json.loads(stripped)
        if isinstance(whole, dict):
            return whole
    except (ValueError, TypeError):
        pass
    return objects[-1] if objects else {}


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    return f"{text[:head]}\n[... {len(text) - limit} caracteres intermedios omitidos solo en este prompt; el texto completo queda guardado en el workflow ...]\n{text[-tail:]}"


class WorkflowMissionDriver:
    """Ejecuta un workflow como misión: el grafo gobierna y los agentes trabajan dentro de cada nodo."""

    def __init__(
        self,
        *,
        workflow: WorkflowDefinition,
        mission: Dict[str, Any],
        goal: str,
        agent_registry: Any,
        workflow_service: Any,
        run_agent_loop: Callable[..., Dict[str, Any]],
        llm_for_agent: Callable[[Any], Any],
        supervised_runner: Any = None,
        poll_seconds: float = 0.3,
        on_node_completed: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_node_started: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self.workflow = workflow
        self.mission = mission
        self.goal = goal
        self.agent_registry = agent_registry
        self.workflow_service = workflow_service
        self.run_agent_loop = run_agent_loop
        self.llm_for_agent = llm_for_agent
        self.poll_seconds = poll_seconds
        self.on_node_completed = on_node_completed
        self.on_node_started = on_node_started
        self.runs: Dict[str, int] = {}
        self._cursor: Optional[str] = None  # última acción del nodo anterior: de ahí cuelga el siguiente
        self._prev_trace: Optional[Dict[str, Any]] = None
        self.history: Dict[str, List[Dict[str, Any]]] = {}
        self.failures: Dict[str, str] = {}      # último error de cada nodo que se reintenta
        self.auto_retried: Dict[str, int] = {}  # reintentos automáticos ya gastados por nodo
        self.max_auto_retries = int(self.mission.get("auto_node_retries", DEFAULT_AUTO_NODE_RETRIES))
        self.total_runs = 0
        self.max_total_runs = int(workflow.metadata.get("max_total_node_runs") or DEFAULT_TOTAL_NODE_RUNS)
        sid = mission["session_id"]
        self.engine = WorkflowEngine(
            workflow,
            execution_id=f"wfx-{sid}",
            node_runner=self._run_node,
            supervised_runner=supervised_runner,
        )

    # ------------------------------------------------------------------ briefing
    def _agent_for(self, node: WorkflowNode, inputs: Dict[str, Any]) -> Any:
        agent_id = inputs.get("_target_agent_id") or node.agent_id
        if not agent_id or self.agent_registry is None:
            return None
        try:
            agent = self.agent_registry.get(agent_id)
            if agent is not None:
                return agent
            # Workflows guardados con el nombre visible («Nombre — Rol») en lugar del id
            wanted = str(agent_id).strip().lower()
            for cand in self.agent_registry.list_all():
                labels = {cand.name.lower(), f"{cand.name} \u2014 {cand.role}".lower(), f"{cand.name} - {cand.role}".lower()}
                if wanted in labels:
                    return cand
        except Exception:
            logger.debug("Agente '%s' no disponible en el registro", agent_id, exc_info=True)
        return None

    def _label(self, node: WorkflowNode) -> str:
        agent = self._agent_for(node, {})
        who = f"agente {agent.name} ({agent.role})" if agent else (f"agente {node.agent_id}" if node.agent_id else "tarea")
        return f"«{node.name}» ({node.node_id}, {who})"

    def effective_upstream(self, node_id: str) -> List[str]:
        """Nodos de trabajo (agente/tarea) de los que depende `node_id`, atravesando nodos de control."""
        found: List[str] = []
        seen: Set[str] = set()

        def walk(nid: str) -> None:
            for up in self.workflow.get_upstream_node_ids(nid):
                if up in seen or up == node_id:
                    continue
                seen.add(up)
                up_node = self.workflow.nodes[up]
                if up_node.node_type in AGENT_NODE_TYPES:
                    if up not in found:
                        found.append(up)
                else:
                    walk(up)

        walk(node_id)
        return found

    @staticmethod
    def _condition_field(cond: Any) -> str:
        if cond is None:
            return ""
        if isinstance(cond, dict):
            return str(cond.get("field") or "")
        return str(getattr(cond, "field", "") or "")

    def required_output_keys(self, node_id: str) -> List[str]:
        """Campos del resultado de `node_id` de los que depende el grafo (bucles, decisiones, condiciones de aristas)."""
        keys: List[str] = []
        seen: Set[str] = set()
        control = (NodeType.WHILE, NodeType.IF, NodeType.DECISION)

        def key_of(cond: Any) -> Optional[str]:
            f = self._condition_field(cond)
            if f.startswith("output."):
                return f[len("output."):]
            if f.startswith("outputs."):
                parts = f.split(".")
                if len(parts) >= 3 and parts[1] == node_id:
                    return ".".join(parts[2:])
            return None

        def add(cond: Any) -> None:
            k = key_of(cond)
            if k and k not in keys:
                keys.append(k)

        def walk(nid: str) -> None:
            if nid in seen:
                return
            seen.add(nid)
            for edge in self.workflow.edges:
                if edge.from_node != nid:
                    continue
                add(getattr(edge, "condition", None))
                target = self.workflow.nodes.get(edge.to_node)
                if target is not None and target.node_type in control:
                    add((target.control_config or {}).get("condition"))
                    walk(target.node_id)

        walk(node_id)
        return keys

    def effective_downstream(self, node_id: str) -> List[str]:
        found: List[str] = []
        seen: Set[str] = set()

        def walk(nid: str) -> None:
            for down in self.workflow.get_downstream_node_ids(nid):
                if down in seen or down == node_id:
                    continue
                seen.add(down)
                down_node = self.workflow.nodes[down]
                if down_node.node_type in AGENT_NODE_TYPES:
                    if down not in found:
                        found.append(down)
                elif down_node.node_type != NodeType.END:
                    walk(down)

        walk(node_id)
        return found

    def build_briefing(self, node: WorkflowNode, inputs: Dict[str, Any], iteration: int) -> str:
        agent = self._agent_for(node, inputs)
        outputs = self.engine.context.node_outputs
        parts: List[str] = [
            "=== OBJETIVO GLOBAL DE LA TAREA DEL USUARIO ===",
            self.goal,
            "",
            f"=== TU PAPEL EN EL WORKFLOW «{self.workflow.name}» ===",
            f"Nodo: {node.name} ({node.node_id})"
            + (f" · Agente: {agent.name} · Rol: {agent.role}" if agent else ""),
            f"Iteración de este nodo: {iteration}",
            f"TU MISIÓN EN ESTE NODO: {node_mission(node)}",
        ]
        expected = node.metadata.get("expected_output") if isinstance(node.metadata, dict) else None
        if expected:
            parts.append(f"FORMATO DE RESULTADO ESPERADO: {expected}")

        upstream = [u for u in self.effective_upstream(node.node_id) if outputs.get(u, {}).get("response") is not None]
        parts += ["", "=== INFORMACIÓN RECIBIDA DE NODOS ANTERIORES ==="]
        if not upstream:
            parts.append("(ninguna: eres el primer nodo de trabajo del flujo)")
        for up_id in upstream:
            up_node = self.workflow.nodes[up_id]
            out = outputs[up_id]
            parts += [
                f"--- De {self._label(up_node)} ---",
                f"Misión de ese nodo: {node_mission(up_node)}",
                "Resultado completo:",
                _clip(str(out.get("response", "")), 12000),
            ]
            structured = {k: v for k, v in out.items() if k not in RESERVED_OUTPUT_KEYS}
            if structured:
                parts.append(f"Campos estructurados: {json.dumps(structured, ensure_ascii=False, default=str)[:2000]}")

        previous = self.history.get(node.node_id) or []
        if previous:
            last = previous[-1]
            parts += [
                "",
                f"=== TU INTENTO ANTERIOR EN ESTE NODO (iteración {last['iteration']}) ===",
                _clip(str(last.get("response", "")), 6000),
                "Corrige o mejora según la información recibida de los nodos anteriores (p. ej. los comentarios de un revisor).",
            ]

        failure = self.failures.get(node.node_id)
        if failure:
            parts += [
                "",
                "=== TU INTENTO ANTERIOR FUE INTERRUMPIDO ===",
                _clip(failure, 1500),
                "Lo que ya escribiste en el directorio de trabajo sigue ahí: usa list_dir y read_file para ver tu avance y continúa "
                "desde ahí en lugar de empezar de cero. Cambia de enfoque en lo que falló; no repitas la misma acción.",
            ]

        required = self.required_output_keys(node.node_id)
        if required:
            example = json.dumps({k: "<valor>" for k in required}, ensure_ascii=False)
            parts += [
                "",
                "=== FORMATO OBLIGATORIO DE TU RESULTADO ===",
                f"El flujo decide su siguiente paso leyendo estos campos de tu resultado: {', '.join(required)}.",
                f"Tu finish DEBE incluir un objeto JSON con ellos, por ejemplo {example}. "
                "Los booleanos van como true o false. El informe detallado puede ir en un campo \"feedback\" o junto al JSON.",
            ]

        downstream = self.effective_downstream(node.node_id)
        if downstream:
            parts += ["", "=== QUIÉN RECIBIRÁ TU RESULTADO ==="]
            for d in downstream:
                parts.append(f"- {self._label(self.workflow.nodes[d])}: {node_mission(self.workflow.nodes[d])}")

        parts += [
            "",
            "=== INSTRUCCIONES ===",
            "Trabaja SOLO en tu misión. Termina con finish(summary=\"...\") entregando tu resultado COMPLETO y autosuficiente:",
            "el siguiente agente no verá tu razonamiento ni tus herramientas, solo ese resumen.",
            "Si tu misión consiste en emitir un veredicto, haz que el resumen sea un objeto JSON con los campos pedidos"
            " (p. ej. {\"approved\": true, \"feedback\": \"...\"}).",
        ]
        return "\n".join(parts)

    # ------------------------------------------------------------------ ejecución de nodos
    def _finish_check(self, node: WorkflowNode) -> Optional[Callable[[str], Optional[str]]]:
        """Rechaza el finish de un nodo si no trae los campos de los que depende el grafo (p. ej. `approved`)."""
        required = self.required_output_keys(node.node_id)
        if not required:
            return None
        wanted = set(required)

        def check(summary: str) -> Optional[str]:
            got = parse_structured_output(summary, prefer_keys=wanted)
            missing = [k for k in required if k not in got]
            if not missing:
                return None
            example = json.dumps({k: ("true o false" if k == "approved" else "<valor>") for k in required}, ensure_ascii=False)
            return (
                "🚨 [SUPERVISOR PRAXEON - RESULTADO INCOMPLETO]: el flujo necesita estos campos en tu resultado y faltan: "
                f"{', '.join(missing)}.\n"
                f"Vuelve a llamar a finish incluyendo un objeto JSON como {example}; el informe completo puede ir en un campo "
                '"feedback" o antes del JSON.'
            )

        return check

    def _tool_filter(self, agent: Any) -> Optional[Callable[[str], Optional[str]]]:
        if agent is None:
            return None
        allowed = [t.lower() for t in (agent.allowed_tools or ["*"])]
        forbidden = {t.lower() for t in (agent.forbidden_tools or [])}
        if "*" in allowed and not forbidden:
            return None

        def check(tool: str) -> Optional[str]:
            t = (tool or "").lower()
            if t in forbidden:
                return f"❌ El agente «{agent.name}» tiene PROHIBIDA la herramienta '{tool}'. Usa otra de las permitidas o concluye con finish."
            if "*" not in allowed and t not in allowed:
                return (
                    f"❌ La herramienta '{tool}' no está permitida para el agente «{agent.name}». "
                    f"Herramientas permitidas: {', '.join(agent.allowed_tools)} (y finish)."
                )
            return None

        return check

    def _run_node(self, node: WorkflowNode, inputs: Dict[str, Any], engine: WorkflowEngine) -> Dict[str, Any]:
        if self.mission.get("stopped"):
            raise RuntimeError("Misión detenida por el operador")
        self.total_runs += 1
        if self.total_runs > self.max_total_runs:
            raise RuntimeError(
                f"Se superó el máximo de {self.max_total_runs} ejecuciones de nodos en el workflow (posible bucle sin salida)."
            )
        iteration = self.runs.get(node.node_id, 0) + 1
        self.runs[node.node_id] = iteration
        agent = self._agent_for(node, inputs)
        configured = inputs.get("_target_agent_id") or node.agent_id
        if configured and agent is None and self.agent_registry is not None:
            raise RuntimeError(
                f"El agente '{configured}' configurado en el nodo '{node.name}' no está registrado. "
                "Elige un agente existente en el editor del workflow."
            )
        if self.on_node_started:
            try:
                self.on_node_started(
                    node.node_id,
                    {
                        "node_id": node.node_id,
                        "node_name": node.name,
                        "agent_id": agent.agent_id if agent else node.agent_id,
                        "agent_name": (agent.name if agent else None) or node.agent_id or "Agente",
                        "agent_role": agent.role if agent else None,
                        "iteration": iteration,
                        "handoff_from": (self._prev_trace or {}).get("agent_name"),
                        "parent_id": self._cursor,
                        "retry_reason": self.failures.get(node.node_id),
                    },
                )
            except Exception:
                logger.debug("No se pudo emitir el inicio del nodo", exc_info=True)
        briefing = self.build_briefing(node, inputs, iteration)
        llm = self.llm_for_agent(agent)

        base: Dict[str, Any] = {
            "agent_id": agent.agent_id if agent else node.agent_id,
            "agent_name": agent.name if agent else None,
            "agent_role": agent.role if agent else None,
            "node_mission": node_mission(node),
            "iteration": iteration,
        }

        if llm is None:
            # Sin LLM real (simulador): el flujo se recorre y se ve por nodos, sin trabajo de modelo
            response = f"[simulado] {node_mission(node)}"
            output = {**base, "response": response, "status": "completed", "steps": 0, "simulated": True}
        else:
            preamble = ""
            if agent is not None:
                preamble = (
                    f"IDENTIDAD DEL AGENTE: {agent.name} — rol {agent.role}.\n{agent.system_prompt.strip()}\n\n"
                    "Actúas como un nodo de un workflow orquestado por PRAXEON; sigue exactamente la misión de tu nodo.\n\n"
                )
            trace = {
                "node_id": node.node_id,
                "node_name": node.name,
                "agent_id": base["agent_id"],
                "agent_name": base["agent_name"] or base["agent_id"] or "Agente",
                "agent_role": base["agent_role"],
                "iteration": iteration,
                "handoff_from": (self._prev_trace or {}).get("agent_name") if self._prev_trace else None,
            }
            steps_cfg = node.metadata.get("max_steps") if isinstance(node.metadata, dict) else None
            # metadata.max_steps: ausente = por defecto; 0 = sin límite; N = máximo de N pasos (tope MAX_NODE_STEPS)
            if steps_cfg is None or steps_cfg == "":
                # sin ajuste propio, el nodo hereda «sin límite» si la misión se lanzó así
                max_steps = 0 if int(self.mission.get("max_steps") or 0) == 0 and "max_steps" in self.mission else DEFAULT_NODE_STEPS
            elif int(steps_cfg) <= 0:
                max_steps = 0
            else:
                max_steps = min(int(steps_cfg), MAX_NODE_STEPS)
            result = self.run_agent_loop(
                self.mission,
                llm,
                goal=briefing,
                max_steps=max_steps,
                delay_sec=max(0.0, self.mission.get("step_delay_ms", 900) / 1000.0),
                provider_name=getattr(llm, "provider_name", "llm"),
                session_mode=self.mission.get("execution_mode") or "local_restricted",
                id_prefix=f"{node.node_id}_i{iteration}_",
                owns_session=False,
                role_preamble=preamble,
                tool_filter=self._tool_filter(agent),
                trace=trace,
                start_parent_id=self._cursor,
                finish_check=self._finish_check(node),
            )
            if result.get("last_action_id"):
                self._cursor = result["last_action_id"]
                self._prev_trace = trace
            if self.mission.get("stopped"):
                raise RuntimeError("Misión detenida por el operador")
            if result.get("forced_termination"):
                raise RuntimeError(
                    f"El supervisor interrumpió al agente del nodo '{node.node_id}' (bucle de acciones sin fundamento): {result.get('final_answer')}"
                )
            response = result.get("final_answer")
            if not response:
                raise RuntimeError(
                    f"El agente del nodo '{node.node_id}' no concluyó su misión en {max_steps} pasos (sin finish válido)."
                )
            output = {**base, "response": str(response), "status": "completed", "steps": result.get("steps_used", 0)}

        structured = parse_structured_output(output["response"], prefer_keys=set(self.required_output_keys(node.node_id)))
        for key, value in structured.items():
            if key not in RESERVED_OUTPUT_KEYS:
                output[key] = value
        self.history.setdefault(node.node_id, []).append({"iteration": iteration, "response": output["response"]})
        self.failures.pop(node.node_id, None)
        if self.on_node_completed:
            try:
                self.on_node_completed(
                    node.node_id,
                    {
                        "node_id": node.node_id,
                        "node_name": node.name,
                        "agent_id": base["agent_id"],
                        "agent_name": base["agent_name"] or base["agent_id"],
                        "agent_role": base["agent_role"],
                        "iteration": iteration,
                        "steps": output.get("steps", 0),
                        "summary": output["response"],
                        "simulated": bool(output.get("simulated")),
                    },
                )
            except Exception:
                logger.debug("No se pudo emitir el hito del nodo", exc_info=True)
        return output

    # ------------------------------------------------------------------ bucle del grafo
    def _sync(self) -> None:
        try:
            self.workflow_service.sync_engine(self.engine)
        except Exception:
            logger.warning("No se pudo sincronizar el estado del workflow", exc_info=True)

    def run(self) -> bool:
        """Recorre el grafo hasta un estado terminal. Devuelve True si el workflow terminó con éxito."""
        engine = self.engine
        engine.start(initial_variables={"goal": self.goal, "workspace_root": self.mission.get("workspace_root")})
        self.workflow_service.register_external_engine(engine)
        self.mission["workflow_execution_id"] = engine.context.execution_id
        self._sync()
        stalled = 0
        try:
            while True:
                if self.mission.get("stopped"):
                    engine.cancel("Misión detenida por el operador")
                    break
                while self.mission.get("paused") and not self.mission.get("stopped"):
                    time.sleep(0.2)
                if engine.context.status == WorkflowStatus.FAILED and self._await_failure_decision():
                    self._sync()
                    continue
                if engine.context.status in TERMINAL:
                    break
                if engine.check_global_timeout():
                    break
                try:
                    stepped = engine.step()
                except Exception as exc:
                    logger.error("Error avanzando el workflow %s: %s", engine.context.execution_id, exc, exc_info=True)
                    engine.context.status = WorkflowStatus.FAILED
                    engine.context.error_message = f"Error del motor: {exc}"
                    break
                self._sync()
                if stepped is not None:
                    stalled = 0
                    continue
                waiting = any(
                    st in (NodeStatus.WAITING_APPROVAL, NodeStatus.WAITING_RESULT, NodeStatus.RETRYING)
                    for st in engine.context.node_states.values()
                )
                if waiting:
                    stalled = 0
                else:
                    stalled += 1
                    if stalled >= 5 and engine.context.status not in TERMINAL:
                        engine.context.status = WorkflowStatus.FAILED
                        engine.context.error_message = "El workflow quedó bloqueado: no hay nodos listos ni en espera."
                        break
                time.sleep(self.poll_seconds)
        finally:
            self._sync()
            self.workflow_service.unregister_external_engine(engine.context.execution_id)
        return engine.context.status == WorkflowStatus.COMPLETED

    def _unattended(self) -> bool:
        """Misión autónoma (Full Access confirmado por el operador): no se pide intervención paso a paso."""
        m = self.mission
        return bool((m.get("autonomous") or m.get("allow_unattended_execution")) and not m.get("require_step_confirmation"))

    def _failed_node(self) -> Optional[str]:
        for nid, st in self.engine.context.node_states.items():
            if st == NodeStatus.FAILED:
                return nid
        return None

    def _await_failure_decision(self) -> bool:
        """Un nodo agotó sus reintentos: la misión se detiene y pide decisión (retry | skip | abort).

        Devuelve True si el operador reanudó el flujo (retry/skip); False si abortó, se detuvo la misión o no hay nodo fallido.
        """
        engine = self.engine
        nid = self._failed_node()
        if nid is None:
            return False
        node = self.workflow.nodes[nid]
        error = (engine.context.node_outputs.get(nid) or {}).get("error") or engine.context.error_message or "error desconocido"
        with engine._lock:
            engine.context.variables["_pending_failure"] = {"node_id": nid, "error": str(error)}
            engine.context.variables.pop("_failure_decision", None)
        self.failures[nid] = str(error)
        if self._unattended() and self.auto_retried.get(nid, 0) < self.max_auto_retries:
            # Full Access autónomo: el operador no interviene hasta que el workflow agote sus recursos
            self.auto_retried[nid] = self.auto_retried.get(nid, 0) + 1
            logger.warning(
                "Nodo '%s' fallido (%s): reintento automático %d/%d", nid, error, self.auto_retried[nid], self.max_auto_retries
            )
            with engine._lock:
                engine.context.variables.pop("_pending_failure", None)
            engine.context.node_retries[nid] = 0
            return engine.retry_node(nid)
        self._sync()
        self.workflow_service.announce_failure(engine, nid, node.name, str(error))
        timeout = self.mission.get("approval_timeout_seconds")
        deadline = time.time() + float(timeout) if timeout else None
        decision = None
        while True:
            if self.mission.get("stopped"):
                break
            with engine._lock:
                chosen = engine.context.variables.get("_failure_decision")
            if chosen and chosen.get("node_id") == nid:
                decision = chosen.get("action")
                break
            if deadline is not None and time.time() >= deadline:
                break
            time.sleep(self.poll_seconds)
        with engine._lock:
            engine.context.variables.pop("_pending_failure", None)
            engine.context.variables.pop("_failure_decision", None)
        if decision == "retry":
            engine.context.node_retries[nid] = 0
            return engine.retry_node(nid)
        if decision == "skip":
            return engine.skip_node(nid)
        return False

    def final_answer(self) -> str:
        """Respuesta final: la del último nodo de trabajo completado, o el motivo del fallo."""
        ctx = self.engine.context
        if ctx.status != WorkflowStatus.COMPLETED:
            return ctx.error_message or f"El workflow terminó en estado {ctx.status.value}."
        for nid in reversed(ctx.execution_history):
            node = self.workflow.nodes.get(nid)
            out = ctx.node_outputs.get(nid) or {}
            if node and node.node_type in AGENT_NODE_TYPES and out.get("response"):
                return str(out["response"])
        return f"Workflow «{self.workflow.name}» completado."
