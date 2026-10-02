"""Pruebas unitarias de integridad, caching determinista e invariantes de seguridad para praxeon.context.

Cubre:
1. Fragmentación tipada e inmutabilidad de ContextFragment.
2. Determinismo estricto de ContextFingerprint ante mutaciones de dependencias.
3. InMemoryContextCache: LRU eviction, cuotas por sesión y concurrencia multi-hilo.
4. TokenBudget: Jerarquía de prioridades de 8 niveles y truncamiento acotado.
5. ContextManager: Flujo completo miss -> build -> hit -> invalidación.
6. INVARIANTE DE SEGURIDAD CRÍTICO: Demostración empírica de que un cache hit NUNCA
   emite Capabilities, NUNCA aprueba (ALLOW) y NUNCA ejecuta acciones físicas.
"""

from concurrent.futures import ThreadPoolExecutor
import pytest
from typing import List

from praxeon.context import (
    ContextManager,
    ContextFragment,
    FragmentType,
    GoalFragment,
    ConstraintFragment,
    EvidenceFragment,
    ObservationFragment,
    TaskFragment,
    ContextFingerprint,
    InMemoryContextCache,
    TokenBudget,
    DAGContextSelector,
    ContextSnapshotBuilder,
)
from praxeon.context.cache import InMemoryFragmentCache
from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.evidence import Evidence
from praxeon.domain.goal import Goal
from praxeon.domain.decision import PolicyDecision, DecisionStatus
from praxeon.runtime.state import SessionState


# =========================================================================
# 1. Fragmentación Tipada y Hashes
# =========================================================================

def test_context_fragments_creation_and_hashing():
    """Verifica que los fragmentos tipados calculen hashes SHA-256 deterministas y sean inmutables."""
    frag1 = GoalFragment(goal_text="Desplegar cluster", criteria=["0 downtime"])
    frag2 = GoalFragment(goal_text="Desplegar cluster", criteria=["0 downtime"])
    
    assert frag1.content_hash == frag2.content_hash
    assert len(frag1.content_hash) == 64
    assert frag1.fragment_type == FragmentType.GOAL
    assert frag1.token_estimate > 0

    # Inmutabilidad garantizada por ConfigDict(frozen=True)
    with pytest.raises(Exception):
        frag1.content = "Modificación no permitida"  # type: ignore


# =========================================================================
# 2. Determinismo de ContextFingerprint
# =========================================================================

def test_context_fingerprint_determinism_and_dependencies():
    """Verifica que ContextFingerprint sea idéntico para dependencias idénticas y mute ante cualquier cambio o inversión de orden."""
    fp1 = ContextFingerprint.generate(
        session_id="sess_1",
        goal_hash="goal_h1",
        relevant_node_ids=["node_1", "node_2"],
        fragment_hashes=["h_a", "h_b"],
    )
    fp1_duplicate = ContextFingerprint.generate(
        session_id="sess_1",
        goal_hash="goal_h1",
        relevant_node_ids=["node_1", "node_2"],
        fragment_hashes=["h_a", "h_b"],
    )
    # A -> A debe ser idéntico
    assert fp1.value == fp1_duplicate.value
    assert fp1 == fp1_duplicate

    # BUG-01: A -> B con fragmentos en orden invertido DEBE producir huellas distintas
    fp_inverted = ContextFingerprint.generate(
        session_id="sess_1",
        goal_hash="goal_h1",
        relevant_node_ids=["node_1", "node_2"],
        fragment_hashes=["h_b", "h_a"],  # Orden invertido
    )
    assert fp1.value != fp_inverted.value

    # BUG-01: Nodos en orden invertido también deben producir huellas distintas si el orden de linaje difiere
    fp_nodes_inverted = ContextFingerprint.generate(
        session_id="sess_1",
        goal_hash="goal_h1",
        relevant_node_ids=["node_2", "node_1"],
        fragment_hashes=["h_a", "h_b"],
    )
    assert fp1.value != fp_nodes_inverted.value

    # Cambio en objetivo
    fp_diff_goal = ContextFingerprint.generate(
        session_id="sess_1",
        goal_hash="goal_h2",
        relevant_node_ids=["node_1", "node_2"],
        fragment_hashes=["h_a", "h_b"],
    )
    assert fp1.value != fp_diff_goal.value

    # Cambio en dependencias de fragmentos
    fp_diff_frags = ContextFingerprint.generate(
        session_id="sess_1",
        goal_hash="goal_h1",
        relevant_node_ids=["node_1", "node_2"],
        fragment_hashes=["h_a", "h_c"],
    )
    assert fp1.value != fp_diff_frags.value


