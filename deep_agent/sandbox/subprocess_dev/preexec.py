from __future__ import annotations

import os
from collections.abc import Callable

from deep_agent.sandbox.subprocess_dev.guardrails import make_soft_guardrail_preexec
from deep_agent.sandbox.subprocess_dev.rlimit import apply_rlimits


def chain_preexec(*steps: Callable[[], None]) -> Callable[[], None]:
    """Run preexec steps in order inside the child before exec."""

    def _preexec() -> None:
        for step in steps:
            step()

    return _preexec


def build_subprocess_preexec(
    *,
    work_dir: str,
    memory_as_bytes: int | None,
    cpu_seconds: int | None,
    max_pids: int | None,
) -> Callable[[], None] | None:
    """Combine rlimit + soft guardrails for subprocess-dev child processes."""
    if os.name != "posix":
        return None

    need_rlimit = (
        (memory_as_bytes is not None and memory_as_bytes > 0)
        or (cpu_seconds is not None and cpu_seconds > 0)
        or (max_pids is not None and max_pids > 0)
    )

    steps: list[Callable[[], None]] = []
    if need_rlimit:
        steps.append(
            lambda: apply_rlimits(
                memory_as_bytes=memory_as_bytes,
                cpu_seconds=cpu_seconds,
                max_pids=max_pids,
            )
        )
    steps.append(make_soft_guardrail_preexec(work_dir=work_dir))
    return chain_preexec(*steps)
