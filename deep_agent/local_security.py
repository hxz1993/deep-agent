"""Dependency-free markers used only by the loopback local development entry."""

LOCAL_CONSOLE_ADMIN_SCOPE_KEY = "deep_agent.local_console_admin_bypass"
LOCAL_CURRENT_USERNAME_SCOPE_KEY = "deep_agent.local_current_username"

_local_runtime_enabled = False


def enable_local_runtime() -> None:
    """Mark this process as having entered through ``deep_agent.local_dev``.

    This marker is deliberately process-local and cannot be enabled by an
    environment variable.  It prevents a deployment from accidentally using
    the local file backend merely because a profile was copied to the server.
    """

    global _local_runtime_enabled
    _local_runtime_enabled = True


def local_runtime_enabled() -> bool:
    return _local_runtime_enabled
