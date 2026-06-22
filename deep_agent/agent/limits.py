from __future__ import annotations


def default_agent_max_requests(global_max: int) -> int:
    """Per-agent default when ``max_requests`` is unset."""
    return max(1, global_max // 2)


def resolve_agent_max_requests(agent_max_requests: int | None, global_max: int) -> int:
    """Resolve effective request limit for one agent turn."""
    if agent_max_requests is None:
        return default_agent_max_requests(global_max)
    return min(agent_max_requests, global_max)


def validate_agent_max_requests(agent_max_requests: int | None, global_max: int) -> None:
    if agent_max_requests is None:
        return
    if agent_max_requests < 1 or agent_max_requests > global_max:
        raise ValueError(
            f"max_requests must be between 1 and {global_max}, got {agent_max_requests}"
        )
