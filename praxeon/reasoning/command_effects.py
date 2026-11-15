"""Qué hace de verdad un comando simple ya analizado (AUD-18): qué borra, qué escribe, qué ejecuta dentro.

El clasificador por expresiones regulares mira el verbo; aquí se miran los argumentos:

- ``analyze(cmd)`` → ``Effects``: rutas que se borran, se vacían o se escriben (y cómo), comandos que se ejecutan
  dentro (``bash -c``, ``powershell -Command``, ``find -exec``, ``xargs``, ``env``, ``timeout``...), código en línea de
  otros intérpretes (``perl -e``, ``node -e``...) y algunos veredictos semánticos (``git clean -n`` solo lista,
  ``cd`` solo cambia de carpeta, una expresión de PowerShell no ejecuta nada).
- ``locate(path, root, cwd)`` → ``Location``: dónde cae una ruta respecto al workspace (dentro, protegida, temporal,
  descarte, fuera o indeterminable).

No ejecuta nada ni toca el disco salvo ``os.path.realpath`` (enlaces simbólicos) y ``isfile`` (sobrescrituras).
"""

from __future__ import annotations

import os
import posixpath
import re
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Sequence

from praxeon.reasoning.shell_syntax import POSIX, POWERSHELL, SimpleCommand, Word

# ------------------------------------------------------------------------------------------------ nombres de programa

_SCRIPT_EXTS = (".exe", ".com", ".cmd", ".bat")


def command_name(word: Word) -> str:
    """Nombre canónico del programa: sin ruta, sin ``.exe``, en minúsculas; ``r\\m`` → ``rm`` (escape POSIX)."""
    value = word.value
    if not re.match(r"^(?:[a-zA-Z]:|[.~/\\])", value) and "\\" in value:
        value = value.replace("\\", "")
    base = re.split(r"[\\/]", value)[-1].lower()
    for ext in _SCRIPT_EXTS:
        if base.endswith(ext):
            return base[: -len(ext)]
    return base


