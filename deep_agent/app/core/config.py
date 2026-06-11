from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.paths import DEEP_AGENT_ROOT, REPO_ROOT


class Settings(BaseSettings):
    """Application configuration, loaded from environment / .env file.

    Provider API keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, ...) are read by
    Pydantic AI directly from the environment and are intentionally not modelled here.
    """

    model_config = SettingsConfigDict(
        env_prefix="DEEP_AGENT_",
        env_file=str(DEEP_AGENT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "deep-agent"

    # PostgreSQL connection. Leave empty to fall back to in-memory session store.
    database_url: str | None = "postgresql+asyncpg://postgres:postgres@localhost:5432/deep_agent"

    # Default agent
    model: str = "openai:gpt-4o"

    # Optional OpenAI-compatible endpoint override. Set ``model_base_url`` to
    # point at a non-OpenAI provider that speaks the OpenAI API (DeepSeek, Qwen,
    # GLM, Kimi, ...). When set, the model string is routed through this endpoint
    # and ``model_api_key`` is used for auth. Leave both unset for vanilla OpenAI.
    model_base_url: str | None = None
    model_api_key: str | None = None

    system_prompt: str = (
        "You are deep-agent, a helpful general-purpose assistant. "
        "Use the available tools when they help you answer accurately. "
        "Be concise and explain your reasoning when it is useful."
    )

    # Agent loop safety limits (mapped to Pydantic AI UsageLimits)
    max_requests: int = 12
    max_total_tokens: int | None = None

    # Per-LLM-call retry attempts handled by Pydantic AI
    request_retries: int = 2

    # Declarative extension points
    skills_dir: str = str(DEEP_AGENT_ROOT / "skills")

    # Cursor SKILL.md skills (comma-separated directories). Each immediate
    # subdirectory containing a SKILL.md becomes one ``cursor-skill:<name>``.
    cursor_skills_dir: str = str(REPO_ROOT / ".cursor" / "skills")


@lru_cache
def get_settings() -> Settings:
    return Settings()
