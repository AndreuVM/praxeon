"""Aislamiento nativo en Windows: AppContainer + Job Object (AUD-19, S-01). Sin instalar nada.

Un proceso en un AppContainer:

- No tiene red: sin las capacidades ``internetClient``/``privateNetworkClientServer`` falla la resolución DNS y se
  rechazan los sockets, también hacia ``localhost``.
- Solo accede a lo que lo permite expresamente: carpetas del sistema legibles por «ALL APPLICATION PACKAGES»
  (``C:\\Windows``, ``C:\\Program Files``) y lo que PRAXEON le concede. El perfil del usuario (``~/.ssh``, ``.env`` de
  otros proyectos, navegadores) queda fuera.

PRAXEON crea un AppContainer por workspace (``praxeon.ws.<hash>``) y le concede:

- Modificar en el workspace (con herencia).
- Leer y ejecutar en las carpetas de herramientas que lo necesitan (el Python base de un entorno virtual, ``git``...).
- Solo «leer atributos», sin herencia, en las carpetas que hay por encima de las anteriores: PowerShell lo necesita
  para entrar en ellas. Se aplica con ``SetFileSecurityW``, que no recorre el contenido de esas carpetas.

Los permisos se pueden revocar (``revoke``), y el proceso corre en un Job Object que mata todo el árbol de procesos
al vencer el tiempo y limita el número de procesos y la memoria.
"""

from __future__ import annotations

import sys

# Módulo solo de Windows: fuera de él no se importa (y mypy no analiza el resto en otras plataformas)
assert sys.platform == "win32"  # nosec B101

import ctypes  # noqa: E402
import ctypes.wintypes as wt  # noqa: E402
import json  # noqa: E402
import msvcrt  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402  # nosec B404 - solo TimeoutExpired y list2cmdline; no se lanza ningún proceso con él
import threading  # noqa: E402
from collections.abc import Iterable  # noqa: E402
from typing import Any  # noqa: E402

from praxeon.runtime.isolation import container_name  # noqa: E402

# Máscaras de acceso a ficheros
READ_ATTRIBUTES = 0x0080
READ_EXECUTE = 0x1200A9            # FILE_GENERIC_READ | FILE_GENERIC_EXECUTE
MODIFY = 0x1301BF                  # lectura, escritura, ejecución y borrado (sin cambiar permisos)

_HRESULT_ALREADY_EXISTS = 0x800700B7


# --------------------------------------------------------------------------------------------- API de Windows

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_uenv = ctypes.WinDLL("userenv", use_last_error=True)
_adv = ctypes.WinDLL("advapi32", use_last_error=True)
PSID = ctypes.c_void_p

_uenv.CreateAppContainerProfile.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(PSID)]
_uenv.CreateAppContainerProfile.restype = ctypes.c_long
_uenv.DeriveAppContainerSidFromAppContainerName.argtypes = [wt.LPCWSTR, ctypes.POINTER(PSID)]
_uenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
_uenv.DeleteAppContainerProfile.argtypes = [wt.LPCWSTR]
_uenv.DeleteAppContainerProfile.restype = ctypes.c_long
_uenv.GetAppContainerFolderPath.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.LPWSTR)]
_uenv.GetAppContainerFolderPath.restype = ctypes.c_long
_adv.ConvertSidToStringSidW.argtypes = [PSID, ctypes.POINTER(wt.LPWSTR)]
_adv.ConvertSidToStringSidW.restype = wt.BOOL
_adv.GetNamedSecurityInfoW.argtypes = [wt.LPCWSTR, ctypes.c_int, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_adv.GetNamedSecurityInfoW.restype = wt.DWORD
_adv.SetNamedSecurityInfoW.argtypes = [wt.LPWSTR, ctypes.c_int, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p]
_adv.SetNamedSecurityInfoW.restype = wt.DWORD
_adv.SetEntriesInAclW.argtypes = [wt.ULONG, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_adv.SetEntriesInAclW.restype = wt.DWORD
_adv.SetFileSecurityW.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.c_void_p]
_adv.SetFileSecurityW.restype = wt.BOOL
_adv.InitializeSecurityDescriptor.argtypes = [ctypes.c_void_p, wt.DWORD]
_adv.SetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, wt.BOOL, ctypes.c_void_p, wt.BOOL]
_adv.SetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, wt.WORD, wt.WORD]
_adv.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.WORD), ctypes.POINTER(wt.DWORD)]
_adv.GetAce.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.POINTER(ctypes.c_void_p)]
_adv.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_k32.LocalFree.argtypes = [ctypes.c_void_p]
_k32.CreateJobObjectW.restype = wt.HANDLE
_k32.CreateFileW.restype = wt.HANDLE
_k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]