# Objetivos que solo se conocen al ejecutar (el ``$`` hace que ``locate`` los dé por indeterminables)
XARGS_INPUT = Word("$__xargs_input__", "$__xargs_input__", dynamic=True)
FIND_RESULT = Word("$__find_result__", "$__find_result__", dynamic=True)
DELETE_VERBS = {"rm", "unlink", "rmdir", "rd", "del", "erase", "remove-item", "ri"}
SHELLS_POSIX = {"sh", "bash", "zsh", "dash", "ksh", "ash", "busybox"}
SHELLS_PS = {"powershell", "pwsh"}
# Prefijos que ejecutan el comando que les sigue (con sus propias opciones delante)
_WRAPPER_OPTS_WITH_VALUE = {
    "env": {"-u", "--unset", "-C", "--chdir"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "--class", "--classdata"},
    "stdbuf": {"-i", "-o", "-e"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "xargs": {"-I", "-n", "-P", "-d", "-a", "-L", "-s", "-E", "--max-args", "--max-procs", "--delimiter", "--arg-file"},
    "watch": {"-n", "--interval", "-d"},
    "nohup": set(), "time": set(), "command": set(), "exec": set(), "builtin": set(), "chronic": set(),
}
BENIGN_BUILTINS = {
    "cd", "chdir", "pushd", "popd", "set-location", "sl", "push-location", "pop-location", "true", "false", ":", "exit",
    "where-object", "?", "foreach-object", "%",
}
# Variables de entorno que cambian qué programa se ejecuta o qué código carga: no son una asignación inocua
DANGEROUS_ENV = re.compile(
    r"^(?:PATH|LD_PRELOAD|LD_LIBRARY_PATH|LD_AUDIT|DYLD_\w+|PYTHONPATH|PYTHONSTARTUP|PYTHONHOME|PYTHONUSERBASE|NODE_OPTIONS|"
    r"NODE_PATH|BASH_ENV|ENV|PROMPT_COMMAND|IFS|GIT_SSH|GIT_SSH_COMMAND|GIT_EXEC_PATH|GIT_CONFIG\w*|GIT_PAGER|PAGER|EDITOR|"
    r"VISUAL|PERL5LIB|PERL5OPT|RUBYOPT|RUBYLIB|JAVA_TOOL_OPTIONS|_JAVA_OPTIONS|PSMODULEPATH|COMSPEC|SHELL)$",
    re.IGNORECASE,
)

# Código en línea de otros intérpretes (el de Python lo trata el clasificador)
INLINE_INTERPRETERS = {"perl": ("-e", "-E"), "ruby": ("-e",), "node": ("-e", "--eval", "-p", "--print"), "php": ("-r",)}
INLINE_DANGER = {
    "perl": re.compile(r"\bunlink\b|\brmtree\b|\brmdir\b|\bsystem\b|\bexec\b|`|\bqx\b|\beval\b|\bopen\s*\(?[^)]*[|>]|\brename\b"),
    "ruby": re.compile(r"File\.(?:delete|unlink|write)|FileUtils\.|Dir\.(?:delete|rmdir|unlink)|\bsystem\b|\bexec\b|`|%x|\beval\b|IO\.popen|Open3|\bspawn\b"),
    "node": re.compile(r"unlink|rmSync|rmdir|\.rm\(|writeFile|child_process|execSync|spawn|\beval\s*\(|Function\s*\(|\bvm\b"),
    "php": re.compile(r"unlink|rmdir|\bexec\b|system|passthru|shell_exec|popen|proc_open|\beval\b|assert\s*\(|file_put_contents|`"),
}
INLINE_NETWORK = {
    "perl": re.compile(r"IO::Socket|LWP|HTTP::Tiny|Net::|\bsocket\b"),
    "ruby": re.compile(r"Net::HTTP|open-uri|TCPSocket|Socket|URI\.open|Faraday"),
    "node": re.compile(r"\bhttps?\b|\bnet\b|fetch\s*\(|axios|dgram|\btls\b"),
    "php": re.compile(r"curl_|fsockopen|file_get_contents\s*\(\s*['\"]https?|stream_socket_client"),
}

# PowerShell: métodos que solo leen o transforman texto (una expresión que solo usa estos no tiene efectos)
_SAFE_METHODS = {
    "trim", "trimstart", "trimend", "tostring", "tolower", "toupper", "tolowerinvariant", "toupperinvariant", "substring",
    "replace", "split", "contains", "startswith", "endswith", "indexof", "lastindexof", "padleft", "padright", "join",
    "format", "equals", "gettype", "count", "length", "getvalue", "keys", "values", "where", "foreach",
}
_DOTNET_FILE_CALL = re.compile(
    r"^\[(?:system\.)?io\.(file|directory)\]::(\w+)\(\s*(['\"])(.*?)\3", re.IGNORECASE
)
_DOTNET_WRITE = {"writealltext", "writealllines", "writeallbytes", "appendalltext", "appendalllines", "create",
                 "createtext", "createdirectory", "copy", "move", "replace", "setattributes", "encrypt", "decrypt"}
_DOTNET_DELETE = {"delete"}


# ------------------------------------------------------------------------------------------------ efectos


@dataclass
class Write:
    target: Word
    mode: str        # truncate (vacía si existe), append, create (no destruye), dest (copia/mueve encima), edit, link, move_source


@dataclass
class Effects:
    name: str = ""
    deletes: list[Word] = field(default_factory=list)
    delete_recursive: bool = False
    delete_dynamic: bool = False          # objetivos que llegan por tubería, xargs o {}: no se conocen
    delete_filtered: bool = False         # find con filtros: no borra la raíz entera
    truncates: list[Word] = field(default_factory=list)
    writes: list[Write] = field(default_factory=list)
    payloads: list[tuple[str, str]] = field(default_factory=list)      # (código, dialecto) que se ejecuta dentro
    inner: list[list[Word]] = field(default_factory=list)              # comandos que lanza (env, xargs, find -exec...)
    inline_code: tuple[str, str] | None = None                       # (lenguaje, código)
    verdict: str = ""                     # find_inspect, git_clean_force, git_clean_dry, git_clean, git_discard,
    #                                       benign, expression, wrapper, dangerous_env, dynamic_exec, dynamic_name,
    #                                       dynamic_read, privilege, foreach_member
    cwd_change: Word | None = None     # cd/Set-Location/pushd a esta carpeta
    cwd_reset: bool = False               # popd / cd - : la carpeta vuelve a una anterior


def _positionals(words: Sequence[Word], with_value: set, *, ps: bool = False) -> list[Word]:
    """Argumentos que no son opciones (ni el valor de una opción que lo lleva)."""
    out: list[Word] = []
    skip = False
    end_of_opts = False
    for w in words:
        v = w.value
        if skip:
            skip = False
            continue
        if not end_of_opts and v == "--":
            end_of_opts = True
            continue
        if not end_of_opts and v.startswith("-") and len(v) > 1 and not w.quoted:
            if v.lower() in with_value or v in with_value:
                skip = True
            continue
        if not end_of_opts and ps is False and re.fullmatch(r"/[a-zA-Z?]", v):
            continue                                                  # opciones de cmd de una letra: /s /q /f (no /etc)
        out.append(w)
    return out


def _ps_params(words: Sequence[Word]) -> tuple[dict, list[Word], set]:
    """Parámetros de un cmdlet: ``{nombre: [valores]}``, posicionales y modificadores sin valor (``-Force``)."""
    params: dict = {}
    positionals: list[Word] = []
    switches: set = set()
    i = 0
    while i < len(words):
        w = words[i]
        v = w.value
        if v.startswith("-") and len(v) > 1 and not w.quoted:
            name = v[1:].rstrip(":").lower()
            if ":" in v[1:]:
                name, _, val = v[1:].partition(":")
                params.setdefault(name.lower(), []).append(Word(val, val))
            elif i + 1 < len(words) and not (words[i + 1].value.startswith("-") and not words[i + 1].quoted) and name in _PS_VALUE_PARAMS:
                params.setdefault(name, []).append(words[i + 1])
                i += 1
            else:
                switches.add(name)
            i += 1
            continue
        positionals.append(w)
        i += 1
    return params, positionals, switches


_PS_VALUE_PARAMS = {
    "path", "literalpath", "lp", "pspath", "destination", "filepath", "value", "itemtype", "type", "name", "target",
    "erroraction", "ea", "include", "exclude", "filter", "credential", "stream", "encoding", "inputobject", "pattern",
    "first", "last", "skip", "membername", "process", "begin", "end", "verb", "argumentlist", "workingdirectory",
    "outvariable", "ov", "outfile", "uri", "method", "body", "infile", "headers", "warningaction", "wa", "property", "expandproperty", "depth", "delimiter", "newname", "width",
}


def _split_paths(words: Sequence[Word]) -> list[Word]:
    """``'a','b'`` de PowerShell llega como una palabra ``a,b``: una ruta por elemento."""
    out: list[Word] = []
    for w in words:
        if "," in w.value and not w.dynamic:
            out.extend(Word(p, p, w.quoted) for p in w.value.split(",") if p)
        else:
            out.append(w)
    return out


def analyze(cmd: SimpleCommand, dialect: str = POSIX) -> Effects:
    """Efectos de un comando simple. Los que no se reconocen quedan vacíos: decide el clasificador por el verbo."""
    eff = Effects()
    words = cmd.words
    for r in cmd.redirects:
        if r.writes:
            eff.writes.append(Write(r.target, "append" if r.appends else "truncate"))
    if not words:
        if cmd.assignments:
            eff.verdict = _assignment_verdict(cmd.assignments)
        return eff

    first = words[0]
    name = command_name(first)
    eff.name = name
    args = words[1:]
    ps = dialect == POWERSHELL

    if cmd.assignments and _assignment_verdict(cmd.assignments) == "dangerous_env":
        eff.verdict = "dangerous_env"

    # El nombre del programa sale de una variable o sustitución: no se sabe qué se ejecuta
    if first.dynamic and not ps:
        folded = _fold_echo(first)
        if folded is None:
            eff.verdict = eff.verdict or "dynamic_name"
            return eff
        name = eff.name = folded.lower()

    if ps and not cmd.call and _is_ps_value(first, cmd):
        return _ps_expression(first, eff)

    handler = _HANDLERS.get(name)
    if handler is not None:
        handler(eff, args, cmd, ps)
        if name in _PAYLOAD_RUNNERS and eff.payloads and not eff.verdict:
            eff.verdict = "wrapper"
    elif name in _WRAPPER_OPTS_WITH_VALUE:
        _wrapper(eff, name, args)
        if eff.inner and not eff.verdict:
            eff.verdict = "wrapper"
    elif name in INLINE_INTERPRETERS:
        _inline(eff, name, args)
    elif name in BENIGN_BUILTINS:
        eff.verdict = eff.verdict or "benign"
    return eff


def _is_ps_value(first: Word, cmd: SimpleCommand) -> bool:
    """La sentencia empieza por un valor (variable, subexpresión, tipo .NET, número) y no por un programa.

    Una cadena entre comillas al principio solo es un valor dentro de un bloque: arriba del todo, el modo Full Access
    antepone ``&`` y la ejecuta como programa.
    """
    if first.raw.startswith(("$", "(", "[", "@")) or re.match(r"^-?\d", first.raw):
        return True
    return cmd.nested and first.raw.startswith(("'", '"'))


def _fold_echo(word: Word) -> str | None:
    """``$(echo rm)`` / `` `echo rm` `` → ``rm``: sustitución constante (un truco de ofuscación habitual)."""
    m = re.fullmatch(r"\$\(\s*(?:echo|printf)\s+(['\"]?)([\w.-]+)\1\s*\)|`\s*(?:echo|printf)\s+(['\"]?)([\w.-]+)\3\s*`", word.raw)
    if not m:
        return None
    return m.group(2) or m.group(4)


def _assignment_verdict(assignments: Sequence[Word]) -> str:
    for a in assignments:
        name = re.sub(r"^\$(?:env:)?", "", a.raw.split("=", 1)[0].strip(), flags=re.IGNORECASE).rstrip("+")
        if DANGEROUS_ENV.match(name):
            return "dangerous_env"
    return "benign"


def _ps_expression(first: Word, eff: Effects) -> Effects:
    """Una sentencia de PowerShell que empieza por un valor (``"..."``, ``$x``, ``(...)``, ``[Tipo]::...``)."""
    raw = first.raw
    m = _DOTNET_FILE_CALL.match(raw)
    if m:
        method, path = m.group(2).lower(), Word(m.group(4), m.group(4))
        if method in _DOTNET_DELETE:
            eff.deletes.append(path)
            eff.delete_recursive = m.group(1).lower() == "directory"
        elif method in _DOTNET_WRITE:
            eff.writes.append(Write(path, "create" if method == "createdirectory" else "truncate"))
        return eff
    if "::" in raw:
        return eff                                                    # otra llamada estática .NET: decide el verbo
    methods = re.findall(r"\.(\w+)\s*\(", raw)
    if all(mm.lower() in _SAFE_METHODS for mm in methods):
        eff.verdict = "expression"
    return eff


# ------------------------------------------------------------------------------------------------ manejadores


def _delete(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    name = eff.name
    if name in ("remove-item", "ri") or (ps and name in ("rm", "del", "erase", "rd", "rmdir")):
        params, pos, switches = _ps_params(args)
        targets = params.get("path", []) + params.get("literalpath", []) + params.get("lp", []) + [
            w for w in pos if not re.fullmatch(r"/[a-zA-Z]", w.value)]
        eff.delete_recursive = any("recurse".startswith(sw) or (len(sw) <= 3 and "r" in sw) for sw in switches) or any(
            w.value.lower() == "/s" for w in args)
    else:
        with_value = {"--interactive"} if name in ("rm", "unlink", "rmdir") else set()
        targets = _positionals(args, with_value)
        flags = " ".join(w.value for w in args if w.value.startswith(("-", "/")) and not w.quoted)
        eff.delete_recursive = bool(re.search(r"(?:^|\s)-[a-zA-Z]*[rR]|--recursive|(?:^|\s)/s\b", flags, re.IGNORECASE))
    targets = _split_paths(targets)
    eff.deletes.extend(targets)
    if not targets and (cmd.piped_input or eff.name in ("rm", "remove-item", "ri")):
        eff.delete_dynamic = True


def _find(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    roots: list[Word] = []
    i = 0
    while i < len(args) and not args[i].value.startswith(("-", "(", "!")):
        roots.append(args[i])
        i += 1
    roots = roots or [Word(".", ".")]
    exprs = args[i:]
    filters = False
    acts = False
    j = 0
    while j < len(exprs):
        v = exprs[j].value
        if v == "-delete":
            acts = True
            eff.deletes.extend(roots)
            eff.delete_recursive = True
        elif v in ("-exec", "-execdir", "-ok", "-okdir"):
            k = j + 1
            inner: list[Word] = []
            while k < len(exprs) and exprs[k].value not in (";", "+"):
                inner.append(exprs[k])
                k += 1
            if inner:
                if command_name(inner[0]) in DELETE_VERBS or command_name(inner[0]) == "shred":
                    acts = True
                    eff.deletes.extend(roots)
                    eff.delete_recursive = True
                else:
                    # `{}` es cada fichero encontrado: está bajo la raíz de búsqueda
                    found = Word(roots[0].value, roots[0].raw, dynamic=True) if len(roots) == 1 else FIND_RESULT
                    eff.inner.append([w if w.value != "{}" else found for w in inner])
            j = k
        elif v in ("-fprint", "-fprint0", "-fls", "-fprintf") and j + 1 < len(exprs):
            acts = True
            eff.writes.append(Write(exprs[j + 1], "truncate"))
            j += 1
        elif v.startswith("-") and v not in ("-depth", "-maxdepth", "-mindepth", "-print", "-print0", "-xdev", "-mount",
                                             "-follow", "-L", "-H", "-P", "-a", "-and", "-o", "-or", "-not"):
            filters = True
        j += 1
    eff.delete_filtered = filters
    if not acts:
        eff.verdict = "find_inspect"


def _git(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    i = 0
    while i < len(args) and args[i].value.startswith("-"):
        i += 2 if args[i].value in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
    if i >= len(args):
        return
    sub = args[i].value
    rest = args[i + 1:]
    opts = [w.value for w in rest if w.value.startswith("-")]
    shorts = "".join(o[1:] for o in opts if not o.startswith("--"))
    if sub == "clean":
        if "n" in shorts or "--dry-run" in opts:
            eff.verdict = "git_clean_dry"
        elif "f" in shorts or "--force" in opts:
            eff.verdict = "git_clean_force"
        else:
            eff.verdict = "git_clean"
    elif sub == "clone":
        pos = _positionals(rest, {"-b", "--branch", "-o", "--origin", "--depth", "-c", "--config", "--reference", "-j", "--jobs"})
        if len(pos) >= 2:
            eff.writes.append(Write(pos[1], "create"))
    elif sub == "restore" and "--staged" not in opts and "-S" not in opts:
        eff.verdict = "git_discard"
    elif sub == "checkout" and ("--" in [w.value for w in rest] or [w.value for w in rest] == ["."]):
        eff.verdict = "git_discard"


def _truncate(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    eff.truncates.extend(_positionals(args, {"-s", "--size", "-r", "--reference"}))


def _shred(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    targets = _positionals(args, {"-n", "--iterations", "-s", "--size", "--random-source"})
    eff.truncates.extend(targets)
    if any(w.value in ("-u", "--remove") or (w.value.startswith("-") and not w.value.startswith("--") and "u" in w.value) for w in args):
        eff.deletes.extend(targets)


def _clear_content(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    params, pos, _ = _ps_params(args)
    eff.truncates.extend(_split_paths(params.get("path", []) + params.get("literalpath", []) + pos))


def _touch_mkdir(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    if ps and eff.name in ("mkdir", "md"):
        params, pos, _ = _ps_params(args)
        targets = params.get("path", []) + params.get("name", []) + pos
    else:
        targets = _positionals(args, {"-d", "-t", "-r", "--date", "--reference", "-m", "--mode"})
    eff.writes.extend(Write(t, "create") for t in _split_paths(targets))


def _new_item(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    params, pos, switches = _ps_params(args)
    kind = " ".join(w.value.lower() for w in params.get("itemtype", []) + params.get("type", []))
    targets = _split_paths(params.get("path", []) + params.get("name", []) + pos[:1])
    mode = "truncate" if "force" in switches else "create"
    if "symbolic" in kind or "junction" in kind or "hardlink" in kind:
        eff.writes.extend(Write(t, "create") for t in targets)
        eff.writes.extend(Write(t, "link") for t in params.get("target", []) + params.get("value", []))
    else:
        eff.writes.extend(Write(t, mode) for t in targets)


def _copy_move(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    name = eff.name
    moving = name in ("mv", "move", "move-item", "mi", "rename-item", "ren", "rni")
    if name in ("copy-item", "cpi", "move-item", "mi", "rename-item", "rni") or (ps and name in ("cp", "copy", "mv", "move", "ren")):
        params, pos, _ = _ps_params(args)
        sources = params.get("path", []) + params.get("literalpath", []) + pos[:1]
        dest = params.get("destination", []) + params.get("newname", []) + pos[1:2]
    else:
        pos = _positionals(args, {"-t", "--target-directory", "-S", "--suffix", "--backup"})
        tdir = [args[k + 1] for k, w in enumerate(args[:-1]) if w.value in ("-t", "--target-directory")]
        if tdir:
            sources, dest = pos, tdir
        else:
            sources, dest = pos[:-1], pos[-1:] if len(pos) >= 2 else []
    eff.writes.extend(Write(d, "dest") for d in dest)
    if moving:
        eff.writes.extend(Write(s, "move_source") for s in _split_paths(sources))


def _robocopy_xcopy(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    pos = [w for w in args if not w.value.startswith("/")]
    flags = {w.value.lower() for w in args if w.value.startswith("/")}
    if len(pos) >= 2:
        eff.writes.append(Write(pos[1], "dest"))
        if eff.name == "robocopy" and flags & {"/mir", "/purge"}:
            eff.deletes.append(pos[1])                                 # /MIR borra del destino lo que no está en el origen
            eff.delete_recursive = True
            eff.delete_filtered = True


def _rsync(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    pos = _positionals(args, {"-e", "--rsh", "--exclude", "--include", "--filter", "-f"})
    if len(pos) >= 2:
        dest = pos[-1]
        if not re.match(r"^[\w.-]+@?[\w.-]*:", dest.value) or re.match(r"^[a-zA-Z]:", dest.value):
            eff.writes.append(Write(dest, "dest"))
            if any(w.value.startswith("--delete") or w.value == "--remove-source-files" for w in args):
                eff.deletes.append(dest)
                eff.delete_recursive = True
                eff.delete_filtered = True


def _tee(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    if eff.name == "tee-object" or ps:
        params, pos, switches = _ps_params(args)
        targets = params.get("filepath", []) + params.get("literalpath", []) + params.get("path", []) + pos[:1]
        mode = "append" if "append" in switches else "truncate"
    else:
        targets = _positionals(args, set())
        mode = "append" if any(w.value in ("-a", "--append") for w in args) else "truncate"
    eff.writes.extend(Write(t, mode) for t in targets)


def _set_content(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    params, pos, switches = _ps_params(args)
    targets = params.get("path", []) + params.get("literalpath", []) + params.get("filepath", []) + pos[:1]
    append = eff.name in ("add-content", "ac") or "append" in switches
    mode = "append" if append else ("create" if "noclobber" in switches else "truncate")
    eff.writes.extend(Write(t, mode) for t in _split_paths(targets))


def _sed(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    if not any(w.value.startswith("-i") or w.value.startswith("--in-place") or re.fullmatch(r"-[a-zA-Z]*i[a-zA-Z]*", w.value) for w in args):
        return
    pos = _positionals(args, {"-e", "-f", "--expression", "--file", "-l"})
    has_script_opt = any(w.value in ("-e", "-f", "--expression", "--file") for w in args)
    files = pos if has_script_opt else pos[1:]
    eff.writes.extend(Write(f, "edit") for f in files)


def _ln(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    pos = _positionals(args, {"-t", "--target-directory", "-S", "--suffix"})
    if len(pos) >= 2:
        eff.writes.append(Write(pos[-1], "create"))
        eff.writes.extend(Write(t, "link") for t in pos[:-1])
    elif pos:
        eff.writes.append(Write(pos[0], "link"))


def _dd(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    for w in args:
        if w.value.startswith("of="):
            eff.writes.append(Write(Word(w.value[3:], w.raw[3:], w.quoted, w.dynamic), "truncate"))


def _shell(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    for k, w in enumerate(args):
        v = w.value
        if v.startswith("-") and not v.startswith("--") and "c" in v[1:] and k + 1 < len(args):
            eff.payloads.append((args[k + 1].value, POSIX))
            return
        if not v.startswith("-"):
            return                                                    # `bash script.sh`: ejecuta un fichero


def _cmd_exe(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    for k, w in enumerate(args):
        if w.value.lower() in ("/c", "/k", "/r"):
            payload = " ".join(x.raw for x in args[k + 1:])
            if len(args) - k - 1 == 1 and args[k + 1].quoted:
                payload = args[k + 1].value
            eff.payloads.append((payload, POWERSHELL))
            return


def _powershell(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    with_value = {"-executionpolicy", "-ep", "-ex", "-windowstyle", "-w", "-workingdirectory", "-wd", "-outputformat",
                  "-of", "-inputformat", "-if", "-configurationname", "-version", "-v", "-psconsolefile", "-settingsfile"}
    k = 0
    while k < len(args):
        v = args[k].value.lower()
        if v in ("-command", "-c", "-com"):
            rest = args[k + 1:]
            payload = rest[0].value if len(rest) == 1 and rest[0].quoted else " ".join(x.raw for x in rest)
            if payload.strip() and payload.strip() != "-":
                eff.payloads.append((payload, POWERSHELL))
            return
        if v in ("-file", "-f") or (v.startswith("-e") and v not in with_value):
            return                                                    # script o -EncodedCommand: lo trata el clasificador
        if v.startswith("-"):
            k += 2 if v in with_value else 1
            continue
        payload = " ".join(x.raw for x in args[k:])                  # sin -Command explícito, el resto es el comando
        eff.payloads.append((payload, POWERSHELL))
        return


def _eval(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    if not args or any(w.dynamic for w in args):
        eff.verdict = "dynamic_exec"
        return
    eff.payloads.append((" ".join(w.value for w in args), POWERSHELL if ps else POSIX))


def _start_process(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    params, pos, _ = _ps_params(args)
    if any(w.value.lower() == "runas" for w in params.get("verb", [])):
        eff.verdict = "privilege"
        return
    program = params.get("filepath", []) + pos[:1]
    if program:
        eff.inner.append(program + params.get("argumentlist", []))


def _foreach(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    params, pos, _ = _ps_params(args)
    members = params.get("membername", []) + [w for w in pos if w.value != "{…}"]
    eff.verdict = "foreach_member" if members else "benign"
    if any(m.value.lower() in ("delete", "remove", "moveto", "clear", "kill") for m in members):
        eff.delete_dynamic = True                                     # `ls | % Delete`: borra cada elemento recibido


def _download(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    """Descargas que guardan en un fichero: ``curl -o``, ``wget -O``/``-P``, ``Invoke-WebRequest -OutFile``."""
    if eff.name in ("invoke-webrequest", "iwr", "invoke-restmethod", "irm") or (ps and eff.name in ("curl", "wget")):
        params, _, _ = _ps_params(args)
        eff.writes.extend(Write(w, "truncate") for w in params.get("outfile", []))
        return
    for k, w in enumerate(args[:-1]):
        v = w.value
        if v in ("-o", "--output", "-O", "--output-document") and not (eff.name == "curl" and v == "-O"):
            eff.writes.append(Write(args[k + 1], "truncate"))
        elif v in ("-P", "--directory-prefix") and eff.name == "wget":
            eff.writes.append(Write(args[k + 1], "create"))
    for w in args:
        if w.value.startswith("--output="):
            eff.writes.append(Write(Word(w.value.split("=", 1)[1], w.raw), "truncate"))


_CONTENT_READERS = {"cat", "type", "get-content", "gc", "head", "tail", "more", "less", "tac", "nl", "strings", "xxd", "od"}


def _content_reader(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    """Volcar ficheros cuya ruta solo se conoce al ejecutar (``Get-Content $_.FullName``, ``ls | Get-Content``) puede
    incluir ``.env`` o claves: la comprobación de lecturas sensibles no los ve."""
    if ps or eff.name in ("get-content", "gc"):
        params, pos, _ = _ps_params(args)
        paths = params.get("path", []) + params.get("literalpath", []) + params.get("lp", []) + pos
    else:
        paths = _positionals(args, {"-n", "-c", "--lines", "--bytes"})
    piped_ps = not paths and cmd.piped_input and (ps or eff.name in ("get-content", "gc"))
    if piped_ps or any(w.dynamic or w.value.startswith("$") for w in paths):
        eff.verdict = "dynamic_read"


def _cd(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    eff.verdict = "benign"
    if eff.name in ("popd", "pop-location"):
        eff.cwd_reset = True
        return
    params, pos, _ = _ps_params(args) if ps else ({}, _positionals(args, set()), set())
    target = (params.get("path", []) + params.get("literalpath", []) + pos)[:1]
    if target and target[0].value == "-":
        eff.cwd_reset = True
    elif target:
        eff.cwd_change = target[0]
    else:
        eff.cwd_change = Word("~", "~")


def _export(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    assigns = [w for w in args if not w.value.startswith("-")]
    if not assigns or not all("=" in w.value for w in assigns):
        return                                                        # `export -p` / `export X` muestran o marcan: verbo
    eff.verdict = _assignment_verdict(assigns)


def _set(eff: Effects, args: list[Word], cmd: SimpleCommand, ps: bool) -> None:
    if not ps and args and all(re.fullmatch(r"[-+][a-zA-Z]+", w.value) or w.value in ("pipefail", "errexit", "nounset", "xtrace")
                               for w in args):
        eff.verdict = "benign"                                        # set -e, set -euo pipefail


def _wrapper(eff: Effects, name: str, args: list[Word]) -> None:
    with_value = _WRAPPER_OPTS_WITH_VALUE[name]
    k = 0
    if name == "command" and args and args[0].value in ("-v", "-V"):
        eff.verdict = "benign"
        return
    while k < len(args):
        v = args[k].value
        if v.startswith("-") and not args[k].quoted:
            k += 2 if v in with_value else 1
            continue
        if name == "env" and "=" in v:
            if _assignment_verdict([args[k]]) == "dangerous_env":
                eff.verdict = "dangerous_env"
            k += 1
            continue
        break
    if name == "timeout" and k < len(args):
        k += 1                                                        # la duración
    rest = args[k:]
    if rest:
        if name == "xargs":
            rest = rest + [XARGS_INPUT]
        eff.inner.append(rest)
    elif name == "xargs":
        eff.inner.append([Word("echo", "echo"), XARGS_INPUT])


def _inline(eff: Effects, name: str, args: list[Word]) -> None:
    flags = INLINE_INTERPRETERS[name]
    for k, w in enumerate(args):
        if w.value in flags and k + 1 < len(args):
            eff.inline_code = (name, args[k + 1].value)
            if name == "perl" and any(re.fullmatch(r"-[a-zA-Z]*i\S*", x.value) for x in args[:k]):
                eff.writes.extend(Write(f, "edit") for f in args[k + 2:] if not f.value.startswith("-"))
            return
        if re.fullmatch(r"-[a-zA-Z]*[eE]", w.value) and name == "perl" and k + 1 < len(args):
            eff.inline_code = (name, args[k + 1].value)                # perl -pie / -ne 'código'
            if "i" in w.value:
                eff.writes.extend(Write(f, "edit") for f in args[k + 2:] if not f.value.startswith("-"))
            return


_PAYLOAD_RUNNERS = SHELLS_POSIX | SHELLS_PS | {"cmd", "eval", "invoke-expression", "iex"}

_HANDLERS = {
    **{v: _delete for v in DELETE_VERBS},
    "find": _find, "git": _git, "truncate": _truncate, "shred": _shred,
    "clear-content": _clear_content, "clc": _clear_content,
    "touch": _touch_mkdir, "mkdir": _touch_mkdir, "md": _touch_mkdir,
    "new-item": _new_item, "ni": _new_item,
    **{v: _copy_move for v in ("cp", "copy", "copy-item", "cpi", "mv", "move", "move-item", "mi", "rename-item", "ren", "rni")},
    "robocopy": _robocopy_xcopy, "xcopy": _robocopy_xcopy, "rsync": _rsync,
    "tee": _tee, "tee-object": _tee,
    "set-content": _set_content, "sc": _set_content, "add-content": _set_content, "ac": _set_content, "out-file": _set_content,
    "sed": _sed, "ln": _ln, "dd": _dd,
    **{v: _shell for v in SHELLS_POSIX}, "cmd": _cmd_exe, **{v: _powershell for v in SHELLS_PS},
    "eval": _eval, "invoke-expression": _eval, "iex": _eval,
    "start-process": _start_process, "saps": _start_process, "start": _start_process,
    "foreach-object": _foreach, "%": _foreach,
    **{v: _cd for v in ("cd", "chdir", "pushd", "popd", "set-location", "sl", "push-location", "pop-location")},
    "export": _export, "set": _set,
    **{v: _content_reader for v in _CONTENT_READERS},
    **{v: _download for v in ("curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod", "irm")},
}


# ------------------------------------------------------------------------------------------------ ubicación de rutas


class Location(str, Enum):
    DISCARD = "discard"          # /dev/null, $null, NUL
    INSIDE = "inside"            # dentro del workspace
    PROTECTED = "protected"      # dentro, pero .git, .env, claves...
    TEMP = "temp"                # carpeta temporal del sistema
    OUTSIDE = "outside"          # fuera del workspace
    UNKNOWN = "unknown"          # depende de una variable o sustitución que no se conoce


_DISCARD = {"/dev/null", "nul", "nul:", "$null", "/dev/stdout", "/dev/stderr", "con"}
PROTECTED_SEGMENT = re.compile(
    r"^(\.git|\.env(?!\.(?:example|sample|template|dist|defaults?)$)(\..*)?|\.ssh|\.aws|\.gnupg|\.kube|"
    r"id_(rsa|dsa|ecdsa|ed25519)(?!\.pub$).*|.*\.(pem|key|p12|pfx)|secrets?(\..*)?|credentials?(\..*)?)$",
    re.IGNORECASE)
_HOME_VARS = re.compile(r"^(?:~|\$HOME|\$\{HOME\}|\$env:(?:USERPROFILE|HOME)|%USERPROFILE%|%HOMEPATH%)(?=$|[\\/])", re.IGNORECASE)
_TEMP_VARS = re.compile(r"^(?:\$TMPDIR|\$\{TMPDIR\}|\$env:(?:TEMP|TMP)|%TEMP%|%TMP%)(?=$|[\\/])", re.IGNORECASE)
_PWD_VARS = re.compile(r"^(?:\$PWD|\$\{PWD\}|\$\(pwd\)|\$pwd)(?=$|[\\/])", re.IGNORECASE)
_VIRTUAL_ROOT = "/__praxeon_workspace__"


def _norm(path: str) -> str:
    p = path.replace("\\", "/")
    unc = p.startswith("//")
    p = posixpath.normpath(p)
    if unc and not p.startswith("//"):
        p = "/" + p
    return p


def _is_abs(p: str) -> bool:
    return p.startswith("/") or bool(re.match(r"^[a-zA-Z]:", p))


def _within(path: str, root: str) -> bool:
    if re.match(r"^[a-zA-Z]:", root) or re.match(r"^[a-zA-Z]:", path):
        path, root = path.lower(), root.lower()
    root = root.rstrip("/") or "/"
    return path == root or path.startswith(root + "/") or root == "/"


def _host_real(path: str) -> str:
    """``realpath`` solo si la ruta es del estilo del sistema actual (resuelve enlaces simbólicos que salen fuera)."""
    native = path.replace("/", os.sep)
    if os.path.isabs(native):
        try:
            return _norm(os.path.realpath(native))
        except (OSError, ValueError):
            return _norm(path)
    return _norm(path)


def temp_roots() -> list[str]:
    # Solo se comparan rutas con estas carpetas; no se crea ni se abre nada en ellas
    return [_norm(tempfile.gettempdir()), "/tmp", "/var/tmp"]  # nosec B108


def resolve(value: str, root: str | None, cwd: str | None = None) -> str | None:
    """Ruta absoluta normalizada de ``value`` (``None`` si depende de una variable que no se conoce).

    Las relativas se resuelven contra ``cwd`` (tras un ``cd``) o el workspace; sin workspace, contra uno virtual.
    """
    v = value.strip()
    home = _norm(os.path.expanduser("~"))
    m = _HOME_VARS.match(v) or _TEMP_VARS.match(v) or _PWD_VARS.match(v)
    if m:
        base = home if _HOME_VARS.match(v) else (temp_roots()[0] if _TEMP_VARS.match(v) else (cwd or root or _VIRTUAL_ROOT))
        v = base + v[m.end():]
    if re.search(r"\$|%\w+%|`", v):
        return None
    base = cwd or (_norm(root) if root else _VIRTUAL_ROOT)
    p = _norm(v) if _is_abs(v.replace("\\", "/")) or v.startswith("\\") else _norm(posixpath.join(base, v.replace("\\", "/")))
    if v.startswith("\\") and not v.startswith("\\\\") and re.match(r"^[a-zA-Z]:", base):
        p = _norm(base[:2] + v)                                       # \ruta: raíz de la unidad actual
    if root and not p.startswith(_VIRTUAL_ROOT):
        p = _host_real(p)
    return p


def locate(word: Word, root: str | None, cwd: str | None = None) -> Location:
    """Dónde cae la ruta ``word`` respecto al workspace ``root`` (sin él, un workspace virtual: relativas dentro)."""
    value = word.value.strip()
    if not value:
        return Location.UNKNOWN
    if value.lower() in _DISCARD:
        return Location.DISCARD
    if value.lower() == "$profile" or value.lower().startswith("$profile."):
        return Location.OUTSIDE                                      # perfil de PowerShell: se ejecuta en cada arranque
    path = resolve(value, root, cwd)
    if path is None:
        return Location.UNKNOWN
    ws = _host_real(_norm(root)) if root else _VIRTUAL_ROOT
    if _within(path, ws):
        rel = path[len(ws):].strip("/") if len(path) > len(ws) else ""
        if any(PROTECTED_SEGMENT.match(seg) for seg in rel.split("/") if seg):
            return Location.PROTECTED
        return Location.INSIDE
    # La carpeta temporal es zona de borrador, salvo que el propio workspace esté en ella: entonces lo que hay a su
    # lado (otros proyectos, otras sesiones) no es borrador sino «fuera»
    temps = temp_roots()
    if any(_within(path, t) for t in temps) and not any(_within(ws, t) for t in temps):
        return Location.TEMP
    return Location.OUTSIDE


def is_whole_workspace(word: Word, root: str | None, cwd: str | None = None) -> bool:
    """La ruta es la carpeta del workspace entera (``.``, ``*``, ``./``, la propia raíz)."""
    path = resolve(word.value.rstrip("/\\*").rstrip("/\\") or ".", root, cwd)
    ws = _host_real(_norm(root)) if root else _VIRTUAL_ROOT
    if path is None:
        return False
    if re.match(r"^[a-zA-Z]:", ws):
        return path.lower() == ws.lower()
    return path == ws


def existing_file(word: Word, root: str | None, cwd: str | None = None) -> bool:
    """La ruta ya existe como fichero (sin workspace real, solo se comprueban las absolutas)."""
    path = resolve(word.value, root, cwd)
    if path is None or path.startswith(_VIRTUAL_ROOT):
        return False
    native = path.replace("/", os.sep)
    try:
        return os.path.isfile(native)
    except (OSError, ValueError):
        return False
