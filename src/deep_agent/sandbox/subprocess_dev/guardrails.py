"""Best-effort guardrails applied to each sandbox subprocess.

They run in the child ``preexec_fn`` after fork and before exec. Failures are
swallowed so the sandbox remains portable across macOS, Linux and restricted
container environments.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import sys
from dataclasses import dataclass
from typing import Callable

# Linux landlock syscalls (stable numbers on x86_64 / aarch64).
_LANDLOCK_CREATE_RULESET = 444
_LANDLOCK_ADD_RULE = 445
_LANDLOCK_RESTRICT_SELF = 446

_LANDLOCK_RULE_PATH_BENEATH = 1

_LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
_LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
_LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
_LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
_LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
_LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
_LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
_LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
_LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
_LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
_LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
_LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
_LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12

_FS_READ = (
    _LANDLOCK_ACCESS_FS_EXECUTE
    | _LANDLOCK_ACCESS_FS_READ_FILE
    | _LANDLOCK_ACCESS_FS_READ_DIR
    # Pathname/abstract AF_UNIX sockets must stay available for stdlib/multiprocessing.
    | _LANDLOCK_ACCESS_FS_MAKE_SOCK
)
_FS_WRITE = (
    _LANDLOCK_ACCESS_FS_WRITE_FILE
    | _LANDLOCK_ACCESS_FS_REMOVE_DIR
    | _LANDLOCK_ACCESS_FS_REMOVE_FILE
    | _LANDLOCK_ACCESS_FS_MAKE_CHAR
    | _LANDLOCK_ACCESS_FS_MAKE_DIR
    | _LANDLOCK_ACCESS_FS_MAKE_REG
    | _LANDLOCK_ACCESS_FS_MAKE_FIFO
    | _LANDLOCK_ACCESS_FS_MAKE_BLOCK
    | _LANDLOCK_ACCESS_FS_MAKE_SYM
)
_FS_HANDLED = _FS_READ | _FS_WRITE

_PR_SET_NO_NEW_PRIVS = 38

# libseccomp
_SCMP_ACT_ALLOW = 0x7FFF0000
# From seccomp.h: SCMP_CMP_NE=1 … SCMP_CMP_EQ=4
_SCMP_CMP_EQ = 4
_AF_INET = 2
_AF_INET6 = 10


@dataclass(frozen=True)
class SoftGuardrailResult:
    no_new_privs: bool = False
    landlock: bool = False
    network_seccomp: bool = False


class _LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _LandlockPathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


class _ScmpArgCmp(ctypes.Structure):
    _fields_ = [
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_int),
        ("datum_a", ctypes.c_uint64),
        ("datum_b", ctypes.c_uint64),
    ]


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def try_set_no_new_privs() -> bool:
    if sys.platform != "linux":
        return False
    try:
        libc = _libc()
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            return False
        return True
    except Exception:
        return False


def try_apply_landlock(work_dir: str) -> bool:
    """Read broadly under ``/``; allow writes only under ``work_dir``.

    Best-effort: any failure returns False without raising.
    """
    if sys.platform != "linux":
        return False
    work_path = os.path.abspath(work_dir)
    if not os.path.isdir(work_path):
        return False

    try:
        libc = _libc()
        syscall = libc.syscall
        syscall.restype = ctypes.c_long

        attr = _LandlockRulesetAttr(handled_access_fs=_FS_HANDLED)
        ruleset_fd = int(
            syscall(
                ctypes.c_long(_LANDLOCK_CREATE_RULESET),
                ctypes.byref(attr),
                ctypes.c_size_t(ctypes.sizeof(attr)),
                ctypes.c_uint32(0),
            )
        )
        if ruleset_fd < 0:
            return False

        def _add_path(path: str, access: int) -> bool:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = _LandlockPathBeneathAttr(
                    allowed_access=access,
                    parent_fd=fd,
                )
                rc = int(
                    syscall(
                        ctypes.c_long(_LANDLOCK_ADD_RULE),
                        ctypes.c_int(ruleset_fd),
                        ctypes.c_uint(_LANDLOCK_RULE_PATH_BENEATH),
                        ctypes.byref(rule),
                        ctypes.c_uint32(0),
                    )
                )
                return rc == 0
            finally:
                os.close(fd)

        ok = _add_path("/", _FS_READ) and _add_path(work_path, _FS_READ | _FS_WRITE)
        if not ok:
            os.close(ruleset_fd)
            return False

        # Landlock requires NO_NEW_PRIVS before restrict_self.
        if not try_set_no_new_privs():
            os.close(ruleset_fd)
            return False

        rc = int(
            syscall(
                ctypes.c_long(_LANDLOCK_RESTRICT_SELF),
                ctypes.c_int(ruleset_fd),
                ctypes.c_uint32(0),
            )
        )
        os.close(ruleset_fd)
        return rc == 0
    except Exception:
        return False


def _scmp_act_errno(err: int) -> int:
    return 0x00050000 | (err & 0xFFFF)


def _find_libseccomp() -> str | None:
    """Locate libseccomp without relying on writable ``/tmp``.

    ``ctypes.util.find_library`` may create temp files; after Landlock that can
    fail. Prefer stable sonames first.
    """
    for name in ("libseccomp.so.2", "libseccomp.so"):
        try:
            ctypes.CDLL(name)
            return name
        except OSError:
            continue
    try:
        return ctypes.util.find_library("seccomp")
    except Exception:
        return None


def try_apply_network_seccomp() -> bool:
    """Deny inet sockets / connect-family calls via libseccomp; allow AF_UNIX.

    Best-effort: missing libseccomp or load failure returns False.
    """
    if sys.platform != "linux":
        return False
    libname = _find_libseccomp()
    if not libname:
        return False
    try:
        lib = ctypes.CDLL(libname)
        lib.seccomp_init.restype = ctypes.c_void_p
        lib.seccomp_release.argtypes = [ctypes.c_void_p]
        lib.seccomp_load.argtypes = [ctypes.c_void_p]
        lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
        lib.seccomp_rule_add_array.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.POINTER(_ScmpArgCmp),
        ]
        lib.seccomp_rule_add_array.restype = ctypes.c_int

        ctx = lib.seccomp_init(_SCMP_ACT_ALLOW)
        if not ctx:
            return False
        try:
            deny = _scmp_act_errno(errno.EPERM)

            def _deny_name(name: str) -> bool:
                nr = lib.seccomp_syscall_resolve_name(name.encode("ascii"))
                if nr < 0:
                    return True  # unknown name on this arch — skip
                return (
                    lib.seccomp_rule_add_array(
                        ctx,
                        deny,
                        nr,
                        0,
                        None,
                    )
                    == 0
                )

            for name in ("connect", "bind", "listen", "accept", "accept4"):
                if not _deny_name(name):
                    return False

            socket_nr = lib.seccomp_syscall_resolve_name(b"socket")
            if socket_nr >= 0:
                for family in (_AF_INET, _AF_INET6):
                    cmp_arg = _ScmpArgCmp(
                        arg=0,
                        op=_SCMP_CMP_EQ,
                        datum_a=family,
                        datum_b=0,
                    )
                    if (
                        lib.seccomp_rule_add_array(
                            ctx,
                            deny,
                            socket_nr,
                            1,
                            ctypes.byref(cmp_arg),
                        )
                        != 0
                    ):
                        return False

            # seccomp_load also benefits from NO_NEW_PRIVS already set.
            try_set_no_new_privs()
            if lib.seccomp_load(ctx) != 0:
                return False
            return True
        finally:
            lib.seccomp_release(ctx)
    except Exception:
        return False


def apply_soft_guardrails(
    *,
    work_dir: str | None,
) -> SoftGuardrailResult:
    """Apply filesystem guardrails without imposing a network destination policy.

    Network access follows the deployment's routing and firewall policy.
    This function remains best-effort and never raises.
    """
    if sys.platform != "linux":
        return SoftGuardrailResult()

    no_new_privs = try_set_no_new_privs()

    landlock_ok = False
    if work_dir:
        landlock_ok = try_apply_landlock(work_dir)
        if landlock_ok:
            no_new_privs = True

    return SoftGuardrailResult(
        no_new_privs=no_new_privs,
        landlock=landlock_ok,
        network_seccomp=False,
    )


def make_soft_guardrail_preexec(
    *,
    work_dir: str | None,
) -> Callable[[], None]:
    """Return a ``preexec_fn`` step that applies soft guardrails."""

    def _preexec() -> None:
        apply_soft_guardrails(work_dir=work_dir)

    return _preexec
