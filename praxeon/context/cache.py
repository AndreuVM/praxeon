"""Almacenes de caché de contexto para PRAXEON (L1 en memoria y protocolo abstracto).

Axioma de Seguridad:
Un ContextSnapshot devuelto por un cache hit es únicamente memoria derivada y estructurada.
NUNCA constituye autorización, NUNCA emite una Capability y NUNCA ejecuta una acción física.
"""

from collections import OrderedDict
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from praxeon.context.fragments import ContextFragment


class ContextSnapshot(BaseModel):
    """Instantánea inmutable de contexto compilada y reutilizable para un fingerprint dado."""
    model_config = ConfigDict(frozen=True)

    fingerprint: str
    session_id: str
    fragments: List[ContextFragment] = Field(default_factory=list)
    formatted_prompt: str
    total_tokens: int
    truncated: bool = False
    created_at: float = Field(default_factory=time.time)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def get_fragment_by_type(self, fragment_type: str) -> List[ContextFragment]:
        """Filtra fragmentos que componen esta instantánea por su categoría."""
        return [f for f in self.fragments if f.fragment_type == fragment_type]


class ContextCache:
    """Protocolo / clase base abstracta para almacenes de caché de contexto."""

    def get(self, key: str) -> Optional[ContextSnapshot]:
        raise NotImplementedError

    def put(self, key: str, snapshot: ContextSnapshot) -> None:
        raise NotImplementedError

    def invalidate(self, predicate: Callable[[ContextSnapshot], bool]) -> int:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError

    def stats(self) -> Dict[str, Any]:
        raise NotImplementedError


class InMemoryContextCache(ContextCache):
    """Caché L1 en memoria de alto rendimiento, thread-safe y con desalojo LRU."""

    def __init__(self, max_entries: int = 200, max_entries_per_session: int = 50):
        self._lock = threading.RLock()
        self.max_entries = max_entries
        self.max_entries_per_session = max_entries_per_session
        
        # OrderedDict gobernado por LRU: clave fingerprint -> ContextSnapshot
        self._store: OrderedDict[str, ContextSnapshot] = OrderedDict()
        # Índice secundario para control y búsqueda por sesión: session_id -> Set[fingerprint]
        self._session_index: Dict[str, set] = {}

        # Métricas operacionales
        self._hits = 0
        self._misses = 0
        self._puts = 0
        self._invalidations = 0
        self._evictions = 0

    def get(self, key: str) -> Optional[ContextSnapshot]:
        """Recupera un snapshot si existe, actualizando su posición en LRU."""
        with self._lock:
            if key in self._store:
                self._hits += 1
                self._store.move_to_end(key)
                return self._store[key]
            self._misses += 1
            return None

    def put(self, key: str, snapshot: ContextSnapshot) -> None:
        """Almacena o actualiza un snapshot de manera idempotente."""
        with self._lock:
            session_id = snapshot.session_id

            if key in self._store:
                self._store[key] = snapshot
                self._store.move_to_end(key)
                self._puts += 1
                return

            # Control de cuota por sesión individual
            session_keys = self._session_index.setdefault(session_id, set())
            if len(session_keys) >= self.max_entries_per_session:
                # Desalojar el snapshot más antiguo de esta sesión concreta
                oldest_session_key = next((k for k in self._store if k in session_keys), None)
                if oldest_session_key:
                    self._remove_key(oldest_session_key)
                    self._evictions += 1

            # Control de capacidad global
            if len(self._store) >= self.max_entries:
                oldest_key, _ = self._store.popitem(last=False)
                # Limpiar del índice de sesión
                for s_id, s_keys in self._session_index.items():
                    if oldest_key in s_keys:
                        s_keys.remove(oldest_key)
                        break
                self._evictions += 1

            # Insertar nuevo elemento
            self._store[key] = snapshot
            session_keys.add(key)
            self._puts += 1

    def _remove_key(self, key: str) -> None:
        """Eliminación interna sin gestión de lock."""
        snapshot = self._store.pop(key, None)
        if snapshot:
            s_keys = self._session_index.get(snapshot.session_id)
            if s_keys and key in s_keys:
                s_keys.remove(key)

    def invalidate(self, predicate: Callable[[ContextSnapshot], bool]) -> int:
        """Invalida quirúrgicamente las entradas que cumplan el predicado."""
        with self._lock:
            keys_to_remove = [k for k, snap in self._store.items() if predicate(snap)]
            for k in keys_to_remove:
                self._remove_key(k)
            count = len(keys_to_remove)
            self._invalidations += count
            return count

    def invalidate_session(self, session_id: str) -> int:
        """Invalida todos los snapshots asociados a una sesión específica."""
        with self._lock:
            keys = list(self._session_index.get(session_id, set()))
            for k in keys:
                self._remove_key(k)
            count = len(keys)
            self._invalidations += count
            return count

    def clear(self) -> None:
        """Limpia la totalidad de snapshots almacenados."""
        with self._lock:
            self._store.clear()
            self._session_index.clear()

    def stats(self) -> Dict[str, Any]:
        """Retorna las métricas agregadas de rendimiento del caché."""
        with self._lock:
            total_reqs = self._hits + self._misses
            hit_rate = (self._hits / total_reqs) if total_reqs > 0 else 0.0
            return {
                "entries_count": len(self._store),
                "sessions_count": len(self._session_index),
                "hits": self._hits,
                "misses": self._misses,
                "puts": self._puts,
                "invalidations": self._invalidations,
                "evictions": self._evictions,
                "hit_rate": round(hit_rate, 4),
            }


class InMemoryFragmentCache:
    """Caché L1 de fragmentos: reutiliza unidades atómicas de contexto pequeñas y estables."""

    def __init__(self, max_entries: int = 1000):
        self._lock = threading.RLock()
        self.max_entries = max_entries
        self._store: OrderedDict[str, ContextFragment] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, content_hash: str) -> Optional[ContextFragment]:
        """Recupera un fragmento por su hash de contenido."""
        with self._lock:
            if content_hash in self._store:
                self._hits += 1
                self._store.move_to_end(content_hash)
                return self._store[content_hash]
            self._misses += 1
            return None

    def put(self, fragment: ContextFragment) -> None:
        """Almacena un fragmento en el caché L1."""
        with self._lock:
            if fragment.content_hash in self._store:
                self._store.move_to_end(fragment.content_hash)
                return
            if len(self._store) >= self.max_entries:
                self._store.popitem(last=False)
            self._store[fragment.content_hash] = fragment

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            rate = (self._hits / total) if total > 0 else 0.0
            return {
                "entries_count": len(self._store),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(rate, 4),
            }