class _TRUSTEE(ctypes.Structure):
    _fields_ = [("pMultipleTrustee", ctypes.c_void_p), ("MultipleTrusteeOperation", ctypes.c_int),
                ("TrusteeForm", ctypes.c_int), ("TrusteeType", ctypes.c_int), ("ptstrName", ctypes.c_void_p)]

class _EXPLICIT_ACCESS(ctypes.Structure):
    _fields_ = [("grfAccessPermissions", wt.DWORD), ("grfAccessMode", ctypes.c_int), ("grfInheritance", wt.DWORD),
                ("Trustee", _TRUSTEE)]

class _ACL(ctypes.Structure):
    _fields_ = [("AclRevision", ctypes.c_ubyte), ("Sbz1", ctypes.c_ubyte), ("AclSize", wt.WORD),
                ("AceCount", wt.WORD), ("Sbz2", wt.WORD)]

class _ACE(ctypes.Structure):
    _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", wt.WORD), ("Mask", wt.DWORD),
                ("SidStart", wt.DWORD)]

class _SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [("AppContainerSid", PSID), ("Capabilities", ctypes.c_void_p), ("CapabilityCount", wt.DWORD),
                ("Reserved", wt.DWORD)]

class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
                ("hStdError", wt.HANDLE)]

class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]

class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wt.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", wt.BOOL)]

class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

class _BASIC_LIMIT(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]

class _EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BASIC_LIMIT), ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class AppContainerError(OSError):
    """No se pudo crear el AppContainer, conceder permisos o lanzar el proceso."""


