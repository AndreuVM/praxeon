"""Pruebas formales de invalidación de contexto ante mutación de evidencias y rollback (CHG-04 y CHG-05).

Cubre:
1. Secuencia de 7 pasos de CHG-04:
   - Paso 1: Construir snapshot a partir de evidencia E1.
   - Paso 2: Mutar o reemplazar la evidencia con E2.
   - Paso 3: Re-solicitar contexto; el contexto obsoleto de E1 no debe ser tratado como actual.
   - Paso 4: Ejecutar un branch/rollback que cambie el linaje del DAG.
   - Paso 5: Solicitar contexto para el nodo afectado.
   - Paso 6: Aseverar que los snapshots incompatibles quedan invalidados o excluidos por fingerprint/selección.
   - Paso 7: Reconstruir desde el estado canónico y verificar reconstrucción determinista idéntica.
2. Demostración de CHG-05:
   - Cached vs uncached policy/execution: las compuertas de decisión, capability HMAC y SecureExecutor
     convergen en exactamente los mismos veredictos y controles de autorización.
"""

import pytest
from praxeon.context.manager import ContextManager
from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.evidence import Evidence
from praxeon.domain.goal import Goal
from praxeon.domain.decision import DecisionStatus, PolicyDecision
from praxeon.policy.engine import PolicyEngine
from praxeon.runtime.executor import SecureExecutor, PolicyViolation
from praxeon.runtime.nonce_store import InMemoryNonceStore
from praxeon.runtime.state import SessionState


def test_chg_04_evidence_mutation_and_rollback_invalidation():
    """Valida formalmente la secuencia de 7 pasos de CHG-04 para garantizar que ningún dato obsoleto
    pueda ser presentado como contexto actual válido tras mutaciones de evidencia o rollbacks.
    """
    manager = ContextManager()
    session_id = "sess_chg_04_invalidation"
    goal = Goal(objective="Desplegar parche de seguridad en cluster", success_criteria=["cluster_actualizado"])
    state = SessionState(session_id=session_id, goal=goal)

    action = ActionCandidate(
        id="act_deploy_patch",
        description="Aplicar manifiesto de despliegue",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "kubectl apply -f patch.yaml"}),
    )

    # 1. Paso 1: Construir snapshot de contexto a partir de evidencia inicial E1
    state.add_evidence(Evidence(id="ev_cluster_auth", claim="cluster_auth_token_v1_valido", content_hash="hash_v1"))
    snap1, hit1 = manager.build(state, action)
    assert hit1 is False, "Primera construcción debe ser un cache miss"
    assert "cluster_auth_token_v1_valido" in snap1.formatted_prompt
    fp1 = snap1.fingerprint

    # 2. Paso 2: Mutar o reemplazar evidencia relevante con E2
    # El token v1 fue revocado o expiró, sustituido por v2
    state.evidence = [e for e in state.evidence if e.id != "ev_cluster_auth"]
    state.add_evidence(Evidence(id="ev_cluster_auth", claim="cluster_auth_token_v2_rotado", content_hash="hash_v2"))
    manager.invalidate_evidence("ev_cluster_auth")

    # 3. Paso 3: Re-solicitar contexto para la misma tarea; el contexto rancio de E1 NO es actual
    snap2, hit2 = manager.build(state, action)
    assert hit2 is False, "Tras invalidación explícita y cambio de hash de evidencia, debe ser miss"
    assert "cluster_auth_token_v1_valido" not in snap2.formatted_prompt, "Contexto rancio E1 no debe aparecer"
    assert "cluster_auth_token_v2_rotado" in snap2.formatted_prompt, "Contexto nuevo E2 debe estar presente"
    assert snap2.fingerprint != fp1, "El fingerprint debe mutar deterministamente ante cambio de hash"

    # 4. Paso 4: Realizar un branch/rollback que cambie el linaje relevante
    # Simulamos avance con un paso intermedio que luego será deshecho
    state.add_step(
        action=action,
        decision=PolicyDecision(status=DecisionStatus.ALLOW),
        observation="Despliegue fallido: timeout en pod staging",
    )
    snap_after_step, _ = manager.build(state, action)
    assert "Despliegue fallido: timeout en pod staging" in snap_after_step.formatted_prompt

    # Realizamos rollback del paso (podando el historial al estado canónico anterior)
    state.steps = []  # Rollback del paso fallido
    manager.invalidate_session(session_id)

    # 5. Paso 5: Solicitar contexto para el nodo afectado tras el rollback
    snap3, hit3 = manager.build(state, action)
    assert hit3 is False, "El rollback con invalidación debe forzar un rebuild"

    # 6. Paso 6: Aseverar que los snapshots incompatibles quedan invalidados/excluidos
    assert "Despliegue fallido: timeout en pod staging" not in snap3.formatted_prompt

    # 7. Paso 7: Reconstruir desde el estado canónico y verificar reconstrucción determinista
    # Purgar totalmente el caché para simular pérdida o reinicio de proceso
    manager.clear()
    snap_canonical_rebuild, hit_rebuild = manager.build(state, action)
    assert hit_rebuild is False
    assert snap_canonical_rebuild.fingerprint == snap3.fingerprint
    assert snap_canonical_rebuild.formatted_prompt == snap3.formatted_prompt
    assert "cluster_auth_token_v2_rotado" in snap_canonical_rebuild.formatted_prompt


