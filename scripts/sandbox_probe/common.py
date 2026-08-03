"""Shared qualification harness for sandbox backend probes (stage B)."""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

# Allow `python3.12 scripts/sandbox_probe/<probe>.py` from repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.sandbox_probe.attacks import (  # noqa: E402
    ATTACKS,
    ATTACK_BY_ID,
    PROFILE_PROBE_CODE,
    SMOKE_PROBE_CODE,
    AttackSpec,
    ExpectedOutcome,
)

CheckStatus = Literal["pass", "fail", "skip", "error"]
ObservedOutcome = ExpectedOutcome | Literal["ok", "unknown", "inconclusive", "skip"]

_PROBE_MARKER_RE = re.compile(
    r"PROBE_(DENIED|INCONCLUSIVE|SKIP|LEAK)(?::([^\s'\"\n]+))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HostInfo:
    cgroup_version: int | None
    kernel: str
    systemd_required: bool


@dataclass(frozen=True)
class BaselineSnapshot:
    pids: frozenset[int]
    cgroup_paths: frozenset[str]
    temp_dirs: frozenset[str]
    captured_at: float


@dataclass
class ProbeRunResult:
    attack_id: str
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool = False
    memory_limited: bool = False
    pid_limited: bool = False
    output_limited: bool = False
    denied: bool = False
    duration_ms: int = 0
    raw_status: str | None = None
    error_message: str | None = None
    cgroup_path: str | None = None


@dataclass
class AttackRunRecord:
    attack_id: str
    expected: ExpectedOutcome
    observed: ObservedOutcome
    status: CheckStatus
    result: ProbeRunResult
    detail: str = ""


@dataclass
class QualificationReport:
    candidate: str
    version: str
    host: HostInfo
    checks: dict[str, CheckStatus]
    attacks: list[AttackRunRecord] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    selection_eligible: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate,
            "version": self.version,
            "host": {
                "cgroup_version": self.host.cgroup_version,
                "kernel": self.host.kernel,
                "systemd_required": self.host.systemd_required,
            },
            "checks": dict(self.checks),
            "attacks": [
                {
                    "attack_id": item.attack_id,
                    "expected": item.expected,
                    "observed": item.observed,
                    "status": item.status,
                    "detail": item.detail,
                    "duration_ms": item.result.duration_ms,
                    "raw_status": item.result.raw_status,
                }
                for item in self.attacks
            ],
            "metadata": dict(self.metadata),
            "selection_eligible": self.selection_eligible,
            "notes": list(self.notes),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False) + "\n"


class ExecutionAdapter(Protocol):
    candidate: str
    version: str

    def execute_python(
        self,
        code: str,
        *,
        attack_id: str,
        timeout_sec: float = 30.0,
        stdout_limit: int = 64 * 1024,
    ) -> ProbeRunResult: ...

    def read_cgroup_path(self) -> str | None: ...


def detect_host_info() -> HostInfo:
    kernel = _read_kernel_version()
    cgroup_version = _detect_cgroup_version()
    systemd_required = _systemd_dbus_available()
    return HostInfo(
        cgroup_version=cgroup_version,
        kernel=kernel,
        systemd_required=systemd_required,
    )


def capture_baseline() -> BaselineSnapshot:
    return BaselineSnapshot(
        pids=_list_probe_pids(),
        cgroup_paths=_list_cgroup_paths(),
        temp_dirs=_list_sandbox_temp_dirs(),
        captured_at=time.time(),
    )


def assert_cleanup(before: BaselineSnapshot, after: BaselineSnapshot) -> tuple[CheckStatus, str]:
    leaked_pids = after.pids - before.pids
    leaked_cgroups = after.cgroup_paths - before.cgroup_paths
    leaked_dirs = after.temp_dirs - before.temp_dirs

    problems: list[str] = []
    if leaked_pids:
        problems.append(f"new pids: {sorted(leaked_pids)[:8]}")
    if leaked_cgroups:
        problems.append(f"new cgroups: {sorted(leaked_cgroups)[:8]}")
    if leaked_dirs:
        problems.append(f"new temp dirs: {sorted(leaked_dirs)[:8]}")

    if problems:
        return "fail", "; ".join(problems)
    return "pass", "baseline restored"


