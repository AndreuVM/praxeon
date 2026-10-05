"""Pruebas unitarias para las entidades de contexto formales (Fase 2 - F2-01).

Valida:
- Inmutabilidad y tipado de ContextItem, ContextReference, ContextDependency.
- Conversión bidireccional entre ContextItem y ContextFragment.
- Grafo de dependencias con ContextDependencyType.
- Registro y consulta de entidades en ContextManager.
"""

import pytest
from pydantic import ValidationError

from praxeon.context import (
    ContextDependency,
    ContextDependencyType,
    ContextItem,
    ContextManager,
    ContextReference,
    FragmentType,
)


def test_context_item_immutability():
    """Valida que ContextItem sea estrictamente inmutable (frozen=True)."""
    item = ContextItem.create(
        item_id="item_01",
        item_type=FragmentType.EVIDENCE,
        source_id="test_ev",
        content="Evidencia confirmada de estado de ejecución",
    )

    assert item.item_id == "item_01"
    assert item.content_hash is not None
    assert item.token_estimate > 0

    with pytest.raises(ValidationError):
        item.content = "Nuevo contenido mutado"


def test_context_dependency_and_reference():
    """Valida la creación y relaciones de dependencias y referencias."""
    ref = ContextReference(
        reference_id="ref_01",
        uri="file://config.json",
        content_hash="abc123def456",
        version=2,
        fragment_type=FragmentType.FILE,
    )

    dep = ContextDependency(
        source_id="item_02",
        target_id="item_01",
        dependency_type=ContextDependencyType.DERIVED_FROM,
        reason="Derivado de la observación inicial de config",
    )

    item = ContextItem.create(
        item_id="item_02",
        item_type=FragmentType.OBSERVATION,
        source_id="step_1",
        content="Observación procesada",
        dependencies=[dep],
        references=[ref],
    )

    assert len(item.dependencies) == 1
    assert item.dependencies[0].dependency_type == ContextDependencyType.DERIVED_FROM
    assert len(item.references) == 1
    assert item.references[0].uri == "file://config.json"


def test_context_item_fragment_roundtrip():
    """Valida la conversión bidireccional entre ContextItem y ContextFragment."""
    dep = ContextDependency(source_id="item_a", target_id="item_b")
    item = ContextItem.create(
        item_id="item_a",
        item_type=FragmentType.GOAL,
        source_id="goal_1",
        content="Objetivo principal del agente",
        dependencies=[dep],
    )

    # A fragment
    frag = item.to_fragment()
    assert frag.fragment_id == "item_a"
    assert "item_b" in frag.dependencies
    assert frag.content_hash == item.content_hash

    # De fragment
    item_back = ContextItem.from_fragment(frag)
    assert item_back.item_id == "item_a"
    assert len(item_back.dependencies) == 1
    assert item_back.dependencies[0].target_id == "item_b"


def test_context_manager_entity_registry():
    """Valida que ContextManager registre y consulte ítems, dependencias y referencias."""
    cm = ContextManager()

    dep1 = ContextDependency(
        source_id="obs_1",
        target_id="act_1",
        dependency_type=ContextDependencyType.REQUIRES,
    )
    ref1 = ContextReference(
        reference_id="ref_main",
        uri="git://commit/head",
        content_hash="hash999",
    )

    item = ContextItem.create(
        item_id="obs_1",
        item_type=FragmentType.OBSERVATION,
        source_id="step_1",
        content="Salida de git status",
        dependencies=[dep1],
        references=[ref1],
    )

    cm.add_item(item)

    retrieved = cm.get_item("obs_1")
    assert retrieved is not None
    assert retrieved.item_id == "obs_1"

    # Fragment en caché L1
    frag_cached = cm.fragment_cache.get(item.content_hash)
    assert frag_cached is not None

    deps = cm.get_dependencies("obs_1")
    assert len(deps) == 1
    assert deps[0].target_id == "act_1"

    # Limpieza
    cm.clear()
    assert cm.get_item("obs_1") is None
    assert len(cm.get_dependencies("obs_1")) == 0


