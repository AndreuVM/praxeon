"""Clasificación sobre el comando ya analizado sintácticamente (auditoría §4.2, AUD-18).

``CommandClassifier`` analiza el texto con ``shell_syntax`` y, comando simple a comando simple, decide con lo que el
comando hace de verdad (``command_effects``) en lugar de solo con el verbo:

- Desofusca el nombre del programa (``r''m``, ``"rm"``, ``r\\m``, ``$(echo rm)``) y analiza lo que va dentro de
  ``bash -c``, ``powershell -Command``, ``cmd /c``, ``eval``, ``find -exec``, ``xargs``, ``env``, ``timeout``...
- Evalúa las rutas objetivo contra el workspace: escribir o borrar fuera de él, en rutas protegidas (``.git``, ``.env``,
  claves) o en un destino que depende de una variable sube el riesgo a HIGH; vaciar un fichero existente, también.
- Reconoce operaciones que antes caían en UNKNOWN: ``find`` (solo lista o borra), ``git clean`` (``-n`` lista, ``-f``
  borra), ``truncate``/``shred``/``Clear-Content``, código en línea de ``perl``/``ruby``/``node``/``php``, cmdlets de
  escritura de PowerShell y expresiones de PowerShell sin efectos dentro de un ``ForEach-Object``.

Las reglas de veto por expresiones regulares siguen corriendo antes sobre el texto completo, y la ruta anterior
(división por operadores) se usa si el texto no se puede analizar. Además, si esa ruta anterior da un riesgo HIGH o
CRITICAL mayor que el análisis sintáctico, se queda la anterior (red de seguridad: el análisis solo puede afinar).
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from praxeon.domain.assessment import CommandCategory, CommandRiskAssessment, RiskLevel
from praxeon.reasoning import command_effects as fx
from praxeon.reasoning.shell_syntax import POSIX, POWERSHELL, ShellSyntaxError, SimpleCommand, Word, parse

MAX_NESTING = 4
_LEVEL = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3, RiskLevel.CRITICAL: 4}
_CLASSIFIER = "deterministic_syntax"


def _assess(op: str, category: CommandCategory, risk: RiskLevel, rule: str, reason: str, *, read_only: bool = False,
            reversible: bool = True, destructive: bool = False, external: bool = False, network: bool = False,
            privilege: bool = False, confidence: float = 0.9) -> CommandRiskAssessment:
    return CommandRiskAssessment(
        operation=op, category=category, risk_level=risk, read_only=read_only, reversible=reversible,
        destructive=destructive, external_side_effect=external, network_access=network, privilege_escalation=privilege,
        confidence=confidence, reasons=[reason], matched_rules=[rule], classifier=_CLASSIFIER,
        context_hash=hashlib.sha256(op.encode("utf-8")).hexdigest(),
    )


class _Cwd:
    """Carpeta de trabajo a lo largo del comando: ``cd /etc && echo x > hosts`` escribe en /etc, no en el workspace."""

    def __init__(self, root: Optional[str]):
        self.root = root
        self.cwd: Optional[str] = None
        self.unknown = False
        self.stack: List[tuple] = []

    def cd(self, target: Word) -> None:
        self.stack.append((self.cwd, self.unknown))
        if self.unknown and not _is_anchored(target.value):
            return
        new = fx.resolve(target.value, self.root, self.cwd)
        self.cwd, self.unknown = (new, False) if new is not None else (None, True)

    def back(self) -> None:
        self.cwd, self.unknown = self.stack.pop() if self.stack else (None, True)

    def locate(self, word: Word) -> fx.Location:
        if self.unknown and not _is_anchored(word.value):
            return fx.Location.UNKNOWN
        return fx.locate(word, self.root, self.cwd)

    def whole_workspace(self, word: Word) -> bool:
        return not self.unknown and fx.is_whole_workspace(word, self.root, self.cwd)

    def existing_file(self, word: Word) -> bool:
        return not self.unknown and fx.existing_file(word, self.root, self.cwd)


def _is_anchored(value: str) -> bool:
    """Ruta que no depende de la carpeta actual (absoluta, de unidad, del home o de una variable)."""
    v = value.replace("\\", "/")
    return v.startswith(("/", "~", "$", "%")) or (len(v) > 1 and v[1] == ":")


class SyntacticAnalysisMixin:
    """Ruta sintáctica de ``CommandClassifier.classify`` (ver el docstring del módulo)."""

    # Los aporta CommandClassifier
    def _classify_atomic(self, op_clean: str, tool: Optional[str] = None, arguments: Optional[Dict[str, Any]] = None,
                         context: Optional[Dict[str, Any]] = None) -> CommandRiskAssessment:
        raise NotImplementedError

    def _sensitive_read(self, op_clean: str, op_lower: str, ctx_hash: str) -> Optional[CommandRiskAssessment]:
        raise NotImplementedError

    def _aggregate(self, subs: List[CommandRiskAssessment], op_clean: str, context: Optional[Dict[str, Any]],
                   extra_reasons: Optional[List[str]] = None, classifier: str = "deterministic_compound") -> CommandRiskAssessment:
        raise NotImplementedError

    # ------------------------------------------------------------------------------------------------ entrada

    def _classify_syntactic(self, op_clean: str, context: Optional[Dict[str, Any]]) -> Optional[CommandRiskAssessment]:
        """Clasificación por análisis sintáctico, o ``None`` si el texto no se puede analizar con seguridad."""
        try:
            script = parse(op_clean, _dialect_of(context))
        except ShellSyntaxError:
            return None
        cwd = _Cwd((context or {}).get("workspace_root"))
        subs: List[CommandRiskAssessment] = []
        for cmd in script.commands:
            subs.extend(self._assess_command(cmd, script.dialect, context, cwd, 0))
        if not subs:
            return None
        if len(subs) == 1:
            only = subs[0]
            return only.model_copy(update={"operation": op_clean,
                                           "context_hash": hashlib.sha256(f"{op_clean}|{context or {}}".encode("utf-8")).hexdigest()})
        return self._aggregate(subs, op_clean, context, classifier=_CLASSIFIER)

    def _classify_payload(self, code: str, dialect: str, context: Optional[Dict[str, Any]], cwd: _Cwd,
                          depth: int) -> List[CommandRiskAssessment]:
        """Lo que se ejecuta dentro de ``bash -c``, ``powershell -Command``, ``eval``..."""
        try:
            script = parse(code, dialect)
        except ShellSyntaxError:
            return [self._classify_atomic(code, context=context)]
        out: List[CommandRiskAssessment] = []
        for cmd in script.commands:
            out.extend(self._assess_command(cmd, dialect, context, cwd, depth + 1))
        return out

    # ------------------------------------------------------------------------------------------------ un comando

    def _assess_command(self, cmd: SimpleCommand, dialect: str, context: Optional[Dict[str, Any]], cwd: _Cwd,
                        depth: int) -> List[CommandRiskAssessment]:
        if not (cmd.words or cmd.redirects or cmd.assignments):
            return []
        text = _command_text(cmd)
        if depth > MAX_NESTING:
            return [_assess(text, CommandCategory.UNKNOWN, RiskLevel.HIGH, "RULE_NESTING_TOO_DEEP",
                            "Comando anidado demasiadas veces para analizarlo: requiere revisión humana.")]
        eff = fx.analyze(cmd, dialect)
        out: List[CommandRiskAssessment] = []
        for code, d in eff.payloads:
            out.extend(self._classify_payload(code, d, context, cwd, depth))
        for argv in eff.inner:
            inner = SimpleCommand(words=argv, raw=" ".join(w.raw for w in argv), nested=True, piped_input=cmd.piped_input)
            out.extend(self._assess_command(inner, dialect, context, cwd, depth + 1))
        base = self._base_assessment(cmd, eff, text, context, cwd, dialect)
        out.append(self._with_targets(base, eff, cwd))
        if eff.cwd_change is not None:
            cwd.cd(eff.cwd_change)
        elif eff.cwd_reset:
            cwd.back()
        return out

    def _base_assessment(self, cmd: SimpleCommand, eff: fx.Effects, text: str, context: Optional[Dict[str, Any]],
                         cwd: _Cwd, dialect: str) -> CommandRiskAssessment:
        v = eff.verdict
        if v == "privilege":
            return _assess(text, CommandCategory.PRIVILEGE, RiskLevel.CRITICAL, "RULE_PRIVILEGE_ESCALATION",
                           "Lanza un proceso con privilegios elevados (Start-Process -Verb RunAs).", reversible=False,
                           external=True, privilege=True, confidence=1.0)
        if v == "git_clean_force":
            return _assess(text, CommandCategory.DESTRUCTIVE, RiskLevel.CRITICAL, "RULE_DESTRUCTIVE_COMMAND",
                           "git clean -f borra sin copia los ficheros no versionados (con -x, también .env y entornos).",
                           reversible=False, destructive=True, confidence=1.0)
        if v == "dynamic_name":
            return _assess(text, CommandCategory.UNKNOWN, RiskLevel.HIGH, "RULE_DYNAMIC_COMMAND_NAME",
                           "El programa a ejecutar sale de una variable o sustitución: no se sabe qué se ejecuta.",
                           reversible=False)
        if v == "dynamic_exec":
            return _assess(text, CommandCategory.UNKNOWN, RiskLevel.HIGH, "RULE_DYNAMIC_CODE_EXECUTION",
                           "Ejecuta código construido al vuelo (eval / Invoke-Expression con contenido variable).",
                           reversible=False)
        if v == "dynamic_read":
            return _assess(text, CommandCategory.UNKNOWN, RiskLevel.MEDIUM, "RULE_DYNAMIC_READ_TARGET",
                           "Vuelca ficheros cuya ruta solo se conoce al ejecutar: podrían incluir .env o claves.",
                           confidence=0.6)
        if v == "dangerous_env":
            return _assess(text, CommandCategory.UNKNOWN, RiskLevel.HIGH, "RULE_EXECUTION_ENVIRONMENT_CHANGE",
                           "Cambia variables que deciden qué programas o código se cargan (PATH, LD_PRELOAD, NODE_OPTIONS...).",
                           reversible=False)
        if eff.inline_code is not None:
            lang, code = eff.inline_code
            if fx.INLINE_DANGER[lang].search(code):
                return _assess(text, CommandCategory.DESTRUCTIVE, RiskLevel.CRITICAL, "RULE_INLINE_CODE_DYNAMIC_OR_DESTRUCTIVE",
                               f"Código {lang} en línea que borra, escribe, lanza procesos o ejecuta código construido.",
                               reversible=False, destructive=True, external=True,
                               network=bool(fx.INLINE_NETWORK[lang].search(code)), confidence=0.95)
            if fx.INLINE_NETWORK[lang].search(code):
                return _assess(text, CommandCategory.NETWORK, RiskLevel.HIGH, "RULE_INLINE_CODE_NETWORK",
                               f"Código {lang} en línea que abre conexiones de red.", reversible=False, external=True,
                               network=True)
        if eff.deletes or eff.delete_dynamic:
            return self._delete_assessment(text, eff, cwd)
        if eff.truncates:
            return _assess(text, CommandCategory.LOCAL_MUTATION, RiskLevel.HIGH, "RULE_CONTENT_DESTRUCTION_REVIEW",
                           "Vacía o destruye el contenido de ficheros sin copia: requiere confirmación humana.",
                           reversible=False)
        if v == "git_discard":
            return _assess(text, CommandCategory.LOCAL_MUTATION, RiskLevel.HIGH, "RULE_DISCARD_UNCOMMITTED_CHANGES",
                           "Descarta cambios locales no guardados en git: no se pueden recuperar.", reversible=False)
        if v == "git_clean":
            return _assess(text, CommandCategory.LOCAL_MUTATION, RiskLevel.HIGH, "RULE_GIT_CLEAN_REVIEW",
                           "git clean borra ficheros no versionados: requiere confirmación humana.", reversible=False)
        if v == "wrapper":
            return _assess(text, CommandCategory.INSPECTION, RiskLevel.LOW, "RULE_COMMAND_WRAPPER",
                           "Lanza otro comando (bash -c, env, timeout, xargs...): decide lo que lanza, evaluado aparte.",
                           read_only=True, confidence=0.9)
        if v in ("git_clean_dry", "find_inspect", "benign", "expression") or not cmd.words:
            low = text.lower()
            sensitive = self._sensitive_read(text, low, hashlib.sha256(text.encode("utf-8")).hexdigest())
            if sensitive is not None:
                return sensitive
            if v in ("benign", "expression") or not cmd.words:
                rule, reason = (("RULE_POWERSHELL_EXPRESSION", "Expresión de PowerShell sin efectos (texto, propiedades).")
                                if v == "expression" else
                                ("RULE_SHELL_BUILTIN", "Orden interna del shell sin efectos fuera de la sesión (cd, set -e, asignación)."))
                return _assess(text, CommandCategory.INSPECTION, RiskLevel.LOW, rule, reason, read_only=True, confidence=0.95)
            return _assess(text, CommandCategory.INSPECTION, RiskLevel.LOW, "RULE_SAFE_INSPECTION",
                           "Solo lista o busca ficheros, sin modificarlos.", read_only=True, confidence=0.95)
        atomic = self._classify_atomic(text, context=context)
        # Un programa que solo escribe ficheros (cp, touch, Set-Content...): no es de red aunque la ruta diga «ssh»
        if (atomic.category in (CommandCategory.UNKNOWN, CommandCategory.NETWORK, CommandCategory.INSPECTION)
                and atomic.risk_level != RiskLevel.CRITICAL and eff.writes and _handled_writer(eff)):
            return _assess(text, CommandCategory.LOCAL_MUTATION, RiskLevel.LOW, "RULE_LOCAL_MUTATION",
                           "Crea, copia o escribe ficheros.", confidence=0.88)
        return atomic

    def _delete_assessment(self, text: str, eff: fx.Effects, cwd: _Cwd) -> CommandRiskAssessment:
        locations = [cwd.locate(t) for t in eff.deletes]
        whole = not eff.delete_filtered and any(cwd.whole_workspace(t) for t in eff.deletes)
        L = fx.Location
        if eff.delete_recursive and (whole or any(loc in (L.OUTSIDE, L.PROTECTED) for loc in locations)):
            return _assess(text, CommandCategory.DESTRUCTIVE, RiskLevel.CRITICAL, "RULE_DESTRUCTIVE_DELETE_SCOPE",
                           "Borrado recursivo del workspace entero, de rutas protegidas o de fuera del workspace.",
                           reversible=False, destructive=True, external=True, confidence=1.0)
        if any(loc == L.OUTSIDE for loc in locations):
            return _assess(text, CommandCategory.LOCAL_MUTATION, RiskLevel.HIGH, "RULE_DELETE_OUTSIDE_WORKSPACE",
                           "Borra ficheros fuera del workspace: requiere confirmación humana.", reversible=False)
        if any(loc == L.PROTECTED for loc in locations):
            return _assess(text, CommandCategory.LOCAL_MUTATION, RiskLevel.HIGH, "RULE_WRITE_PROTECTED_PATH",
                           "Borra una ruta protegida (.git, .env, claves): requiere confirmación humana.", reversible=False)
        if eff.delete_dynamic or any(loc == L.UNKNOWN for loc in locations):
            return _assess(text, CommandCategory.LOCAL_MUTATION, RiskLevel.HIGH, "RULE_DELETE_DYNAMIC_TARGET",
                           "Borra objetivos que llegan por tubería, xargs o una variable: no se sabe qué se borra.",
                           reversible=False)
        if eff.name == "rmdir" and not eff.delete_recursive:
            return _assess(text, CommandCategory.LOCAL_MUTATION, RiskLevel.LOW, "RULE_LOCAL_MUTATION",
                           "Borra carpetas vacías del workspace.", confidence=0.88)
        return _assess(text, CommandCategory.LOCAL_MUTATION, RiskLevel.HIGH, "RULE_WORKSPACE_DELETE_REVIEW",
                       "Borrado dentro del workspace sobre rutas no protegidas: requiere confirmación humana.",
                       reversible=False)

    # ------------------------------------------------------------------------------------------------ rutas objetivo

    def _with_targets(self, base: CommandRiskAssessment, eff: fx.Effects, cwd: _Cwd) -> CommandRiskAssessment:
        """Sube el riesgo según dónde escribe el comando (fuera, protegido, destino variable, sobrescritura)."""
        L = fx.Location
        findings: List[tuple] = []
        writes = False
        for w in eff.writes:
            loc = cwd.locate(w.target)
            if loc == L.DISCARD:
                continue
            writes = True
            label = w.target.value
            if w.mode == "link":
                if loc in (L.OUTSIDE, L.UNKNOWN, L.PROTECTED):
                    findings.append(("RULE_LINK_OUTSIDE_WORKSPACE",
                                     f"Crea un enlace hacia '{label}', fuera del workspace o protegido: las rutas que pasen por él escapan de los controles."))
                continue
            if loc == L.OUTSIDE:
                findings.append(("RULE_WRITE_OUTSIDE_WORKSPACE",
                                 f"Escribe o mueve '{label}', fuera del workspace: requiere confirmación humana."))
            elif loc == L.UNKNOWN:
                findings.append(("RULE_WRITE_DYNAMIC_TARGET",
                                 f"El destino '{label}' depende de una variable o de la carpeta actual: no se sabe dónde escribe."))
            elif loc == L.PROTECTED:
                findings.append(("RULE_WRITE_PROTECTED_PATH",
                                 f"Escribe en una ruta protegida ('{label}': .git, .env, claves): requiere confirmación humana."))
            elif w.mode in ("truncate", "dest") and cwd.existing_file(w.target):
                findings.append(("RULE_OVERWRITE_EXISTING_FILE",
                                 f"Sobrescribiría un fichero existente ('{label}') sin copia: requiere confirmación humana."))
        if not writes:
            return base
        update: Dict[str, Any] = {"read_only": False}
        category = base.category
        if category in (CommandCategory.INSPECTION, CommandCategory.BUILD_TEST):
            category = CommandCategory.LOCAL_MUTATION
        if findings:
            risk = base.risk_level if _LEVEL[base.risk_level] >= _LEVEL[RiskLevel.HIGH] else RiskLevel.HIGH
            update.update(risk_level=risk, reversible=False,
                          reasons=list(base.reasons) + [f[1] for f in findings],
                          matched_rules=list(base.matched_rules) + list(dict.fromkeys(f[0] for f in findings)))
            if category == CommandCategory.UNKNOWN:
                category = CommandCategory.LOCAL_MUTATION
        update["category"] = category
        return base.model_copy(update=update)


def _handled_writer(eff: fx.Effects) -> bool:
    """Las escrituras vienen del propio programa (Set-Content, cp, mkdir...), no solo de una redirección."""
    return bool(eff.name) and eff.name in _WRITER_NAMES


_WRITER_NAMES = {
    "touch", "mkdir", "md", "new-item", "ni", "cp", "copy", "copy-item", "cpi", "mv", "move", "move-item", "mi",
    "rename-item", "ren", "rni", "robocopy", "xcopy", "rsync", "tee", "tee-object", "set-content", "sc", "add-content",
    "ac", "out-file", "sed", "ln", "dd",
}


def _command_text(cmd: SimpleCommand) -> str:
    """Texto del comando para las reglas por verbo: nombre desofuscado, argumentos y redirecciones de entrada.

    Las redirecciones de salida no van: sus destinos se evalúan como rutas objetivo (``_with_targets``).
    """
    if not cmd.words:
        return cmd.raw or " ".join(a.raw for a in cmd.assignments)
    first = cmd.words[0]
    name = first.value
    folded = fx._fold_echo(first) if first.dynamic else None
    if folded:
        name = folded
    elif "\\" in name and fx.command_name(first) == name.replace("\\", "").lower():
        name = name.replace("\\", "")
    if any(ch in name for ch in " \t"):
        name = first.raw                                              # ruta con espacios: el texto original
    parts = [name] + [w.raw for w in cmd.words[1:]]
    parts += [f"< {r.target.raw}" for r in cmd.redirects if r.reads]
    return " ".join(parts)


def _dialect_of(context: Optional[Dict[str, Any]]) -> Optional[str]:
    """Dialecto indicado en el contexto (``shell``); sin él, se deduce del propio texto."""
    shell = str((context or {}).get("shell") or "").lower()
    if shell in ("powershell", "pwsh"):
        return POWERSHELL
    if shell in ("sh", "bash", "posix", "zsh", "dash"):
        return POSIX
    return None
