"""Entidades inmutables de contexto para PRAXEON (Fase 2).

Define con tipado estricto e inmutabilidad:
- ContextDependencyType & ContextDependency: Grafo dirigido de dependencias causales e invalidación.
- ContextReference: Referencia canónica versionada a orígenes de datos y evidencias.
- ContextItem: Unidad tipada de contexto enriquecida con dependencias formales y referencias.
"""

from enum import Enum
import hashlib
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from praxeon.context.fragments import (
    ContextFragment,
    FragmentType,
    compute_content_hash,
    estimate_tokens,
)


class ContextDependencyType(str, Enum):
    """Tipo semántico de la relación de dependencia entre elementos de contexto."""
    REQUIRES = "requires"                # Dependencia de existencia (necesita al objetivo para ser válido)
    DERIVED_FROM = "derived_from"        # Génesis o derivación directa de evidencia/observación previa
    SUPERSEDES = "supersedes"            # Actualización o sustitución de un elemento anterior obsoleto
    INVALIDATED_BY = "invalidated_by"    # Regla de invalidación (si el objetivo cambia, este elemento se invalida)


class ContextDependency(BaseModel):
    """Dependencia formal y dirigida entre entidades de contexto."""
    model_config = ConfigDict(frozen=True)

    source_id: str
    target_id: str
    dependency_type: ContextDependencyType = ContextDependencyType.REQUIRES
    reason: Optional[str] = None
    created_at: float = Field(default_factory=time.time)


class ContextReference(BaseModel):
    """Referencia tipada y versionada a una fuente de verdad o recurso subyacente."""
    model_config = ConfigDict(frozen=True)

    reference_id: str
    uri: Optional[str] = None
    content_hash: str
    version: int = 1
    fragment_type: Optional[FragmentType] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ContextItem(BaseModel):
    """Entidad canónica de contexto inmutable (Fase 2) con grafo de dependencias y referencias."""
    model_config = ConfigDict(frozen=True)

    item_id: str
    item_type: FragmentType
    source_id: str
    content: str
    content_hash: str
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    token_estimate: int = 0
    sensitivity: str = "internal"  # "public", "internal", "secret"
    dependencies: List[ContextDependency] = Field(default_factory=list)
    references: List[ContextReference] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        item_id: str,
        item_type: FragmentType,
        source_id: str,
        content: str,
        dependencies: Optional[List[ContextDependency]] = None,
        references: Optional[List[ContextReference]] = None,
        sensitivity: str = "internal",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ContextItem":
        """Constructor de conveniencia que calcula el hash criptográfico y los tokens."""
        clean_content = content.strip()
        chash = compute_content_hash(clean_content)
        tokens = estimate_tokens(clean_content)
        now = time.time()
        return cls(
            item_id=item_id,
            item_type=item_type,
            source_id=source_id,
            content=clean_content,
            content_hash=chash,
            created_at=now,
            updated_at=now,
            token_estimate=tokens,
            sensitivity=sensitivity,
            dependencies=list(dependencies or []),
            references=list(references or []),
            metadata=dict(metadata or {}),
        )

    def to_fragment(self) -> ContextFragment:
        """Convierte este ContextItem en un ContextFragment compatible con la caché L1/L2."""
        dep_ids = [d.target_id for d in self.dependencies]
        return ContextFragment(
            fragment_id=self.item_id,
            fragment_type=self.item_type,
            source_id=self.source_id,
            content=self.content,
            content_hash=self.content_hash,
            created_at=self.created_at,
            updated_at=self.updated_at,
            dependencies=dep_ids,
            sensitivity=self.sensitivity,
            token_estimate=self.token_estimate,
            metadata=dict(self.metadata),
        )

    @classmethod
    def from_fragment(
        cls,
        fragment: ContextFragment,
        dependencies: Optional[List[ContextDependency]] = None,
        references: Optional[List[ContextReference]] = None,
    ) -> "ContextItem":
        """Reconstruye un ContextItem a partir de un ContextFragment."""
        deps = list(dependencies or [])
        if not deps and fragment.dependencies:
            deps = [
                ContextDependency(source_id=fragment.fragment_id, target_id=d_id)
                for d_id in fragment.dependencies
            ]
        return cls(
            item_id=fragment.fragment_id,
            item_type=fragment.fragment_type,
            source_id=fragment.source_id,
            content=fragment.content,
            content_hash=fragment.content_hash,
            created_at=fragment.created_at,
            updated_at=fragment.updated_at,
            token_estimate=fragment.token_estimate,
            sensitivity=fragment.sensitivity,
            dependencies=deps,
            references=list(references or []),
            metadata=dict(fragment.metadata),
        )