def test_bug_01_state_observation_order_inversion_causes_cache_miss():
    """BUG-01: Dos estados con observaciones en orden invertido no deben colisionar en caché."""
    manager = ContextManager()

    goal = Goal(objective="Probar sensibilidad al orden")
    state_a = SessionState(session_id="sess_order_a", goal=goal)
    state_b = SessionState(session_id="sess_order_b", goal=goal)

    act1 = ActionCandidate(id="act1", description="Paso 1", tool_call=ToolCall(tool_name="read_file", arguments={"path": "a.txt"}))
    act2 = ActionCandidate(id="act2", description="Paso 2", tool_call=ToolCall(tool_name="read_file", arguments={"path": "b.txt"}))

    # Estado A: Obs 1 luego Obs 2
    state_a.add_step(action=act1, decision=PolicyDecision(status=DecisionStatus.ALLOW), observation="Primera observación de compilación")
    state_a.add_step(action=act2, decision=PolicyDecision(status=DecisionStatus.ALLOW), observation="Segunda observación de despliegue")

    # Estado B: Obs 2 luego Obs 1
    state_b.add_step(action=act2, decision=PolicyDecision(status=DecisionStatus.ALLOW), observation="Segunda observación de despliegue")
    state_b.add_step(action=act1, decision=PolicyDecision(status=DecisionStatus.ALLOW), observation="Primera observación de compilación")

    cand = ActionCandidate(id="cand_eval", description="Evaluar siguiente acción", tool_call=ToolCall(tool_name="read_file", arguments={"path": "status.txt"}))

    snap_a, hit_a = manager.build(state_a, cand)
    assert hit_a is False
    assert snap_a.fingerprint != ""

    # Estado B debe ser miss y generar un fingerprint distinto a pesar de tener exactamente los mismos fragmentos en orden invertido
    snap_b, hit_b = manager.build(state_b, cand)
    assert hit_b is False
    assert snap_a.fingerprint != snap_b.fingerprint
    # Comprobar que el prompt refleje el orden real
    assert snap_a.formatted_prompt != snap_b.formatted_prompt


# =========================================================================
# 3. Caché L1 en Memoria, LRU y Concurrencia
# =========================================================================

def test_in_memory_cache_lru_and_session_eviction():
    """Verifica la capacidad máxima, desalojo LRU y métricas de acierto/fallo."""
    cache = InMemoryContextCache(max_entries=3, max_entries_per_session=2)
    builder = ContextSnapshotBuilder()

    snap1 = builder.build_snapshot("fp1", "sess_A", [GoalFragment("meta 1")])
    snap2 = builder.build_snapshot("fp2", "sess_A", [GoalFragment("meta 2")])
    snap3 = builder.build_snapshot("fp3", "sess_A", [GoalFragment("meta 3")])

    cache.put("fp1", snap1)
    cache.put("fp2", snap2)
    # Sess_A alcanza cuota de sesión (2), debe desalojar fp1
    cache.put("fp3", snap3)

    assert cache.get("fp1") is None  # Desalojado por cuota de sesión
    assert cache.get("fp2") is not None
    assert cache.get("fp3") is not None

    stats = cache.stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 1
    assert stats["evictions"] == 1


def test_in_memory_cache_thread_safety():
    """Verifica que el caché sea thread-safe bajo acceso altamente concurrente."""
    cache = InMemoryContextCache(max_entries=500, max_entries_per_session=100)
    builder = ContextSnapshotBuilder()

    def worker(worker_id: int):
        for i in range(25):
            fp = f"fp_w_{worker_id}_{i}"
            snap = builder.build_snapshot(fp, f"sess_{worker_id}", [GoalFragment(f"goal {i}")])
            cache.put(fp, snap)
            retrieved = cache.get(fp)
            assert retrieved is not None
            assert retrieved.fingerprint == fp

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker, w) for w in range(10)]
        for f in futures:
            f.result()

    stats = cache.stats()
    assert stats["entries_count"] == 250
    assert stats["hits"] == 250