def parse_probe_marker(text: str) -> tuple[ObservedOutcome, str] | None:
    """Return observed outcome and detail from the last PROBE_* marker in stdout."""
    matches = list(_PROBE_MARKER_RE.finditer(text))
    if not matches:
        return None
    kind, detail = matches[-1].group(1).lower(), matches[-1].group(2) or ""
    if kind == "denied":
        return "denied", detail
    if kind == "inconclusive":
        return "inconclusive", detail
    if kind == "skip":
        return "skip", detail
    if kind == "leak":
        return "ok", detail
    return None


def classify_outcome(result: ProbeRunResult) -> ObservedOutcome:
    marker = parse_probe_marker(result.stdout)
    if marker is not None:
        return marker[0]

    if result.memory_limited:
        return "MEMORY_LIMIT"
    if result.pid_limited:
        return "PID_LIMIT"
    if result.timed_out:
        return "TIMEOUT"
    if result.output_limited:
        return "OUTPUT_LIMIT"
    if result.denied:
        return "denied"
    if result.exit_code == 0 and not result.error_message:
        return "ok"
    return _infer_outcome_from_output(result)


def evaluate_attack(spec: AttackSpec, result: ProbeRunResult) -> AttackRunRecord:
    observed = classify_outcome(result)
    marker = parse_probe_marker(result.stdout)
    marker_detail = marker[1] if marker else ""

    if observed == "skip":
        status: CheckStatus = "skip"
        detail = marker_detail or "precondition not met"
    elif observed == "inconclusive":
        status = "fail"
        detail = (
            f"inconclusive ({marker_detail}); not proof of isolation "
            f"(expected {spec.expected})"
        )
    elif observed == spec.expected:
        status = "pass"
        detail = ""
    elif observed == "ok":
        status = "fail"
        detail = marker_detail or "attack succeeded unexpectedly"
    else:
        status = "fail"
        detail = f"expected {spec.expected}, observed {observed}"
    return AttackRunRecord(
        attack_id=spec.attack_id,
        expected=spec.expected,
        observed=observed,
        status=status,
        result=result,
        detail=detail,
    )


def aggregate_attack_group(records: list[AttackRunRecord]) -> CheckStatus:
    """Pass only when every non-skipped attack passed; inconclusive fails the group."""
    actionable = [item for item in records if item.observed != "skip"]
    if not actionable:
        return "skip"
    if any(item.observed == "inconclusive" for item in actionable):
        return "fail"
    if all(item.status == "pass" for item in actionable):
        return "pass"
    return "fail"


def run_attack_suite(adapter: ExecutionAdapter) -> list[AttackRunRecord]:
    records: list[AttackRunRecord] = []
    for spec in ATTACKS:
        baseline = capture_baseline()
        result = adapter.execute_python(spec.code, attack_id=spec.attack_id)
        cleanup_status, cleanup_detail = assert_cleanup(baseline, capture_baseline())
        if cleanup_status != "pass" and not result.error_message:
            result.error_message = cleanup_detail
        records.append(evaluate_attack(spec, result))
    return records


def resolve_host_cgroup_containers_dir(
    explicit: Path | str | None = None,
) -> Path | None:
    if explicit is not None:
        path = Path(explicit)
        return path if path.is_dir() else None
    default = Path("/sys/fs/cgroup/system.slice/gojudge.scope/containers")
    return default if default.is_dir() else None


