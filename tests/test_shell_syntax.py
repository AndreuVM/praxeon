"""Análisis sintáctico de comandos de shell (praxeon/reasoning/shell_syntax.py, AUD-18)."""

import random

import pytest

from praxeon.reasoning.shell_syntax import POSIX, POWERSHELL, ShellSyntaxError, detect_dialect, parse


def argvs(text, dialect=None):
    return [c.argv for c in parse(text, dialect).commands]


def top(text, dialect=None):
    return [c for c in parse(text, dialect).commands if not c.nested]


# --- separadores y comillas ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("ls -la", [["ls", "-la"]]),
    ("a; b && c || d | e & f", [["a"], ["b"], ["c"], ["d"], ["e"], ["f"]]),
    ("a\nb", [["a"], ["b"]]),
    ('echo "a; b && c"', [["echo", "a; b && c"]]),
    ("echo 'a | b'", [["echo", "a | b"]]),
    ("grep -rn \"rm -rf\" src", [["grep", "-rn", "rm -rf", "src"]]),
    ("echo hi # rm -rf /", [["echo", "hi"]]),
])
def test_commands_are_split_on_operators_but_not_inside_quotes(text, expected):
    assert argvs(text, POSIX) == expected


@pytest.mark.parametrize("text", ["r''m -rf /", '"rm" -rf /', "\\rm -rf /", "r\\m -rf /", "'r'm -rf /"])
def test_posix_quoting_and_escapes_are_removed_from_words(text):
    assert argvs(text, POSIX) == [["rm", "-rf", "/"]]


def test_posix_assignments_before_the_program_are_not_the_program():
    (cmd,) = parse("FOO=1 LD_PRELOAD=x.so ls -l", POSIX).commands
    assert cmd.argv == ["ls", "-l"] and [a.value for a in cmd.assignments] == ["FOO=1", "LD_PRELOAD=x.so"]


def test_pipeline_members_know_they_receive_piped_input():
    first, second = parse("ls | wc -l", POSIX).commands
    assert not first.piped_input and second.piped_input


# --- redirecciones ------------------------------------------------------------------------------------------------
def test_redirections_are_separated_from_the_arguments():
    (cmd,) = parse("sort a.txt > out.txt 2>> err.log < in.txt 2>&1", POSIX).commands
    assert cmd.argv == ["sort", "a.txt"]
    ops = [(r.fd, r.op, r.target.value) for r in cmd.redirects]
    assert ops == [("", ">", "out.txt"), ("2", ">>", "err.log"), ("", "<", "in.txt"), ("2", ">&", "1")]
    out, err, inp, dup = cmd.redirects
    assert out.writes and not out.appends and err.appends and inp.reads and dup.is_dup and not dup.writes


def test_redirection_without_spaces_and_to_both_streams():
    assert [(r.op, r.target.value) for r in parse("ls>a.txt", POSIX).commands[0].redirects] == [(">", "a.txt")]
    assert [(r.op, r.target.value) for r in parse("make &> build.log", POSIX).commands[0].redirects] == [("&>", "build.log")]


def test_a_redirect_inside_quotes_is_text():
    (cmd,) = parse('echo "a > b.txt"', POSIX).commands
    assert cmd.argv == ["echo", "a > b.txt"] and not cmd.redirects


# --- sustituciones, subshells y bloques ------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["echo $(rm -rf ~)", 'echo "$(rm -rf ~)"', "echo `rm -rf ~`", "x=$(rm -rf ~)",
                                  "cat <(rm -rf ~)", "(rm -rf ~)", "{ rm -rf ~; }", "echo $(echo $(rm -rf ~))"])
def test_commands_inside_substitutions_subshells_and_groups_are_found(text):
    nested = [c.argv for c in parse(text, POSIX).commands if c.nested]
    assert ["rm", "-rf", "~"] in nested


def test_a_substitution_makes_the_word_dynamic():
    (_, cmd) = parse("ls $(cat dirs.txt)", POSIX).commands
    assert cmd.words[1].dynamic and not cmd.words[0].dynamic


