"""Graba flujos de eventos REALES del backend (runtime en proceso) para los fixtures del reducer de sesión (FE-007).

Uso (desde la raíz del repo, con el entorno del proyecto): python -I web/scripts/record_session_events.py <carpeta_de_salida>
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

for key in ("PRAXEON_API_KEY", "PRAXEON_SECRET_KEY", "PRAXEON_PROFILE", "PRAXEON_ENV", "PRAXEON_REQUIRE_AUTH"):
    os.environ.pop(key, None)

from fastapi.testclient import TestClient  # noqa: E402

from praxeon.server.app import create_app  # noqa: E402
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service  # noqa: E402

out_dir = Path(sys.argv[1])
out_dir.mkdir(parents=True, exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="fe007-"))
service = RuntimeApplicationService(db_dir=str(tmp / "db"))
set_runtime_service(service)
client = TestClient(create_app(profile="dev"))

(tmp / "ws").mkdir()
(tmp / "ws" / "hola.txt").write_text("hola mundo\n", encoding="utf-8")


def events_of(sid):
    allv = []
    after = 0
    for _ in range(20):
        r = client.get(f"/v1/sessions/{sid}/events", params={"after_sequence": after, "limit": 1000}).json()["data"]
        allv.extend(r["events"])
        if not r.get("has_more") or r["latest_sequence"] <= after:
            break
        after = r["latest_sequence"]
    return allv


def wait_done(sid, secs=40):
    t0 = time.time()
    last = -1
    while time.time() - t0 < secs:
        evs = events_of(sid)
        if any(e["type"] == "session.completed" for e in evs):
            return evs
        if len(evs) == last and time.time() - t0 > 10:
            return evs
        last = len(evs)
        time.sleep(0.5)
    return events_of(sid)


def save(name, sid, data=None):
    evs = events_of(sid)
    (out_dir / f"{name}.json").write_text(json.dumps({"session": data, "events": evs}, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = sorted({e["type"] for e in evs})
    print(name, len(evs), kinds)


# 1) misión con el simulador, modo restringido
r = client.post("/v1/sessions/run", json={"goal": "Lee hola.txt y resume su contenido", "llm_provider": "simulator", "supervisor": "laya", "execution_mode": "local_restricted", "max_steps": 6, "step_delay_ms": 0, "workspace_root": str(tmp / "ws"), "session_id": "rec-sim"})
print("run", r.status_code, r.text[:300])
d = r.json().get("data")
if d:
    wait_done(d["session_id"])
    save("mission_simulator", d["session_id"], d)

# 2) acciones manuales: ALLOW, REVIEW, BLOCK y aprobación
service.create_session(goal="manual", session_id="rec-manual", execution_mode="local_restricted", workspace_root=str(tmp / "ws"), metadata={})
from praxeon.server.schemas.action import ProposeActionRequest  # noqa: E402

def propose(i, tool, args):
    return service.propose_action(session_id="rec-manual", proposal=ProposeActionRequest(action_id=f"a{i}", tool=tool, operation=f"{i}. {tool}", arguments=args, thought_rationale="x"))

out = []
for i, (tool, args) in enumerate([("read_file", {"path": "hola.txt"}), ("run_command", {"command": "git push origin main"}), ("run_command", {"command": "rm -rf /"})], 1):
    try:
        resp = propose(i, tool, args)
        out.append((tool, resp.status))
    except Exception as exc:  # noqa: BLE001
        out.append((tool, f"ERR {exc}"))
print(out)
def dec_of(action):
    for e in events_of("rec-manual"):
        if e["type"] == "action.proposed" and e["node_id"] == action:
            return e["decision_id"]

def cap_of(action):
    for e in events_of("rec-manual"):
        if e["type"] == "capability.issued" and e["node_id"] == action:
            return e["payload"]
for label, call in [
    ("confirm a2", lambda: client.post(f"/v1/decisions/{dec_of('a2')}/confirm", json={"approved": True, "reason": "ok", "operator_id": "operator_admin", "role": "operator"})),
    ("execute a1", lambda: client.post(f"/v1/decisions/{dec_of('a1')}/execute", json={"capability_token": cap_of("a1"), "operator_id": "operator_admin", "role": "operator"})),
    ("execute a2", lambda: client.post(f"/v1/decisions/{dec_of('a2')}/execute", json={"capability_token": cap_of("a2"), "operator_id": "operator_admin", "role": "operator"})),
]:
    r = call()
    print(label, r.status_code, r.text[:160])
save("manual_actions", "rec-manual")
