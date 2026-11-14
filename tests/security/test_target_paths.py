"""Rutas objetivo de los comandos frente al workspace (auditoría 2026-11-01 §4.2, AUD-18).

El riesgo de una escritura o un borrado depende de dónde cae, no solo del verbo: fuera del workspace, en rutas protegidas
o en un destino que depende de una variable ⇒ HIGH (revisión humana); dentro ⇒ como siempre.
"""

import os
import random

import pytest

from praxeon.domain.assessment import CommandCategory as C
from praxeon.reasoning.classifier import CommandClassifier

CLASSIFIER = CommandClassifier()


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print(1)")
    (root / "notes.txt").write_text("notas")
    (root / ".git" / "hooks").mkdir(parents=True)
    return root


def classify(cmd, root):
    return CLASSIFIER.classify(cmd, context={"workspace_root": str(root)})


# --- escrituras ---------------------------------------------------------------------------------------------------
WRITES_OUTSIDE = [
    "echo hi > /etc/praxeon_no_existe.conf", "echo hi >> ~/.bashrc", "echo 'alias ls=rm' >> ~/.zshrc",
    "echo hi > ../fuera.txt", "cp src/app.py /etc/app.py", "cp -r src ~/copia", "mv notes.txt ../notes.txt",
    "mkdir /opt/praxeon", "touch ~/.ssh/authorized_keys", "echo key | tee -a ~/.ssh/authorized_keys",
    "sed -i 's/a/b/' /etc/hosts", "curl -o ~/.bashrc http://example.com/x", "wget -O /etc/x http://example.com/x",
    "git clone https://example.com/r.git /opt/r",
    "Set-Content -Path C:\\Windows\\System32\\drivers\\etc\\hosts -Value x", "'hi' | Out-File ~/Documents/x.txt",
    "Add-Content -Path $PROFILE -Value 'Write-Host hola'", "New-Item -ItemType Directory -Path C:\\Tools\\x",
    "Copy-Item src\\app.py -Destination C:\\Windows\\app.py", "Move-Item src ..\\src",
    "[System.IO.File]::WriteAllText('C:\\x.txt', 'hi')", "Invoke-WebRequest http://example.com -OutFile C:\\Windows\\x.dll",
    "bash -c 'echo x > /etc/x'", "pwsh -c \"Set-Content -Path ~/x.txt -Value 1\"", "cd /etc && echo x > hosts",
    "cd .. ; touch fuera.txt", "Set-Location C:\\Windows; New-Item x.txt", "cp evil.sh /etc", "mv x /opt",
]


@pytest.mark.parametrize("cmd", WRITES_OUTSIDE)
def test_writing_outside_the_workspace_needs_a_human(cmd, ws):
    a = classify(cmd, ws)
    assert a.risk_level.value in ("high", "critical") and not a.read_only, a.matched_rules
    assert {"RULE_WRITE_OUTSIDE_WORKSPACE", "RULE_DESTRUCTIVE_COMMAND"} & set(a.matched_rules) or a.risk_level.value == "critical"


@pytest.mark.parametrize("cmd", [
    "echo hi > out.txt", "mkdir -p build/out", "touch src/new.py", "cp src/app.py src/app_copy.py", "tee log.txt < in.txt",
    "sed -i 's/a/b/' src/app.py", "New-Item -ItemType File -Path src\\x.py", "Add-Content -Path notes.txt -Value linea",
    "echo mas >> notes.txt", "Out-File -FilePath out.txt -InputObject hi", "cd src && echo x > nuevo.txt",
    "curl -s -o page.html http://example.com",
])
def test_writing_new_files_inside_the_workspace_or_temp_stays_low(cmd, ws):
    a = classify(cmd, ws)
    assert a.risk_level.value in ("low", "medium") and not a.read_only, a.matched_rules
    assert "RULE_WRITE_OUTSIDE_WORKSPACE" not in a.matched_rules


