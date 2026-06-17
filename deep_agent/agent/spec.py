from __future__ import annotations

from dataclasses import dataclass

from deep_agent.core.config import Settings


@dataclass
class AgentSpec:
    """Declarative description of an agent.

    ``capabilities=None`` means "all registered capabilities"; otherwise it is a
    list of capability names (e.g. ["native", "skill:calculator"]) to enable.
    """

    name: str
    model: str
    instructions: str
    capabilities: list[str] | None = None
    # Optional OpenAI-compatible endpoint override (see Settings.model_base_url).
    base_url: str | None = None
    api_key: str | None = None
    max_requests: int | None = None


def default_spec(settings: Settings) -> AgentSpec:
    return AgentSpec(
        name="default",
        model=settings.model,
        instructions=settings.system_prompt,
        capabilities=None,
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
    )
