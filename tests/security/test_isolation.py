"""Aislamiento fuerte de la ejecución (auditoría 2026-11-01 S-01, AUD-19).

Unitarias en cualquier sistema (elección del backend, modo por defecto de las sesiones, argumentos de bubblewrap y del
contenedor, falla cerrado sin backend) y de integración con el aislamiento real: AppContainer en Windows y bubblewrap
en Linux (el CI instala bwrap). Las de integración comprueban lo que el sandbox local no garantiza: sin red aunque la
dirección se construya al ejecutar, sin acceso fuera del workspace y sin procesos vivos tras el tiempo límite.
"""

import os
import subprocess
import sys

import pytest

from praxeon.runtime import isolation
from praxeon.runtime.isolation import bubblewrap
from praxeon.runtime.isolation.sandbox import IsolatedSandbox, _powershell_in
from praxeon.runtime.sandbox import ContainerSandboxAdapter, ContainerSandboxConfig, SandboxTier, SandboxViolation


@pytest.fixture
def isolation_env(monkeypatch):
    """Devuelve una función que fija PRAXEON_ISOLATION y vuelve a detectar; al acabar, la suite vuelve a «off»."""

    def set_to(value):
        monkeypatch.setenv("PRAXEON_ISOLATION", value)
        return isolation.refresh()

    yield set_to
    monkeypatch.setenv("PRAXEON_ISOLATION", "off")
    isolation.refresh()


def fake_status(monkeypatch, backend):
    st = isolation.IsolationStatus("auto", backend, "prueba" if backend else "sin backend en la prueba")
    monkeypatch.setattr(isolation, "status", lambda: st)
    return st


# --- elección del backend y modo por defecto -----------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [("", "auto"), ("auto", "auto"), ("off", "off"), ("none", "off"), ("0", "off"),
                                            ("appcontainer", "appcontainer"), ("BUBBLEWRAP", "bubblewrap"),
                                            ("container", "container"), ("algo", "auto")])
def test_requested_backend_parsing(value, expected, monkeypatch):
    monkeypatch.setenv("PRAXEON_ISOLATION", value)
    assert isolation.requested_backend() == expected


def test_auto_picks_the_native_backend_of_the_system(isolation_env):
    st = isolation_env("auto")
    native = {"win32": "appcontainer", "linux": "bubblewrap"}.get(sys.platform)
    assert st.backend in (native, None)                     # None: el sistema no lo permite (p. ej. sin bwrap)
    assert st.backend != "container"                        # Docker nunca se elige solo: no tiene las herramientas


def test_off_disables_isolation(isolation_env):
    st = isolation_env("off")
    assert not st.available and "desactivado" in st.reason


@pytest.mark.parametrize("requested", [None, "", "auto"])
def test_sessions_without_a_mode_are_isolated_when_possible(requested, monkeypatch):
    fake_status(monkeypatch, "bubblewrap")
    assert isolation.resolve_execution_mode(requested) == ("container", None)
    fake_status(monkeypatch, None)
    mode, warning = isolation.resolve_execution_mode(requested)
    assert mode == "local_restricted" and "Sin aislamiento fuerte" in warning


@pytest.mark.parametrize("mode", ["local_restricted", "full_access"])
def test_an_explicit_mode_is_respected(mode, monkeypatch):
    fake_status(monkeypatch, "bubblewrap")
    assert isolation.resolve_execution_mode(mode) == (mode, None)


def test_explicit_isolation_without_backend_warns_that_commands_will_be_refused(monkeypatch):
    fake_status(monkeypatch, None)
    mode, warning = isolation.resolve_execution_mode("container")
    assert mode == "container" and "se rechazarán" in warning


def test_create_session_records_the_effective_mode_and_the_warning(monkeypatch, tmp_path):
    from praxeon.server.dependencies import RuntimeApplicationService

    service = RuntimeApplicationService(db_dir=str(tmp_path / "cache"))
    fake_status(monkeypatch, "bubblewrap")
    isolated = service.get_session_summary(service.create_session(goal="x")["session_id"])
    assert (isolated["execution_mode"], isolated["isolation_backend"], isolated["isolation_warning"]) == ("container", "bubblewrap", None)
    fake_status(monkeypatch, None)
    local = service.get_session_summary(service.create_session(goal="x")["session_id"])
    assert local["execution_mode"] == "local_restricted" and local["isolation_warning"]
    started = [e for e in service.event_bus.get_all_events(local["session_id"]) if e.type.value == "session.started"][0]
    assert started.payload["isolation_warning"] == local["isolation_warning"]


