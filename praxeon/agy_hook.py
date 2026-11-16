"""Hook PreToolUse de agy con lista de herramientas permitidas (FE-015). Solo biblioteca estándar y FALLA CERRADO.

Lo ejecuta agy antes de CADA herramienta (también las de sus subagentes) con un JSON por stdin que lleva
``toolCall.name`` y ``toolCall.args``. Uso: ``agy_hook.py <policy.json> <state_dir>``. Sale con 0 para permitir y con 2 para bloquear.

La política la escribe PRAXEON en la carpeta temporal de la sesión: ``{"tools": [...], "subagents": [...], "caps": {...}}``.
  * ``tools``: nombres de herramienta que se dejan pasar. ``invoke_subagent`` nunca va aquí: solo se permite para los tipos de
    ``subagents`` (p. ej. ``image-generator``) y siempre con el espacio de trabajo heredado.
  * ``caps``: tope de llamadas permitidas de esa herramienta por turno (el turno lo anota PRAXEON en ``<state_dir>/turn``).

Cualquier error (política ilegible, entrada rara, disco lleno) bloquea: lo que no está expresamente permitido no se ejecuta.
Cada decisión se anota en ``<state_dir>/hook_events.jsonl`` (sin los prompts completos).
"""

import json
import os
import sys
import time

DETAIL_FIELDS = {"invoke_subagent": "TypeName", "generate_image": "ImageName", "search_web": "query"}
MAX_DETAIL = 120


def _read_int(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _count_allowed(events_path: str, name: str, turn: int) -> int:
    count = 0
    try:
        with open(events_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("decision") == "allow" and rec.get("name") == name and rec.get("turn") == turn:
                    count += 1
    except OSError:
        pass
    return count


def _append(path: str, record: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def decide(policy: dict, name: str, args: dict, used: int | None = None) -> tuple[bool, str]:
    """(permitido, motivo). ``used`` = llamadas ya permitidas de esta herramienta en el turno."""
    if name == "invoke_subagent":
        if args.get("TypeName") not in (policy.get("subagents") or []):
            return False, "tipo de subagente no permitido"
        if args.get("Workspace", "inherit") != "inherit":
            return False, "el subagente solo puede usar el espacio de trabajo heredado"
    elif name not in (policy.get("tools") or []):
        return False, "herramienta no activada"
    cap = (policy.get("caps") or {}).get(name)
    if isinstance(cap, int) and used is not None and used >= cap:
        return False, f"tope de {cap} llamadas por turno alcanzado"
    return True, "permitida"


def main(argv: list[str]) -> int:
    name = "?"
    try:
        policy_path, state_dir = argv[1], argv[2]
        with open(policy_path, encoding="utf-8") as f:
            policy = json.load(f)
        data = json.load(sys.stdin)
        call = data.get("toolCall")
        if not isinstance(call, dict) or not isinstance(call.get("name"), str):
            raise ValueError("entrada sin toolCall.name")
        name = call["name"]
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        turn = _read_int(os.path.join(state_dir, "turn"))
        events = os.path.join(state_dir, "hook_events.jsonl")
        allowed, why = decide(policy, name, args, _count_allowed(events, name, turn))
        field = DETAIL_FIELDS.get(name)
        detail = str(args.get(field, ""))[:MAX_DETAIL] if field else ""
        _append(events, {"t": time.time(), "turn": turn, "decision": "allow" if allowed else "deny", "name": name, "why": why, "detail": detail})
        if allowed:
            return 0
        # ASCII a propósito: con acentos el mensaje llega roto al modelo. Le dice qué hacer en vez de dejarlo a ciegas: PRAXEON y agy usan los
        # MISMOS nombres (run_command, read_file…) y el modelo tiende a llamar a la herramienta nativa en vez de escribir la acción como texto.
        sys.stderr.write(
            f"PRAXEON: la herramienta propia '{name}' de agy no esta activada ({why}). No uses tus herramientas propias: "
            f"para actuar, responde con TEXTO en el formato Thought/Action de las instrucciones (por ejemplo Action: {name}(...)) y PRAXEON lo ejecutara por ti."
        )
        return 2
    except BaseException as exc:  # noqa: BLE001 - falla cerrado: cualquier error bloquea
        try:
            sys.stderr.write(f"PRAXEON: el hook de herramientas bloqueo '{name}' por un error: {type(exc).__name__}")
        except BaseException:  # noqa: BLE001, S110
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
