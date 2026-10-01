"""Servidor y Bridge Model Context Protocol (MCP) para conectar con agentes en tiempo real."""

import hmac
import json
import os
import sys
from typing import Any, Dict, List, Optional

from praxeon import __version__
from praxeon.config import JEVConfig, default_config
from praxeon.core.intervention_policy import InterventionPolicy
from praxeon.core.jev_engine import JEVEngine
from praxeon.core.state_graph import StateGraph
from praxeon.domain.action import compute_action_hash
from praxeon.domain.models import ActionCandidate, Goal, ToolCall
from praxeon.models.schema import LoopReport, LoopType, Step, StepType, Trajectory
from praxeon.models.trace import TraceParser
from praxeon.providers.typesafe import TypeSafeAdapter
from praxeon.runtime.navigator import Navigator


class MCPBridge:
    """Implementación de herramientas MCP para supervisión de razonamiento LLM."""

    def __init__(
        self,
        config: Optional[JEVConfig] = None,
        navigator: Optional[Navigator] = None,
    ):
        self.config = config or default_config
        if navigator is not None:
            self.navigator = navigator
        else:
            provider = TypeSafeAdapter(api_key=self.config.typesafe_api_key)
            self.navigator = Navigator(provider=provider)

    def evaluate_next_step(
        self,
        goal: str,
        history: List[Dict[str, Any]],
        proposed_step: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Evalúa un siguiente paso propuesto frente al historial y meta del agente mediante TypeSafe AI."""
        trajectory = Trajectory(
            session_id="mcp_session",
            goal=goal,
            steps=[]
        )
        for idx, h in enumerate(history):
            s_type = StepType(h.get("step_type", "thought"))
            step = Step(
                id=h.get("id", f"hist_{idx}"),
                step_type=s_type,
                content=h.get("content", ""),
                tool_name=h.get("tool_name"),
                tool_args=h.get("tool_args"),
            )
            trajectory.add_step(step)

        graph = StateGraph(self.config)
        graph.load_trajectory(trajectory)

        engine = JEVEngine(graph, self.config)
        policy = InterventionPolicy(graph, self.config)

        # Evaluar candidato
        cand_type = StepType(proposed_step.get("step_type", "thought"))
        cand_step = Step(
            id="proposed_candidate",
            step_type=cand_type,
            content=proposed_step.get("content", ""),
            tool_name=proposed_step.get("tool_name"),
            tool_args=proposed_step.get("tool_args"),
        )
        score = engine.evaluate_step(cand_step)

        ts_eval = score.details.get("typesafe_eval") or {}
        is_loop = bool(ts_eval.get("is_loop", False) or score.total_jev < self.config.critical_jev_threshold)
        loop_type = ts_eval.get("loop_type", LoopType.ONE_HOP_TOOL_REPEAT if is_loop else LoopType.NONE)

        loop_rep = LoopReport(
            loop_detected=is_loop,
            loop_type=loop_type,
            severity=3 if is_loop else 0,
            explanation=f"Evaluación TypeSafe: {'bucle o estancamiento detectado' if is_loop else 'acción constructiva'}",
            culprit_tool=cand_step.tool_name,
        )

        directive = policy.evaluate_and_intervene(
            current_step=cand_step,
            jev_score=score,
            loop_report=loop_rep,
        )

        is_safe = not is_loop and (score.total_jev >= self.config.critical_jev_threshold)

        return {
            "safe": is_safe,
            "total_jev": score.total_jev,
            "p_progress": score.p_progress,
            "delta_u": score.delta_u,
            "loop_penalty": score.loop_penalty,
            "loop_detected": loop_rep.loop_detected,
            "loop_type": loop_rep.loop_type.value,
            "severity": loop_rep.severity,
            "explanation": loop_rep.explanation,
            "directive": directive.model_dump() if directive else None,
        }

    def diagnose_trace(self, trace_data: Dict[str, Any]) -> Dict[str, Any]:
        """Diagnostica una traza completa usando TypeSafe AI y retorna el informe global."""
        trajectory = TraceParser.from_dict(trace_data)
        graph = StateGraph(self.config)
        graph.load_trajectory(trajectory)

        engine = JEVEngine(graph, self.config)
        policy = InterventionPolicy(graph, self.config)

        loop_rep = engine.diagnose_trajectory(trajectory)
        step_scores = []
        for step in trajectory.steps:
            sc = engine.evaluate_step(step)
            step_scores.append({
                "step_id": step.id,
                "type": step.step_type.value,
                "jev": sc.total_jev,
            })

        directive = policy.evaluate_and_intervene(
            current_step=trajectory.steps[-1] if trajectory.steps else None,
            loop_report=loop_rep,
        )

        return {
            "session_id": trajectory.session_id,
            "total_steps": len(trajectory.steps),
            "loop_detected": loop_rep.loop_detected,
            "loop_type": loop_rep.loop_type.value,
            "severity": loop_rep.severity,
            "culprit_tool": loop_rep.culprit_tool,
            "explanation": loop_rep.explanation,
            "step_scores": step_scores,
            "directive": directive.model_dump() if directive else None,
        }

    def evaluate_step_chunk(
        self,
        goal: str,
        history: List[Dict[str, Any]],
        proposed_steps: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Evalúa un bloque agrupado de pasos candidatos para supervisión eficiente y ahorro de RPM/RPD."""
        from praxeon.interceptor.proxy_middleware import JEVProxyMiddleware

        middleware = JEVProxyMiddleware(goal=goal, config=self.config)
        for h in history:
            s_type = StepType(h.get("step_type", "thought"))
            if s_type == StepType.OBSERVATION:
                middleware.record_observation(h.get("content", ""))
            elif s_type == StepType.THOUGHT:
                middleware.record_thought(h.get("content", ""))
            else:
                middleware.graph.add_step(Step(
                    id=h.get("id", f"hist_{len(middleware.graph.get_chronological_nodes())}"),
                    step_type=s_type,
                    content=h.get("content", ""),
                    tool_name=h.get("tool_name"),
                    tool_args=h.get("tool_args"),
                ))

        res = middleware.intercept_step_chunk(proposed_steps)
        return {
            "all_safe": res.all_safe,
            "valid_step_count": res.valid_step_count,
            "flagged_step_index": res.flagged_step_index,
            "hallucination_detected": res.hallucination_detected,
            "hallucination_type": res.hallucination_type,
            "explanation": res.explanation,
            "directive": res.directive.model_dump() if res.directive else None,
            "step_scores": [s.model_dump() for s in res.step_scores],
        }

    def _parse_goal(self, goal_input: Any) -> Goal:
        if isinstance(goal_input, Goal):
            return goal_input
        if isinstance(goal_input, str):
            return Goal(objective=goal_input)
        if isinstance(goal_input, dict):
            return Goal(**goal_input)
        return Goal(objective=str(goal_input))

    def _parse_action(self, action_dict: Dict[str, Any]) -> ActionCandidate:
        tc_data = action_dict.get("tool_call")
        tool_call = None
        if isinstance(tc_data, dict):
            tool_call = ToolCall(**tc_data)
        elif action_dict.get("tool_name"):
            tool_call = ToolCall(
                tool_name=action_dict.get("tool_name"),
                arguments=action_dict.get("tool_args") or action_dict.get("arguments") or {},
            )
        return ActionCandidate(
            id=action_dict.get("id", "act_mcp"),
            description=action_dict.get("description", action_dict.get("content", "Acción MCP")),
            tool_call=tool_call,
            rationale=action_dict.get("rationale"),
            requires_evidence=action_dict.get("requires_evidence", []),
        )

    def v2_start_session(self, goal: Any, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Inicia una sesión de supervisión formal v0.2."""
        g = self._parse_goal(goal)
        state = self.navigator.start_session(g, session_id=session_id)
        return {
            "session_id": state.session_id,
            "goal": state.goal.model_dump(),
            "state_hash": state.compute_hash(),
            "checkpoints_count": len(state.checkpoint_ids),
        }

    def v2_evaluate_action(
        self,
        action: Dict[str, Any],
        goal: Optional[Any] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Evalúa formalmente una acción candidata emitiendo decisión operacional y recibo."""
        if self.navigator.state is None:
            g = self._parse_goal(goal or "Supervisión de agente MCP")
            self.navigator.start_session(g, session_id=session_id)

        act = self._parse_action(action)
        decision, receipt = self.navigator.decide(act)
        return {
            "decision": decision.model_dump(),
            "receipt": receipt.model_dump(),
            "state_hash": self.navigator.state.compute_hash(),
        }

    def v2_step_and_execute(
        self,
        action: Dict[str, Any],
        goal: Optional[Any] = None,
        auto_checkpoint: bool = True,
    ) -> Dict[str, Any]:
        """Evalúa y ejecuta físicamente una acción autorizada con protección estricta."""
        if self.navigator.state is None:
            g = self._parse_goal(goal or "Supervisión de agente MCP")
            self.navigator.start_session(g)

        act = self._parse_action(action)
        decision, obs = self.navigator.step(act, auto_checkpoint=auto_checkpoint)
        return {
            "decision": decision.model_dump(),
            "observation": obs.model_dump() if obs else None,
            "state_hash": self.navigator.state.compute_hash(),
        }

    def v2_rollback(
        self,
        checkpoint_id: Optional[str] = None,
        culprit_tool: Optional[str] = None,
        reason: str = "Rollback solicitado via MCP",
    ) -> Dict[str, Any]:
        """Restaura el estado al checkpoint indicado e invalida descendientes."""
        if self.navigator.state is None:
            raise RuntimeError("No hay sesión activa para rollback.")
        restored = self.navigator.rollback(checkpoint_id=checkpoint_id, culprit_tool=culprit_tool, reason=reason)
        return {
            "restored_step_count": len(restored.steps),
            "forbidden_tools": list(restored.forbidden_tools),
            "state_hash": restored.compute_hash(),
        }

    def v2_get_session_state(self) -> Dict[str, Any]:
        """Devuelve el snapshot completo del estado actual de la sesión."""
        if self.navigator.state is None:
            return {"active": False, "state": None}
        return {
            "active": True,
            "state": self.navigator.state.to_snapshot(),
            "state_hash": self.navigator.state.compute_hash(),
        }

    def v2_confirm_action(
        self,
        action_id: str,
        action_hash: Optional[str] = None,
        operator_token: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Marca una acción sensible como confirmada formalmente por el operador humano (Finding 7)."""
        if self.navigator.state is None:
            raise RuntimeError("No hay sesión activa para confirmar acción.")

        expected_secret = (
            os.getenv("PRAXEON_OPERATOR_KEY")
            or os.getenv("PRAXEON_SECRET_KEY")
            or os.getenv("PRAXEON_API_KEY")
        )
        if expected_secret:
            if not operator_token or not hmac.compare_digest(str(operator_token).strip(), str(expected_secret).strip()):
                raise PermissionError(
                    "Acceso denegado: praxeon_confirm_action requiere un 'operator_token' autenticado del operador humano. "
                    "El agente o cliente MCP no puede auto-aprobar sus propias acciones (Finding 7)."
                )

        if not action_hash or action_hash == "*":
            # Si no se pasó hash, buscarlo en la acción pendiente del estado actual
            target_hash = None
            if self.navigator.state:
                for step in self.navigator.state.steps:
                    if step.action.id == action_id:
                        target_hash = compute_action_hash(step.action)
                        break
            if target_hash:
                action_hash = target_hash
            else:
                raise ValueError(
                    "Acceso denegado: se requiere un 'action_hash' específico coincidente con la acción evaluada "
                    "(no se admiten comodines '*' para confirmar vía MCP - Finding 7)."
                )

        self.navigator.confirm_action(
            action_id=action_id,
            action_hash=action_hash,
            approver_id="authenticated_operator" if expected_secret else "mcp_operator",
            reason=reason or "Confirmado por canal autenticado MCP",
        )
        return {
            "action_id": action_id,
            "action_hash": action_hash,
            "confirmed": True,
            "message": f"Acción '{action_id}' (hash: {action_hash[:8]}...) confirmada formalmente para PolicyEngine.",
        }

    def _handle_request(self, req: Dict[str, Any]) -> None:
        """Procesa una solicitud JSON-RPC individual y escribe la respuesta en stdout."""
        try:
            req_id = req.get("id")
            method = req.get("method")
            params = req.get("params", {})

            if method == "initialize":
                out = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {}
                        },
                        "serverInfo": {
                            "name": "jev-navigator",
                            "version": __version__
                        }
                    }
                }
            elif method == "ping":
                out = {"jsonrpc": "2.0", "id": req_id, "result": {}}
            elif method and method.startswith("notifications/"):
                # Las notificaciones en JSON-RPC no requieren respuesta
                return
            elif method == "tools/list":
                res = {
                    "tools": [
                        # Herramientas primarias PRAXEON 1.0
                        {
                            "name": "praxeon_start_session",
                            "description": "Inicia una sesión de supervisión formal con un objetivo y checkpoint génesis.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "goal": {"type": ["string", "object"]},
                                    "session_id": {"type": "string"},
                                },
                                "required": ["goal"],
                            },
                        },
                        {
                            "name": "praxeon_validate_action",
                            "description": "Valida y evalúa una acción candidata mediante el pipeline normativo de supervisión PRAXEON emitiendo decisión formal y capability auditable.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "object"},
                                    "goal": {"type": ["string", "object"]},
                                    "session_id": {"type": "string"},
                                },
                                "required": ["action"],
                            },
                        },
                        {
                            "name": "praxeon_evaluate_action",
                            "description": "Alias de praxeon_validate_action para evaluar una acción candidata frente a políticas y emitir capability.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "object"},
                                    "goal": {"type": ["string", "object"]},
                                    "session_id": {"type": "string"},
                                },
                                "required": ["action"],
                            },
                        },
                        {
                            "name": "praxeon_step_and_execute",
                            "description": "Evalúa y ejecuta físicamente una acción autorizada con checkpoints automáticos e ingestión de evidencia.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "object"},
                                    "goal": {"type": ["string", "object"]},
                                    "auto_checkpoint": {"type": "boolean"},
                                },
                                "required": ["action"],
                            },
                        },
                        {
                            "name": "praxeon_rollback",
                            "description": "Restaura el estado al último checkpoint seguro (o especificado) e invalida la herramienta reincidente.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "checkpoint_id": {"type": "string"},
                                    "culprit_tool": {"type": "string"},
                                    "reason": {"type": "string"},
                                },
                            },
                        },
                        {
                            "name": "praxeon_get_session_state",
                            "description": "Obtiene el estado serializado y el hash SHA-256 canónico de la sesión actual.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {},
                            },
                        },
                        {
                            "name": "praxeon_confirm_action",
                            "description": "Confirma explícitamente una acción de alto riesgo o que requiere autorización humana (PermissionManager).",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "action_id": {"type": "string"},
                                    "action_hash": {"type": "string", "description": "Hash SHA-256 de la acción a confirmar (requerido para evitar spoofing)"},
                                    "operator_token": {"type": "string", "description": "Token secreto del operador humano para autorizar la confirmación"},
                                    "reason": {"type": "string", "description": "Motivo u observaciones de la aprobación"},
                                },
                                "required": ["action_id", "action_hash", "operator_token"],
                            },
                        },
                        {
                            "name": "praxeon_evaluate_next_step",
                            "description": "Evalúa mediante JEV si la siguiente acción de razonamiento o llamada a herramienta es convergente o degenerativa (bucle).",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "goal": {"type": "string"},
                                    "history": {"type": "array", "items": {"type": "object"}},
                                    "proposed_step": {"type": "object"},
                                },
                                "required": ["goal", "proposed_step"],
                            },
                        },
                        {
                            "name": "praxeon_evaluate_step_chunk",
                            "description": "Evalúa en lote un bloque (chunk) de pasos candidatos para supervisión eficiente, prevención de alucinaciones y ahorro de peticiones.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "goal": {"type": "string"},
                                    "history": {"type": "array", "items": {"type": "object"}},
                                    "proposed_steps": {"type": "array", "items": {"type": "object"}},
                                },
                                "required": ["goal", "proposed_steps"],
                            },
                        },
                        {
                            "name": "praxeon_diagnose_trace",
                            "description": "Diagnostica una traza completa de razonamiento en busca de bucles y estancamiento.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "trace_data": {"type": "object"},
                                },
                                "required": ["trace_data"],
                            },
                        },
                        # Alias secundarios de retrocompatibilidad
                        {
                            "name": "jev_v2_start_session",
                            "description": "Alias legacy de praxeon_start_session.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "goal": {"type": ["string", "object"]},
                                    "session_id": {"type": "string"},
                                },
                                "required": ["goal"],
                            },
                        },
                        {
                            "name": "jev_v2_evaluate_action",
                            "description": "Alias legacy de praxeon_evaluate_action.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "object"},
                                    "goal": {"type": ["string", "object"]},
                                    "session_id": {"type": "string"},
                                },
                                "required": ["action"],
                            },
                        },
                        {
                            "name": "jev_v2_step_and_execute",
                            "description": "Alias legacy de praxeon_step_and_execute.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "object"},
                                    "goal": {"type": ["string", "object"]},
                                    "auto_checkpoint": {"type": "boolean"},
                                },
                                "required": ["action"],
                            },
                        },
                        {
                            "name": "jev_v2_rollback",
                            "description": "Alias legacy de praxeon_rollback.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "checkpoint_id": {"type": "string"},
                                    "culprit_tool": {"type": "string"},
                                    "reason": {"type": "string"},
                                },
                            },
                        },
                        {
                            "name": "jev_v2_get_session_state",
                            "description": "Alias legacy de praxeon_get_session_state.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {},
                            },
                        },
                        {
                            "name": "jev_v2_confirm_action",
                            "description": "Alias legacy de praxeon_confirm_action.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "action_id": {"type": "string"},
                                },
                                "required": ["action_id"],
                            },
                        },
                        {
                            "name": "jev_evaluate_next_step",
                            "description": "Alias legacy de praxeon_evaluate_next_step.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "goal": {"type": "string"},
                                    "history": {"type": "array", "items": {"type": "object"}},
                                    "proposed_step": {"type": "object"},
                                },
                                "required": ["goal", "proposed_step"],
                            },
                        },
                        {
                            "name": "jev_evaluate_step_chunk",
                            "description": "Alias legacy de praxeon_evaluate_step_chunk.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "goal": {"type": "string"},
                                    "history": {"type": "array", "items": {"type": "object"}},
                                    "proposed_steps": {"type": "array", "items": {"type": "object"}},
                                },
                                "required": ["goal", "proposed_steps"],
                            },
                        },
                        {
                            "name": "jev_diagnose_trace",
                            "description": "Alias legacy de praxeon_diagnose_trace.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "trace_data": {"type": "object"},
                                },
                                "required": ["trace_data"],
                            },
                        },
                    ]
                }
                out = {"jsonrpc": "2.0", "id": req_id, "result": res}
            elif method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                if tool_name in ("praxeon_evaluate_next_step", "jev_evaluate_next_step"):
                    result = self.evaluate_next_step(
                        goal=arguments.get("goal", ""),
                        history=arguments.get("history", []),
                        proposed_step=arguments.get("proposed_step", {}),
                    )
                    out = {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}}
                elif tool_name in ("praxeon_evaluate_step_chunk", "jev_evaluate_step_chunk"):
                    result = self.evaluate_step_chunk(
                        goal=arguments.get("goal", ""),
                        history=arguments.get("history", []),
                        proposed_steps=arguments.get("proposed_steps", []),
                    )
                    out = {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}}
                elif tool_name in ("praxeon_diagnose_trace", "jev_diagnose_trace"):
                    result = self.diagnose_trace(arguments.get("trace_data", {}))
                    out = {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}}
                elif tool_name in ("praxeon_start_session", "jev_v2_start_session"):
                    result = self.v2_start_session(
                        goal=arguments.get("goal"),
                        session_id=arguments.get("session_id"),
                    )
                    out = {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}}
                elif tool_name in ("praxeon_validate_action", "praxeon_evaluate_action", "jev_v2_evaluate_action"):
                    result = self.v2_evaluate_action(
                        action=arguments.get("action", {}),
                        goal=arguments.get("goal"),
                        session_id=arguments.get("session_id"),
                    )
                    out = {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}}
                elif tool_name in ("praxeon_step_and_execute", "jev_v2_step_and_execute"):
                    result = self.v2_step_and_execute(
                        action=arguments.get("action", {}),
                        goal=arguments.get("goal"),
                        auto_checkpoint=arguments.get("auto_checkpoint", True),
                    )
                    out = {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}}
                elif tool_name in ("praxeon_rollback", "jev_v2_rollback"):
                    result = self.v2_rollback(
                        checkpoint_id=arguments.get("checkpoint_id"),
                        culprit_tool=arguments.get("culprit_tool"),
                        reason=arguments.get("reason", "Rollback solicitado via MCP"),
                    )
                    out = {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}}
                elif tool_name in ("praxeon_get_session_state", "jev_v2_get_session_state"):
                    result = self.v2_get_session_state()
                    out = {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}}
                elif tool_name in ("praxeon_confirm_action", "jev_v2_confirm_action"):
                    result = self.v2_confirm_action(
                        action_id=arguments.get("action_id", ""),
                        action_hash=arguments.get("action_hash"),
                        operator_token=arguments.get("operator_token"),
                        reason=arguments.get("reason"),
                    )
                    out = {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}}
                else:
                    out = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}
            else:
                out = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method {method} not handled"}}


            sys.stdout.write(json.dumps(out) + "\n")

            sys.stdout.flush()
        except Exception as e:
            err_resp = {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32603, "message": str(e)}}
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()

    def run_stdio_server(self) -> None:
        """Bucle de servidor MCP estándar JSON-RPC 2.0 sobre stdin/stdout con soporte de framing robusto."""
        if hasattr(sys.stdin, "reconfigure"):
            try:
                sys.stdin.reconfigure(encoding="utf-8")
            except Exception:
                pass
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass

        buffer = ""
        content_length: Optional[int] = None

        while True:
            # Framing estilo Content-Length (LSP / MCP HTTP/stdio header)
            if content_length is not None:
                body = sys.stdin.read(content_length)
                content_length = None
                if not body:
                    break
                try:
                    req = json.loads(body)
                except Exception as e:
                    err_resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {e}"}}
                    sys.stdout.write(json.dumps(err_resp) + "\n")
                    sys.stdout.flush()
                    continue
                self._handle_request(req)
                continue

            line = sys.stdin.readline()
            if not line:
                break

            stripped = line.strip()
            # Ignorar silenciosamente líneas vacías/en blanco (ej. enter en consola)
            if not buffer and not stripped:
                continue

            # Detectar encabezados estilo Content-Length
            if stripped.lower().startswith("content-length:"):
                parts = stripped.split(":")
                try:
                    content_length = int(parts[1].strip())
                except ValueError:
                    content_length = None
                # Consumir líneas de encabezado restantes hasta la línea vacía
                while True:
                    h_line = sys.stdin.readline()
                    if not h_line or not h_line.strip():
                        break
                continue

            buffer += line
            try:
                req = json.loads(buffer)
                buffer = ""  # Parse exitoso, reiniciar buffer
            except json.JSONDecodeError:
                # Si el contenido no empieza con estructura JSON, reportar y reiniciar
                if not buffer.strip().startswith(("{", "[")):
                    err_resp = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {buffer.strip()}"},
                    }
                    sys.stdout.write(json.dumps(err_resp) + "\n")
                    sys.stdout.flush()
                    buffer = ""
                # Si empieza con '{' o '[', continuar acumulando líneas para JSON multilínea
                continue

            self._handle_request(req)


if __name__ == "__main__":
    bridge = MCPBridge()
    bridge.run_stdio_server()