# =========================================================================
# 4. Presupuesto de Tokens por Capas (TokenBudget)
# =========================================================================

def test_token_budget_priority_allocation():
    """Verifica que la asignación respete la jerarquía de 8 niveles del PDF."""
    budget = TokenBudget(default_max_tokens=60)

    frag_goal = GoalFragment("Desplegar aplicación")               # ~12 tokens (Prio 1)
    frag_evidence = EvidenceFragment("ev1", "cluster_disponible")   # ~10 tokens (Prio 5)
    frag_obs = ObservationFragment("s1", "cat", "obs larga " * 20) # ~60 tokens (Prio 6)
    frag_task = TaskFragment(1, "tarea anterior", "resumen breve") # ~15 tokens (Prio 7)

    all_frags = [frag_task, frag_obs, frag_evidence, frag_goal]

    selected, truncated, used_tokens = budget.allocate(all_frags, max_tokens=35)

    assert truncated is True
    # Nivel 1 (Goal) y Nivel 5 (Evidence) deben ser retenidos antes que Observaciones o Tareas antiguas
    selected_types = [f.fragment_type for f in selected]
    assert FragmentType.GOAL in selected_types
    assert FragmentType.EVIDENCE in selected_types
    assert FragmentType.OBSERVATION not in selected_types  # Podado por sobrepasar el presupuesto


# =========================================================================
# 5. Flujo Completo de ContextManager
# =========================================================================

def test_context_manager_build_and_caching_lifecycle():
    """Verifica el ciclo miss -> hit -> invalidación en ContextManager."""
    manager = ContextManager()
    goal = Goal(objective="Crear microservicio", success_criteria=["tests pasan"])
    state = SessionState(session_id="sess_lifecycle", goal=goal)
    state.add_evidence(Evidence(id="ev_init", claim="repo clonado", content_hash="h1"))

    cand = ActionCandidate(
        id="act_1",
        description="Listar directorio",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "ls"}),
    )

    # 1. Primera llamada: Cache Miss (construye y cachea)
    snap1, hit1 = manager.build(state, cand)
    assert hit1 is False
    assert snap1 is not None
    assert "Crear microservicio" in snap1.formatted_prompt

    # 2. Segunda llamada con estado y acción idénticos: Cache Hit
    snap2, hit2 = manager.build(state, cand)
    assert hit2 is True
    assert snap2.fingerprint == snap1.fingerprint
    assert snap2.formatted_prompt == snap1.formatted_prompt

    metrics = manager.get_metrics()
    assert metrics["context_cache_hits_total"] == 1
    assert metrics["context_cache_misses_total"] == 1
    assert metrics["context_tokens_saved"] > 0

    # 3. Invalidar evidencia: Provoca que la siguiente llamada sea Cache Miss
    inv_count = manager.invalidate_evidence("ev_init")
    assert inv_count >= 1

    snap3, hit3 = manager.build(state, cand)
    assert hit3 is False  # Reconstruido tras invalidación


# =========================================================================
# 6. INVARIANTE CRÍTICO DE SEGURIDAD (PDF Invariant I1, I2, I3)
# =========================================================================

