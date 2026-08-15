from __future__ import annotations

import sys

# Product config is RSS-intent (aligns with stage-C cgroup memory.max).
RLIMIT_AS_PER_RSS_RATIO = 3


def rlimit_as_supported() -> bool:
    """Whether the current platform can enforce a finite RLIMIT_AS.

    Darwin exposes ``RLIMIT_AS`` through ``resource`` but rejects finite values
    with ``EINVAL``. Treating it as supported makes every sandbox worker fail in
    ``preexec_fn`` before Python starts.
    """

    return sys.platform != "darwin"


def rss_bytes_to_rlimit_as(
    rss_bytes: int,
    *,
    ratio: int = RLIMIT_AS_PER_RSS_RATIO,
) -> int:
    """Translate RSS product budget into RLIMIT_AS for subprocess-dev."""
    if rss_bytes < 0:
        raise ValueError("rss_bytes must be non-negative")
    if ratio < 1:
        raise ValueError("ratio must be at least 1")
    return int(rss_bytes) * int(ratio)


def apply_rlimits(
    *,
    memory_as_bytes: int | None,
    cpu_seconds: int | None,
    max_pids: int | None,
) -> None:
    """Apply POSIX rlimits in the child preexec. No-op when all limits are unset."""
    import resource

    if (
        rlimit_as_supported()
        and memory_as_bytes is not None
        and memory_as_bytes > 0
    ):
        resource.setrlimit(resource.RLIMIT_AS, (memory_as_bytes, memory_as_bytes))
    if cpu_seconds is not None and cpu_seconds > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    if max_pids is not None and max_pids > 0:
        resource.setrlimit(resource.RLIMIT_NPROC, (max_pids, max_pids))