def test_multilevel_cache_node_and_dependency_invalidation():
    """Valida la caché multinivel por nodo y las reglas de invalidación por dependencia (F2-02)."""
    from praxeon.context.cache import InMemoryContextCache, ContextSnapshot
    from praxeon.context.fragments import ContextFragment, FragmentType

    cache = InMemoryContextCache()

    frag1 = ContextFragment.create(
        fragment_id="f1",
        fragment_type=FragmentType.EVIDENCE,
        source_id="ev_01",
        content="Evidencia A",
        dependencies=["dep_root"],
    )
    frag2 = ContextFragment.create(
        fragment_id="f2",
        fragment_type=FragmentType.OBSERVATION,
        source_id="step_node_2",
        content="Observación B",
        dependencies=["ev_01"],
    )

    snap_node1 = ContextSnapshot(
        fingerprint="fp_node_1",
        session_id="sess_tree",
        fragments=[frag1],
        formatted_prompt="Prompt 1",
        total_tokens=100,
        metadata={"node_id": "node_branch_A"},
    )
    snap_node2 = ContextSnapshot(
        fingerprint="fp_node_2",
        session_id="sess_tree",
        fragments=[frag2],
        formatted_prompt="Prompt 2",
        total_tokens=120,
        metadata={"node_id": "node_branch_B"},
    )

    cache.put("fp_node_1", snap_node1)
    cache.put("fp_node_2", snap_node2)

    stats = cache.stats()
    assert stats["entries_count"] == 2
    assert stats["nodes_count"] == 2

    # 1. Invalidar solo node_branch_A
    removed = cache.invalidate_node("node_branch_A")
    assert removed == 1
    assert cache.get("fp_node_1") is None
    assert cache.get("fp_node_2") is not None

    # 2. Invalidar por dependencia (dep_root o ev_01)
    from praxeon.context.policies import invalidate_by_dependency
    # snap_node2 depende de ev_01
    removed_dep = cache.invalidate(invalidate_by_dependency("ev_01"))
    assert removed_dep == 1
    assert cache.get("fp_node_2") is None