class AppContainer:
    """El AppContainer de un workspace: su SID, su carpeta de datos y los permisos concedidos."""

    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.realpath(workspace_root)
        self.name = container_name(self.workspace_root)
        self._sid = PSID()
        hr = _uenv.CreateAppContainerProfile(self.name, "PRAXEON sandbox", f"PRAXEON: {self.workspace_root}"[:250],
                                             None, 0, ctypes.byref(self._sid))
        if (hr & 0xFFFFFFFF) == _HRESULT_ALREADY_EXISTS:
            hr = _uenv.DeriveAppContainerSidFromAppContainerName(self.name, ctypes.byref(self._sid))
        if hr != 0:
            raise AppContainerError(f"No se pudo crear el AppContainer {self.name} (HRESULT {hr & 0xFFFFFFFF:#x}).")
        text = wt.LPWSTR()
        if not _adv.ConvertSidToStringSidW(self._sid, ctypes.byref(text)):
            raise AppContainerError("No se pudo leer el SID del AppContainer.")
        self.sid: str = text.value or ""
        _k32.LocalFree(text)
        folder = wt.LPWSTR()
        if _uenv.GetAppContainerFolderPath(self.sid, ctypes.byref(folder)) != 0:
            raise AppContainerError("No se pudo obtener la carpeta de datos del AppContainer.")
        self.data_folder: str = folder.value or ""
        _k32.LocalFree(folder)

    # ----------------------------------------------------------------------------------------- permisos

    def ensure_access(self, read_paths: Iterable[str] = ()) -> list[str]:
        """Concede lo necesario (idempotente): modificar el workspace, leer las herramientas, atravesar los antecesores.

        Devuelve las carpetas en las que se añadió un permiso ahora. En el registro (para ``revoke``) quedan todas las
        que tienen permiso de este AppContainer, nuevas o no, y se guarda aunque falle a mitad.
        """
        granted: list[str] = []
        present: list[str] = []
        inheritable: list[str] = []
        targets: list[tuple[str, int]] = [(self.workspace_root, MODIFY)]
        targets += [(os.path.realpath(p), READ_EXECUTE) for p in read_paths if p and os.path.isdir(p)]
        try:
            for path, mask in targets:
                for ancestor in _ancestors(path):
                    try:
                        if self._grant(ancestor, READ_ATTRIBUTES, inherit=False):
                            granted.append(ancestor)
                        present.append(ancestor)
                    except PermissionError:
                        continue                                      # carpeta del sistema (C:\Users): no es del usuario
                if self._grant(path, mask, inherit=True):
                    granted.append(path)
                present.append(path)
                inheritable.append(path)
        finally:
            if present:
                _record_grants(self.name, self.sid, present, inheritable)
        return granted

    def _grant(self, path: str, mask: int, *, inherit: bool) -> bool:
        dacl, sd = _get_dacl(path)
        try:
            if _has_allow_ace(dacl, self._sid, mask):
                return False
            new_dacl = _set_entries(dacl, self._sid, mask, grant=True, inherit=inherit)
            try:
                if inherit:
                    _set_named_dacl(path, new_dacl)          # propaga a lo que hay dentro
                else:
                    _set_file_dacl(path, new_dacl, sd)       # solo esta carpeta, sin recorrer su contenido
            finally:
                _k32.LocalFree(new_dacl)
        finally:
            _k32.LocalFree(sd)
        return True

    # ----------------------------------------------------------------------------------------- ejecución

    def run(self, args: list[str], cwd: str, env: dict[str, str], timeout: float, max_bytes: int = 524288,
            max_processes: int = 64, memory_mb: int = 2048) -> tuple[int, str, bool]:
        """Lanza ``args`` en el AppContainer dentro de un Job Object. Devuelve ``(código, salida, desbordada)``.

        Al vencer ``timeout`` termina todo el árbol de procesos y lanza ``subprocess.TimeoutExpired``.
        """
        sa = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True)
        read_end, write_end = wt.HANDLE(), wt.HANDLE()
        if not _k32.CreatePipe(ctypes.byref(read_end), ctypes.byref(write_end), ctypes.byref(sa), 0):
            raise AppContainerError(f"CreatePipe: {ctypes.get_last_error()}")
        _k32.SetHandleInformation(read_end, 1, 0)                     # solo el hijo hereda el extremo de escritura
        nul = _k32.CreateFileW("NUL", 0x80000000, 3, ctypes.byref(sa), 3, 0, None)

        size = ctypes.c_size_t()
        _k32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
        attrs = (ctypes.c_byte * size.value)()
        caps = _SECURITY_CAPABILITIES(self._sid, None, 0, 0)          # sin capacidades: sin red
        handles = (wt.HANDLE * 2)(write_end, nul)
        if not (_k32.InitializeProcThreadAttributeList(attrs, 2, 0, ctypes.byref(size))
                and _k32.UpdateProcThreadAttribute(attrs, 0, 0x00020009, ctypes.byref(caps), ctypes.sizeof(caps), None, None)
                and _k32.UpdateProcThreadAttribute(attrs, 0, 0x00020002, handles, ctypes.sizeof(handles), None, None)):
            _close(read_end, write_end, nul)
            raise AppContainerError(f"No se pudo preparar el proceso: {ctypes.get_last_error()}")

        si = _STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        si.StartupInfo.dwFlags = 0x100                                # STARTF_USESTDHANDLES
        si.StartupInfo.hStdInput = nul
        si.StartupInfo.hStdOutput = write_end
        si.StartupInfo.hStdError = write_end
        si.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)

        child_env = dict(env)
        for key in ("TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME"):
            child_env[key] = self.data_folder                         # el único sitio escribible además del workspace
        block = "".join(f"{k}={v}\0" for k, v in sorted(child_env.items(), key=lambda kv: kv[0].upper())) + "\0"
        env_buf = ctypes.create_unicode_buffer(block, len(block))
        cmd_buf = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
        pi = _PROCESS_INFORMATION()
        flags = 0x00080000 | 0x00000004 | 0x08000000 | 0x00000400   # EXTENDED_STARTUPINFO | SUSPENDED | NO_WINDOW | UNICODE_ENV
        ok = _k32.CreateProcessW(None, cmd_buf, None, None, True, flags, env_buf, cwd, ctypes.byref(si), ctypes.byref(pi))
        _close(write_end, nul)
        _k32.DeleteProcThreadAttributeList(attrs)
        if not ok:
            error = ctypes.get_last_error()
            _close(read_end)
            raise AppContainerError(f"No se pudo lanzar el proceso en el AppContainer (error {error}).")

        job = _k32.CreateJobObjectW(None, None)
        limits = _EXTENDED_LIMIT()
        # KILL_ON_JOB_CLOSE | ACTIVE_PROCESS | JOB_MEMORY | DIE_ON_UNHANDLED_EXCEPTION
        limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x8 | 0x200 | 0x400
        limits.BasicLimitInformation.ActiveProcessLimit = max_processes
        limits.JobMemoryLimit = memory_mb * 1024 * 1024
        if not (job and _k32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                and _k32.AssignProcessToJobObject(job, pi.hProcess)):
            _k32.TerminateProcess(pi.hProcess, 1)
            _close(pi.hProcess, pi.hThread, read_end, job)
            raise AppContainerError(f"No se pudo asignar el proceso al Job Object ({ctypes.get_last_error()}).")
        _k32.ResumeThread(pi.hThread)

        chunks: list[bytes] = []
        state = {"total": 0, "overflow": False}

        def reader(fd: int) -> None:
            with os.fdopen(fd, "rb") as stream:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    room = max_bytes - state["total"]
                    if room > 0:
                        chunks.append(chunk[:room])
                        state["total"] += min(len(chunk), room)
                    if len(chunk) > room:
                        state["overflow"] = True

        thread = threading.Thread(target=reader, args=(msvcrt.open_osfhandle(int(read_end.value or 0), os.O_RDONLY),), daemon=True)
        thread.start()
        try:
            waited = _k32.WaitForSingleObject(pi.hProcess, max(1, int(timeout * 1000)))
            if waited == 0x102:                                       # WAIT_TIMEOUT
                _k32.TerminateJobObject(job, 124)
                thread.join(2.0)
                raise subprocess.TimeoutExpired(args, timeout)
            _k32.TerminateJobObject(job, 0)                           # lo que quedara en segundo plano
            thread.join(5.0)
            code = wt.DWORD()
            _k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        finally:
            _close(pi.hProcess, pi.hThread, job)
        out = _decode(b"".join(chunks)).strip() or "Comando ejecutado sin salida"
        if state["overflow"]:
            out += f"\n\n[... Truncado: la salida excedió el límite de {max_bytes // 1024}KB ...]"
        return int(code.value), out, bool(state["overflow"])

    # ----------------------------------------------------------------------------------------- limpieza

    def revoke(self) -> list[str]:
        """Retira todos los permisos concedidos a este AppContainer y borra su perfil."""
        return revoke_container(self.name)