def test_security_invariant_cache_hit_never_grants_capability_or_execution():
    """INVARIANTE CRÍTICO:
    
    Demuestra formalmente que obtener un snapshot desde la caché (incluso 1.000 veces)
    es una operación PURAMENTE de datos informativos para el razonador:
    - NUNCA equivale a un veredicto ALLOW.
    - NUNCA emite un recibo firmado ni una Capability HMAC.
    - NUNCA modifica el NonceStore.
    - NUNCA despacha comandos al SecureExecutor ni al sistema operativo host.
    """
    from praxeon.domain.decision import DecisionStatus, DecisionReceipt
    from praxeon.runtime.nonce_store import InMemoryNonceStore
    from praxeon.runtime.executor import SecureExecutor

    manager = ContextManager()
    goal = Goal(objective="Comando peligroso de prueba", success_criteria=[])
    state = SessionState(session_id="sess_security_test", goal=goal)

    destructive_action = ActionCandidate(
        id="act_dangerous",
        description="Eliminar datos de producción",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "rm -rf /"}),
    )

    nonce_store = InMemoryNonceStore()
    executor = SecureExecutor(nonce_store=nonce_store)

    # 1. Calentar el cache con el contexto
    snapshot, is_hit = manager.build(state, destructive_action)
    assert is_hit is False
    assert snapshot is not None

    # 2. Consultar repetidamente el cache (simulando 100 cache hits sucesivos)
    for _ in range(100):
        snap_hit, hit_flag = manager.build(state, destructive_action)
        assert hit_flag is True
        
        # El snapshot es sólo una representación estructurada
        assert isinstance(snap_hit.formatted_prompt, str)
        
        # NO es un objeto de decisión ni posee estatus de autorización
        assert not hasattr(snap_hit, "status")
        assert not hasattr(snap_hit, "decision")
        assert not hasattr(snap_hit, "capability")
        assert not hasattr(snap_hit, "signature")

    # 3. Demostrar que SecureExecutor rechaza terminantemente ejecutar basándose en un ContextSnapshot
    with pytest.raises(Exception):
        # SecureExecutor solo acepta ActionCandidate + capability firmada
        executor.execute(destructive_action, capability=snapshot)  # type: ignore

    # 4. El NonceStore se mantiene inmaculado (0 nonces consumidos)
    assert len(nonce_store._store) == 0

    # 5. La política real y el executor físico se mantienen intocados y seguros
    assert manager.get_metrics()["context_cache_hits_total"] == 100


def test_provider_context_builder_caching_and_invalidation():
    """Verifica que ProviderContextBuilder se beneficie del caching transparente de ContextManager."""
    from praxeon.providers.context import ProviderContextBuilder

    goal = Goal(objective="Optimizar base de datos", success_criteria=["índices creados"])
    state = SessionState(session_id="sess_builder_caching", goal=goal)
    state.add_evidence(Evidence(id="ev_schema", claim="esquema_verificado", content_hash="h_schema"))

    action = ActionCandidate(
        id="act_migrate",
        description="Migrar tablas",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "migrate"}),
    )

    builder = ProviderContextBuilder(default_max_tokens=2048, enable_caching=True)

    # 1. Primera llamada: Cache Miss
    ctx1 = builder.build(state, action)
    assert ctx1.metadata.get("cache_hit") is False
    assert "Optimizar base de datos" in ctx1.goal

    # 2. Segunda llamada idéntica: Cache Hit
    ctx2 = builder.build(state, action)
    assert ctx2.metadata.get("cache_hit") is True
    assert ctx2.metadata.get("fingerprint") == ctx1.metadata.get("fingerprint")
    assert ctx2.formatted_prompt == ctx1.formatted_prompt

    # 3. Añadir nueva evidencia muta el fingerprint y provoca Cache Miss
    state.add_evidence(Evidence(id="ev_new", claim="backup_realizado", content_hash="h_backup"))
    ctx3 = builder.build(state, action)
    assert ctx3.metadata.get("cache_hit") is False
    assert ctx3.metadata.get("fingerprint") != ctx1.metadata.get("fingerprint")


def test_prefix_caching_for_alternative_candidate_actions():
    """Verifica que ContextManager soporte Prefix Caching cuando se evalúan acciones alternativas en el mismo estado."""
    manager = ContextManager()
    goal = Goal(objective="Refactorizar arquitectura", success_criteria=["0 bugs"])
    state = SessionState(session_id="sess_prefix_test", goal=goal)
    state.add_evidence(Evidence(id="ev_arch", claim="patron_hexagonal_activo", content_hash="h_hex"))

    act_primary = ActionCandidate(
        id="act_primary",
        description="Escribir interfaz de repositorio",
        tool_call=ToolCall(tool_name="write_file", arguments={"path": "repo.py"}),
    )
    act_alternative = ActionCandidate(
        id="act_alternative",
        description="Escribir adaptador en memoria",
        tool_call=ToolCall(tool_name="write_file", arguments={"path": "adapter.py"}),
    )

    # 1. Primera acción candidata en el estado E1: Cache Miss total
    snap1, hit1 = manager.build(state, act_primary)
    assert hit1 is False
    assert "Escribir interfaz de repositorio" in snap1.formatted_prompt

    # 2. Segunda acción candidata (id diferente) en el MISMO estado E1: Prefix Hit
    snap2, hit2 = manager.build(state, act_alternative)
    assert hit2 is True  # Prefix Cache Hit
    assert "Escribir adaptador en memoria" in snap2.formatted_prompt
    assert snap2.metadata.get("prefix_hit") is True

    # 3. Re-evaluar la primera acción candidata: Exact Cache Hit
    snap1_again, hit1_again = manager.build(state, act_primary)
    assert hit1_again is True

    metrics = manager.get_metrics()
    assert metrics["context_cache_hits_total"] == 2
    assert metrics["context_prefix_hits_total"] == 1
    assert metrics["context_cache_misses_total"] == 1
    assert metrics["context_tokens_saved"] > 0


