"""Coordinación de escritura entre sesiones sobre un mismo workspace (FE-009, nota N-001).

Varios agentes pueden trabajar a la vez en el mismo workspace. ``edit_file`` reemplaza el archivo entero, de modo que si dos sesiones lo
leen y luego lo escriben, la segunda borra sin aviso lo que hizo la primera. Aquí cada sesión «ve» una versión de cada archivo cuando lo
lee o lo escribe, y una escritura se rechaza si el archivo cambió desde que esa sesión lo vio: el agente recibe un error claro, relee y
vuelve a aplicar su cambio sobre la versión actual. No se bloquea ni se encola a nadie (D2): se rechaza al segundo con aviso.

Alcance y límites, a propósito:
  * Es un registro en memoria de UN proceso del servidor. Otro proceso (una CLI externa) no lo comparte, pero sí lo detecta si cambia el
    contenido en disco, porque la comparación es contra el contenido real y no solo contra lo que anotó el registro.
  * Solo cubre ``read_file``/``view_file`` y ``edit_file``/``write_file``. Un ``run_command`` que modifique archivos (``sed -i``…) no
    pasa por aquí; la versión que vio una sesión sí cambiará y se detectará en su siguiente escritura.
  * Sin ninguna anotación previa se permite escribir (crear archivos nuevos no se rompe). Si el archivo lo escribió otra sesión y esta no
    lo ha leído, se rechaza: sobrescribir a ciegas el trabajo de otro agente es justo el caso que se quiere evitar.
  * Sin interbloqueos: se sostiene un único cerrojo por archivo y solo durante una lectura o escritura.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

READ_TOOLS = frozenset({"read_file", "view_file"})
WRITE_TOOLS = frozenset({"edit_file", "write_file"})
EVENT_TYPE = "workspace.file_conflict"

_HASH_LIMIT = 32 * 1024 * 1024        # por encima de este tamaño se compara tamaño + fecha de modificación en lugar del contenido
_MAX_SEEN = 20_000                    # versiones «vistas» que se recuerdan; las más antiguas se olvidan (y vuelven a «sin anotación»)


def fingerprint(real_path: str) -> str | None:
    """Huella de la versión actual del archivo (``None`` si no existe o no es un archivo)."""
    try:
        st = os.stat(real_path)
    except OSError:
        return None
    if not os.path.isfile(real_path):
        return None
    if st.st_size > _HASH_LIMIT:
        return f"big:{st.st_size}:{st.st_mtime_ns}"
    digest = hashlib.sha256()
    try:
        with open(real_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def file_key(root: str, path: str) -> str:
    """Clave estable de un archivo: ruta real (sin enlaces simbólicos) y, en Windows, sin distinguir mayúsculas."""
    return os.path.normcase(os.path.realpath(os.path.join(root, path)))


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


@dataclass
class _LastWrite:
    session_id: str
    at: float
    fingerprint: str | None


class FileGuard:
    """Resultado de pedir paso para leer o escribir un archivo. Mantiene el cerrojo del archivo hasta ``release()``."""

    def __init__(self, coordinator: FileCoordinator, tool: str, session_id: str, key: str, label: str, lock: threading.Lock) -> None:
        self._coordinator = coordinator
        self._tool = tool
        self._session_id = session_id
        self._key = key
        self._label = label
        self._lock = lock
        self._released = False
        self.conflict: dict[str, Any] | None = None

    def finish(self, success: bool) -> None:
        """Anota la versión que la sesión ha visto (lectura) o dejado (escritura). Sin éxito no se anota nada."""
        if success and self.conflict is None:
            self._coordinator._record(self._tool, self._session_id, self._key)

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._lock.release()


class FileCoordinator:
    """Registro de qué versión de cada archivo ha visto cada sesión y quién lo escribió por última vez."""

    def __init__(self, max_seen: int = _MAX_SEEN) -> None:
        self._max_seen = max_seen
        self._mutex = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._seen: OrderedDict[tuple[str, str], str | None] = OrderedDict()
        self._last_write: dict[str, _LastWrite] = {}

    # ------------------------------------------------------------------ API
    def begin(self, tool: str, arguments: dict[str, Any], session_id: str, root: str) -> FileGuard | None:
        """Pide paso para ``tool``. Devuelve ``None`` si la herramienta no es de archivos o falta la ruta.

        Si la escritura entra en conflicto, ``guard.conflict`` trae los datos y NO debe ejecutarse la herramienta.
        El llamante debe llamar siempre a ``release()`` (``guard(...)`` lo hace por él).
        """
        if tool not in READ_TOOLS and tool not in WRITE_TOOLS:
            return None
        label = str(arguments.get("path") or arguments.get("file") or "").strip()
        if not label:
            return None
        key = file_key(root, label)
        with self._mutex:
            lock = self._locks.setdefault(key, threading.Lock())
        lock.acquire()
        guard = FileGuard(self, tool, session_id, key, label, lock)
        try:
            if tool in WRITE_TOOLS:
                guard.conflict = self._check_write(session_id, key, label)
            else:
                # La huella se toma ANTES de leer y con el cerrojo: si algo cambiara a mitad, la sesión quedaría con una huella más
                # antigua que lo leído (falso conflicto seguro) y nunca con una más nueva (conflicto perdido).
                self._remember(session_id, key, fingerprint(key))
        except BaseException:
            guard.release()
            raise
        return guard

    @contextmanager
    def guard(self, tool: str, arguments: dict[str, Any], session_id: str, root: str) -> Iterator[FileGuard | None]:
        g = self.begin(tool, arguments, session_id, root)
        try:
            yield g
        finally:
            if g is not None:
                g.release()

    def forget_session(self, session_id: str) -> None:
        """Olvida lo que vio una sesión (al terminar). Lo que escribió sigue constando como última escritura del archivo."""
        with self._mutex:
            for k in [k for k in self._seen if k[0] == session_id]:
                del self._seen[k]

    # ------------------------------------------------------------------ interno
    def _remember(self, session_id: str, key: str, fp: str | None) -> None:
        with self._mutex:
            self._seen[(session_id, key)] = fp
            self._seen.move_to_end((session_id, key))
            while len(self._seen) > self._max_seen:
                self._seen.popitem(last=False)

    def _record(self, tool: str, session_id: str, key: str) -> None:
        fp = fingerprint(key)
        if tool in WRITE_TOOLS:
            with self._mutex:
                self._last_write[key] = _LastWrite(session_id, time.time(), fp)
            self._remember(session_id, key, fp)
        # Una lectura ya anotó su huella en ``begin`` (antes de leer)

    def _check_write(self, session_id: str, key: str, label: str) -> dict[str, Any] | None:
        current = fingerprint(key)
        with self._mutex:
            seen_known = (session_id, key) in self._seen
            seen = self._seen.get((session_id, key))
            last = self._last_write.get(key)
        if seen_known:
            if current == seen:
                return None
            if current is None:
                return self._conflict(label, "deleted", None, None, "ya no existe: lo eliminaron después de que lo leyeras")
            if last is not None and last.fingerprint == current and last.session_id != session_id:
                return self._conflict(
                    label, "modified_since_read", last.session_id, last.at,
                    f"la sesión '{last.session_id}' lo modificó a las {_clock(last.at)} después de que lo leyeras",
                )
            return self._conflict(label, "modified_since_read", None, None, "se modificó fuera de esta sesión después de que lo leyeras")
        if last is not None and last.session_id != session_id:
            return self._conflict(
                label, "unread_overwrite", last.session_id, last.at,
                f"la sesión '{last.session_id}' lo escribió a las {_clock(last.at)} y tú no lo has leído",
            )
        return None

    @staticmethod
    def _conflict(label: str, reason: str, by: str | None, at: float | None, why: str) -> dict[str, Any]:
        message = (
            f"Conflicto de escritura en '{label}': {why}. No se ha escrito nada. "
            "Vuelve a leerlo con read_file y aplica tu cambio sobre la versión actual."
        )
        return {"path": label, "reason": reason, "conflicting_session": by, "modified_at": at, "message": message}
