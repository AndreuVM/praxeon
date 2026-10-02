"""Modelos de datos para fragmentos tipados de contexto (ContextFragment).

Permite descomponer el contexto monolítico en unidades discretas, inmutables y
cacheables con dependencias explícitas y cálculo determinista de hashes.
"""

from enum import Enum
import hashlib
import json
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class FragmentType(str, Enum):
    """Categorías canónicas de fragmentos de contexto."""
    GOAL = "goal"
    CONSTRAINT = "constraint"
    EVIDENCE = "evidence"
    OBSERVATION = "observation"
    DECISION = "decision"
    TASK = "task"
    ENVIRONMENT = "environment"
    FILE = "file"
    SUMMARY = "summary"


def compute_content_hash(content: str) -> str:
    """Calcula el hash SHA-256 canónico de un contenido en texto."""
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


def estimate_tokens(content: str) -> int:
    """Aproximación heurística de tokens (~4 caracteres por token)."""
    if not content:
        return 0
    return max(1, len(content) // 4)


class ContextFragment(BaseModel):
    """Unidad atómica de contexto estructurado con dependencias e integridad criptográfica."""
    model_config = ConfigDict(frozen=True)

    fragment_id: str
    fragment_type: FragmentType
    source_id: str
    content: str
    content_hash: str
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    dependencies: List[str] = Field(default_factory=list)
    sensitivity: str = "internal"  # "public", "internal", "secret"
    token_estimate: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        fragment_id: str,
        fragment_type: FragmentType,
        source_id: str,
        content: str,
        dependencies: Optional[List[str]] = None,
        sensitivity: str = "internal",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ContextFragment":
        """Constructor de conveniencia que calcula automáticamente hash y estimación de tokens."""
        clean_content = content.strip()
        chash = compute_content_hash(clean_content)
        tokens = estimate_tokens(clean_content)
        now = time.time()
        return cls(
            fragment_id=fragment_id,
            fragment_type=fragment_type,
            source_id=source_id,
            content=clean_content,
            content_hash=chash,
            created_at=now,
            updated_at=now,
            dependencies=list(dependencies or []),
            sensitivity=sensitivity,
            token_estimate=tokens,
            metadata=dict(metadata or {}),
        )


# =========================================================================
# Constructores Especializados para Tipos Canónicos
# =========================================================================

def GoalFragment(
    goal_text: str,
    criteria: Optional[List[str]] = None,
    session_id: str = "default_session",
    metadata: Optional[Dict[str, Any]] = None,
) -> ContextFragment:
    """Crea un fragmento de meta con criterios de éxito asociados."""
    crit_str = ("\nCriterios de éxito:\n" + "\n".join(f"- {c}" for c in criteria)) if criteria else ""
    full_content = f"OBJETIVO: {goal_text}{crit_str}"
    meta = dict(metadata or {})
    if criteria:
        meta["criteria"] = criteria
    return ContextFragment.create(
        fragment_id=f"frag_goal_{compute_content_hash(full_content)[:12]}",
        fragment_type=FragmentType.GOAL,
        source_id=session_id,
        content=full_content,
        dependencies=[],
        metadata=meta,
    )


def ConstraintFragment(
    constraint_text: str,
    rule_id: str = "rule_custom",
    metadata: Optional[Dict[str, Any]] = None,
) -> ContextFragment:
    """Crea un fragmento de restricción u orden operativa."""
    full_content = f"RESTRICCIÓN [{rule_id}]: {constraint_text}"
    return ContextFragment.create(
        fragment_id=f"frag_constraint_{rule_id}",
        fragment_type=FragmentType.CONSTRAINT,
        source_id=rule_id,
        content=full_content,
        dependencies=[],
        metadata=metadata,
    )


def EvidenceFragment(
    evidence_id: str,
    claim: str,
    source_step_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ContextFragment:
    """Crea un fragmento de evidencia empírica contrastada."""
    full_content = f"EVIDENCIA [{evidence_id}]: {claim}"
    deps = [source_step_id] if source_step_id else []
    return ContextFragment.create(
        fragment_id=f"frag_ev_{evidence_id}",
        fragment_type=FragmentType.EVIDENCE,
        source_id=evidence_id,
        content=full_content,
        dependencies=deps,
        metadata=metadata,
    )


def ObservationFragment(
    step_id: str,
    tool_name: str,
    observation: str,
    arguments: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ContextFragment:
    """Crea un fragmento que registra la observación obtenida al invocar una herramienta."""
    args_repr = json.dumps(arguments or {}, ensure_ascii=False) if arguments else ""
    call_repr = f"{tool_name}({args_repr})" if args_repr else tool_name
    full_content = f"PASO {step_id} [{call_repr}]: {observation}"
    meta = dict(metadata or {})
    meta["tool_name"] = tool_name
    if arguments:
        meta["arguments"] = arguments
    return ContextFragment.create(
        fragment_id=f"frag_obs_{step_id}",
        fragment_type=FragmentType.OBSERVATION,
        source_id=step_id,
        content=full_content,
        dependencies=[step_id],
        metadata=meta,
    )


def DecisionFragment(
    decision_id: str,
    status: str,
    explanation: str = "",
    action_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ContextFragment:
    """Crea un fragmento de veredicto o decisión de supervisión."""
    full_content = f"DECISIÓN [{decision_id}]: {status.upper()} - {explanation}".strip()
    deps = [action_id] if action_id else []
    return ContextFragment.create(
        fragment_id=f"frag_dec_{decision_id}",
        fragment_type=FragmentType.DECISION,
        source_id=decision_id,
        content=full_content,
        dependencies=deps,
        metadata=metadata,
    )


def TaskFragment(
    task_id: int,
    goal: str,
    summary: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> ContextFragment:
    """Crea un fragmento de tarea previa completada (memoria episódica de sesión concatenada)."""
    full_content = f"TAREA PREVIA #{task_id} [{goal}]: {summary}"
    return ContextFragment.create(
        fragment_id=f"frag_task_{task_id}",
        fragment_type=FragmentType.TASK,
        source_id=f"task_{task_id}",
        content=full_content,
        dependencies=[],
        metadata=metadata,
    )


def EnvironmentFragment(
    environment_info: str,
    source_id: str = "host_env",
    metadata: Optional[Dict[str, Any]] = None,
) -> ContextFragment:
    """Crea un fragmento de contexto del entorno anfitrión (SO, cwd, dependencias)."""
    full_content = f"ENTORNO: {environment_info}"
    return ContextFragment.create(
        fragment_id=f"frag_env_{compute_content_hash(full_content)[:12]}",
        fragment_type=FragmentType.ENVIRONMENT,
        source_id=source_id,
        content=full_content,
        dependencies=[],
        metadata=metadata,
    )


def FileFragment(
    file_path: str,
    content_preview: str,
    file_hash: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ContextFragment:
    """Crea un fragmento de contenido o esquema de archivo descubierto."""
    fhash = file_hash or compute_content_hash(content_preview)
    full_content = f"ARCHIVO [{file_path}] (hash: {fhash[:8]}):\n{content_preview}"
    meta = dict(metadata or {})
    meta["file_path"] = file_path
    meta["file_hash"] = fhash
    return ContextFragment.create(
        fragment_id=f"frag_file_{compute_content_hash(file_path)[:12]}",
        fragment_type=FragmentType.FILE,
        source_id=file_path,
        content=full_content,
        dependencies=[],
        metadata=meta,
    )


def SummaryFragment(
    summary_text: str,
    covered_step_ids: Optional[List[str]] = None,
    source_id: str = "compaction_summary",
    metadata: Optional[Dict[str, Any]] = None,
) -> ContextFragment:
    """Crea un fragmento de resumen compacto de pasos históricos."""
    deps = list(covered_step_ids or [])
    deps_repr = f" (pasos: {', '.join(deps)})" if deps else ""
    full_content = f"RESUMEN HISTÓRICO{deps_repr}: {summary_text}"
    return ContextFragment.create(
        fragment_id=f"frag_summary_{compute_content_hash(full_content)[:12]}",
        fragment_type=FragmentType.SUMMARY,
        source_id=source_id,
        content=full_content,
        dependencies=deps,
        metadata=metadata,
    )