def verify_host_per_request_cgroups(
    adapter: ExecutionAdapter,
    containers_dir: Path | str,
    *,
    sleep_sec: float = 0.4,
) -> tuple[bool, str]:
    """When guest cgroup paths are opaque, prove isolation via host transient dirs."""
    root = Path(containers_dir)
    if not root.is_dir():
        return False, f"host cgroup dir missing: {root}"

    sleep_code = (
        "import time\n"
        f"time.sleep({sleep_sec})\n"
        "print('done')\n"
    )
    seen_ids: set[str] = set()
    lock = threading.Lock()
    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            try:
                for child in root.iterdir():
                    if child.is_dir():
                        with lock:
                            seen_ids.add(child.name)
            except OSError:
                pass
            time.sleep(0.05)

    poll_thread = threading.Thread(target=poll, daemon=True)
    poll_thread.start()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    adapter.execute_python,
                    sleep_code,
                    attack_id=f"host_cgroup_{index}",
                )
                for index in range(2)
            ]
            concurrent.futures.wait(futures, timeout=30)
    finally:
        stop.set()
        poll_thread.join(timeout=1)

    residual = sorted(
        child.name for child in root.iterdir() if child.is_dir()
    ) if root.is_dir() else []
    if len(seen_ids) >= 2:
        detail = f"distinct_host_cgroups={sorted(seen_ids)}"
        if residual:
            return True, f"{detail}; residual={residual}"
        return True, detail
    return False, f"only saw {sorted(seen_ids)} host cgroup(s)"


def run_structural_checks(
    adapter: ExecutionAdapter,
    *,
    host_cgroup_containers_dir: Path | str | None = None,
) -> tuple[dict[str, CheckStatus], list[str]]:
    checks: dict[str, CheckStatus] = {
        "smoke_ok": "skip",
        "per_request_cgroup": "skip",
        "oom_is_local": "skip",
        "seccomp_enforced": "skip",
        "network_blocked": "skip",
        "host_files_hidden": "skip",
        "cleanup_complete": "skip",
        "python_profile": "skip",
    }

    extra_notes: list[str] = []

    # Bootstrap: if bare Python cannot run, denial-oriented checks are false positives.
    smoke = adapter.execute_python(SMOKE_PROBE_CODE, attack_id="smoke_bootstrap")
    checks["smoke_ok"] = "pass" if classify_outcome(smoke) == "ok" else "fail"

    cgroup_a = adapter.read_cgroup_path()
    cgroup_b = adapter.read_cgroup_path()
    host_cgroup_dir = resolve_host_cgroup_containers_dir(host_cgroup_containers_dir)
    if cgroup_a is None or cgroup_b is None:
        if host_cgroup_dir is not None:
            ok, detail = verify_host_per_request_cgroups(adapter, host_cgroup_dir)
            checks["per_request_cgroup"] = "pass" if ok else "fail"
            if detail:
                extra_notes.append(f"per_request_cgroup_host_verify={detail}")
        else:
            checks["per_request_cgroup"] = "skip"
    elif cgroup_a != cgroup_b or cgroup_a.strip() == "":
        checks["per_request_cgroup"] = "pass"
    elif cgroup_a.strip() in {"/", "."}:
        # Nested cgroup v2 often exposes 0::/ for every request even when
        # memory/pids limits work. Opaque guest path ≠ proof of missing isolation.
        if host_cgroup_dir is not None:
            ok, detail = verify_host_per_request_cgroups(adapter, host_cgroup_dir)
            checks["per_request_cgroup"] = "pass" if ok else "fail"
            if detail:
                extra_notes.append(f"per_request_cgroup_host_verify={detail}")
        else:
            checks["per_request_cgroup"] = "skip"
    else:
        checks["per_request_cgroup"] = "fail"

    mem_spec = ATTACK_BY_ID["allocate_600m"]
    mem_result = adapter.execute_python(mem_spec.code, attack_id=mem_spec.attack_id)
    smoke_after = adapter.execute_python(SMOKE_PROBE_CODE, attack_id="smoke_after_oom")
    if classify_outcome(mem_result) == "MEMORY_LIMIT" and classify_outcome(smoke_after) == "ok":
        checks["oom_is_local"] = "pass"
    else:
        checks["oom_is_local"] = "fail"

    seccomp_ids = ("mount", "ptrace")
    seccomp_records = [
        evaluate_attack(
            ATTACK_BY_ID[attack_id],
            adapter.execute_python(ATTACK_BY_ID[attack_id].code, attack_id=attack_id),
        )
        for attack_id in seccomp_ids
    ]
    checks["seccomp_enforced"] = aggregate_attack_group(seccomp_records)

    network_ids = ("connect_loopback", "connect_service", "connect_public")
    network_records = [
        evaluate_attack(
            ATTACK_BY_ID[attack_id],
            adapter.execute_python(ATTACK_BY_ID[attack_id].code, attack_id=attack_id),
        )
        for attack_id in network_ids
    ]
    checks["network_blocked"] = aggregate_attack_group(network_records)

    host_ids = ("read_env", "read_service_account", "read_host_proc", "write_rootfs")
    host_records = [
        evaluate_attack(
            ATTACK_BY_ID[attack_id],
            adapter.execute_python(ATTACK_BY_ID[attack_id].code, attack_id=attack_id),
        )
        for attack_id in host_ids
    ]
    checks["host_files_hidden"] = aggregate_attack_group(host_records)

    baseline = capture_baseline()
    adapter.execute_python(SMOKE_PROBE_CODE, attack_id="cleanup_probe")
    cleanup_status, _ = assert_cleanup(baseline, capture_baseline())
    checks["cleanup_complete"] = cleanup_status

    profile_result = adapter.execute_python(
        PROFILE_PROBE_CODE,
        attack_id="python_profile",
        timeout_sec=60.0,
    )
    profile_blob = f"{profile_result.stdout}\n{profile_result.stderr}".lower()
    numpy_ok = "numpy" in profile_blob and "true" in profile_blob
    pandas_ok = "pandas" in profile_blob and "true" in profile_blob
    if profile_result.exit_code == 0 and numpy_ok and pandas_ok:
        checks["python_profile"] = "pass"
    else:
        checks["python_profile"] = "fail"

    return checks, extra_notes


