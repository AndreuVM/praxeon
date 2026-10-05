"""Gestor orquestador central de contexto y caché (ContextManager).

Coordina la selección DAG-aware, el presupuesto de tokens, el cálculo determinista
de fingerprints criptográficos y el almacenamiento en caché L1/L2.

Axioma de Seguridad de PRAXEON:
Un cache hit NUNCA otorga permisos ni veredictos ALLOW, NUNCA genera una Capability HMAC
y NUNCA ejecuta comandos ni herramientas en el sistema anfitrión. Su único efecto es
optimizar la representación de entrada suministrada al razonador.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

from praxeon.context.budget import TokenBudget
from praxeon.context.builder import ContextSnapshotBuilder
from praxeon.context.cache import ContextCache, ContextSnapshot, InMemoryContextCache, InMemoryFragmentCache
from praxeon.context.entities import (
    ContextDependency,
    ContextDependencyType,
    ContextItem,
    ContextReference,
)
from praxeon.context.fingerprint import ContextFingerprint
from praxeon.context.fragments import ContextFragment, compute_content_hash
from praxeon.context.policies import (
    invalidate_by_dependency,
    invalidate_by_evidence_id,
    invalidate_by_node,
    invalidate_by_session,
)
from praxeon.context.selector import DAGContextSelector
from praxeon.domain.action import ActionCandidate


class ContextManager:
    """Orquestador central para la construcción, presupuestado y caching de contexto."""

    def __init__(
        self,
        cache: Optional[ContextCache] = None,
        fragment_cache: Optional[InMemoryFragmentCache] = None,
        selector: Optional[DAGContextSelector] = None,
        budget: Optional[TokenBudget] = None,
        builder: Optional[ContextSnapshotBuilder] = None,
        enabled: bool = True,
        strategy_version: str = "dag_priority_v1",
        policy_version: str = "v1.0",
    ):
        self.cache = cache or InMemoryContextCache()
        self.fragment_cache = fragment_cache or InMemoryFragmentCache()
        self.selector = selector or DAGContextSelector()
        self.budget = budget or TokenBudget()
        self.builder = builder or ContextSnapshotBuilder()
        self.enabled = enabled
        self.strategy_version = strategy_version
        self.policy_version = policy_version

        # Registro formal de entidades de contexto (Fase 2)
        self._items: Dict[str, ContextItem] = {}
        self._dependencies: List[ContextDependency] = []
        self._references: Dict[str, ContextReference] = {}

        # Telemetría y observabilidad según Sección 13 del PDF y Change Request CHG-11
        self._builds_total = 0
        self._rebuilds_total = 0
        self._cache_hits_total = 0
        self._prefix_hits_total = 0
        self._cache_misses_total = 0
        self._invalidations_total = 0
        self._tokens_before = 0
        self._tokens_after = 0
        self._tokens_saved = 0
        self._fragments_reused = 0
        self._latencies_ms: List[float] = []

    def build(
        self,
        state: Any,
        candidate_action: Optional[ActionCandidate] = None,
        session_context: Optional[Any] = None,
        max_tokens: Optional[int] = None,
        force_refresh: bool = False,
        model_profile: str = "default",
        active_node_id: Optional[str] = None,
    ) -> Tuple[ContextSnapshot, bool]:
        """Obtiene o construye de forma incremental un ContextSnapshot para el estado dado.
        
        Retorna:
            (snapshot, is_cache_hit)
        """
        start_t = time.perf_counter()
        self._builds_total += 1

        session_id = getattr(state, "session_id", "session_default")

        # 1. Si el caché está desactivado globalmente, construir sin consultar ni almacenar
        if not self.enabled:
            self._rebuilds_total += 1
            raw_fragments = self.selector.select(
                state=state,
                candidate_action=candidate_action,
                session_context=session_context,
                active_node_id=active_node_id,
            )
            budgeted_fragments, truncated, total_tokens = self.budget.allocate(
                raw_fragments, max_tokens=max_tokens
            )
            snapshot = self.builder.build_snapshot(
                fingerprint="no_cache",
                session_id=session_id,
                fragments=budgeted_fragments,
                candidate_action=candidate_action,
                truncated=truncated,
            )
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            self._latencies_ms.append(elapsed_ms)
            return (snapshot, False)

        # 2. Selección estructural determinista de fragmentos
        extracted_fragments = self.selector.select(
            state=state,
            candidate_action=candidate_action,
            session_context=session_context,
            active_node_id=active_node_id,
        )

        # L1: Reutilizar fragmentos atómicos mediante InMemoryFragmentCache
        raw_fragments = []
        for frag in extracted_fragments:
            cached_frag = self.fragment_cache.get(frag.content_hash)
            if cached_frag is not None:
                self._fragments_reused += 1
                raw_fragments.append(cached_frag)
            else:
                self.fragment_cache.put(frag)
                raw_fragments.append(frag)

        raw_tokens_estimate = sum(f.token_estimate for f in raw_fragments)
        self._tokens_before += raw_tokens_estimate

        # 3. Extraer componentes para el fingerprint determinista
        goal_frag = next((f for f in raw_fragments if f.fragment_type.value == "goal"), None)
        goal_hash = goal_frag.content_hash if goal_frag else compute_content_hash(str(getattr(state, "goal", "")))

        fragment_hashes = [f.content_hash for f in raw_fragments]

        # 3a. Huella del prefijo de estado base (independiente de la acción candidata)
        base_node_ids = [active_node_id] if active_node_id else []
        base_fingerprint = ContextFingerprint.generate(
            session_id=session_id,
            goal_hash=goal_hash,
            relevant_node_ids=base_node_ids,
            fragment_hashes=fragment_hashes,
            policy_context_version=self.policy_version,
            context_strategy_version=self.strategy_version,
            model_context_profile=model_profile,
        )

        # 3b. Huella completa (incluye la acción candidata si existe)
        full_node_ids = list(base_node_ids)
        if candidate_action:
            full_node_ids.append(candidate_action.id)

        fingerprint = ContextFingerprint.generate(
            session_id=session_id,
            goal_hash=goal_hash,
            relevant_node_ids=full_node_ids,
            fragment_hashes=fragment_hashes,
            policy_context_version=self.policy_version,
            context_strategy_version=self.strategy_version,
            model_context_profile=model_profile,
        )

        # 4. Comprobar caché L2 (Exact Hit y Prefix Hit)
        if not force_refresh:
            # 4a. Exact Cache Hit: Snapshot completo para estado + acción idénticos
            cached_snapshot = self.cache.get(fingerprint.value)
            if cached_snapshot is not None:
                self._cache_hits_total += 1
                tokens_saved_this_call = cached_snapshot.total_tokens
                self._tokens_saved += tokens_saved_this_call
                self._fragments_reused += len(cached_snapshot.fragments)
                elapsed_ms = (time.perf_counter() - start_t) * 1000.0
                self._latencies_ms.append(elapsed_ms)
                return (cached_snapshot, True)

            # 4b. Prefix Cache Hit: El estado base ya está en caché para esta sesión
            if candidate_action:
                cached_base = self.cache.get(base_fingerprint.value)
                if cached_base is not None:
                    # Reutilizar fragmentos base presupuestados sin recalcular el DAG
                    snapshot = self.builder.build_snapshot(
                        fingerprint=fingerprint.value,
                        session_id=session_id,
                        fragments=cached_base.fragments,
                        candidate_action=candidate_action,
                        truncated=cached_base.truncated,
                        extra_metadata={
                            "strategy": self.strategy_version,
                            "model_profile": model_profile,
                            "prefix_hit": True,
                        },
                    )
                    self.cache.put(fingerprint.value, snapshot)
                    self._cache_hits_total += 1
                    self._prefix_hits_total += 1
                    tokens_saved_this_call = cached_base.total_tokens
                    self._tokens_saved += tokens_saved_this_call
                    self._fragments_reused += len(cached_base.fragments)
                    elapsed_ms = (time.perf_counter() - start_t) * 1000.0
                    self._latencies_ms.append(elapsed_ms)
                    return (snapshot, True)

        # 5. Cache Miss: Aplicar presupuesto de tokens y compilar snapshot
        self._cache_misses_total += 1
        self._rebuilds_total += 1
        budgeted_fragments, truncated, total_tokens = self.budget.allocate(
            raw_fragments, max_tokens=max_tokens
        )
        self._tokens_after += total_tokens

        # Guardar en caché el snapshot base para permitir Prefix Hits futuros
        if candidate_action:
            base_snapshot = self.builder.build_snapshot(
                fingerprint=base_fingerprint.value,
                session_id=session_id,
                fragments=budgeted_fragments,
                candidate_action=None,
                truncated=truncated,
                extra_metadata={"is_base_prefix": True},
            )
            self.cache.put(base_fingerprint.value, base_snapshot)

        snapshot = self.builder.build_snapshot(
            fingerprint=fingerprint.value,
            session_id=session_id,
            fragments=budgeted_fragments,
            candidate_action=candidate_action,
            truncated=truncated,
            extra_metadata={
                "strategy": self.strategy_version,
                "model_profile": model_profile,
                "node_id": active_node_id,
            },
        )

        # 6. Almacenar snapshot completo en caché de manera idempotente
        self.cache.put(fingerprint.value, snapshot)

        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        self._latencies_ms.append(elapsed_ms)

        return (snapshot, False)

    def invalidate_evidence(self, evidence_id: str) -> int:
        """Invalida quirúrgicamente snapshots dependientes de una evidencia revocada o alterada."""
        count = self.cache.invalidate(invalidate_by_evidence_id(evidence_id))
        self._invalidations_total += count
        return count

    def invalidate_session(self, session_id: str) -> int:
        """Invalida todos los snapshots de una sesión."""
        count = self.cache.invalidate(invalidate_by_session(session_id))
        self._invalidations_total += count
        return count

    def invalidate_node(self, node_id: str) -> int:
        """Invalida todos los snapshots vinculados a un nodo específico en el árbol de razonamiento."""
        if hasattr(self.cache, "invalidate_node"):
            count = self.cache.invalidate_node(node_id)
        else:
            count = self.cache.invalidate(invalidate_by_node(node_id))
        self._invalidations_total += count
        return count

    def invalidate_dependency(self, target_id: str) -> int:
        """Invalida en cascada todos los snapshots que dependan del target_id especificado."""
        count = self.cache.invalidate(invalidate_by_dependency(target_id))
        self._invalidations_total += count
        return count

    def add_item(self, item: ContextItem) -> None:
        """Registra un ContextItem y almacena su fragmento subyacente en la caché L1."""
        self._items[item.item_id] = item
        frag = item.to_fragment()
        self.fragment_cache.put(frag)
        for dep in item.dependencies:
            self.register_dependency(dep)
        for ref in item.references:
            self.register_reference(ref)

    def get_item(self, item_id: str) -> Optional[ContextItem]:
        """Recupera un ContextItem registrado por su ID."""
        return self._items.get(item_id)

    def get_items(self) -> List[ContextItem]:
        """Devuelve todos los ContextItem registrados en el gestor."""
        return list(self._items.values())

    def register_dependency(self, dependency: ContextDependency) -> None:
        """Registra una dependencia formal y dirigida entre entidades de contexto."""
        if dependency not in self._dependencies:
            self._dependencies.append(dependency)

    def register_reference(self, reference: ContextReference) -> None:
        """Registra una referencia a origen o recurso canónico."""
        self._references[reference.reference_id] = reference

    def get_dependencies(
        self,
        item_id: str,
        dependency_type: Optional[ContextDependencyType] = None,
    ) -> List[ContextDependency]:
        """Obtiene las dependencias asociadas a un elemento."""
        deps = [d for d in self._dependencies if d.source_id == item_id]
        if dependency_type is not None:
            deps = [d for d in deps if d.dependency_type == dependency_type]
        return deps

    def clear(self) -> None:
        """Limpia el almacenamiento de caché y el registro de entidades."""
        self.cache.clear()
        self.fragment_cache.clear()
        self._items.clear()
        self._dependencies.clear()
        self._references.clear()

    def get_metrics(self) -> Dict[str, Any]:
        """Genera el reporte cuantitativo estandarizado de observabilidad de contexto (CHG-11)."""
        cache_stats = self.cache.stats()
        frag_stats = self.fragment_cache.stats()
        total_requests = self._cache_hits_total + self._cache_misses_total
        hit_rate = (self._cache_hits_total / total_requests) if total_requests > 0 else 0.0

        reduction_ratio = (
            (self._tokens_saved / (self._tokens_saved + self._tokens_after))
            if (self._tokens_saved + self._tokens_after) > 0
            else 0.0
        )

        avg_latency = (
            sum(self._latencies_ms) / len(self._latencies_ms)
            if self._latencies_ms
            else 0.0
        )

        snapshot_evictions = cache_stats.get("evictions", 0)
        fragment_evictions = frag_stats.get("evictions", 0)
        total_evictions = snapshot_evictions + fragment_evictions
        exact_hits = self._cache_hits_total - self._prefix_hits_total

        return {
            "context_builds_total": self._builds_total,
            "context_rebuilds_total": self._rebuilds_total,
            "rebuild_count": self._rebuilds_total,
            "context_cache_hits_total": self._cache_hits_total,
            "context_prefix_hits_total": self._prefix_hits_total,
            "context_cache_misses_total": self._cache_misses_total,
            "context_cache_hit_rate": round(hit_rate, 4),
            "context_invalidations_total": self._invalidations_total,
            "context_cache_evictions_total": total_evictions,
            "eviction_count": total_evictions,
            "snapshot_evictions": snapshot_evictions,
            "fragment_evictions": fragment_evictions,
            "snapshot_cache_hits": exact_hits,
            "prefix_cache_hits": self._prefix_hits_total,
            "context_tokens_before": self._tokens_before,
            "context_tokens_after": self._tokens_after,
            "context_tokens_saved": self._tokens_saved,
            "context_reduction_ratio": round(reduction_ratio, 4),
            "context_fragments_reused": self._fragments_reused,
            "context_build_latency_ms": round(avg_latency, 3),
            "cache_entries": cache_stats.get("entries_count", 0),
            "fragment_cache_entries": frag_stats.get("entries_count", 0),
            "fragment_cache_hits": frag_stats.get("hits", 0),
            "fragment_cache_misses": frag_stats.get("misses", 0),
            "fragment_cache_evictions": fragment_evictions,
        }
