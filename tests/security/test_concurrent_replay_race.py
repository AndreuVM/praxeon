"""Pruebas de concurrencia y prevención de condiciones de carrera anti-replay (tests/security/test_concurrent_replay_race.py).

Verifica formalmente:
1. Exactamente 1 consumidor gana cuando 20 hilos concurrentes compiten por el mismo par (decision_id, nonce) en SqliteNonceStore.
2. Cero race conditions ni ejecuciones duplicadas a través de SecureExecutor con barrera de sincronización multihilo.
3. Rendimiento y consistencia bajo WAL concurrency sin bloqueos espurios.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import os
import tempfile
import threading
import pytest

from praxeon.domain.action import compute_action_hash
from praxeon.domain.decision import (
    compute_state_hash,
    DecisionReceipt,
    DecisionStatus,
    sign_receipt,
)
from praxeon.domain.models import ActionCandidate, Goal, ToolCall
from praxeon.runtime.executor import PolicyViolation, SecureExecutor
from praxeon.runtime.nonce_store import SqliteNonceStore
from praxeon.runtime.state import SessionState


def test_sqlite_nonce_store_concurrent_race_condition():
    """Demuestra que bajo contienda extrema (20 workers concurrentes con barrera),
    exactamente 1 trabajador consume el nonce con éxito y 19 fallan atómicamente.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "race_nonces.db")
        store = SqliteNonceStore(db_path=db_path)

        num_threads = 20
        barrier = threading.Barrier(num_threads)
        decision_id = "concurrent_decision_001"
        nonce = "single_use_crypto_nonce_xyz"
        results = []

        def worker():
            barrier.wait()  # Sincronización exacta en el microsegundo
            success = store.consume(decision_id=decision_id, nonce=nonce)
            return success

        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(worker) for _ in range(num_threads)]
            for fut in as_completed(futures):
                results.append(fut.result())

        # Exactamente 1 True, exactamente 19 False
        assert results.count(True) == 1, f"Se esperaba exactamente 1 True, se obtuvieron: {results.count(True)}"
        assert results.count(False) == (num_threads - 1), f"Se esperaban {num_threads - 1} False, se obtuvieron: {results.count(False)}"
        assert len(store) == 1


@pytest.mark.parametrize("num_threads", [20, 50, 100])
def test_secure_executor_concurrent_receipt_replay_prevention(num_threads):
    """Demuestra formalmente (P0.3) que bajo contienda masiva (20, 50 y 100 workers simultáneos),
    intentar ejecutar el mismo capability receipt mediante SecureExecutor resulta invariablemente
    en exactamente 1 ejecución exitosa y N-1 PolicyViolation por rechazo atómico de nonce/replay.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, f"executor_race_nonces_{num_threads}.db")
        store = SqliteNonceStore(db_path=db_path)
        secret_key = "test-concurrent-secret-key-32-chars"
        executor = SecureExecutor(
            dry_run=True,
            secret_key=secret_key,
            nonce_store=store,
        )

        goal = Goal(objective=f"Concurrent capability stress test {num_threads}")
        state = SessionState(session_id=f"race_sess_{num_threads}", goal=goal)
        action = ActionCandidate(
            id=f"act_race_{num_threads}",
            description="Concurrent action execution",
            tool_call=ToolCall(tool_name="read_file", arguments={"path": "race.txt"}),
        )

        a_hash = compute_action_hash(action)
        s_hash = compute_state_hash(state)
        receipt = DecisionReceipt(
            decision_id=f"race_dec_exec_{num_threads}",
            action_id=action.id,
            action_hash=a_hash,
            state_hash=s_hash,
            session_id=state.session_id,
            decision_status=DecisionStatus.ALLOW,
            nonce=f"unique_token_race_{num_threads}",
            issued_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
        signed = sign_receipt(receipt, secret_key)

        barrier = threading.Barrier(num_threads)
        successes = []
        violations = []

        def worker():
            barrier.wait()
            try:
                obs = executor.execute(action=action, state=state, receipt=signed)
                return ("SUCCESS", obs)
            except PolicyViolation as pv:
                return ("VIOLATION", str(pv))

        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(worker) for _ in range(num_threads)]
            for fut in as_completed(futures):
                status, payload = fut.result()
                if status == "SUCCESS":
                    successes.append(payload)
                else:
                    violations.append(payload)

        assert len(successes) == 1, (
            f"Se esperaba exactamente 1 éxito con {num_threads} hilos, pero hubo {len(successes)}"
        )
        assert len(violations) == (num_threads - 1), (
            f"Se esperaban {num_threads - 1} violaciones por replay, pero hubo {len(violations)}"
        )
        for msg in violations:
            assert "ya ha sido consumido" in msg or "Replay detectado" in msg


def test_sqlite_nonce_store_multiple_distinct_nonces_concurrent():
    """Verifica que el almacén SQLite soporte múltiples nonces independientes en concurrencia
    sin deadlocks ni corrupción de base de datos en modo WAL.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "wal_concurrency.db")
        store = SqliteNonceStore(db_path=db_path)

        num_threads = 20
        barrier = threading.Barrier(num_threads)

        def worker(worker_id: int):
            barrier.wait()
            dec_id = f"dec_worker_{worker_id}"
            nonce = f"nonce_worker_{worker_id}"
            return store.consume(decision_id=dec_id, nonce=nonce)

        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(worker, i) for i in range(num_threads)]
            results = [f.result() for f in as_completed(futures)]

        # Todos son nonces distintos, por ende todos deben ser consumidos exitosamente
        assert all(results)
        assert len(store) == num_threads