def test_chg_11_cache_eviction_and_rebuild_observability_metrics():
    """CHG-11: Verificación de métricas de desalojo (eviction) y reconstrucción (rebuild)."""
    # 1. Test de desalojo en InMemoryFragmentCache
    frag_cache = InMemoryFragmentCache(max_entries=2)
    f1 = GoalFragment(goal_text="f1")
    f2 = GoalFragment(goal_text="f2")
    f3 = GoalFragment(goal_text="f3")

    frag_cache.put(f1)
    frag_cache.put(f2)
    assert frag_cache.stats()["evictions"] == 0
    frag_cache.put(f3)  # Provoca desalojo de f1
    assert frag_cache.stats()["evictions"] == 1

    # 2. Test de desalojo y rebuild en ContextManager
    # Caché con cuota muy baja para forzar desalojos rápidos
    snap_cache = InMemoryContextCache(max_entries=2, max_entries_per_session=2)
    manager = ContextManager(cache=snap_cache, fragment_cache=frag_cache)

    goal = Goal(objective="Métricas CHG-11")
    state = SessionState(session_id="sess_obs_1", goal=goal)

    # Paso 1: Miss inicial -> Rebuild 1
    snap1, hit1 = manager.build(state)
    assert hit1 is False
    m1 = manager.get_metrics()
    assert m1["rebuild_count"] == 1
    assert m1["context_cache_misses_total"] == 1
    assert m1["context_cache_hits_total"] == 0

    # Paso 2: Consulta idéntica -> Hit (no incrementa rebuild)
    snap2, hit2 = manager.build(state)
    assert hit2 is True
    m2 = manager.get_metrics()
    assert m2["rebuild_count"] == 1
    assert m2["context_cache_hits_total"] == 1

    # Paso 3: Invalidación de sesión y nueva consulta -> Rebuild 2
    manager.invalidate_session("sess_obs_1")
    snap3, hit3 = manager.build(state)
    assert hit3 is False
    m3 = manager.get_metrics()
    assert m3["rebuild_count"] == 2
    assert m3["context_invalidations_total"] >= 1

    # Paso 4: Provocar múltiples inserciones para disparar evictions de snapshots
    for i in range(5):
        st_i = SessionState(session_id=f"sess_evict_{i}", goal=Goal(objective=f"Obj {i}"))
        manager.build(st_i)

    m4 = manager.get_metrics()
    assert m4["eviction_count"] > 0
    assert m4["snapshot_evictions"] > 0
    assert "fragment_evictions" in m4
    assert "context_cache_evictions_total" in m4
    assert m4["rebuild_count"] >= 7


def test_chg_13_cache_pressure_and_eviction_benchmark_execution():
    """CHG-13: Verificación de ejecución del benchmark de presión de caché."""
    from scripts.benchmark_cache_pressure import run_pressure_benchmark

    res = run_pressure_benchmark(
        num_sessions=3,
        steps_per_session=3,
        cache_max_entries=4,
        cache_per_session=2,
        seed=123,
    )

    assert "latency_analysis" in res
    assert "cache_pressure_metrics" in res
    assert res["latency_analysis"]["cold_start_ms"]["avg"] >= 0.0
    assert res["cache_pressure_metrics"]["context_builds_total"] > 0
    assert res["cache_pressure_metrics"]["snapshot_evictions"] > 0