def test_chg_05_cache_hits_never_alter_execution_authority():
    """CHG-05: Demuestra formalmente que un cache hit NUNCA altera las compuertas de autorización.
    
    Tanto el camino cacheado como el camino no cacheado convergen exactamente en la misma
    cadena de custodia: Context -> Provider -> Evidence/Risk -> Policy -> Decision -> Capability -> SecureExecutor.
    """
    secret_key = "test_chg_05_secret_key_32_bytes_long"
    nonce_store = InMemoryNonceStore()
    executor = SecureExecutor(dry_run=True, secret_key=secret_key, nonce_store=nonce_store)
    policy_engine = PolicyEngine(secret_key=secret_key)

    manager = ContextManager()
    session_id = "sess_chg_05_authority"
    goal = Goal(objective="Administración segura", success_criteria=[])
    state = SessionState(session_id=session_id, goal=goal)

    # Caso A: Acción permitida (read-only)
    safe_action = ActionCandidate(
        id="act_safe",
        description="Leer estado de servicios",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "status.json"}),
    )

    # 1. Camino no cacheado (miss)
    snap_uncached, hit_a1 = manager.build(state, safe_action)
    assert hit_a1 is False
    dec_uncached, receipt_uncached = policy_engine.evaluate_action(safe_action, state, session_id=state.session_id)
    assert dec_uncached.status == DecisionStatus.ALLOW
    assert receipt_uncached is not None
    # Ejecución válida con capability legítimo emitido por PolicyEngine
    obs_uncached = executor.execute(safe_action, state, receipt=receipt_uncached)
    assert obs_uncached.success is True

    # 2. Camino cacheado (hit)
    snap_cached, hit_a2 = manager.build(state, safe_action)
    assert hit_a2 is True
    # El cache hit NO genera capabilities ni autorizaciones por sí mismo
    assert not hasattr(snap_cached, "signature")
    assert not hasattr(snap_cached, "nonce")
    with pytest.raises(Exception):
        # Intentar ejecutar pasando el snapshot como capability es terminantemente rechazado por tipo o política
        executor.execute(safe_action, state, receipt=snap_cached)  # type: ignore

    # Intentar ejecutar sin recibo legítimo es rechazado por PolicyViolation
    with pytest.raises(PolicyViolation):
        executor.execute(safe_action, state, receipt=None)

    # 3. Cuando la acción pasa por la cadena de custodia normal tras un cache hit,
    # PolicyEngine evalúa de forma idéntica
    dec_cached, receipt_cached = policy_engine.evaluate_action(safe_action, state, session_id=state.session_id)
    assert dec_cached.status == dec_uncached.status
    assert dec_cached.status == DecisionStatus.ALLOW

    # Caso B: Acción destructiva (rm -rf /) -> BLOCK incondicional en ambos caminos
    state_b = SessionState(session_id="sess_chg_05_destruct", goal=goal)
    destructive_action = ActionCandidate(
        id="act_destruct",
        description="Eliminar raíz",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "rm -rf /"}),
    )

    # Miss destructivo
    snap_destruct_1, hit_d1 = manager.build(state_b, destructive_action)
    assert hit_d1 is False
    dec_d1, receipt_d1 = policy_engine.evaluate_action(destructive_action, state_b, session_id=state_b.session_id)
    assert dec_d1.status == DecisionStatus.BLOCK
    assert receipt_d1 is not None
    assert receipt_d1.decision_status == DecisionStatus.BLOCK
    with pytest.raises(PolicyViolation):
        executor.execute(destructive_action, state_b, receipt=receipt_d1)

    # Hit destructivo
    snap_destruct_2, hit_d2 = manager.build(state_b, destructive_action)
    assert hit_d2 is True
    dec_d2, receipt_d2 = policy_engine.evaluate_action(destructive_action, state_b, session_id=state_b.session_id)
    # INVARIANTE: El cache hit no relaja BLOCK ni emite capability ejecutable
    assert dec_d2.status == DecisionStatus.BLOCK
    assert receipt_d2.decision_status == DecisionStatus.BLOCK
    with pytest.raises(PolicyViolation):
        executor.execute(destructive_action, state_b, receipt=receipt_d2)