def build_report(
    adapter: ExecutionAdapter,
    *,
    host: HostInfo | None = None,
    metadata: dict[str, Any] | None = None,
    run_structural: bool = True,
    run_attacks: bool = True,
    notes: list[str] | None = None,
    host_cgroup_containers_dir: Path | str | None = None,
) -> QualificationReport:
    host_info = host or detect_host_info()
    attack_records = run_attack_suite(adapter) if run_attacks else []
    structural_notes: list[str] = []
    if run_structural:
        checks, structural_notes = run_structural_checks(
            adapter,
            host_cgroup_containers_dir=host_cgroup_containers_dir,
        )
    else:
        checks = {}
    report_notes = list(notes or [])
    report_notes.extend(structural_notes)

    if run_attacks:
        for record in attack_records:
            if record.status == "skip":
                checks.setdefault(record.attack_id, "skip")
            elif record.status != "pass":
                checks.setdefault(record.attack_id, "fail")
            else:
                checks.setdefault(record.attack_id, "pass")

    mandatory = (
        "smoke_ok",
        "per_request_cgroup",
        "oom_is_local",
        "seccomp_enforced",
        "network_blocked",
        "host_files_hidden",
        "cleanup_complete",
        "python_profile",
    )
    # per_request_cgroup may skip when guest cgroup paths are opaque (nested /).
    # That blocks selection_eligible until a node shows distinct paths — intentional.
    selection_eligible = all(
        checks.get(name) == "pass" for name in mandatory
    ) and adapter.candidate in {"go-judge", "nsjail"}

    return QualificationReport(
        candidate=adapter.candidate,
        version=adapter.version,
        host=host_info,
        checks=checks,
        attacks=attack_records,
        metadata=metadata or {},
        selection_eligible=selection_eligible,
        notes=report_notes,
    )