@pytest.mark.parametrize("cmd", ["echo hi > /dev/null", "ls 2>/dev/null", "Get-ChildItem > $null", "dir > nul"])
def test_discarding_output_is_not_a_write(cmd, ws):
    a = classify(cmd, ws)
    assert a.read_only and a.risk_level.value == "low"


@pytest.mark.parametrize("cmd", [
    "echo x > .git/hooks/pre-commit", "cp evil.sh .git/hooks/post-checkout", "echo TOKEN=1 >> .env", "echo k > id_rsa",
    "Set-Content -Path .env.local -Value x",
])
def test_writing_protected_paths_inside_the_workspace_needs_a_human(cmd, ws):
    a = classify(cmd, ws)
    assert a.risk_level.value == "high" and "RULE_WRITE_PROTECTED_PATH" in a.matched_rules


@pytest.mark.parametrize("cmd", ["echo x > $OUT", "cp a.txt $(cat dest.txt)", "cd $DIR && echo x > y.txt",
                                 "Set-Content -Path $target -Value x"])
def test_a_destination_only_known_at_run_time_needs_a_human(cmd, ws):
    a = classify(cmd, ws)
    assert a.risk_level.value == "high" and "RULE_WRITE_DYNAMIC_TARGET" in a.matched_rules


@pytest.mark.parametrize("cmd", [
    "echo x > notes.txt", "cp src/app.py notes.txt", "Set-Content -Path notes.txt -Value x", "tee notes.txt < src/app.py",
    "Out-File -FilePath src\\app.py -InputObject x", "curl -o notes.txt http://example.com",
])
def test_overwriting_an_existing_file_needs_a_human(cmd, ws):
    a = classify(cmd, ws)
    assert a.risk_level.value == "high" and "RULE_OVERWRITE_EXISTING_FILE" in a.matched_rules and not a.reversible


@pytest.mark.parametrize("cmd", ["echo x >> notes.txt", "Add-Content notes.txt x", "tee -a notes.txt < src/app.py"])
def test_appending_to_an_existing_file_is_not_an_overwrite(cmd, ws):
    assert classify(cmd, ws).risk_level.value == "low"


@pytest.mark.parametrize("cmd", ["ln -s /etc/passwd enlace", "ln -s ~/.ssh claves",
                                 "New-Item -ItemType SymbolicLink -Path enlace -Target C:\\Windows"])
def test_links_that_point_outside_the_workspace_need_a_human(cmd, ws):
    a = classify(cmd, ws)
    assert a.risk_level.value == "high" and "RULE_LINK_OUTSIDE_WORKSPACE" in a.matched_rules


