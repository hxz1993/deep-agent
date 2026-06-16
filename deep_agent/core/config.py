from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy.engine import URL

from deep_agent.core.paths import DEEP_AGENT_ROOT, REPO_ROOT
from deep_agent.options import OPTION
from deepfos_platform.conf.option import OptionNotSetError


def _as_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass
class Settings:
    """Application configuration from unified OPTION entry."""

    app_name: str = "deep-agent"
    model: str = "openai:gpt-4o"
    model_base_url: str | None = None
    model_api_key: str | None = None
    system_prompt: str = (
        "You are deep-agent, a helpful general-purpose assistant. "
        "Use the available tools when they help you answer accurately. "
        "Be concise and explain your reasoning when it is useful."
    )
    max_requests: int = 12
    max_total_tokens: int | None = None
    request_retries: int = 2
    skills_dir: str = str(DEEP_AGENT_ROOT / "skills")
    cursor_skills_dir: str = str(REPO_ROOT / ".cursor" / "skills")
    mcp_forward_request_headers: str = "authorization,x-tenant-id,x-user-token"
    mcp_stdio_session_scope: bool = True

    @classmethod
    def from_option(cls) -> "Settings":
        return cls(
            app_name=OPTION.agent.app_name,
            model=OPTION.agent.model,
            model_base_url=_as_optional_str(OPTION.agent.model_base_url),
            model_api_key=_as_optional_str(OPTION.agent.model_api_key),
            system_prompt=OPTION.agent.system_prompt,
            max_requests=OPTION.agent.max_requests,
            max_total_tokens=OPTION.agent.max_total_tokens,
            request_retries=OPTION.agent.request_retries,
            skills_dir=str((REPO_ROOT / OPTION.agent.skills_dir).resolve()),
            cursor_skills_dir=str((REPO_ROOT / OPTION.agent.cursor_skills_dir).resolve()),
            mcp_forward_request_headers=OPTION.agent.mcp_forward_request_headers,
            mcp_stdio_session_scope=OPTION.agent.mcp_stdio_session_scope,
        )


def get_database_url() -> str | None:
    """Build async SQLAlchemy DSN from `OPTION.dbms`, or return None if unset."""
    try:
        dialect = OPTION.dbms.name
        username = OPTION.dbms.username
        password = OPTION.dbms.password
        host = OPTION.dbms.hostname
        port = OPTION.dbms.port
        database = OPTION.dbms.db_name
    except (OptionNotSetError, KeyError):
        return None

    driver_map = {
        "postgres": "postgresql+asyncpg",
        "mysql": "mysql+asyncmy",
    }
    drivername = driver_map.get(dialect)
    if not drivername:
        return None
    return URL.create(
        drivername=drivername,
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
    ).render_as_string(hide_password=False)


def _load_dotenv_if_exists() -> None:
    env_file = Path(DEEP_AGENT_ROOT) / ".env"
    if env_file.is_file():
        try:
            from dotenv import load_dotenv
        except Exception:
            return
        load_dotenv(env_file, override=False)


@lru_cache
def get_settings() -> Settings:
    _load_dotenv_if_exists()
    return Settings.from_option()