# --- el sandbox aislado ---------------------------------------------------------------------------------------
def test_isolated_sandbox_without_backend_fails_closed(tmp_path, monkeypatch):
    fake_status(monkeypatch, None)
    with pytest.raises(SandboxViolation, match="no hay backend"):
        IsolatedSandbox(workspace_root=str(tmp_path)).execute_command("echo hola")


def test_the_executor_runs_isolated_mode_in_the_isolated_sandbox(tmp_path, monkeypatch):
    from praxeon.runtime.executor import SecureExecutor
    from praxeon.runtime.state import SessionState
    from praxeon.domain.goal import Goal

    fake_status(monkeypatch, None)
    executor = SecureExecutor(insecure_skip_signature=True)
    state = SessionState(session_id="s", goal=Goal(objective="x"), metadata={"workspace_root": str(tmp_path)})
    sandbox = executor._get_effective_container_sandbox(state)
    assert isinstance(sandbox, IsolatedSandbox) and sandbox.workspace_root == os.path.realpath(tmp_path)
    assert executor._get_effective_container_sandbox(state) is sandbox          # se reutiliza por workspace


def test_powershell_prefix_enters_the_workspace_through_a_drive():
    ws = os.path.realpath(os.path.join(os.sep, "w o", "it's"))
    prefix = _powershell_in(ws, os.path.join(ws, "src"))
    assert "New-PSDrive -Name PRAXEON -PSProvider FileSystem -Root '" in prefix
    assert "it''s" in prefix and "PRAXEON:\\src'" in prefix and prefix.endswith("; ")


# --- bubblewrap (argumentos) -------------------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform == "win32", reason="rutas POSIX")
def test_bubblewrap_arguments(monkeypatch, tmp_path):
    monkeypatch.setattr(bubblewrap, "binary", lambda: "/usr/bin/bwrap")
    ws = os.path.realpath(tmp_path)
    args = bubblewrap.wrap(["/bin/sh", "-c", "ls"], workspace_root=ws, cwd=ws, env={"PATH": "/usr/bin", "LANG": "C"},
                           read_paths=["/home"])
    joined = " ".join(args)
    assert args[0] == "/usr/bin/bwrap" and args[-4:] == ["--", "/bin/sh", "-c", "ls"]
    assert "--unshare-all" in args and "--share-net" not in args and "--die-with-parent" in args
    assert "--ro-bind / /" in joined and "--clearenv" in args
    assert f"--bind {ws} {ws}" in joined and f"--chdir {ws}" in joined
    for hidden in ("/home", "/root", "/run", "/tmp"):
        if os.path.isdir(hidden) and not os.path.islink(hidden):
            assert f"--tmpfs {hidden}" in joined
    assert f"--setenv HOME {ws}" in joined and "--setenv LANG C" in joined
    with_net = bubblewrap.wrap(["true"], workspace_root=ws, cwd=ws, env={}, allow_network=True)
    assert "--share-net" in with_net


# --- contenedor (argumentos y tiempo límite) -----------------------------------------------------------------
def test_container_is_killed_by_name_on_timeout(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[1] == "run":
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(ContainerSandboxAdapter, "_responds", classmethod(lambda cls, binary: True))
    adapter = ContainerSandboxAdapter(config=ContainerSandboxConfig(runtime_binary="docker"), workspace_root=str(tmp_path))
    adapter.runtime_binary = "docker"
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = adapter.execute_command("sleep 100", timeout=1)
    run_args = calls[0]
    name = next(a.split("=", 1)[1] for a in run_args if a.startswith("--name="))
    assert result.exit_code == 124 and ["docker", "kill", name] in calls
    assert "--network=none" in run_args and "--read-only" in run_args and "--cap-drop=ALL" in run_args
    if sys.platform != "win32":
        assert f"--user={os.getuid()}:{os.getgid()}" in run_args


def test_container_image_is_configurable(monkeypatch):
    monkeypatch.setenv("PRAXEON_CONTAINER_IMAGE", "ghcr.io/ejemplo/dev:1")
    assert ContainerSandboxConfig().image == "ghcr.io/ejemplo/dev:1"


def test_container_availability_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(ContainerSandboxAdapter, "_availability", {})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0))
    assert ContainerSandboxAdapter._responds("docker") and ContainerSandboxAdapter._responds("docker")
    assert len(calls) == 1


