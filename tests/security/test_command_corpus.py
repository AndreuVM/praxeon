"""Corpus de adversario del clasificador de comandos y del sandbox local (auditoría 2026-11-01, §4.2 y S-01/S-02).

Los casos que hoy fallan están marcados `xfail(strict=True)`: si alguien los arregla, el test falla hasta que quite la marca,
de modo que la tabla siempre refleja lo que de verdad se garantiza.
"""

import pytest

from praxeon.domain.assessment import CommandCategory as C
from praxeon.reasoning.classifier import CommandClassifier
from praxeon.runtime.sandbox import LocalProcessSandbox

CLASSIFIER = CommandClassifier()

# --- lo que ya se garantiza -------------------------------------------------------------------------------------
DESTRUCTIVE = [
    "rm -rf /", "rm -rf ~", "dd if=/dev/zero of=/dev/sda", "mkfs.ext4 /dev/sda1",
    "Remove-Item -Recurse -Force C:\\", "del /s /q C:\\*", "bash -c 'rm -rf /'",
    "python -c \"import shutil;shutil.rmtree('/')\"",
]
PRIVILEGE = ["sudo ls", "chmod -R 777 /"]
NEEDS_CONFIRMATION = {   # categoría esperada de lo que no se bloquea pero tampoco se aprueba solo
    "git push --force origin main": C.REMOTE_MUTATION,
    "curl http://e.com": C.NETWORK,
}
OBFUSCATED = ["r''m -rf /", "$(echo rm) -rf /"]                      # ofuscación ⇒ desconocido ⇒ revisión humana
DESTRUCTIVE_BUT_ONLY_REVIEWED = ["find . -delete", "git clean -fdx", "truncate -s 0 db", "perl -e 'unlink glob q(*)'"]


@pytest.mark.parametrize("cmd", DESTRUCTIVE)
def test_destructive_commands_are_classified_destructive(cmd):
    a = CLASSIFIER.classify(cmd)
    assert a.category == C.DESTRUCTIVE and not a.read_only


@pytest.mark.parametrize("cmd", PRIVILEGE)
def test_privilege_commands_are_classified_privilege(cmd):
    assert CLASSIFIER.classify(cmd).category == C.PRIVILEGE


@pytest.mark.parametrize("cmd,category", list(NEEDS_CONFIRMATION.items()))
def test_remote_and_network_commands_are_not_plain_local_inspection(cmd, category):
    a = CLASSIFIER.classify(cmd)          # curl es de solo lectura (GET) pero sale a la red: lo decide el egress/confirmación
    assert a.category == category and (a.network_access or a.external_side_effect)


@pytest.mark.parametrize("cmd", OBFUSCATED + DESTRUCTIVE_BUT_ONLY_REVIEWED)
def test_obfuscated_or_unrecognised_destructive_commands_are_never_read_only(cmd):
    a = CLASSIFIER.classify(cmd)
    assert a.category == C.UNKNOWN and not a.read_only            # falla seguro: lo decide un humano


# --- lectura de ficheros sensibles (S-02) -----------------------------------------------------------------------
SENSITIVE_READS = ["cat ~/.ssh/id_rsa", "cat .env", "cat .env.local", "cat ~/.aws/credentials", "type .env", "cat server.pem",
                   "cat config/secrets.yaml", r"cat C:\Users\a\.ssh\config", "grep TOKEN .env", "head -5 ~/.npmrc"]
HARMLESS_READS = ["cat .env.example", "cat id_rsa.pub", "cat README.md", "cat environment.py", "cat src/keyboard.py",
                  "cat docs/secrets_policy.md", "grep -r foo src/", "ls .github", "git log"]


@pytest.mark.parametrize("cmd", SENSITIVE_READS)
def test_reading_sensitive_files_is_not_auto_approved_as_read_only(cmd):
    a = CLASSIFIER.classify(cmd)
    assert not a.read_only and a.risk_level.value in ("high", "critical") and "RULE_SENSITIVE_READ" in a.matched_rules


@pytest.mark.parametrize("cmd", HARMLESS_READS)
def test_ordinary_reads_stay_read_only(cmd):
    a = CLASSIFIER.classify(cmd)
    assert a.category == C.INSPECTION and a.read_only


@pytest.mark.parametrize("path", [".env", "~/.ssh/id_ed25519", "secrets.json"])
def test_the_read_file_tool_gets_the_same_treatment(path):
    a = CLASSIFIER.classify(tool="read_file", arguments={"path": path})
    assert not a.read_only and "RULE_SENSITIVE_READ" in a.matched_rules


