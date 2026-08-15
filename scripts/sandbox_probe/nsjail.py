#!/usr/bin/env python3
"""Qualification probe for google/nsjail sandbox backend (stage B)."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.sandbox_probe.attacks import CGROUP_PROBE_CODE  # noqa: E402
from scripts.sandbox_probe.common import (  # noqa: E402
    ProbeRunResult,
    build_report,
    detect_host_info,
    print_report_summary,
    write_report,
    _extract_cgroup_path,
)

# Pinned upstream metadata — update only with deliberate qualification re-run.
PINNED_VERSION = "3.4"
PINNED_COMMIT_SHA = "079d70dda4aa1edd9512cfd25ff1e47e316dc355"
PINNED_LICENSE = "Apache-2.0"
PINNED_BUILD_ARGS = (
    "bazel build --config=opt //cmd/nsjail",
)

DEFAULT_TIMEOUT_SEC = 30.0
DEFAULT_MEMORY_MB = 512
DEFAULT_MAX_PROCS = 64


@dataclass
class NsJailLimits:
    timeout_sec: float = DEFAULT_TIMEOUT_SEC
    memory_mb: int = DEFAULT_MEMORY_MB
    max_procs: int = DEFAULT_MAX_PROCS


class NsJailAdapter:
    candidate = "nsjail"

    def __init__(
        self,
        nsjail_bin: Path,
        profile_path: Path,
        *,
        python_executable: str = "/usr/bin/python3",
        limits: NsJailLimits | None = None,
        version: str = PINNED_VERSION,
    ) -> None:
        self.nsjail_bin = nsjail_bin
        self.profile_path = profile_path
        self.python_executable = python_executable
        self.limits = limits or NsJailLimits()
        self.version = version

    def execute_python(
        self,
        code: str,
        *,
        attack_id: str,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        stdout_limit: int = 64 * 1024,
    ) -> ProbeRunResult:
        del stdout_limit
        started = time.monotonic()
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            prefix="sandbox_probe_",
            delete=False,
            encoding="utf-8",
        ) as handle:
            handle.write(code)
            script_path = handle.name

        try:
            cmd = [
                str(self.nsjail_bin),
                "--config",
                str(self.profile_path),
                "--time_limit",
                str(int(timeout_sec)),
                "--cgroup_mem_max",
                str(self.limits.memory_mb),
                "--cgroup_pids_max",
                str(self.limits.max_procs),
                "--",
                self.python_executable,
                script_path,
            ]
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec + 5.0,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            return ProbeRunResult(
                attack_id=attack_id,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                exit_code=None,
                timed_out=True,
                duration_ms=duration_ms,
                raw_status="TimeoutExpired",
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

        duration_ms = int((time.monotonic() - started) * 1000)
        return _map_nsjail_result(
            completed,
            attack_id=attack_id,
            duration_ms=duration_ms,
        )

    def read_cgroup_path(self) -> str | None:
        result = self.execute_python(CGROUP_PROBE_CODE, attack_id="cgroup_probe")
        text = result.stdout.strip() or result.stderr.strip()
        return _extract_cgroup_path(text)


def fetch_runtime_version(nsjail_bin: Path) -> str:
    try:
        completed = subprocess.run(
            [str(nsjail_bin), "--version"],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    output = (completed.stdout or completed.stderr).strip()
    return output.splitlines()[0] if output else "unknown"


def runtime_help_hash(nsjail_bin: Path) -> str:
    try:
        completed = subprocess.run(
            [str(nsjail_bin), "--help"],
            capture_output=True,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    payload = (completed.stdout or b"") + (completed.stderr or b"")
    return hashlib.sha256(payload).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qualify NsJail as secure sandbox backend")
    parser.add_argument(
        "--nsjail",
        type=Path,
        required=True,
        help="Path to nsjail binary",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        required=True,
        help="Fixed NsJail profile (ONCE, namespaces, seccomp, cgroup v2)",
    )
    parser.add_argument(
        "--python",
        default="/usr/bin/python3",
        help="Python executable inside the jail",
    )
    parser.add_argument(
        "--report",
        required=True,
        help="Output path for machine-readable qualification JSON",
    )
    parser.add_argument(
        "--skip-attacks",
        action="store_true",
        help="Only run structural checks (faster smoke)",
    )
    args = parser.parse_args(argv)

    if not args.nsjail.is_file():
        print(f"nsjail binary not found: {args.nsjail}", file=sys.stderr)
        return 2
    if not args.profile.is_file():
        print(f"nsjail profile not found: {args.profile}", file=sys.stderr)
        return 2

    runtime_version = fetch_runtime_version(args.nsjail)
    help_hash = runtime_help_hash(args.nsjail)
    notes = [
        f"pinned_version={PINNED_VERSION}",
        f"pinned_commit={PINNED_COMMIT_SHA}",
        f"license={PINNED_LICENSE}",
        f"build_args={' '.join(PINNED_BUILD_ARGS)}",
        f"runtime_version={runtime_version}",
        f"help_sha256={help_hash}",
    ]

    adapter = NsJailAdapter(
        args.nsjail,
        args.profile,
        python_executable=args.python,
        version=runtime_version if runtime_version != "unknown" else PINNED_VERSION,
    )
    metadata = {
        "nsjail": str(args.nsjail),
        "profile": str(args.profile),
        "python_executable": args.python,
        "pinned": {
            "version": PINNED_VERSION,
            "commit_sha": PINNED_COMMIT_SHA,
            "license": PINNED_LICENSE,
            "build_args": list(PINNED_BUILD_ARGS),
            "help_sha256": help_hash,
        },
        "limits": {
            "timeout_sec": DEFAULT_TIMEOUT_SEC,
            "memory_mb": DEFAULT_MEMORY_MB,
            "max_procs": DEFAULT_MAX_PROCS,
        },
    }

    report = build_report(
        adapter,
        host=detect_host_info(),
        metadata=metadata,
        run_attacks=not args.skip_attacks,
        notes=notes,
    )
    write_report(args.report, report)
    print_report_summary(report)
    return 0 if report.selection_eligible else 1


def _map_nsjail_result(
    completed: subprocess.CompletedProcess[str],
    *,
    attack_id: str,
    duration_ms: int,
) -> ProbeRunResult:
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    blob = f"{stdout}\n{stderr}".lower()
    exit_code = completed.returncode

    timed_out = "time limit" in blob or exit_code == 137
    memory_limited = any(
        token in blob for token in ("memory limit", "oom", "cannot allocate", "killed")
    ) or exit_code == 137
    output_limited = "output limit" in blob
    pid_limited = attack_id == "fork_pressure" and (
        "pids max" in blob or "process limit" in blob or exit_code not in (0, None)
    )
    denied = exit_code not in (0, None) and not (
        timed_out or memory_limited or output_limited or pid_limited
    )

    return ProbeRunResult(
        attack_id=attack_id,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        timed_out=timed_out,
        memory_limited=memory_limited,
        output_limited=output_limited,
        pid_limited=pid_limited,
        denied=denied,
        duration_ms=duration_ms,
        raw_status=f"exit={exit_code}",
        error_message=stderr.strip() or None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
