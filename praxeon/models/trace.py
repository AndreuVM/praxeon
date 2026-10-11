"""Parsers de trazas de razonamiento para múltiples formatos de LLM y agentes."""

import json
import re
import uuid
from typing import Any, Dict, List, Optional
from .schema import Step, StepType, Trajectory


class TraceParser:
    """Parser multiformato para transformar logs y secuencias CoT en Trajectory."""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Trajectory:
        """Crea una trayectoria a partir de un diccionario estructurado."""
        session_id = data.get("session_id", f"session_{uuid.uuid4().hex[:8]}")
        goal = data.get("goal", "Resolver la tarea solicitada")
        raw_steps = data.get("steps", [])

        steps: List[Step] = []
        for idx, item in enumerate(raw_steps):
            if isinstance(item, Step):
                steps.append(item)
            elif isinstance(item, dict):
                step_id = item.get("id", f"step_{idx}")
                step_type_str = item.get("step_type", "thought").lower()
                step_type = StepType(step_type_str) if step_type_str in StepType._value2member_map_ else StepType.THOUGHT
                step = Step(
                    id=step_id,
                    step_type=step_type,
                    content=item.get("content", ""),
                    tool_name=item.get("tool_name"),
                    tool_args=item.get("tool_args"),
                    parent_id=item.get("parent_id", steps[-1].id if steps else None),
                    metadata=item.get("metadata", {}),
                )
                steps.append(step)

        return Trajectory(
            session_id=session_id,
            goal=goal,
            steps=steps,
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> Trajectory:
        """Parsea una cadena JSON."""
        data = json.loads(json_str)
        if isinstance(data, list):
            return cls.from_dict({"steps": data})
        return cls.from_dict(data)

    @classmethod
    def from_scratchpad(cls, text: str, goal: str = "Objetivo no especificado") -> Trajectory:
        """Parsea un scratchpad de texto plano con patrones CoT (Thought / Action / Observation / XML tags)."""
        trajectory = Trajectory(
            session_id=f"scratch_{uuid.uuid4().hex[:8]}",
            goal=goal,
            steps=[]
        )

        # 1. Intentar parsear por tags XML: <thought>, <tool_call>, <observation>
        tag_pattern = re.compile(r"<(thought|tool_call|observation|response)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
        matches = list(tag_pattern.finditer(text))

        if matches:
            for idx, match in enumerate(matches):
                tag_name = match.group(1).lower()
                content = match.group(2).strip()
                step_type = StepType(tag_name)

                tool_name = None
                tool_args = None
                if step_type == StepType.TOOL_CALL:
                    # Intentar extraer JSON si viene formateado
                    try:
                        parsed_json = json.loads(content)
                        if isinstance(parsed_json, dict):
                            tool_name = parsed_json.get("name") or parsed_json.get("tool")
                            tool_args = parsed_json.get("args") or parsed_json.get("arguments") or parsed_json
                    except Exception:
                        tool_name = content.split()[0] if content else "unknown_tool"

                step = Step(
                    id=f"step_{idx}",
                    step_type=step_type,
                    content=content,
                    tool_name=tool_name,
                    tool_args=tool_args if isinstance(tool_args, dict) else None,
                    parent_id=trajectory.steps[-1].id if trajectory.steps else None,
                )
                trajectory.add_step(step)
            return trajectory

        # 2. Parsear formato ReAct tradicional: Thought: ... \n Action: ... \n Action Input: ... \n Observation: ...
        lines = text.splitlines()
        current_type = StepType.THOUGHT
        current_content: List[str] = []
        current_tool: Optional[str] = None
        current_args: Optional[Dict[str, Any]] = None

        def flush_step():
            nonlocal current_content, current_tool, current_args, current_type
            if current_content or current_tool:
                idx = len(trajectory.steps)
                step = Step(
                    id=f"step_{idx}",
                    step_type=current_type,
                    content="\n".join(current_content).strip(),
                    tool_name=current_tool,
                    tool_args=current_args,
                    parent_id=trajectory.steps[-1].id if trajectory.steps else None,
                )
                trajectory.add_step(step)
                current_content = []
                current_tool = None
                current_args = None

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("Thought:"):
                flush_step()
                current_type = StepType.THOUGHT
                current_content.append(stripped[len("Thought:"):].strip())
            elif stripped.startswith("Action:"):
                flush_step()
                current_type = StepType.TOOL_CALL
                current_tool = stripped[len("Action:"):].strip()
            elif stripped.startswith("Action Input:"):
                raw_args = stripped[len("Action Input:"):].strip()
                try:
                    current_args = json.loads(raw_args)
                except Exception:
                    current_args = {"input": raw_args}
            elif stripped.startswith("Observation:"):
                flush_step()
                current_type = StepType.OBSERVATION
                current_content.append(stripped[len("Observation:"):].strip())
            else:
                if stripped:
                    current_content.append(stripped)

        flush_step()

        # Si no hubo patrones reconocibles, tratar todo como un pensamiento
        if not trajectory.steps and text.strip():
            trajectory.add_step(
                Step(
                    id="step_0",
                    step_type=StepType.THOUGHT,
                    content=text.strip(),
                )
            )

        return trajectory

    @classmethod
    def from_openai_messages(cls, messages: List[Dict[str, Any]], goal: Optional[str] = None) -> Trajectory:
        """Parsea un array de mensajes con formato de la API de OpenAI."""
        steps: List[Step] = []
        detected_goal = goal or ""

        for idx, msg in enumerate(messages):
            role = msg.get("role", "")
            content = msg.get("content") or ""

            if role == "user" and not detected_goal:
                detected_goal = content if isinstance(content, str) else str(content)
                step = Step(
                    id=f"step_{idx}",
                    step_type=StepType.USER_INPUT,
                    content=detected_goal,
                )
                steps.append(step)
                continue

            if role == "tool":
                step = Step(
                    id=f"step_{idx}",
                    step_type=StepType.OBSERVATION,
                    content=str(content),
                    parent_id=steps[-1].id if steps else None,
                )
                steps.append(step)
                continue

            if role == "assistant":
                # Comprobar tool_calls
                tool_calls = msg.get("tool_calls", [])
                if content:
                    steps.append(
                        Step(
                            id=f"step_{len(steps)}",
                            step_type=StepType.THOUGHT,
                            content=str(content),
                            parent_id=steps[-1].id if steps else None,
                        )
                    )
                for tc in tool_calls:
                    func = tc.get("function", {})
                    fn_name = func.get("name", "tool")
                    raw_args = func.get("arguments", "{}")
                    try:
                        args_dict = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        args_dict = {"raw": raw_args}

                    steps.append(
                        Step(
                            id=f"step_{len(steps)}",
                            step_type=StepType.TOOL_CALL,
                            content="",
                            tool_name=fn_name,
                            tool_args=args_dict,
                            parent_id=steps[-1].id if steps else None,
                        )
                    )

        return Trajectory(
            session_id=f"openai_{uuid.uuid4().hex[:8]}",
            goal=detected_goal or "Objetivo extraído de OpenAI messages",
            steps=steps,
        )

    @classmethod
    def from_anthropic_messages(cls, messages: List[Dict[str, Any]], goal: Optional[str] = None) -> Trajectory:
        """Parsea un array de mensajes con formato de la API de Anthropic (Claude)."""
        steps: List[Step] = []
        detected_goal = goal or ""

        for msg in messages:
            role = msg.get("role", "")
            raw_content = msg.get("content", "")

            if role == "user" and not detected_goal:
                if isinstance(raw_content, str):
                    detected_goal = raw_content
                elif isinstance(raw_content, list):
                    for b in raw_content:
                        if b.get("type") == "text":
                            detected_goal = b.get("text", "")
                            break

            if isinstance(raw_content, str):
                s_type = StepType.USER_INPUT if role == "user" else StepType.THOUGHT
                steps.append(
                    Step(
                        id=f"step_{len(steps)}",
                        step_type=s_type,
                        content=raw_content,
                        parent_id=steps[-1].id if steps else None,
                    )
                )
            elif isinstance(raw_content, list):
                for block in raw_content:
                    b_type = block.get("type")
                    if b_type == "text":
                        steps.append(
                            Step(
                                id=f"step_{len(steps)}",
                                step_type=StepType.THOUGHT if role == "assistant" else StepType.USER_INPUT,
                                content=block.get("text", ""),
                                parent_id=steps[-1].id if steps else None,
                            )
                        )
                    elif b_type == "tool_use":
                        steps.append(
                            Step(
                                id=f"step_{len(steps)}",
                                step_type=StepType.TOOL_CALL,
                                tool_name=block.get("name"),
                                tool_args=block.get("input"),
                                parent_id=steps[-1].id if steps else None,
                            )
                        )
                    elif b_type == "tool_result":
                        steps.append(
                            Step(
                                id=f"step_{len(steps)}",
                                step_type=StepType.OBSERVATION,
                                content=str(block.get("content", "")),
                                parent_id=steps[-1].id if steps else None,
                            )
                        )

        return Trajectory(
            session_id=f"anthropic_{uuid.uuid4().hex[:8]}",
            goal=detected_goal or "Objetivo extraído de Anthropic messages",
            steps=steps,
        )