def test_variables_stay_as_text_and_make_the_word_dynamic():
    (cmd,) = parse('rm -rf "$HOME/x" ${DIR}', POSIX).commands
    assert [w.value for w in cmd.words[2:]] == ["$HOME/x", "${DIR}"] and all(w.dynamic for w in cmd.words[2:])


def test_arithmetic_and_brace_expansion_run_no_commands():
    assert argvs("echo $((1+2)) file{1,2}.txt", POSIX) == [["echo", "$((1+2))", "file{1,2}.txt"]]


def test_find_exec_placeholder_is_a_word():
    assert argvs("find . -exec rm {} \\;", POSIX) == [["find", ".", "-exec", "rm", "{}", ";"]]


# --- PowerShell ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,dialect", [
    ("Get-ChildItem -Recurse", POWERSHELL), ("$env:X='1'; python x.py", POWERSHELL), ("ls | % { $_.Name }", POWERSHELL),
    ("del /q build\\out.o", POWERSHELL), ("type C:\\a.txt", POWERSHELL), (".\\build.ps1", POWERSHELL),
    ("ls -la", POSIX), ("rm -rf build", POSIX), ("git status", POSIX), ("python -m pytest -q", POSIX),
])
def test_dialect_detection(text, dialect):
    assert detect_dialect(text) == dialect


def test_powershell_keeps_backslashes_and_uses_backtick_as_escape():
    assert argvs(r"Remove-Item C:\Users\a\x.txt", POWERSHELL) == [["Remove-Item", r"C:\Users\a\x.txt"]]
    assert argvs("Write-Host a`;b", POWERSHELL) == [["Write-Host", "a;b"]]
    assert argvs("Write-Host 'it''s'", POWERSHELL) == [["Write-Host", "it's"]]


def test_powershell_scriptblocks_are_parsed_as_commands():
    cmds = parse("Get-ChildItem | ForEach-Object { Remove-Item $_.FullName -Recurse }", POWERSHELL).commands
    assert ["Remove-Item", "$_.FullName", "-Recurse"] in [c.argv for c in cmds if c.nested]
    assert ["ForEach-Object", "{…}"] in [c.argv for c in cmds if not c.nested]


def test_powershell_call_operator_and_assignments():
    (cmd,) = parse("& 'C:\\Program Files\\x\\python.exe' -c \"print(1)\"", POWERSHELL).commands
    assert cmd.call and cmd.argv == ["C:\\Program Files\\x\\python.exe", "-c", "print(1)"]
    first, second = parse("$x = Get-Content a.txt; $env:DEBUG='1'", POWERSHELL).commands
    assert first.argv == ["Get-Content", "a.txt"] and [a.raw for a in first.assignments] == ["$x"]
    assert second.argv == [] and second.assignments


def test_powershell_subexpressions_inside_strings_are_commands():
    cmds = parse('Write-Output "files: $(Get-ChildItem | Measure-Object)"', POWERSHELL).commands
    assert ["Get-ChildItem"] in [c.argv for c in cmds if c.nested]


# --- lo que no se analiza ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["cat <<EOF\nx\nEOF", 'echo "sin cerrar', "echo 'sin cerrar", "echo $(ls", "ls )",
                                  "echo `ls", "ls | ", "echo > ", "(" * 20 + "ls" + ")" * 20])
def test_unsupported_or_broken_syntax_raises_shell_syntax_error(text):
    with pytest.raises(ShellSyntaxError):
        parse(text, POSIX)


def test_random_input_never_raises_anything_but_shell_syntax_error():
    rng = random.Random(2026)
    alphabet = list("ab01 -/\\.*~$(){}[]|&;<>'\"`#=:%@\n") + ["rm", "&&", "$(", "2>&1", ">>", "<<", "${", "$env:", "$_"]
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 25)))
        for dialect in (POSIX, POWERSHELL):
            try:
                parse(text, dialect)
            except ShellSyntaxError:
                pass