def write_report(path: str | Path, report: QualificationReport) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report.to_json(), encoding="utf-8")


def print_report_summary(report: QualificationReport) -> None:
    print(f"candidate={report.candidate} version={report.version}")
    print(
        f"host: cgroup_v{report.host.cgroup_version} kernel={report.host.kernel} "
        f"systemd_required={report.host.systemd_required}"
    )
    print(f"selection_eligible={report.selection_eligible}")
    for name, status in sorted(report.checks.items()):
        print(f"  check {name}: {status}")
    failed_attacks = [item for item in report.attacks if item.status != "pass"]
    if failed_attacks:
        print(f"failed attacks ({len(failed_attacks)}):")
        for item in failed_attacks:
            print(f"  - {item.attack_id}: {item.detail or item.observed}")


def _infer_outcome_from_output(result: ProbeRunResult) -> ObservedOutcome:
    """Fallback when attacks do not emit PROBE_* markers (resource-limit cases only)."""
    blob = f"{result.stdout}\n{result.stderr}\n{result.error_message or ''}".lower()
    if any(
        token in blob
        for token in (
            "memory limit",
            "cannot allocate",
            "memoryerror",
            "killed",
            "oom",
        )
    ):
        return "MEMORY_LIMIT"
    if result.attack_id == "fork_pressure" and any(
        token in blob
        for token in (
            "process limit",
            "pids.max",
            "resource temporarily unavailable",
        )
    ):
        return "PID_LIMIT"
    if any(token in blob for token in ("time limit exceeded", "timed out", "timeout")):
        return "TIMEOUT"
    if any(token in blob for token in ("output limit", "file too large", "collectsizeexceeded")):
        return "OUTPUT_LIMIT"
    if any(
        token in blob
        for token in (
            "permission denied",
            "operation not permitted",
            "access denied",
            "eperm",
            "eacces",
        )
    ):
        return "denied"
    if "connection refused" in blob or "network is unreachable" in blob:
        return "inconclusive"
    if result.exit_code not in (None, 0):
        return "inconclusive"
    return "unknown"


def _read_kernel_version() -> str:
    try:
        return Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def _detect_cgroup_version() -> int | None:
    try:
        stat = Path("/sys/fs/cgroup/cgroup.controllers").stat()
    except OSError:
        return None
    if stat.st_dev == stat.st_ino == 0:
        return None
    return 2


def _systemd_dbus_available() -> bool:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        return False
    return Path(runtime_dir, "systemd").exists()


def _list_probe_pids() -> frozenset[int]:
    pids: set[int] = set()
    proc = Path("/proc")
    if not proc.is_dir():
        return frozenset()
    for entry in proc.iterdir():
        if entry.name.isdigit():
            pids.add(int(entry.name))
    return frozenset(pids)


def _list_cgroup_paths() -> frozenset[str]:
    paths: set[str] = set()
    cgroup_root = Path("/sys/fs/cgroup")
    if not cgroup_root.is_dir():
        return frozenset()
    for path in cgroup_root.rglob("*"):
        if path.is_dir() and "deep" in path.name.lower():
            paths.add(str(path))
    return frozenset(paths)


def _list_sandbox_temp_dirs() -> frozenset[str]:
    temp_root = Path(tempfile.gettempdir())
    names = (
        "deep-agent-sandbox-",
        "go-judge",
        "nsjail",
        "sandbox_probe",
    )
    found: set[str] = set()
    try:
        for entry in temp_root.iterdir():
            if not entry.is_dir():
                continue
            if any(entry.name.startswith(prefix) for prefix in names):
                found.add(str(entry))
    except OSError:
        return frozenset()
    return frozenset(found)


def _extract_cgroup_path(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    # cgroup v2: 0::/user.slice/...
    match = re.search(r"0::(.+)", stripped)
    if match:
        return match.group(1).strip()
    return stripped
