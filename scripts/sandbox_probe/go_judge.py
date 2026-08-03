#!/usr/bin/env python3
"""Qualification probe for criyle/go-judge sandbox backend (stage B)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
PINNED_VERSION = "v1.12.2"
PINNED_COMMIT_SHA = "955585f050286993ead2b2dd11c107d529b6b508"
PINNED_LICENSE = "MIT"
PINNED_BINARY_NAME = "go-judge_linux_amd64"
# Record release asset digest when pinning; probe warns on mismatch.
PINNED_BINARY_SHA256: str | None = None

DEFAULT_MEMORY_LIMIT = 512 * 1024 * 1024
DEFAULT_PROC_LIMIT = 64
DEFAULT_CLOCK_LIMIT_NS = 30_000_000_000
DEFAULT_CPU_LIMIT_NS = 10_000_000_000
DEFAULT_STDOUT_LIMIT = 64 * 1024
DEFAULT_STDERR_LIMIT = 64 * 1024


@dataclass
class GoJudgeLimits:
    memory_limit: int = DEFAULT_MEMORY_LIMIT
    proc_limit: int = DEFAULT_PROC_LIMIT
    clock_limit_ns: int = DEFAULT_CLOCK_LIMIT_NS
    cpu_limit_ns: int = DEFAULT_CPU_LIMIT_NS
    stdout_limit: int = DEFAULT_STDOUT_LIMIT
    stderr_limit: int = DEFAULT_STDERR_LIMIT


class GoJudgeAdapter:
    candidate = "go-judge"

    def __init__(
        self,
        endpoint: str,
        *,
        python_executable: str = "python3",
        limits: GoJudgeLimits | None = None,
        env: list[str] | None = None,
        version: str = PINNED_VERSION,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.python_executable = python_executable
        self.limits = limits or GoJudgeLimits()
        base_env = ["PATH=/usr/local/bin:/usr/bin:/bin", "PYTHONNOUSERSITE=1"]
        if env:
            base_env.extend(env)
        self.env = base_env
        self.version = version

    def execute_python(
        self,
        code: str,
        *,
        attack_id: str,
        timeout_sec: float = 30.0,
        stdout_limit: int = DEFAULT_STDOUT_LIMIT,
    ) -> ProbeRunResult:
        del timeout_sec  # go-judge uses clockLimit on the request payload.
        started = time.monotonic()
        payload = self._build_run_payload(code, stdout_limit=stdout_limit)
        try:
            response = _http_post_json(f"{self.endpoint}/run", payload)
        except urllib.error.URLError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            return ProbeRunResult(
                attack_id=attack_id,
                stdout="",
                stderr="",
                exit_code=None,
                denied=False,
                duration_ms=duration_ms,
                raw_status="ConnectionError",
                error_message=str(exc),
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        if not isinstance(response, list) or not response:
            return ProbeRunResult(
                attack_id=attack_id,
                stdout="",
                stderr="",
                exit_code=None,
                duration_ms=duration_ms,
                raw_status="InvalidResponse",
                error_message="go-judge /run returned empty body",
            )

        item = response[0]
        return _map_go_judge_result(
            item,
            attack_id=attack_id,
            duration_ms=duration_ms,
            stdout_limit=stdout_limit,
        )

    def read_cgroup_path(self) -> str | None:
        result = self.execute_python(CGROUP_PROBE_CODE, attack_id="cgroup_probe")
        text = result.stdout.strip() or result.stderr.strip()
        return _extract_cgroup_path(text)

    def _build_run_payload(self, code: str, *, stdout_limit: int) -> dict[str, Any]:
        if len(code) > 4000:
            return {
                "cmd": [
                    {
                        "args": [self.python_executable, "/w/probe.py"],
                        "env": self.env,
                        "files": [
                            {"content": ""},
                            {"name": "stdout", "max": stdout_limit},
                            {"name": "stderr", "max": self.limits.stderr_limit},
                        ],
                        "cpuLimit": self.limits.cpu_limit_ns,
                        "clockLimit": self.limits.clock_limit_ns,
                        "memoryLimit": self.limits.memory_limit,
                        "procLimit": self.limits.proc_limit,
                        "copyIn": {
                            "probe.py": {"content": code},
                        },
                    }
                ]
            }
        return {
            "cmd": [
                {
                    "args": [self.python_executable, "-c", code],
                    "env": self.env,
                    "files": [
                        {"content": ""},
                        {"name": "stdout", "max": stdout_limit},
                        {"name": "stderr", "max": self.limits.stderr_limit},
                    ],
                    "cpuLimit": self.limits.cpu_limit_ns,
                    "clockLimit": self.limits.clock_limit_ns,
                    "memoryLimit": self.limits.memory_limit,
                    "procLimit": self.limits.proc_limit,
                }
            ]
        }


def fetch_runtime_version(endpoint: str) -> str:
    data = _http_get_json(f"{endpoint.rstrip('/')}/version")
    if isinstance(data, dict):
        version = data.get("buildVersion") or data.get("version")
        if isinstance(version, str) and version:
            return version
    return "unknown"


def verify_pinned_binary(binary_path: Path) -> list[str]:
    notes: list[str] = []
    if not binary_path.is_file():
        notes.append(f"binary not found: {binary_path}")
        return notes
    digest = hashlib.sha256(binary_path.read_bytes()).hexdigest()
    notes.append(f"binary_sha256={digest}")
    if PINNED_BINARY_SHA256 and digest != PINNED_BINARY_SHA256:
        notes.append(
            f"sha256 mismatch: expected {PINNED_BINARY_SHA256}, got {digest}"
        )
    return notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qualify go-judge as secure sandbox backend")
    parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:5050",
        help="go-judge HTTP endpoint (default: http://127.0.0.1:5050)",
    )
    parser.add_argument(
        "--python",
        default="python3",
        help="Python executable inside go-judge container",
    )
    parser.add_argument(
        "--report",
        required=True,
        help="Output path for machine-readable qualification JSON",
    )
    parser.add_argument(
        "--binary",
        type=Path,
        help="Optional local go-judge binary path for SHA256 recording",
    )
    parser.add_argument(
        "--skip-attacks",
        action="store_true",
        help="Only run structural checks (faster smoke)",
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="Extra environment variables for sandbox runs (repeatable)",
    )
    parser.add_argument(
        "--host-cgroup-containers-dir",
        type=Path,
        help="Host path to go-judge transient cgroup dirs (WSL opaque guest paths)",
    )
    args = parser.parse_args(argv)

    notes: list[str] = [
        f"pinned_version={PINNED_VERSION}",
        f"pinned_commit={PINNED_COMMIT_SHA}",
        f"license={PINNED_LICENSE}",
    ]
    if args.binary:
        notes.extend(verify_pinned_binary(args.binary))

    runtime_version = fetch_runtime_version(args.endpoint)
    notes.append(f"runtime_version={runtime_version}")
    pinned_numeric = PINNED_VERSION.removeprefix("v")
    runtime_numeric = runtime_version.removeprefix("v")
    if runtime_version != "unknown" and not (
        runtime_version == PINNED_VERSION
        or runtime_numeric.startswith(pinned_numeric)
        or pinned_numeric.startswith(runtime_numeric)
    ):
        notes.append(
            f"version drift: pinned {PINNED_VERSION}, runtime {runtime_version}"
        )

    adapter = GoJudgeAdapter(
        args.endpoint,
        python_executable=args.python,
        env=args.env or None,
        version=runtime_version if runtime_version != "unknown" else PINNED_VERSION,
    )
    metadata = {
        "endpoint": args.endpoint,
        "python_executable": args.python,
        "sandbox_env": adapter.env,
        "pinned": {
            "version": PINNED_VERSION,
            "commit_sha": PINNED_COMMIT_SHA,
            "license": PINNED_LICENSE,
            "binary_name": PINNED_BINARY_NAME,
            "binary_sha256": PINNED_BINARY_SHA256,
        },
        "limits": {
            "memory_limit": DEFAULT_MEMORY_LIMIT,
            "proc_limit": DEFAULT_PROC_LIMIT,
            "clock_limit_ns": DEFAULT_CLOCK_LIMIT_NS,
        },
    }

    report = build_report(
        adapter,
        host=detect_host_info(),
        metadata=metadata,
        run_attacks=not args.skip_attacks,
        notes=notes,
        host_cgroup_containers_dir=args.host_cgroup_containers_dir,
    )
    write_report(args.report, report)
    print_report_summary(report)
    return 0 if report.selection_eligible else 1


def _map_go_judge_result(
    item: dict[str, Any],
    *,
    attack_id: str,
    duration_ms: int,
    stdout_limit: int = DEFAULT_STDOUT_LIMIT,
) -> ProbeRunResult:
    status = str(item.get("status", ""))
    files = item.get("files") or {}
    stdout = str(files.get("stdout", ""))
    stderr = str(files.get("stderr", ""))
    exit_code = item.get("exitStatus")
    if isinstance(exit_code, bool):
        exit_code = int(exit_code)
    elif exit_code is not None:
        exit_code = int(exit_code)

    # go-judge often returns Nonzero Exit Status (e.g. 120) when the pipe
    # collector truncates stdout, not the literal "Output Limit Exceeded".
    output_limited = status == "Output Limit Exceeded" or _is_output_limit(
        status=status,
        attack_id=attack_id,
        stdout=stdout,
        stdout_limit=stdout_limit,
    )
    return ProbeRunResult(
        attack_id=attack_id,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        timed_out=status == "Time Limit Exceeded",
        memory_limited=status == "Memory Limit Exceeded",
        output_limited=output_limited,
        pid_limited=_is_pid_limit(status, stdout, stderr, attack_id),
        denied=(
            False
            if output_limited
            else _is_denied(status, stdout, stderr, exit_code)
        ),
        duration_ms=duration_ms,
        raw_status=status,
        error_message=item.get("error"),
    )


def _is_output_limit(
    *,
    status: str,
    attack_id: str,
    stdout: str,
    stdout_limit: int,
) -> bool:
    if attack_id != "infinite_output":
        return False
    if status == "Output Limit Exceeded":
        return True
    if status not in {"Nonzero Exit Status", "Signalled", "Accepted"}:
        return False
    # Collector max hit (go-judge may return max or max+1 bytes).
    return len(stdout) >= max(1, stdout_limit)


def _is_pid_limit(status: str, stdout: str, stderr: str, attack_id: str) -> bool:
    if attack_id != "fork_pressure":
        return False
    blob = f"{status}\n{stdout}\n{stderr}".lower()
    return status == "Signalled" or "process" in blob or "resource temporarily unavailable" in blob


def _is_denied(status: str, stdout: str, stderr: str, exit_code: int | None) -> bool:
    blob = f"{stdout}\n{stderr}"
    if "PROBE_DENIED" in stdout:
        return True
    if "PROBE_INCONCLUSIVE" in stdout or "PROBE_SKIP" in stdout or "PROBE_LEAK" in stdout:
        return False
    if status in {
        "Accepted",
        "Memory Limit Exceeded",
        "Time Limit Exceeded",
        "Output Limit Exceeded",
    }:
        return False
    lowered = blob.lower()
    if any(token in lowered for token in ("permission denied", "operation not permitted")):
        return True
    if status in {"Nonzero Exit Status", "Signalled", "File Error"}:
        return exit_code not in (None, 0)
    return status not in {"", "Accepted"}


def _http_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_post_json(url: str, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _http_opener().open(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_get_json(url: str) -> Any:
    request = urllib.request.Request(url, method="GET")
    with _http_opener().open(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
