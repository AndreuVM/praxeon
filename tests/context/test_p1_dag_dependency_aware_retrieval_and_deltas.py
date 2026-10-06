"""Pruebas unitarias de recuperación topológica consciente de dependencias (DAG) y cálculo de Context Deltas."""

import networkx as nx
import pytest

from praxeon.context.delta import ContextDelta
from praxeon.context.fragments import EvidenceFragment, FragmentType, GoalFragment, ObservationFragment
from praxeon.context.manager import ContextManager
from praxeon.context.selector import DAGContextSelector
from praxeon.domain.models import ActionCandidate, DecisionStatus, Evidence, Goal, PolicyDecision, ToolCall
from praxeon.runtime.state import SessionState, StepRecord


def test_dag_selector_resolves_ancestral_nodes():
    """Verifica que resolve_ancestral_nodes resuelva la cadena topológica excluyendo ramas paralelas."""
    selector = DAGContextSelector()

    # Grafo con bifurcación:
    #         root
    #        /    \
    #     b_A1    b_B1
    #       |       |
    #     b_A2    b_B2
    dep_dict = {
        "b_A1": ["root"],
        "b_A2": ["b_A1"],
        "b_B1": ["root"],
        "b_B2": ["b_B1"],
    }

    ancestors_a2 = selector.resolve_ancestral_nodes(
        active_node_id="b_A2",
        steps=[],
        metadata={"node_dependencies": dep_dict},
    )
    assert ancestors_a2 == {"root", "b_A1", "b_A2"}
    assert "b_B1" not in ancestors_a2
    assert "b_B2" not in ancestors_a2

    ancestors_b2 = selector.resolve_ancestral_nodes(
        active_node_id="b_B2",
        steps=[],
        metadata={"node_dependencies": dep_dict},
    )
    assert ancestors_b2 == {"root", "b_B1", "b_B2"}
    assert "b_A1" not in ancestors_b2


def test_dependency_aware_retrieval_filters_irrelevant_branch_steps_and_evidence():
    """Verifica que DAGContextSelector incluya únicamente los pasos y evidencias ancestras requeridas."""
    selector = DAGContextSelector()

    # Evidencias
    ev_root = Evidence(id="ev_root", claim="Configuración base cargada")
    ev_branch_a = Evidence(id="ev_branch_a", claim="Credenciales de base de datos validadas")
    ev_branch_b = Evidence(id="ev_branch_b", claim="Token de servicio externo temporal")

    decision_ok = PolicyDecision(status=DecisionStatus.ALLOW, reason_codes=["OK"])

    # Pasos en ramas separadas
    step_root = StepRecord(
        id="s_root",
        index=0,
        decision=decision_ok,
        action=ActionCandidate(
            id="act_root",
            description="Cargar config",
            tool_call=ToolCall(tool_name="read_file", arguments={"path": "config.yaml"}),
            metadata={"node_id": "root", "produced_evidence": ["ev_root"]},
        ),
        observation="Config OK",
    )

    step_a1 = StepRecord(
        id="s_a1",
        index=1,
        decision=decision_ok,
        action=ActionCandidate(
            id="act_a1",
            description="Verificar DB",
            tool_call=ToolCall(tool_name="test_conn", arguments={"target": "db"}),
            metadata={"node_id": "b_A1", "parent_id": "root", "requires_evidence": ["ev_root"], "produced_evidence": ["ev_branch_a"]},
        ),
        observation="DB reachable",
    )

    step_b1 = StepRecord(
        id="s_b1",
        index=2,
        decision=decision_ok,
        action=ActionCandidate(
            id="act_b1",
            description="Autenticar servicio externo",
            tool_call=ToolCall(tool_name="auth_service", arguments={"svc": "cloud"}),
            metadata={"node_id": "b_B1", "parent_id": "root", "produced_evidence": ["ev_branch_b"]},
        ),
        observation="Cloud token issued",
    )

    state = SessionState(
        session_id="sess_dag_test",
        goal=Goal(objective="Completar migración en rama A"),
        steps=[step_root, step_a1, step_b1],
        evidence=[ev_root, ev_branch_a, ev_branch_b],
    )

    cand_a2 = ActionCandidate(
        id="act_a2",
        description="Ejecutar migración en DB",
        tool_call=ToolCall(tool_name="run_migration", arguments={"db": "users"}),
        requires_evidence=["ev_branch_a"],
        metadata={"node_id": "b_A2", "parent_id": "b_A1"},
    )

    # Selección topológica hacia el nodo b_A1
    fragments = selector.select(
        state=state,
        candidate_action=cand_a2,
        active_node_id="b_A1",
    )

    frag_step_ids = [
        f.source_id for f in fragments if f.fragment_type == FragmentType.OBSERVATION
    ]
    # s_root y s_a1 deben estar incluidos; s_b1 (rama B) debe ser podado
    assert "act_root" in frag_step_ids or "s_root" in frag_step_ids
    assert "act_a1" in frag_step_ids or "s_a1" in frag_step_ids
    assert "act_b1" not in frag_step_ids and "s_b1" not in frag_step_ids

    # Evidencia de rama A debe estar priorizada antes de evidencia ajena de rama B
    ev_frags = [f.source_id for f in fragments if f.fragment_type == FragmentType.EVIDENCE]
    assert "ev_branch_a" in ev_frags
    assert "ev_root" in ev_frags


def test_context_delta_computation_and_application():
    """Verifica que ContextDelta calcule fielmente las diferencias y permita su reconstrucción."""
    manager = ContextManager(enabled=True)
    state = SessionState(session_id="sess_delta_test", goal=Goal(objective="Construcción modular"))

    # Paso 1
    cand1 = ActionCandidate(
        id="act_step1",
        description="Paso 1",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "a.txt"}),
    )
    snap1, _ = manager.build(state, cand1)

    # Paso 2 con nuevo paso acumulado
    step1 = StepRecord(
        id="s_step1",
        index=0,
        decision=PolicyDecision(status=DecisionStatus.ALLOW, reason_codes=["OK"]),
        action=cand1,
        observation="Contenido de a.txt",
    )
    state.steps.append(step1)

    cand2 = ActionCandidate(
        id="act_step2",
        description="Paso 2",
        tool_call=ToolCall(tool_name="write_file", arguments={"path": "b.txt", "content": "ok"}),
    )
    snap2, _, delta = manager.build_with_delta(state, cand2, previous_snapshot=snap1)

    assert delta.base_fingerprint == snap1.fingerprint
    assert delta.target_fingerprint == snap2.fingerprint
    assert not delta.is_empty
    assert delta.reused_fragments_count > 0
    assert len(delta.added_fragments) >= 1

    # Aplicar delta sobre fragmentos del snap1
    reconstructed_frags = delta.apply_to_fragments(snap1.fragments)
    reconstructed_hashes = {f.content_hash for f in reconstructed_frags}
    target_hashes = {f.content_hash for f in snap2.fragments}
    assert reconstructed_hashes == target_hashes

    # Delta idéntico consigo mismo debe ser empty
    self_delta = ContextDelta.compute(snap2, snap2)
    assert self_delta.is_empty
    assert len(self_delta.added_fragments) == 0
    assert len(self_delta.removed_fragment_hashes) == 0
    assert self_delta.tokens_diff == 0
