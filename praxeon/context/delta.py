"""Deltas incrementales de contexto entre pasos consecutivos (ContextDelta).

Permite calcular y transmitir únicamente la diferencia entre dos ContextSnapshots,
evitando el reenvío redundante de fragmentos compartidos o invariantes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, ConfigDict, Field

from praxeon.context.cache import ContextSnapshot
from praxeon.context.fragments import ContextFragment


class ContextDelta(BaseModel):
    """Representa la diferencia incremental entre dos instantáneas de contexto consecutivas."""
    model_config = ConfigDict(frozen=True)

    base_fingerprint: str
    target_fingerprint: str
    added_fragments: List[ContextFragment] = Field(default_factory=list)
    removed_fragment_hashes: List[str] = Field(default_factory=list)
    reused_fragments_count: int = 0
    tokens_diff: int = 0
    is_empty: bool = False

    @classmethod
    def compute(cls, base: Optional[ContextSnapshot], target: ContextSnapshot) -> ContextDelta:
        """Calcula el delta exacto desde base hacia target."""
        if base is None:
            return cls(
                base_fingerprint="",
                target_fingerprint=target.fingerprint,
                added_fragments=list(target.fragments),
                removed_fragment_hashes=[],
                reused_fragments_count=0,
                tokens_diff=target.total_tokens,
                is_empty=False,
            )

        if base.fingerprint == target.fingerprint:
            return cls(
                base_fingerprint=base.fingerprint,
                target_fingerprint=target.fingerprint,
                added_fragments=[],
                removed_fragment_hashes=[],
                reused_fragments_count=len(target.fragments),
                tokens_diff=0,
                is_empty=True,
            )

        base_hashes: Dict[str, ContextFragment] = {f.content_hash: f for f in base.fragments}
        target_hashes: Dict[str, ContextFragment] = {f.content_hash: f for f in target.fragments}

        added = [f for h, f in target_hashes.items() if h not in base_hashes]
        removed_hashes = [h for h in base_hashes.keys() if h not in target_hashes]
        reused_count = len(set(base_hashes.keys()) & set(target_hashes.keys()))
        tok_diff = target.total_tokens - base.total_tokens
        empty = (len(added) == 0 and len(removed_hashes) == 0 and tok_diff == 0)

        return cls(
            base_fingerprint=base.fingerprint,
            target_fingerprint=target.fingerprint,
            added_fragments=added,
            removed_fragment_hashes=removed_hashes,
            reused_fragments_count=reused_count,
            tokens_diff=tok_diff,
            is_empty=empty,
        )

    def apply_to_fragments(self, base_fragments: List[ContextFragment]) -> List[ContextFragment]:
        """Aplica las adiciones y eliminaciones sobre una lista base de fragmentos."""
        removed_set = set(self.removed_fragment_hashes)
        retained = [f for f in base_fragments if f.content_hash not in removed_set]
        existing_hashes = {f.content_hash for f in retained}
        new_frags = [f for f in self.added_fragments if f.content_hash not in existing_hashes]
        return retained + new_frags
