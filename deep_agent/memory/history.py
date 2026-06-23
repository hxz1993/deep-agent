import warnings

warnings.warn(
    "deep_agent.memory.history is deprecated; use deep_agent.agent.history",
    DeprecationWarning,
    stacklevel=2,
)

from deep_agent.agent.history import *  # noqa: F403