def revoke_container(name: str) -> list[str]:
    """Retira los permisos registrados de un AppContainer de PRAXEON y borra su perfil."""
    state = _load_grants()
    entry = state.pop(name, None)
    revoked: list[str] = []
    if entry:
        sid = PSID()
        if _uenv.DeriveAppContainerSidFromAppContainerName(name, ctypes.byref(sid)) == 0:
            for path in sorted(entry.get("paths", []), key=len, reverse=True):
                if not os.path.exists(path):
                    continue
                dacl, sd = _get_dacl(path)
                try:
                    new_dacl = _set_entries(dacl, sid, 0, grant=False, inherit=False)
                    try:
                        if path in entry.get("inheritable", []):
                            _set_named_dacl(path, new_dacl)
                        else:
                            _set_file_dacl(path, new_dacl, sd)
                    finally:
                        _k32.LocalFree(new_dacl)
                finally:
                    _k32.LocalFree(sd)
                revoked.append(path)
        _uenv.DeleteAppContainerProfile(name)
    _save_grants(state)
    return revoked


def revoke_all() -> list[str]:
    """Retira los permisos de todos los AppContainer de PRAXEON (``praxeon sandbox revoke``)."""
    revoked: list[str] = []
    for name in list(_load_grants()):
        revoked += revoke_container(name)
    return revoked


# --------------------------------------------------------------------------------------------- auxiliares


def _ancestors(path: str) -> list[str]:
    """Carpetas por encima de ``path``, sin la raíz de la unidad (ya legible para los AppContainer)."""
    out: list[str] = []
    current = os.path.dirname(os.path.realpath(path))
    while True:
        drive_rest = os.path.splitdrive(current)[1]
        if drive_rest in ("", "\\", "/"):
            break
        out.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return out


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp850", errors="replace")                  # consola de Windows en español


def _close(*handles: Any) -> None:
    for h in handles:
        if h:
            _k32.CloseHandle(h)