def test_context_selector_prioritized_evidence_and_branch_pruning():
    """Valida la recuperación de evidencias requeridas, exclusión de ramas y decisiones clave (F2-03, F2-04)."""
    from praxeon.context.selector import DAGContextSelector
    from praxeon.context.builder import ContextSnapshotBuilder
    from praxeon.domain.models import ActionCandidate, DecisionStatus, Goal, PolicyDecision, ToolCall
    from praxeon.runtime.state import SessionState

    goal = Goal(objective="Probar selección contextual y poda de ramas")
    state = SessionState(session_id="sess_pruning", goal=goal)

    # 1. Añadir evidencias
    from praxeon.domain.models import Evidence
    ev_common = Evidence(id="ev_01", claim="Archivo común existe")
    ev_crucial = Evidence(id="ev_99", claim="Credencial validada en secrets.env")
    state.evidence = [ev_common, ev_crucial]

    # 2. Añadir metadatos de poda de ramas
    state.metadata["pruned_branches"] = ["branch_deprecated_1"]

    # 3. Añadir pasos: uno válido, uno en rama podada, uno con decisión BLOCK
    act_ok = ActionCandidate(
        id="act_ok",
        description="Lectura permitida",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "a.txt"}),
        metadata={"branch_id": "main"},
    )
    state.add_step(action=act_ok, decision=PolicyDecision(status=DecisionStatus.ALLOW), observation="Contenido A")

    act_pruned = ActionCandidate(
        id="act_pruned",
        description="Paso en rama descartada",
        tool_call=ToolCall(tool_name="cat", arguments={"file": "temp.txt"}),
        metadata={"branch_id": "branch_deprecated_1"},
    )
    state.add_step(action=act_pruned, decision=PolicyDecision(status=DecisionStatus.ALLOW), observation="Contenido temp")

    act_blocked = ActionCandidate(
        id="act_blocked",
        description="Comando peligroso denegado",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "rm -rf /"}),
        metadata={"branch_id": "main"},
    )
    state.add_step(
        action=act_blocked,
        decision=PolicyDecision(status=DecisionStatus.BLOCK, reason_codes=["DESTRUCTIVE_COMMAND"]),
        observation=None,
    )

    # Acción candidata que requiere específicamente ev_99
    candidate = ActionCandidate(
        id="act_target",
        description="Operación sensible que requiere credencial",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "secrets.env"}),
        requires_evidence=["ev_99"],
    )

    selector = DAGContextSelector()
    fragments = selector.select(state=state, candidate_action=candidate)

    # Verificación F2-03: Evidencias priorizadas
    ev_frags = [f for f in fragments if f.fragment_type.value == "evidence"]
    assert len(ev_frags) == 2
    assert ev_frags[0].source_id == "ev_99"  # La evidencia requerida debe aparecer de primera

    # Verificación F2-04: Rama descartada excluida de las observaciones
    obs_frags = [f for f in fragments if f.fragment_type.value == "observation"]
    obs_texts = [f.content for f in obs_frags]
    assert any("Contenido A" in t for t in obs_texts)
    assert not any("Contenido temp" in t for t in obs_texts)

    # Verificación F2-03: Decisión clave BLOCK extraída
    dec_frags = [f for f in fragments if f.fragment_type.value == "decision"]
    assert len(dec_frags) == 1
    assert "BLOCK" in dec_frags[0].content
    assert "DESTRUCTIVE_COMMAND" in dec_frags[0].content

    # Verificación de compilación del prompt
    builder = ContextSnapshotBuilder()
    snapshot = builder.build_snapshot(fingerprint="test_fp", session_id="sess_pruning", fragments=fragments, candidate_action=candidate)
    assert "DECISIONES OPERACIONALES CLAVE:" in snapshot.formatted_prompt
    assert "DESTRUCTIVE_COMMAND" in snapshot.formatted_prompt
    assert "Contenido temp" not in snapshot.formatted_prompt


def test_semantic_compression_and_summarization_on_budget_exhaustion():
    """Valida la condensación semántica automática al desbordar el presupuesto de tokens (F2-05)."""
    from praxeon.context.budget import TokenBudget
    from praxeon.context.compressor import SemanticContextCompressor
    from praxeon.context.fragments import ContextFragment, FragmentType, GoalFragment

    compressor = SemanticContextCompressor()
    budget = TokenBudget(default_max_tokens=180, auto_summarize=True, compressor=compressor)

    goal = GoalFragment(goal_text="Alinear parámetros del sistema en producción")

    # Crear 8 observaciones detalladas con alto consumo de tokens
    obs_list = []
    for i in range(8):
        frag = ContextFragment.create(
            fragment_id=f"obs_step_{i}",
            fragment_type=FragmentType.OBSERVATION,
            source_id=f"step_{i}",
            content=(
                f"Paso {i}: Lectura exhaustiva de métricas del subsistema #{i}. "
                f"Se detectaron 45 parámetros operacionales evaluados correctamente sin anomalías críticas en el nodo {i}."
            ),
            metadata={"tool_name": "read_metrics", "arguments": {"node": f"node_{i}"}},
        )
        obs_list.append(frag)

    all_fragments = [goal] + obs_list
    selected, truncated, used_tokens = budget.allocate(all_fragments, max_tokens=150)

    # Debe haberse activado el truncamiento
    assert truncated is True

    # Debe contener el Goal
    assert any(f.fragment_type == FragmentType.GOAL for f in selected)

    # Debe haberse inyectado un SummaryFragment condensando los pasos truncados
    summary_frags = [f for f in selected if f.fragment_type == FragmentType.SUMMARY]
    assert len(summary_frags) == 1
    assert "Historial condensado de" in summary_frags[0].content
    assert "read_metrics" in summary_frags[0].content



