"""Análisis sintáctico de comandos de shell para el clasificador (auditoría 2026-11-01 §4.2, AUD-18).

Convierte el texto de un comando en una lista de comandos simples (palabras ya sin comillas ni escapes, más sus
redirecciones), incluidos los que van dentro de sustituciones ``$(...)``/`` `...` ``, subshells ``( ... )`` y bloques
``{ ... }`` de PowerShell. No ejecuta ni expande nada: las variables y sustituciones quedan como texto y la palabra se
marca como dinámica (su valor real solo se conoce al ejecutar).

Dos dialectos, los dos shells con los que PRAXEON ejecuta (``/bin/sh`` en Unix, PowerShell en Windows):

- ``posix``: ``\\`` escapa, `` ` `` es sustitución de comando, ``'...'`` literal, ``"..."`` con ``\\"``.
- ``powershell``: ``\\`` es un carácter más (rutas de Windows), `` ` `` escapa, ``''`` dentro de ``'...'`` es una comilla.

Lo que no se entiende con seguridad (here-docs, comillas o paréntesis sin cerrar, anidamiento excesivo) lanza
``ShellSyntaxError``: quien llama decide qué hacer (el clasificador vuelve a su ruta por expresiones regulares).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

POSIX = "posix"
POWERSHELL = "powershell"
MAX_DEPTH = 6

# Pistas inequívocas de PowerShell (o de rutas de Windows, que en PRAXEON se ejecutan en PowerShell)
_PS_VERBS = (
    "get|set|new|remove|copy|move|rename|out|add|clear|write|read|invoke|select|where|foreach|test|start|stop|restart|"
    "import|export|convertto|convertfrom|measure|sort|format|join|split|resolve|push|pop|expand|compress|tee|group|"
    "compare|update|enable|disable|register|unregister|wait|show|find|install|uninstall|save|send|receive|enter|grant|"
    "revoke|protect|unprotect|lock|unlock|block|unblock|suspend|resume|mount|dismount|use"
)
_PS_HINTS = re.compile(
    r"(?i)(?:^|[\s;|&({])(?:" + _PS_VERBS + r")-[a-z]{2,}\b"     # cmdlet Verbo-Nombre
    r"|\$env:|\$_\b|\$null\b|\$true\b|\$false\b|\$psitem\b"       # variables automáticas
    r"|\[[a-z][\w.]*\]::"                                          # llamada estática .NET
    r"|(?:^|[\s'\"=(])[a-z]:\\"                                    # ruta con unidad: C:\...
    r"|(?:^|[\s'\"])\.{1,2}\\"                                     # .\script, ..\dir
    r"|\w\\[\w*]"                                                  # dir\fichero
)


def detect_dialect(text: str) -> str:
    """``powershell`` si el texto tiene sintaxis o rutas de PowerShell/Windows; si no, ``posix``."""
    return POWERSHELL if _PS_HINTS.search(text or "") else POSIX


class ShellSyntaxError(ValueError):
    """El comando no se puede analizar con seguridad (here-doc, comillas sin cerrar, anidamiento excesivo...)."""


@dataclass
class Word:
    """Una palabra del comando: ``value`` sin comillas ni escapes; ``raw`` tal como venía."""

    value: str
    raw: str
    quoted: bool = False
    dynamic: bool = False          # contiene variables, sustituciones o expansiones: su valor real se sabe al ejecutar


@dataclass
class Redirect:
    """Redirección de un comando: ``op`` es ``>``, ``>>``, ``>|``, ``&>``, ``&>>``, ``<``, ``<<<``, ``>&`` o ``<&``."""

    op: str
    target: Word
    fd: str = ""

    @property
    def is_dup(self) -> bool:
        """``2>&1``, ``>&-``: duplica o cierra un descriptor, no toca ficheros."""
        return self.op in (">&", "<&") and (self.target.value.isdigit() or self.target.value == "-")

    @property
    def writes(self) -> bool:
        return ">" in self.op and not self.is_dup

    @property
    def appends(self) -> bool:
        return self.op in (">>", "&>>")

    @property
    def reads(self) -> bool:
        return self.op == "<"


@dataclass
class SimpleCommand:
    """Un comando simple: asignaciones previas, palabras (la primera es el programa) y redirecciones."""

    words: List[Word] = field(default_factory=list)
    redirects: List[Redirect] = field(default_factory=list)
    assignments: List[Word] = field(default_factory=list)
    raw: str = ""
    nested: bool = False           # dentro de una sustitución, subshell o bloque
    piped_input: bool = False      # recibe la salida de otro comando por `|`
    call: bool = False             # PowerShell `& 'programa' args`: la primera palabra es un programa, no un valor

    @property
    def argv(self) -> List[str]:
        return [w.value for w in self.words]


@dataclass
class Script:
    """Resultado del análisis: todos los comandos simples, en orden de aparición, incluidos los anidados."""

    commands: List[SimpleCommand]
    dialect: str


def parse(text: str, dialect: Optional[str] = None) -> Script:
    """Analiza ``text``; ``dialect`` por defecto se deduce del propio texto (``detect_dialect``)."""
    d = dialect or detect_dialect(text)
    return Script(commands=_Parser(text, d, 0).run(), dialect=d)


class _Builder:
    def __init__(self, start: int):
        self.start = start
        self.words: List[Word] = []
        self.redirects: List[Redirect] = []
        self.assignments: List[Word] = []
        self.piped_input = False
        self.group = False             # hubo un subshell/bloque `( ... )` o `{ ...; }` en posición de comando
        self.call = False

    @property
    def empty(self) -> bool:
        return not (self.words or self.redirects or self.assignments or self.group)


class _WordBuf:
    def __init__(self, start: int):
        self.start = start
        self.chars: List[str] = []
        self.quoted = False
        self.dynamic = False


_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
_PS_ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "??="}


class _Parser:
    def __init__(self, text: str, dialect: str, depth: int):
        if depth > MAX_DEPTH:
            raise ShellSyntaxError("anidamiento de sustituciones o bloques demasiado profundo")
        self.text = text
        self.n = len(text)
        self.ps = dialect == POWERSHELL
        self.dialect = dialect
        self.depth = depth
        self.out: List[SimpleCommand] = []

    # ------------------------------------------------------------------------------------------------ utilidades

    def _nested(self, inner: str) -> None:
        """Analiza el contenido de una sustitución/bloque y añade sus comandos (marcados como anidados)."""
        for cmd in _Parser(inner, self.dialect, self.depth + 1).run():
            cmd.nested = True
            self.out.append(cmd)

    def _skip_quoted(self, i: int) -> int:
        """Índice tras la cadena entre comillas que empieza en ``i``."""
        q = self.text[i]
        j = i + 1
        while j < self.n:
            c = self.text[j]
            if q == "'":
                if c == "'":
                    if self.ps and j + 1 < self.n and self.text[j + 1] == "'":
                        j += 2
                        continue
                    return j + 1
            else:
                if c == "\\" and not self.ps:
                    j += 2
                    continue
                if c == "`" and self.ps:
                    j += 2
                    continue
                if c == '"':
                    return j + 1
            j += 1
        raise ShellSyntaxError("comillas sin cerrar")

    def _matching(self, i: int, open_c: str, close_c: str) -> int:
        """Índice del cierre que corresponde a la apertura en ``i`` (respeta comillas y anidamiento)."""
        depth = 0
        j = i
        while j < self.n:
            c = self.text[j]
            if c in ("'", '"'):
                j = self._skip_quoted(j)
                continue
            if (c == "\\" and not self.ps) or (c == "`" and self.ps):
                j += 2
                continue
            if c == open_c:
                depth += 1
            elif c == close_c:
                depth -= 1
                if depth == 0:
                    return j
            j += 1
        raise ShellSyntaxError(f"'{open_c}' sin cerrar")

    def _backtick_end(self, i: int) -> int:
        """Fin de una sustitución POSIX `` `...` `` que empieza en ``i``."""
        j = i + 1
        while j < self.n:
            if self.text[j] == "\\":
                j += 2
                continue
            if self.text[j] == "`":
                return j
            j += 1
        raise ShellSyntaxError("sustitución ` sin cerrar")

    def _dollar(self, i: int, buf: _WordBuf) -> int:
        """Consume una expansión que empieza por ``$`` en ``i``; devuelve el índice siguiente."""
        t = self.text
        nxt = t[i + 1] if i + 1 < self.n else ""
        buf.dynamic = True
        if nxt == "(":
            if not self.ps and t.startswith("$((", i):
                end = self._matching(i + 1, "(", ")")              # aritmética: no ejecuta comandos
                buf.chars.append(t[i:end + 1])
                return end + 1
            end = self._matching(i + 1, "(", ")")
            self._nested(t[i + 2:end])
            buf.chars.append(t[i:end + 1])
            return end + 1
        if nxt == "{":
            end = self._matching(i + 1, "{", "}")
            buf.chars.append(t[i:end + 1])
            return end + 1
        m = re.compile(r"\$(?:[A-Za-z_][\w]*(?::[\w]+)?|[0-9?#@*!$_-])" if not self.ps else r"\$(?:[A-Za-z_][\w]*(?::[\w]+)?|[?^$_])").match(t, i)
        if m:
            buf.chars.append(m.group(0))
            return m.end()
        buf.chars.append("$")
        buf.dynamic = nxt != ""
        return i + 1

    def _double_quoted(self, i: int, buf: _WordBuf) -> int:
        t = self.text
        buf.quoted = True
        j = i + 1
        while j < self.n:
            c = t[j]
            if c == '"':
                if self.ps and j + 1 < self.n and t[j + 1] == '"':
                    buf.chars.append('"')
                    j += 2
                    continue
                return j + 1
            if c == "\\" and not self.ps and j + 1 < self.n:
                nxt = t[j + 1]
                if nxt in '"\\$`':
                    buf.chars.append(nxt)
                    j += 2
                    continue
                if nxt == "\n":
                    j += 2
                    continue
                buf.chars.append(c)
                j += 1
                continue
            if c == "`":
                if self.ps:
                    if j + 1 < self.n:
                        buf.chars.append(t[j + 1])
                    j += 2
                    continue
                end = self._backtick_end(j)
                self._nested(t[j + 1:end])
                buf.chars.append(t[j:end + 1])
                buf.dynamic = True
                j = end + 1
                continue
            if c == "$":
                j = self._dollar(j, buf)
                continue
            buf.chars.append(c)
            j += 1
        raise ShellSyntaxError("comillas sin cerrar")

    # ------------------------------------------------------------------------------------------------ análisis

    def run(self) -> List[SimpleCommand]:
        t, n = self.text, self.n
        cmd = _Builder(0)
        word: Optional[_WordBuf] = None
        pending: Optional[tuple] = None          # (op, fd) de una redirección que espera su destino
        i = 0

        def flush(end: int) -> None:
            nonlocal word, pending
            if word is None:
                return
            w = Word(value="".join(word.chars), raw=t[word.start:end], quoted=word.quoted, dynamic=word.dynamic)
            word = None
            if pending is not None:
                cmd.redirects.append(Redirect(op=pending[0], target=w, fd=pending[1]))
                pending = None
            elif not cmd.words and not self.ps and not w.raw.startswith(("'", '"')) and _ASSIGNMENT.match(w.value):
                cmd.assignments.append(w)
            else:
                cmd.words.append(w)

        def end_command(end: int, piped: bool = False) -> None:
            nonlocal cmd
            flush(end)
            if pending is not None:
                raise ShellSyntaxError("redirección sin destino")
            if not cmd.empty:
                self.out.append(self._build(cmd, end))
            elif piped or cmd.piped_input:
                raise ShellSyntaxError("tubería sin comando")
            cmd = _Builder(end)
            cmd.piped_input = piped

        while i < n:
            c = t[i]

            if c in " \t\r":
                flush(i)
                i += 1
                continue

            if c == "\n" and word is None and cmd.empty and cmd.piped_input:
                i += 1                                              # `a |` + salto de línea: la tubería sigue
                continue

            if c in "\n;":
                end_command(i)
                i += 1
                continue

            if c == "&":
                nxt = t[i + 1] if i + 1 < n else ""
                if nxt == "&":
                    end_command(i)
                    i += 2
                    continue
                if nxt == ">" and not self.ps:                      # &> y &>>: stdout y stderr al fichero
                    flush(i)
                    op = "&>>" if t.startswith("&>>", i) else "&>"
                    pending = (op, "&")
                    i += len(op)
                    continue
                if self.ps and word is None and cmd.empty:          # operador de llamada: & 'ruta\prog.exe' args
                    cmd.call = True
                    i += 1
                    continue
                end_command(i)                                       # segundo plano
                i += 1
                continue

            if c == "|":
                nxt = t[i + 1] if i + 1 < n else ""
                if nxt == "|":
                    end_command(i)
                    i += 2
                    continue
                end_command(i, piped=True)
                i += 2 if nxt == "&" else 1
                continue

            if c in "<>":
                if self.ps and c == "<" and t.startswith("<#", i):  # comentario de bloque de PowerShell
                    end = t.find("#>", i + 2)
                    if end < 0:
                        raise ShellSyntaxError("comentario <# sin cerrar")
                    i = end + 2
                    continue
                if not self.ps and t.startswith(("<(", ">("), i) and word is None:   # sustitución de proceso
                    end = self._matching(i + 1, "(", ")")
                    self._nested(t[i + 2:end])
                    buf = _WordBuf(i)
                    buf.chars.append(t[i:end + 1])
                    buf.dynamic = True
                    word = buf
                    i = end + 1
                    continue
                fd = ""
                if word is not None and not word.quoted and t[word.start:i] in ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "*"):
                    fd = t[word.start:i]
                    word = None
                else:
                    flush(i)
                if t.startswith("<<<", i):
                    op = "<<<"
                elif t.startswith("<<", i):
                    raise ShellSyntaxError("here-doc no soportado")
                else:
                    op = next(o for o in (">>", ">|", ">&", "<&", "<>", ">", "<") if t.startswith(o, i))
                i += len(op)
                if op in (">&", "<&"):
                    m = re.compile(r"\s*([0-9]+|-)(?=$|[\s;|&)])").match(t, i)
                    if m:
                        cmd.redirects.append(Redirect(op=op, target=Word(m.group(1), m.group(1)), fd=fd))
                        i = m.end()
                        continue
                pending = (op, fd)
                continue

            if c == "#" and word is None:                            # comentario hasta fin de línea
                end = t.find("\n", i)
                i = n if end < 0 else end
                continue

            if c == "(":
                end = self._matching(i, "(", ")")
                inner = t[i + 1:end]
                if word is None or "".join(word.chars) in ("@", "$"):
                    # Subshell / agrupación al inicio, o expresión entre paréntesis como argumento: son comandos
                    self._nested(inner)
                    if word is None and cmd.empty and not self.ps:
                        cmd.group = True                            # (cd x; ls) > out
                    else:
                        buf = word or _WordBuf(i)                   # (Get-Content x).Length, Write-Output (Get-Date)
                        buf.chars.append(t[i:end + 1])
                        buf.dynamic = True
                        word = buf
                else:
                    word.chars.append(t[i:end + 1])                 # llamada a método: $x.Trim(), [IO.File]::Exists(...)
                    word.dynamic = True
                i = end + 1
                continue

            if c == ")":
                raise ShellSyntaxError("')' sin abrir")

            if c == "{" and word is None:
                end = self._matching(i, "{", "}")
                inner = t[i + 1:end]
                if not inner.strip():
                    buf = _WordBuf(i)                               # `{}` de find -exec
                    buf.chars.append("{}")
                    word = buf
                elif not self.ps and not re.search(r"[\s;|&]", inner):
                    buf = _WordBuf(i)                               # expansión de llaves: {a,b}, {1..3}
                    buf.chars.append(t[i:end + 1])
                    buf.dynamic = True
                    word = buf
                else:
                    self._nested(inner)                             # bloque: { a; b; } o scriptblock de PowerShell
                    if cmd.empty:
                        cmd.group = True
                    else:
                        buf = _WordBuf(i)
                        buf.chars.append("{…}")
                        buf.dynamic = True
                        word = buf
                i = end + 1
                continue

            # Caracteres que forman parte de una palabra
            if word is None:
                word = _WordBuf(i)
            if c == "'":
                end = self._skip_quoted(i)
                word.chars.append(t[i + 1:end - 1].replace("''", "'") if self.ps else t[i + 1:end - 1])
                word.quoted = True
                i = end
            elif c == '"':
                i = self._double_quoted(i, word)
            elif c == "\\" and not self.ps:
                if i + 1 < n and t[i + 1] == "\n":
                    i += 2                                          # continuación de línea
                elif i + 1 < n:
                    word.chars.append(t[i + 1])
                    word.quoted = True
                    i += 2
                else:
                    i += 1
            elif c == "`":
                if self.ps:
                    if i + 1 < n and t[i + 1] != "\n":
                        word.chars.append(t[i + 1])
                    i += 2
                else:
                    end = self._backtick_end(i)
                    self._nested(t[i + 1:end])
                    word.chars.append(t[i:end + 1])
                    word.dynamic = True
                    i = end + 1
            elif c == "$":
                i = self._dollar(i, word)
            else:
                word.chars.append(c)
                i += 1

        end_command(n)
        return self.out

    def _build(self, b: _Builder, end: int) -> SimpleCommand:
        words = list(b.words)
        assignments = list(b.assignments)
        # PowerShell: en `$x = <comando>` y `$env:X='1'` la parte izquierda es una asignación
        if self.ps and words and words[0].raw.startswith("$"):
            first = words[0]
            m = re.match(r"^\$[\w:]+(?:\+|-|\*|/|%|\?\?)?=(.*)$", first.raw, re.S)
            if m:
                assignments.append(first)
                rest = m.group(1)
                words = words[1:]
                if rest and not rest.startswith(("'", '"', "$", "(", "@", "[")) and not rest[0].isdigit():
                    words.insert(0, Word(value=rest, raw=rest))     # `$x=Get-Content a.txt`
            elif len(words) >= 2 and words[1].raw in _PS_ASSIGN_OPS:
                assignments.append(first)
                words = words[2:]
        raw = self.text[b.start:end].strip().lstrip("|&;").strip()
        return SimpleCommand(words=words, redirects=b.redirects, assignments=assignments, raw=raw,
                             nested=self.depth > 0, piped_input=b.piped_input, call=b.call)