def _get_dacl(path: str) -> tuple[Any, Any]:
    dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    err = _adv.GetNamedSecurityInfoW(path, 1, 4, None, None, ctypes.byref(dacl), None, ctypes.byref(sd))
    if err:
        raise AppContainerError(f"No se pudieron leer los permisos de {path} (error {err}).")
    return dacl, sd


def _has_allow_ace(dacl: Any, sid: Any, mask: int) -> bool:
    if not dacl:
        return False
    acl = ctypes.cast(dacl, ctypes.POINTER(_ACL)).contents
    for i in range(acl.AceCount):
        ace_ptr = ctypes.c_void_p()
        if not _adv.GetAce(dacl, i, ctypes.byref(ace_ptr)):
            continue
        ace = ctypes.cast(ace_ptr, ctypes.POINTER(_ACE)).contents
        if ace.AceType == 0 and (ace.Mask & mask) == mask:
            sid_addr = int(ace_ptr.value or 0) + _ACE.SidStart.offset
            if _adv.EqualSid(ctypes.c_void_p(sid_addr), sid):
                return True
    return False


def _set_entries(dacl: Any, sid: Any, mask: int, *, grant: bool, inherit: bool) -> Any:
    trustee = _TRUSTEE(None, 0, 0, 0, ctypes.cast(sid, ctypes.c_void_p))   # TRUSTEE_IS_SID
    access = _EXPLICIT_ACCESS(mask, 1 if grant else 4, 3 if inherit else 0, trustee)  # GRANT / REVOKE; (OI)(CI)
    new_dacl = ctypes.c_void_p()
    err = _adv.SetEntriesInAclW(1, ctypes.byref(access), dacl, ctypes.byref(new_dacl))
    if err:
        raise AppContainerError(f"No se pudo preparar la lista de permisos (error {err}).")
    return new_dacl


def _set_named_dacl(path: str, dacl: Any) -> None:
    err = _adv.SetNamedSecurityInfoW(ctypes.create_unicode_buffer(path), 1, 4, None, None, dacl, None)
    if err == 5:
        raise PermissionError(f"Sin permiso para cambiar los permisos de {path}.")
    if err:
        raise AppContainerError(f"No se pudieron cambiar los permisos de {path} (error {err}).")


def _set_file_dacl(path: str, dacl: Any, old_sd: Any) -> None:
    sd = (ctypes.c_byte * 64)()
    control, revision = wt.WORD(), wt.DWORD()
    _adv.GetSecurityDescriptorControl(old_sd, ctypes.byref(control), ctypes.byref(revision))
    if not (_adv.InitializeSecurityDescriptor(sd, 1) and _adv.SetSecurityDescriptorDacl(sd, True, dacl, False)):
        raise AppContainerError(f"No se pudo preparar el descriptor de {path}.")
    keep = control.value & (0x0400 | 0x1000)                          # SE_DACL_AUTO_INHERITED | SE_DACL_PROTECTED
    if keep:
        _adv.SetSecurityDescriptorControl(sd, keep, keep)
    if not _adv.SetFileSecurityW(path, 4, sd):
        error = ctypes.get_last_error()
        if error == 5:                                                # ERROR_ACCESS_DENIED: la carpeta no es del usuario
            raise PermissionError(f"Sin permiso para cambiar los permisos de {path}.")
        raise AppContainerError(f"No se pudieron cambiar los permisos de {path} (error {error}).")


def _grants_file() -> str:
    from praxeon.config import resolve_cache_dir

    return os.path.join(resolve_cache_dir(None), "appcontainer_grants.json")


def _load_grants() -> dict[str, dict[str, Any]]:
    try:
        with open(_grants_file(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_grants(state: dict[str, dict[str, Any]]) -> None:
    path = _grants_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, ensure_ascii=False)


def _record_grants(name: str, sid: str, paths: list[str], inheritable: list[str]) -> None:
    """Registro de lo concedido, para poder revocarlo después aunque el workspace ya no esté abierto."""
    state = _load_grants()
    entry = state.setdefault(name, {"sid": sid, "paths": [], "inheritable": []})
    entry["paths"] = sorted(set(entry["paths"]) | set(paths))
    entry["inheritable"] = sorted(set(entry["inheritable"]) | set(inheritable))
    _save_grants(state)


def granted_containers() -> dict[str, dict[str, Any]]:
    """AppContainer de PRAXEON con permisos concedidos (``praxeon sandbox status``)."""
    return _load_grants()