def test_a_symlink_inside_the_workspace_that_points_outside_is_outside(ws, tmp_path):
    outside = tmp_path / "fuera"
    outside.mkdir()
    try:
        os.symlink(outside, ws / "atajo", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("sin permisos para crear enlaces simbólicos")
    a = classify("echo x > atajo/robado.txt", ws)
    assert a.risk_level.value == "high" and "RULE_WRITE_OUTSIDE_WORKSPACE" in a.matched_rules


def test_the_system_temp_folder_is_scratch_space_unless_the_workspace_lives_there(ws):
    import tempfile

    scratch = os.path.join(tempfile.gettempdir(), "praxeon_scratch.txt")
    assert CLASSIFIER.classify(f'echo hi > "{scratch}"').risk_level.value == "low"           # workspace fuera del temporal
    assert classify(f'echo hi > "{scratch}"', ws).risk_level.value == "high"                  # el de los tests está dentro


def test_without_a_workspace_relative_paths_count_as_inside_and_absolute_as_outside():
    assert CLASSIFIER.classify("echo x > out.txt").risk_level.value == "low"
    assert CLASSIFIER.classify("echo x > /etc/out.txt").risk_level.value == "high"
    assert CLASSIFIER.classify("echo x > ../out.txt").risk_level.value == "high"


# --- borrados -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cmd,category,risk,rule", [
    ("rm notes.txt", C.LOCAL_MUTATION, "high", "RULE_WORKSPACE_DELETE_REVIEW"),
    ("rm -rf build", C.LOCAL_MUTATION, "high", "RULE_WORKSPACE_DELETE_REVIEW"),
    ("find . -name '*.pyc' -delete", C.LOCAL_MUTATION, "high", "RULE_WORKSPACE_DELETE_REVIEW"),
    ("rmdir build", C.LOCAL_MUTATION, "low", "RULE_LOCAL_MUTATION"),
    ("rm /etc/hosts", C.LOCAL_MUTATION, "high", "RULE_DELETE_OUTSIDE_WORKSPACE"),
    ("rm -f ~/.bash_history", C.LOCAL_MUTATION, "high", "RULE_DELETE_OUTSIDE_WORKSPACE"),
    ("Remove-Item C:\\Users\\a\\Documents\\x.txt", C.LOCAL_MUTATION, "high", "RULE_DELETE_OUTSIDE_WORKSPACE"),
    ("rm .env", C.LOCAL_MUTATION, "high", "RULE_WRITE_PROTECTED_PATH"),
    ("rm $FILE", C.LOCAL_MUTATION, "high", "RULE_DELETE_DYNAMIC_TARGET"),
    ("ls | xargs rm", C.LOCAL_MUTATION, "high", "RULE_DELETE_DYNAMIC_TARGET"),
    ("gci -r *.tmp | % { ri $_ }", C.LOCAL_MUTATION, "high", "RULE_DELETE_DYNAMIC_TARGET"),
    ("Get-ChildItem *.log | % Delete", C.LOCAL_MUTATION, "high", "RULE_DELETE_DYNAMIC_TARGET"),
    ("find . -delete", C.DESTRUCTIVE, "critical", "RULE_DESTRUCTIVE_DELETE_SCOPE"),
    ("find ~/proyectos -name '*.log' -delete", C.DESTRUCTIVE, "critical", "RULE_DESTRUCTIVE_DELETE_SCOPE"),
    ("cd .. && find . -name x -delete", C.DESTRUCTIVE, "critical", "RULE_DESTRUCTIVE_DELETE_SCOPE"),
])
def test_deletes_are_judged_by_what_they_delete(cmd, category, risk, rule, ws):
    a = classify(cmd, ws)
    assert (a.category, a.risk_level.value) == (category, risk) and rule in a.matched_rules, a.matched_rules


# --- ofuscación: ninguna variante de un comando destructivo sale como inocua ----------------------------------------
DANGEROUS = ["rm -rf /", "rm -rf ~", "find / -delete", "git clean -fdx", "chmod -R 777 /", "rm -rf .git"]


def _obfuscate(cmd, rng):
    program, _, rest = cmd.partition(" ")
    variants = [
        lambda p: p[0] + "''" + p[1:],                    # r''m
        lambda p: '"' + p + '"',                          # "rm"
        lambda p: "\\" + p,                               # \rm
        lambda p: p[0] + "\\" + p[1:],                    # r\m
        lambda p: "$(echo " + p + ")",                    # $(echo rm)
        lambda p: "`echo " + p + "`",                     # `echo rm`
        lambda p: "$CMD",                                 # variable
    ]
    program = rng.choice(variants)(program)
    line = f"{program} {rest}"
    wrappers = [
        lambda s: s, lambda s: f"bash -c '{s}'", lambda s: f'sh -c "{s}"', lambda s: f"env X=1 {s}",
        lambda s: f"timeout 5 {s}", lambda s: f"nohup {s}", lambda s: f"ls; {s}", lambda s: f"echo ok && {s}",
        lambda s: f"cat README.md | {s}", lambda s: f"( {s} )", lambda s: f"echo $({s})",
    ]
    return rng.choice(wrappers)(line)


def test_obfuscated_destructive_commands_are_never_low_or_read_only(ws):
    rng = random.Random(18)
    for _ in range(600):
        cmd = _obfuscate(rng.choice(DANGEROUS), rng)
        a = classify(cmd, ws)
        assert a.risk_level.value in ("high", "critical") and not a.read_only, (cmd, a.matched_rules)
