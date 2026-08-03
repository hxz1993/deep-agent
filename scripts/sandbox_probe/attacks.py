"""Fixed security attack payloads for sandbox backend qualification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ExpectedOutcome = Literal[
    "denied",
    "PID_LIMIT",
    "MEMORY_LIMIT",
    "TIMEOUT",
    "OUTPUT_LIMIT",
]

ATTACK_IDS: tuple[str, ...] = (
    "read_env",
    "read_service_account",
    "read_host_proc",
    "connect_loopback",
    "connect_service",
    "connect_public",
    "write_rootfs",
    "mount",
    "ptrace",
    "fork_pressure",
    "allocate_600m",
    "infinite_loop",
    "infinite_output",
)

# Shared helper embedded in every attack snippet. Helpers print PROBE_* to stdout.
PROBE_PREAMBLE = (
    "import errno as _errno\n"
    "def _emit(msg):\n"
    "    print(msg, flush=True)\n"
    "    return msg\n"
    "def _probe_denied(reason=''):\n"
    "    return _emit(f'PROBE_DENIED:{reason}')\n"
    "def _probe_inconclusive(reason=''):\n"
    "    return _emit(f'PROBE_INCONCLUSIVE:{reason}')\n"
    "def _probe_leak(reason=''):\n"
    "    import sys\n"
    "    _emit(f'PROBE_LEAK:{reason}')\n"
    "    sys.exit(0)\n"
    "def _probe_skip(reason=''):\n"
    "    return _emit(f'PROBE_SKIP:{reason}')\n"
    "def _os_denied(exc):\n"
    "    errno = getattr(exc, 'errno', None)\n"
    "    if errno in (_errno.EPERM, _errno.EACCES, _errno.EROFS):\n"
    "        return _probe_denied(errno)\n"
    "    label = errno if errno is not None else type(exc).__name__\n"
    "    return _probe_inconclusive(label)\n"
    "def _probe_connect(host, port, timeout=3):\n"
    "    import socket\n"
    "    s = socket.socket()\n"
    "    s.settimeout(timeout)\n"
    "    try:\n"
    "        s.connect((host, port))\n"
    "        return _probe_leak(f'connected:{host}:{port}')\n"
    "    except OSError as exc:\n"
    "        return _os_denied(exc)\n"
)


@dataclass(frozen=True)
class AttackSpec:
    attack_id: str
    code: str
    expected: ExpectedOutcome
    description: str = ""


def _wrap(body: str) -> str:
    return f"{PROBE_PREAMBLE}\n{body}"


def _attacks() -> tuple[AttackSpec, ...]:
    return (
        AttackSpec(
            attack_id="read_env",
            expected="denied",
            description="Kubernetes service env must not leak into sandbox",
            code=_wrap(
                "import os\n"
                "for key in ('KUBERNETES_SERVICE_HOST', 'KUBERNETES_PORT', "
                "'HOSTNAME', 'DEEP_AGENT_PYTHON_PORT'):\n"
                "    if os.environ.get(key):\n"
                "        _probe_leak(f'env:{key}={os.environ.get(key)!r}')\n"
                "_probe_denied('env_isolated')\n"
            ),
        ),
        AttackSpec(
            attack_id="read_service_account",
            expected="denied",
            description="Read Kubernetes service account token when mounted",
            code=_wrap(
                "import os\n"
                "p = '/var/run/secrets/kubernetes.io/serviceaccount/token'\n"
                "if not os.path.exists(p):\n"
                "    _probe_skip('no_service_account_mount')\n"
                "    raise SystemExit(0)\n"
                "try:\n"
                "    with open(p) as f:\n"
                "        data = f.read(64)\n"
                "except OSError as exc:\n"
                "    # ENOENT: path vanished / not mounted into jail — not a hard deny proof.\n"
                "    if getattr(exc, 'errno', None) == _errno.ENOENT:\n"
                "        _probe_skip('no_service_account_mount')\n"
                "    else:\n"
                "        _os_denied(exc)\n"
                "    raise SystemExit(0)\n"
                "if data.strip():\n"
                "    _probe_leak('service_account_readable')\n"
                "_probe_denied('empty_token')\n"
            ),
        ),
        AttackSpec(
            attack_id="read_host_proc",
            expected="denied",
            description=(
                "Host init metadata must not leak; jail-local /proc/1 "
                "(e.g. container_init) is expected under PID namespace"
            ),
            code=_wrap(
                "try:\n"
                "    with open('/proc/1/cmdline', 'rb') as f:\n"
                "        data = f.read(256)\n"
                "except OSError as exc:\n"
                "    _os_denied(exc)\n"
                "    raise SystemExit(0)\n"
                "text = data.replace(b'\\0', b' ').decode('utf-8', 'replace').strip().lower()\n"
                "if not text:\n"
                "    _probe_denied('proc1_empty')\n"
                "    raise SystemExit(0)\n"
                # Jail-local init is normal under NEWPID; only host-like names count as leak.
                "host_markers = ('systemd', '/sbin/init', '/usr/lib/systemd')\n"
                "jail_markers = ('container_init', 'go-judge', '/opt/go-judge', 'nsjail')\n"
                "if any(m in text for m in jail_markers):\n"
                "    _probe_denied('sandbox_pidns')\n"
                "elif any(m in text for m in host_markers):\n"
                "    _probe_leak(f'host_proc1:{text[:80]!r}')\n"
                "else:\n"
                "    _probe_inconclusive(f'proc1_unknown:{text[:80]!r}')\n"
            ),
        ),
        AttackSpec(
            attack_id="connect_loopback",
            expected="denied",
            description="Loopback TCP must be blocked by policy (not mere connection refused)",
            code=_wrap("_probe_connect('127.0.0.1', 9)\n"),
        ),
        AttackSpec(
            attack_id="connect_service",
            expected="denied",
            description="Cluster service CIDR must be blocked by policy",
            code=_wrap("_probe_connect('10.96.0.1', 443)\n"),
        ),
        AttackSpec(
            attack_id="connect_public",
            expected="denied",
            description="Public egress must be blocked by policy",
            code=_wrap("_probe_connect('1.1.1.1', 53)\n"),
        ),
        AttackSpec(
            attack_id="write_rootfs",
            expected="denied",
            description="System path must not be writable",
            code=_wrap(
                "p = '/etc/deep_agent_probe_write_test'\n"
                "try:\n"
                "    with open(p, 'w') as f:\n"
                "        f.write('x')\n"
                "    _probe_leak('writable_system_path')\n"
                "except OSError as exc:\n"
                "    _os_denied(exc)\n"
            ),
        ),
        AttackSpec(
            attack_id="mount",
            expected="denied",
            description="Mount syscall must be blocked (seccomp or capability)",
            code=_wrap(
                "import subprocess\n"
                "try:\n"
                "    subprocess.run(\n"
                "        ['mount', '-t', 'tmpfs', 'tmpfs', '/tmp/deep_agent_probe_mount'],\n"
                "        check=True,\n"
                "        capture_output=True,\n"
                "        text=True,\n"
                "    )\n"
                "    _probe_leak('mounted')\n"
                "except FileNotFoundError:\n"
                "    _probe_inconclusive('mount_binary_missing')\n"
                "except subprocess.CalledProcessError as exc:\n"
                "    blob = f'{exc.stderr or ''}{exc.stdout or ''}'.lower()\n"
                "    if 'not permitted' in blob or 'permission denied' in blob:\n"
                "        _probe_denied('mount_blocked')\n"
                "    else:\n"
                "        _probe_inconclusive(f'exit_{exc.returncode}')\n"
            ),
        ),
        AttackSpec(
            attack_id="ptrace",
            expected="denied",
            description="PTRACE_TRACEME must be blocked",
            code=_wrap(
                "import ctypes\n"
                "libc = ctypes.CDLL('libc.so.6', use_errno=True)\n"
                "rc = libc.ptrace(0, 0, 0, 0)\n"
                "err = ctypes.get_errno()\n"
                "if rc == 0:\n"
                "    _probe_leak('ptrace_ok')\n"
                "elif err in (_errno.EPERM, _errno.EACCES):\n"
                "    _probe_denied(err)\n"
                "else:\n"
                "    _probe_inconclusive(err)\n"
            ),
        ),
        AttackSpec(
            attack_id="fork_pressure",
            expected="PID_LIMIT",
            description="Fork more processes than PID limit",
            code=_wrap(
                "import os\n"
                "children = []\n"
                "for _ in range(256):\n"
                "    pid = os.fork()\n"
                "    if pid == 0:\n"
                "        os._exit(0)\n"
                "    children.append(pid)\n"
                "len(children)"
            ),
        ),
        AttackSpec(
            attack_id="allocate_600m",
            expected="MEMORY_LIMIT",
            description="Allocate more memory than cgroup limit",
            code="bytearray(600 * 1024 * 1024)",
        ),
        AttackSpec(
            attack_id="infinite_loop",
            expected="TIMEOUT",
            description="Busy loop until clock limit",
            code="import time\nwhile True:\n    time.sleep(0)",
        ),
        AttackSpec(
            attack_id="infinite_output",
            expected="OUTPUT_LIMIT",
            description="Emit unbounded stdout",
            code="i = 0\nwhile True:\n    print('x' * 4096)\n    i += 1",
        ),
    )


ATTACKS: tuple[AttackSpec, ...] = _attacks()

ATTACK_BY_ID: dict[str, AttackSpec] = {item.attack_id: item for item in ATTACKS}

PROFILE_PROBE_CODE = (
    "import importlib\n"
    "out = {}\n"
    "for name in ('numpy', 'pandas'):\n"
    "    try:\n"
    "        importlib.import_module(name)\n"
    "        out[name] = True\n"
    "    except ImportError:\n"
    "        out[name] = False\n"
    # go-judge / nsjail run via `python -c` (no worker last-expr eval) — must print.
    "print(out)\n"
)

CGROUP_PROBE_CODE = "open('/proc/self/cgroup', encoding='utf-8').read()"

SMOKE_PROBE_CODE = "21 + 21"
