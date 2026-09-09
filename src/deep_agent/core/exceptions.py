from __future__ import annotations


class DeepAgentError(Exception):
    """Base class for application errors."""


class SessionNotFoundError(DeepAgentError):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session not found: {session_id}")
        self.session_id = session_id


class ToolPackageError(DeepAgentError):
    """Raised when a product tool package is unknown or misconfigured."""


class AgentRunError(DeepAgentError):
    """Raised when an agent run fails (LLM error, limit exceeded, etc.)."""


class AgentNotFoundError(DeepAgentError):
    def __init__(self, agent_id: object) -> None:
        super().__init__(f"Agent not found: {agent_id}")
        self.agent_id = agent_id


class LlmModelNotFoundError(DeepAgentError):
    def __init__(self, model_id: object) -> None:
        super().__init__(f"LLM model not found: {model_id}")
        self.model_id = model_id
