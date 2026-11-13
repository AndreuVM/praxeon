"""Entorno filtrado de los procesos hijo, terminación del árbol de procesos y Full Access opt-in (auditoría S-06, S-09)."""

import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from praxeon.runtime.child_env import child_environment, full_access_enabled, run_with_tree_timeout
from praxeon.runtime.executor import SecureExecutor
from praxeon.runtime.full_access import FullAccessExecutor
from praxeon.runtime.sandbox import LocalProcessSandbox


SECRETS = {
    "OPENAI_API_KEY": "sk-test",
    "MY_SERVICE_TOKEN": "tok",
    "PRAXEON_RECEIPT_KEY": "receipt",
    "DB_PASSWORD": "pw",
    "DATABASE_URL": "postgres://u:p@host/db",  # sin nombre de secreto, pero fuera de la lista blanca
}


def test_child_environment_keeps_what_tools_need_and_drops_the_rest():
    source = {"PATH": "/bin", "HOME": "/home/u", "LANG": "es_ES.UTF-8", "LC_ALL": "C", **SECRETS}
    env = child_environment(source)
    assert env == {"PATH": "/bin", "HOME": "/home/u", "LANG": "es_ES.UTF-8", "LC_ALL": "C"}


def test_the_operator_can_widen_the_allowlist_but_never_to_secrets():
    source = {"PATH": "/bin", "JAVA_HOME": "/jdk", "CONDA_PREFIX": "/c", "MY_API_KEY": "x",
              "PRAXEON_CHILD_ENV_ALLOW": "java_home, CONDA_*, MY_API_KEY"}
    env = child_environment(source)
    assert env["JAVA_HOME"] == "/jdk" and env["CONDA_PREFIX"] == "/c"
    assert "MY_API_KEY" not in env


def test_local_sandbox_does_not_inherit_unlisted_variables(monkeypatch, tmp_path):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    env = LocalProcessSandbox(workspace_root=str(tmp_path))._sanitize_environment()
    assert not set(SECRETS) & set(env)
    assert "PATH" in {k.upper() for k in env}


def test_full_access_commands_do_not_see_the_server_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host/db")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    command = "echo \"[$env:DATABASE_URL][$env:OPENAI_API_KEY]\"" if sys.platform == "win32" else 'echo "[$DATABASE_URL][$OPENAI_API_KEY]"'
    result = FullAccessExecutor(default_cwd=str(tmp_path)).execute_tool("run_command", {"command": command})
    assert result.success, result.output
    assert "[][]" in result.output


def test_timeout_kills_the_grandchildren_too(tmp_path):
    marker = tmp_path / "grandchild_survived.txt"
    grandchild = f"import time; time.sleep(3); open(r'{marker}', 'w').write('x')"
    child = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(30)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_with_tree_timeout([sys.executable, "-c", child], timeout=1.0)
    time.sleep(4)
    assert not marker.exists(), "el nieto siguió vivo tras el timeout"


def test_full_access_is_opt_in_per_process():
    assert full_access_enabled({}) is False
    assert full_access_enabled({"PRAXEON_ALLOW_FULL_ACCESS": "1"}) is True
    assert SecureExecutor(secret_key="k" * 32).allow_full_access is False


@pytest.fixture
def server_without_full_access(monkeypatch, tmp_path):
    from praxeon.server.app import create_app
    from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service

    for name in ("PRAXEON_PROFILE", "PRAXEON_ENV", "PRAXEON_SECRET_KEY", "PRAXEON_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PRAXEON_API_KEY", "t" * 32)
    monkeypatch.setenv("PRAXEON_ALLOW_FULL_ACCESS", "0")
    service = RuntimeApplicationService(db_dir=str(tmp_path / "cache"))
    set_runtime_service(service)
    try:
        yield TestClient(create_app(service=service), base_url="http://localhost:8000", headers={"X-API-Key": "t" * 32})
    finally:
        set_runtime_service(None)


def test_server_rejects_full_access_sessions_when_not_enabled(server_without_full_access):
    client = server_without_full_access
    res = client.post("/v1/sessions", json={"goal": "x", "execution_mode": "full_access", "full_access_confirmed": True})
    assert res.status_code == 403 and "Full Access" in res.text
    ok = client.post("/v1/sessions", json={"goal": "x", "execution_mode": "local_restricted"})
    assert ok.status_code == 201, ok.text
