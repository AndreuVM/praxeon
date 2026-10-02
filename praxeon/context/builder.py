"""Constructor y compilador de instantáneas de contexto (ContextSnapshotBuilder).

Ensambla los fragmentos seleccionados y presupuestados en una representación textual
canónica y estructurada (`ContextSnapshot`) lista para ser consumida por proveedores o el agente LLM.
"""

from typing import Any, Dict, List, Optional
from praxeon.context.cache import ContextSnapshot
from praxeon.context.fragments import ContextFragment, FragmentType, estimate_tokens
from praxeon.domain.action import ActionCandidate


class ContextSnapshotBuilder:
    """Compila una colección ordenada de fragmentos en un ContextSnapshot final."""

    def __init__(self, tokenizer=None):
        self.tokenizer = tokenizer or estimate_tokens

    def build_snapshot(
        self,
        fingerprint: str,
        session_id: str,
        fragments: List[ContextFragment],
        candidate_action: Optional[ActionCandidate] = None,
        truncated: bool = False,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> ContextSnapshot:
        """Ensambla el prompt canónico a partir de los fragmentos provistos."""
        sections: Dict[FragmentType, List[str]] = {}
        for frag in fragments:
            sections.setdefault(frag.fragment_type, []).append(frag.content)

        prompt_lines: List[str] = []

        # 1. Meta y requisitos
        if FragmentType.GOAL in sections:
            prompt_lines.extend(sections[FragmentType.GOAL])

        # 2. Restricciones
        if FragmentType.CONSTRAINT in sections:
            prompt_lines.append("\nRESTRICCIONES OPERACIONALES:")
            prompt_lines.extend(f" - {c}" for c in sections[FragmentType.CONSTRAINT])

        # 3. Tareas previas (memoria episódica)
        if FragmentType.TASK in sections:
            prompt_lines.append("\nMEMORIA DE TAREAS PREVIAS:")
            prompt_lines.extend(f" - {t}" for t in sections[FragmentType.TASK])

        # 4. Evidencia contrastada
        if FragmentType.EVIDENCE in sections:
            prompt_lines.append("\nEVIDENCIA CONFIRMADA:")
            prompt_lines.extend(f" - {e}" for e in sections[FragmentType.EVIDENCE])

        # 5. Entorno
        if FragmentType.ENVIRONMENT in sections:
            prompt_lines.extend(sections[FragmentType.ENVIRONMENT])

        # 6. Observaciones de pasos recientes
        if FragmentType.OBSERVATION in sections:
            prompt_lines.append("\nPASOS Y OBSERVACIONES RECIENTES:")
            prompt_lines.extend(f" - {obs}" for obs in sections[FragmentType.OBSERVATION])

        # 7. Resúmenes
        if FragmentType.SUMMARY in sections:
            prompt_lines.append("\nRESÚMENES HISTÓRICOS:")
            prompt_lines.extend(f" - {s}" for s in sections[FragmentType.SUMMARY])

        # 8. Archivos referenciados
        if FragmentType.FILE in sections:
            prompt_lines.append("\nARCHIVOS RELEVANTES:")
            prompt_lines.extend(sections[FragmentType.FILE])

        # 9. Acción candidata (si aplica)
        if candidate_action:
            tool_call = candidate_action.tool_call
            tc_repr = f"{tool_call.tool_name}({tool_call.arguments})" if tool_call else "thought"
            prompt_lines.append(f"\nACCIÓN PROPUESTA: {candidate_action.description} -> {tc_repr}")

        formatted_prompt = "\n".join(prompt_lines).strip()
        total_tokens = self.tokenizer(formatted_prompt)

        meta = dict(extra_metadata or {})
        meta["fragment_count"] = len(fragments)
        meta["fragment_types"] = [f.fragment_type.value for f in fragments]

        return ContextSnapshot(
            fingerprint=fingerprint,
            session_id=session_id,
            fragments=fragments,
            formatted_prompt=formatted_prompt,
            total_tokens=total_tokens,
            truncated=truncated,
            metadata=meta,
        )
