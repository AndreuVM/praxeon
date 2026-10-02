"""Generador y validador de huellas digitales deterministas de contexto (ContextFingerprint).

Calcula un hash SHA-256 inmutable que representa la totalidad de las dependencias
activas del contexto para una evaluación de razonamiento:
SHA-256(session_id + goal_hash + relevant_node_ids + relevant_fragment_hashes +
        policy_context_version + context_strategy_version + model_context_profile)
"""

import hashlib
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ContextFingerprint(BaseModel):
    """Huella criptográfica determinista del estado y dependencias de contexto."""
    model_config = ConfigDict(frozen=True)

    value: str
    session_id: str
    goal_hash: str
    relevant_node_ids: List[str] = Field(default_factory=list)
    fragment_hashes: List[str] = Field(default_factory=list)
    policy_context_version: str = "v1.0"
    context_strategy_version: str = "dag_priority_v1"
    model_context_profile: str = "default"

    @classmethod
    def generate(
        cls,
        session_id: str,
        goal_hash: str,
        relevant_node_ids: Optional[List[str]] = None,
        fragment_hashes: Optional[List[str]] = None,
        policy_context_version: str = "v1.0",
        context_strategy_version: str = "dag_priority_v1",
        model_context_profile: str = "default",
    ) -> "ContextFingerprint":
        """Genera una huella SHA-256 canónica garantizando ordenamiento estricto."""
        sorted_nodes = sorted(list(relevant_node_ids or []))
        sorted_hashes = sorted(list(fragment_hashes or []))

        raw_components = [
            f"session_id={session_id}",
            f"goal_hash={goal_hash}",
            f"nodes={','.join(sorted_nodes)}",
            f"fragments={','.join(sorted_hashes)}",
            f"policy_ver={policy_context_version}",
            f"strategy_ver={context_strategy_version}",
            f"model_profile={model_context_profile}",
        ]
        composite_str = "||".join(raw_components)
        computed_hash = hashlib.sha256(composite_str.encode("utf-8")).hexdigest()

        return cls(
            value=computed_hash,
            session_id=session_id,
            goal_hash=goal_hash,
            relevant_node_ids=sorted_nodes,
            fragment_hashes=sorted_hashes,
            policy_context_version=policy_context_version,
            context_strategy_version=context_strategy_version,
            model_context_profile=model_context_profile,
        )

    def matches(self, other_fingerprint_value: str) -> bool:
        """Comprueba si coincide exactamente con otra huella en string."""
        return self.value == other_fingerprint_value

    def __str__(self) -> str:
        return self.value

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, ContextFingerprint):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other
        return False

    def __hash__(self) -> int:
        return hash(self.value)
