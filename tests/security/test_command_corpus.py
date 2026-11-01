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


# --- huecos conocidos (S-02, §4.2) -----------------------------------------------------------------------------
SENSITIVE_READS = ["cat ~/.ssh/id_rsa", "cat .env", "cat ~/.aws/credentials", "type .env"]


@pytest.mark.xfail(strict=True, reason="S-02: la lectura de ficheros sensibles aún se aprueba como solo lectura (tarea T1.3)")
@pytest.mark.parametrize("cmd", SENSITIVE_READS)
def test_reading_sensitive_files_is_not_auto_approved_as_read_only(cmd):
    a = CLASSIFIER.classify(cmd)
    assert not (a.category == C.INSPECTION and a.read_only)


@pytest.mark.xfail(strict=True, reason="§4.2: escribir fuera del workspace aún no sube el riesgo (tarea de rutas objetivo, fase 3)")
def test_writing_outside_the_workspace_is_high_risk():
    a = CLASSIFIER.classify("echo hi > /etc/passwd")
    assert a.risk_level.value in ("high", "critical")


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
