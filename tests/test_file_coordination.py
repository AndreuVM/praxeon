"""Coordinación de escritura entre sesiones sobre un mismo workspace (FE-009, nota N-001)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.decision import DecisionStatus, PolicyDecision
from praxeon.domain.goal import Goal
from praxeon.runtime.executor import SecureExecutor
from praxeon.runtime.file_coordination import FileCoordinator, fingerprint, file_key
from praxeon.runtime.sandbox import LocalProcessSandbox
from praxeon.runtime.state import SessionState


def make(tmp_path: Path, **kw):
    coord = FileCoordinator()
    ex = SecureExecutor(
        sandbox=LocalProcessSandbox(workspace_root=str(tmp_path)),
        strict_capability=False,
        file_coordinator=coord,
        **kw,
    )
    return ex, coord


def state(tmp_path: Path, sid: str) -> SessionState:
    return SessionState(session_id=sid, goal=Goal(objective="g", success_criteria=["c"]), metadata={"workspace_root": str(tmp_path), "execution_mode": "local_restricted"})


def run(ex: SecureExecutor, st: SessionState, tool: str, **args):
    action = ActionCandidate(id=f"a-{tool}", description=tool, tool_call=ToolCall(tool_name=tool, arguments=args))
    return ex.execute(action, st, decision=PolicyDecision(status=DecisionStatus.ALLOW))


def read(ex, tmp_path, sid, path="f.txt"):
    return run(ex, state(tmp_path, sid), "read_file", path=path)


def write(ex, tmp_path, sid, content, path="f.txt"):
    return run(ex, state(tmp_path, sid), "edit_file", path=path, content=content)


def test_a_lee_b_escribe_a_escribe_es_conflicto_y_no_pisa(tmp_path):
    ex, _ = make(tmp_path)
    (tmp_path / "f.txt").write_text("original", encoding="utf-8")
    assert read(ex, tmp_path, "A").success
    assert read(ex, tmp_path, "B").success
    assert write(ex, tmp_path, "B", "de B").success

    res = write(ex, tmp_path, "A", "de A")
    assert res.success is False and res.is_error is True
    assert res.file_conflict is not None
    assert res.file_conflict["reason"] == "modified_since_read"
    assert res.file_conflict["conflicting_session"] == "B"
    assert "sesión 'B'" in res.output and "a las " in res.output and "vuelve a leerlo" in res.output.lower()
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "de B"          # lo de B sigue intacto


def test_a_relee_y_entonces_puede_escribir(tmp_path):
    ex, _ = make(tmp_path)
    (tmp_path / "f.txt").write_text("original", encoding="utf-8")
    read(ex, tmp_path, "A")
    write(ex, tmp_path, "B", "de B")
    assert write(ex, tmp_path, "A", "de A").file_conflict is not None
    assert read(ex, tmp_path, "A").output.startswith("de B") or "de B" in read(ex, tmp_path, "A").output
    res = write(ex, tmp_path, "A", "de A sobre de B")
    assert res.success and res.file_conflict is None
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "de A sobre de B"


def test_archivos_distintos_no_se_afectan(tmp_path):
    ex, _ = make(tmp_path)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    read(ex, tmp_path, "A", "a.txt")
    read(ex, tmp_path, "B", "b.txt")
    assert write(ex, tmp_path, "B", "b2", "b.txt").success
    assert write(ex, tmp_path, "A", "a2", "a.txt").success


def test_sin_anotacion_previa_se_puede_escribir_y_crear(tmp_path):
    ex, _ = make(tmp_path)
    assert write(ex, tmp_path, "A", "nuevo", "nuevo.txt").success
    (tmp_path / "ya.txt").write_text("x", encoding="utf-8")
    assert write(ex, tmp_path, "A", "y", "ya.txt").success         # existe pero nadie lo ha leído ni escrito: no hay conflicto


def test_sobrescribir_a_ciegas_lo_que_otra_sesion_escribio_se_rechaza(tmp_path):
    ex, _ = make(tmp_path)
    assert write(ex, tmp_path, "B", "de B").success
    res = write(ex, tmp_path, "A", "de A")
    assert res.file_conflict is not None and res.file_conflict["reason"] == "unread_overwrite"
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "de B"
    read(ex, tmp_path, "A")
    assert write(ex, tmp_path, "A", "de A").success


def test_la_misma_sesion_puede_seguir_editando_sin_releer(tmp_path):
    ex, _ = make(tmp_path)
    (tmp_path / "f.txt").write_text("v0", encoding="utf-8")
    read(ex, tmp_path, "A")
    assert write(ex, tmp_path, "A", "v1").success
    assert write(ex, tmp_path, "A", "v2").success


def test_un_cambio_fuera_de_las_sesiones_tambien_se_detecta(tmp_path):
    ex, _ = make(tmp_path)
    (tmp_path / "f.txt").write_text("v0", encoding="utf-8")
    read(ex, tmp_path, "A")
    (tmp_path / "f.txt").write_text("editado a mano", encoding="utf-8")   # p. ej. el editor de la interfaz o una CLI
    res = write(ex, tmp_path, "A", "de A")
    assert res.file_conflict is not None
    assert res.file_conflict["conflicting_session"] is None
    assert "fuera de esta sesión" in res.output


def test_archivo_eliminado_despues_de_leerlo(tmp_path):
    ex, _ = make(tmp_path)
    (tmp_path / "f.txt").write_text("v0", encoding="utf-8")
    read(ex, tmp_path, "A")
    (tmp_path / "f.txt").unlink()
    res = write(ex, tmp_path, "A", "de A")
    assert res.file_conflict is not None and res.file_conflict["reason"] == "deleted"
    assert not (tmp_path / "f.txt").exists()


def test_misma_ruta_con_distinta_escritura_o_alias_es_el_mismo_archivo(tmp_path):
    ex, _ = make(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.txt").write_text("v0", encoding="utf-8")
    read(ex, tmp_path, "A", "sub/f.txt")
    write(ex, tmp_path, "B", "de B", "./sub/../sub/f.txt")
    assert write(ex, tmp_path, "A", "de A", "sub/f.txt").file_conflict is not None


def test_una_lectura_fallida_o_una_herramienta_ajena_no_anotan_nada(tmp_path):
    ex, coord = make(tmp_path)
    assert read(ex, tmp_path, "A", "no-existe.txt").success is False
    assert write(ex, tmp_path, "B", "de B", "no-existe.txt").success
    # A «vio» que no existía: B lo creó después -> conflicto (se pisaría lo de B)
    assert write(ex, tmp_path, "A", "de A", "no-existe.txt").file_conflict is not None
    assert coord.begin("run_command", {"command": "ls"}, "A", str(tmp_path)) is None
    assert coord.begin("edit_file", {}, "A", str(tmp_path)) is None


def test_sin_coordinador_o_en_dry_run_todo_sigue_igual(tmp_path):
    ex = SecureExecutor(sandbox=LocalProcessSandbox(workspace_root=str(tmp_path)), strict_capability=False)
    (tmp_path / "f.txt").write_text("v0", encoding="utf-8")
    read(ex, tmp_path, "A")
    write(ex, tmp_path, "B", "de B")
    assert write(ex, tmp_path, "A", "de A").success                       # sin coordinador gana el último, como antes
    ex_dry = SecureExecutor(dry_run=True, strict_capability=False, file_coordinator=FileCoordinator())
    assert run(ex_dry, state(tmp_path, "A"), "edit_file", path="f.txt", content="x").file_conflict is None


def test_forget_session_olvida_lo_visto_pero_no_lo_escrito(tmp_path):
    ex, coord = make(tmp_path)
    (tmp_path / "f.txt").write_text("v0", encoding="utf-8")
    read(ex, tmp_path, "A")
    write(ex, tmp_path, "B", "de B")
    coord.forget_session("A")
    res = write(ex, tmp_path, "A", "de A")                                # ya no recuerda qué vio, pero B escribió y A no lo ha leído
    assert res.file_conflict is not None and res.file_conflict["reason"] == "unread_overwrite"


def test_el_registro_no_crece_sin_limite(tmp_path):
    coord = FileCoordinator(max_seen=5)
    for i in range(50):
        (tmp_path / f"{i}.txt").write_text("x", encoding="utf-8")
        g = coord.begin("read_file", {"path": f"{i}.txt"}, "A", str(tmp_path))
        assert g is not None
        g.release()
    assert len(coord._seen) == 5


def test_huella_de_archivos_grandes_y_ausentes(tmp_path):
    assert fingerprint(str(tmp_path / "no.txt")) is None
    assert fingerprint(str(tmp_path)) is None                             # una carpeta no es un archivo
    p = tmp_path / "x.txt"
    p.write_text("a", encoding="utf-8")
    h1 = fingerprint(str(p))
    p.write_text("b", encoding="utf-8")
    assert fingerprint(str(p)) != h1
    assert file_key(str(tmp_path), "x.txt") == file_key(str(tmp_path), "./x.txt")


# ------------------------------------------------------------------ hilos ----------------------------------------------------------
def test_dos_hilos_escriben_a_la_vez_y_solo_gana_uno(tmp_path):
    """A y B leen y luego escriben en paralelo muchas veces: nunca se pisan; cada ronda tiene exactamente un ganador."""
    ex, _ = make(tmp_path)
    target = tmp_path / "f.txt"
    for ronda in range(40):
        target.write_text(f"base{ronda}", encoding="utf-8")
        read(ex, tmp_path, "A")
        read(ex, tmp_path, "B")
        barrier = threading.Barrier(2)
        results: dict[str, object] = {}

        def worker(sid: str, ronda: int = ronda) -> None:
            barrier.wait()
            results[sid] = write(ex, tmp_path, sid, f"de {sid} {ronda}")

        ts = [threading.Thread(target=worker, args=(s,)) for s in ("A", "B")]
        [t.start() for t in ts]
        [t.join() for t in ts]
        winners = [s for s, r in results.items() if r.success]          # type: ignore[attr-defined]
        losers = [s for s, r in results.items() if not r.success]        # type: ignore[attr-defined]
        assert len(winners) == 1 and len(losers) == 1, f"ronda {ronda}: {results}"
        assert results[losers[0]].file_conflict is not None               # type: ignore[attr-defined]
        assert target.read_text(encoding="utf-8") == f"de {winners[0]} {ronda}"


def test_muchas_sesiones_sobre_archivos_distintos_no_se_bloquean(tmp_path):
    ex, _ = make(tmp_path)
    for i in range(8):
        (tmp_path / f"{i}.txt").write_text("v0", encoding="utf-8")
    errors: list[str] = []

    def worker(i: int) -> None:
        sid = f"S{i}"
        for n in range(20):
            if not read(ex, tmp_path, sid, f"{i}.txt").success or not write(ex, tmp_path, sid, f"{sid}-{n}", f"{i}.txt").success:
                errors.append(f"{sid} ronda {n}")

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join(timeout=60) for t in ts]
    assert not any(t.is_alive() for t in ts), "interbloqueo"
    assert errors == []


# ------------------------------------------------------------------ evento de extremo a extremo ------------------------------------
def test_la_api_emite_workspace_file_conflict_al_ejecutar(tmp_path, monkeypatch):
    for key in ("PRAXEON_API_KEY", "PRAXEON_SECRET_KEY", "PRAXEON_PROFILE", "PRAXEON_ENV", "PRAXEON_REQUIRE_AUTH"):
        monkeypatch.delenv(key, raising=False)
    from praxeon.server.app import create_app
    from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service
    from praxeon.server.schemas.action import ProposeActionRequest

    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.txt").write_text("original", encoding="utf-8")
    try:
        client = TestClient(create_app(profile="dev"))
        for sid in ("sa", "sb"):
            service.create_session(goal="g", session_id=sid, execution_mode="local_restricted", workspace_root=str(ws), metadata={})

        def go(sid: str, i: int, tool: str, args: dict):
            resp = service.propose_action(session_id=sid, proposal=ProposeActionRequest(action_id=f"{sid}{i}", tool=tool, operation=f"{i}. {tool}", arguments=args, thought_rationale="x"))
            dec = next(e["decision_id"] for e in client.get(f"/v1/sessions/{sid}/events", params={"limit": 1000}).json()["data"]["events"] if e["type"] == "action.proposed" and e["node_id"] == f"{sid}{i}")
            cap = next((e["payload"] for e in client.get(f"/v1/sessions/{sid}/events", params={"limit": 1000}).json()["data"]["events"] if e["type"] == "capability.issued" and e["node_id"] == f"{sid}{i}"), None)
            return resp, client.post(f"/v1/decisions/{dec}/execute", json={"capability_token": cap, "operator_id": "operator_admin", "role": "operator"})

        go("sa", 1, "read_file", {"path": "f.txt"})
        go("sb", 1, "edit_file", {"path": "f.txt", "content": "de B"})
        _, res = go("sa", 2, "edit_file", {"path": "f.txt", "content": "de A"})
        events = client.get("/v1/sessions/sa/events", params={"limit": 1000}).json()["data"]["events"]
        conflicts = [e for e in events if e["type"] == "workspace.file_conflict"]
        if res.status_code == 200:
            assert conflicts, [e["type"] for e in events]
            assert conflicts[0]["payload"]["conflicting_session"] == "sb"
            assert conflicts[0]["payload"]["path"] == "f.txt"
            assert (ws / "f.txt").read_text(encoding="utf-8") == "de B"
        else:                                                                # la política exige otra confirmación en este entorno
            pytest.skip(f"la ejecución física fue denegada por política ({res.status_code})")
    finally:
        set_runtime_service(None)