# --- integración: aislamiento real -------------------------------------------------------------------------------
@pytest.fixture
def real_isolation(isolation_env, tmp_path, monkeypatch):
    st = isolation_env("auto")
    if not st.available:
        pytest.skip(f"sin aislamiento nativo en este equipo: {st.reason}")
    if st.backend == "appcontainer":
        from praxeon.runtime.isolation import appcontainer

        monkeypatch.setattr(appcontainer, "_grants_file", lambda: str(tmp_path / "grants.json"))
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    outside = tmp_path / "fuera"
    outside.mkdir()
    (outside / "secreto.txt").write_text("CLAVE")
    yield IsolatedSandbox(workspace_root=str(ws)), ws, outside
    if st.backend == "appcontainer":
        from praxeon.runtime.isolation import appcontainer

        appcontainer.revoke_all()                     # retira los permisos concedidos y borra el perfil de la prueba
        assert appcontainer.granted_containers() == {}


def run_script(sandbox, ws, code, *, cwd=None, timeout=60):
    """Escribe ``code`` como script dentro del workspace y lo ejecuta aislado (sin pelearse con las comillas del shell)."""
    script = ws / "sonda.py"
    script.write_text(code, encoding="utf-8")
    # El intérprete base, no el del entorno virtual de la suite: ese vive fuera del workspace (en Linux, bajo /home,
    # que el aislamiento oculta) y no debe verse. El base es una de las herramientas que PRAXEON deja leer.
    if sys.platform == "win32":
        py = os.path.join(sys.base_prefix, "python.exe")
    else:
        py = getattr(sys, "_base_executable", None) or os.path.join(sys.base_prefix, "bin", "python3")
    command = f"& '{py}' '{script}'" if sys.platform == "win32" else f"'{py}' '{script}'"
    return sandbox.execute_command(command, cwd=cwd, timeout=timeout)


def test_isolated_commands_run_inside_the_workspace(real_isolation):
    sandbox, ws, _ = real_isolation
    result = run_script(sandbox, ws, "import os\nopen('creado.txt', 'w').write('ok')\nprint(os.getcwd())\n", cwd="src")
    assert result.success, result.output
    assert result.tier == SandboxTier.CONTAINER and result.container_runtime == isolation.status().backend
    assert os.path.realpath(result.output.strip().splitlines()[-1]) == os.path.realpath(ws / "src")
    assert (ws / "src" / "creado.txt").read_text() == "ok"


def test_isolated_commands_have_no_network_even_with_a_runtime_built_address(real_isolation):
    sandbox, ws, _ = real_isolation
    code = "import socket\ns = socket.socket()\ns.settimeout(4)\nprint('connect', s.connect_ex(('1.1.1.' + '1', 53)))\n"
    result = run_script(sandbox, ws, code)
    assert "connect" in result.output and "connect 0" not in result.output, result.output   # en local, sí sale


def test_isolated_commands_cannot_read_or_write_outside_the_workspace(real_isolation):
    sandbox, ws, outside = real_isolation
    code = (
        f"secret, target = {str(outside / 'secreto.txt')!r}, {str(outside / 'escrito.txt')!r}\n"
        "try:\n    print(open(secret).read())\nexcept OSError as e:\n    print('LECTURA', type(e).__name__)\n"
        "try:\n    open(target, 'w').write('x')\n    print('ESCRITO')\nexcept OSError as e:\n    print('ESCRITURA', type(e).__name__)\n"
    )
    result = run_script(sandbox, ws, code)
    assert "LECTURA" in result.output and "ESCRITURA" in result.output, result.output
    assert "CLAVE" not in result.output and not (outside / "escrito.txt").exists()


def test_isolated_commands_are_killed_with_their_children_on_timeout(real_isolation):
    import time

    sandbox, ws, _ = real_isolation
    marker = ws / "vivo.txt"
    child = f"import time; time.sleep(4); open({str(marker)!r}, 'w').write('x')"
    code = f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', {child!r}])\ntime.sleep(60)\n"
    result = run_script(sandbox, ws, code, timeout=3)
    assert result.exit_code == 124
    time.sleep(6)
    assert not marker.exists()                                    # el hijo murió con el proceso principal