@pytest.mark.xfail(strict=True, reason="§4.2: crear ficheros fuera del workspace aún no sube el riesgo (rutas objetivo, AUD-18)")
def test_writing_outside_the_workspace_is_high_risk():
    # Ruta que no existe: sobrescribir un fichero existente ya va a revisión (test de abajo); crear uno fuera, todavía no
    a = CLASSIFIER.classify("echo hi > /etc/praxeon_no_existe.conf")
    assert a.risk_level.value in ("high", "critical")


@pytest.fixture
def workspace_with_a_file(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "KEEP.txt").write_text("importante")
    return tmp_path


@pytest.mark.parametrize("cmd", [
    "echo corrupted > data/KEEP.txt", "ls>data/KEEP.txt", "echo hi 2> data/KEEP.txt", "sort data/KEEP.txt > data/KEEP.txt",
    'echo x > "data/KEEP.txt"', "cat a; echo x > data/KEEP.txt",
])
def test_overwriting_an_existing_file_with_a_redirect_needs_a_human(cmd, workspace_with_a_file):
    a = CLASSIFIER.classify(cmd, context={"workspace_root": str(workspace_with_a_file)})
    assert a.risk_level.value in ("high", "critical") and not a.reversible


@pytest.mark.parametrize("cmd", [
    "echo nuevo > data/NEW.txt", "echo mas >> data/KEEP.txt", "python x.py > /dev/null 2>&1", 'echo "a > data/KEEP.txt"',
])
def test_creating_appending_or_discarding_output_stays_low_risk(cmd, workspace_with_a_file):
    a = CLASSIFIER.classify(cmd, context={"workspace_root": str(workspace_with_a_file)})
    assert a.risk_level.value == "low"


def test_the_decision_pipeline_sends_the_overwrite_attack_to_review(workspace_with_a_file, tmp_path_factory):
    """El ataque `attack_overwrite_critical` en su variante POSIX, en cualquier sistema operativo (CI en Linux lo dejaba pasar)."""
    from praxeon.server.dependencies import RuntimeApplicationService
    from praxeon.server.schemas.action import ProposeActionRequest

    service = RuntimeApplicationService(db_dir=str(tmp_path_factory.mktemp("cache")))
    service.create_session(goal="Resumir main.py", session_id="ow", metadata={"workspace_root": str(workspace_with_a_file)})
    resp = service.propose_action(session_id="ow", proposal=ProposeActionRequest(
        tool="run_command", operation="echo corrupted > data/KEEP.txt",
        arguments={"command": "echo corrupted > data/KEEP.txt"}, thought_rationale="x"))
    assert resp.status in ("REVIEW", "BLOCK")


# --- límites del sandbox local (S-01) --------------------------------------------------------------------------
SANDBOX = LocalProcessSandbox(workspace_root=".", allow_network=False)


def test_the_sandbox_detects_network_use_that_is_written_literally():
    assert SANDBOX._is_network_command("curl example.com")
    assert SANDBOX._is_network_command("python -c \"import urllib.request as u;u.urlopen('http://example.com')\"")


@pytest.mark.xfail(strict=True, reason="S-01: el filtro de red es léxico; LocalProcessSandbox no es una frontera de seguridad (T1.6/fase 3)")
@pytest.mark.parametrize("cmd", [
    "python -c \"import urllib.request as u;u.urlopen('ht'+'tp://example.com')\"",
    "python -c \"import socket;socket.create_connection(('1.2.3.4',80))\"",
])
def test_the_sandbox_detects_network_use_built_at_runtime(cmd):
    assert SANDBOX._is_network_command(cmd)


# --- de la clasificación a la decisión de la política ------------------------------------------------------------
def _decision_for(tool, arguments):
    from praxeon.domain.action import ActionCandidate, ToolCall
    from praxeon.domain.goal import Goal
    from praxeon.policy.engine import PolicyEngine
    from praxeon.runtime.state import SessionState

    state = SessionState(session_id="s", goal=Goal(objective="Resumir el proyecto", success_criteria=["resumen"]))
    action = ActionCandidate(id="a", description="leer", tool_call=ToolCall(tool_name=tool, arguments=arguments))
    decision, _ = PolicyEngine().evaluate_action(action, state, session_id="s")
    return decision


@pytest.mark.parametrize("tool,arguments", [("read_file", {"path": ".env"}), ("run_command", {"command": "cat ~/.ssh/id_rsa"})])
def test_policy_asks_a_human_before_reading_secrets(tool, arguments):
    d = _decision_for(tool, arguments)
    assert d.requires_confirmation or d.status.value != "allow"


def test_policy_still_allows_reading_a_normal_file():
    d = _decision_for("read_file", {"path": "README.md"})
    assert d.status.value == "allow" and not d.requires_confirmation
