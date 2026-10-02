"""Políticas de retención e invalidación de caché de contexto.

Define predicados funcionales para invalidación reactiva por mutación de evidencia,
término de sesión o expiración por TTL.
"""

import time
from typing import Callable, List, Set
from praxeon.context.cache import ContextSnapshot
from praxeon.context.fragments import FragmentType


def invalidate_by_session(session_id: str) -> Callable[[ContextSnapshot], bool]:
    """Invalida cualquier snapshot perteneciente a la sesión especificada."""
    return lambda snap: snap.session_id == session_id


def invalidate_by_evidence_id(evidence_id: str) -> Callable[[ContextSnapshot], bool]:
    """Invalida cualquier snapshot que contenga o dependa de la evidencia dada."""
    def _matches(snap: ContextSnapshot) -> bool:
        for frag in snap.fragments:
            if frag.fragment_type == FragmentType.EVIDENCE:
                if frag.source_id == evidence_id or frag.fragment_id == f"frag_ev_{evidence_id}":
                    return True
            if evidence_id in frag.dependencies:
                return True
        return False
    return _matches


def invalidate_by_evidence_ids(evidence_ids: Set[str]) -> Callable[[ContextSnapshot], bool]:
    """Invalida snapshots dependientes de un conjunto de evidencias modificadas."""
    def _matches(snap: ContextSnapshot) -> bool:
        for frag in snap.fragments:
            if frag.fragment_type == FragmentType.EVIDENCE and frag.source_id in evidence_ids:
                return True
            if any(dep in evidence_ids for dep in frag.dependencies):
                return True
        return False
    return _matches


def invalidate_by_ttl(max_age_seconds: float) -> Callable[[ContextSnapshot], bool]:
    """Invalida snapshots cuya antigüedad supere el TTL configurado."""
    now = time.time()
    return lambda snap: (now - snap.created_at) > max_age_seconds
