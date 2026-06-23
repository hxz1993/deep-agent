from deep_agent.db.repositories.agent import AgentRepository
from deep_agent.db.repositories.mcp import McpServerRepository
from deep_agent.db.repositories.message import MessageRepository
from deep_agent.db.repositories.ui_tool import UiToolRepository

__all__ = [
    "AgentRepository",
    "McpServerRepository",
    "MessageRepository",
    "SessionRepository",
    "UiToolRepository",
]


def __getattr__(name: str):
    if name == "SessionRepository":
        from deep_agent.db.repositories.session import SessionRepository

        return SessionRepository
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
