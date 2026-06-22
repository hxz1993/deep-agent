from __future__ import annotations

import os
import sys
from pathlib import Path

from deepfos_platform.conf.option import GlobalOptions, Category, Option, Action
from deepfos_platform.lib.cipher import maybe_decrypt_pwd
from deepfos_platform.lib.constants import BOOLEAN_STATES
from deep_agent.core.paths import DEEP_AGENT_ROOT, REPO_ROOT

DEFAULT_MCP_FORWARD_HEADERS = frozenset(
    {
        "authorization",
        "app",
        "space",
        "cookie",
        "user",
        "language",
        "x-deepfos-*",
        "x-tenant-id",
        "x-user-token",
    }
)


def _should_load_dotenv() -> bool:
    explicit = os.environ.get("DEEP_AGENT_LOAD_DOTENV")
    if explicit is not None:
        return BOOLEAN_STATES.get(explicit.lower(), False)
    return sys.platform != "linux"


def _load_dotenv_for_local_dev() -> None:
    env_file = Path(DEEP_AGENT_ROOT) / ".env"
    if not env_file.is_file():
        return
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    load_dotenv(env_file, override=False)


if _should_load_dotenv():
    _load_dotenv_for_local_dev()


def _ensure_db_module(value):
    if value == 'dameng':
        try:
            from sqlalchemy_dm.base import DMCompiler
            from sqlalchemy_dm.dmPython import DMDialect_dmPython
        except (ModuleNotFoundError, AttributeError):
            raise RuntimeError(f"sqlalchemy_dm模块未安装")


def _resolve_repo_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path.resolve())
    return str((REPO_ROOT / value).resolve())


def _strip_optional_str(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_forward_headers(value: str | frozenset[str]) -> frozenset[str]:
    if isinstance(value, frozenset):
        return value
    text = str(value).strip()
    if not text:
        return DEFAULT_MCP_FORWARD_HEADERS
    return frozenset(part.strip().lower() for part in text.split(",") if part.strip())


class DBMS(Category):
    _ENV_PREFIX = "DEEP_AGENT_DBMS_"

    name = Option(
        'postgres', val_type=str, bind_env="NAME",
        convertor=lambda s: s.lower(),
        val_choices=("postgres", "mysql", "kingbase", "dameng"),
        extra_check=_ensure_db_module
    )
    username = Option(..., val_type=str, bind_env="USER")
    hostname = Option(..., val_type=str, bind_env="HOSTNAME")
    port = Option(..., val_type=int, bind_env="PORT")
    db_name = Option(..., val_type=str, bind_env="DATABASE")
    password = Option(
        ..., val_type=str, protected=True,
        bind_env="PASSWORD",
        convertor=maybe_decrypt_pwd
    )

    def __get__(self, instance, owner) -> 'DBMS':
        """defined to help ide"""
        return super().__get__(instance, owner)



class Agent(Category):
    _ENV_PREFIX = "DEEP_AGENT_"

    app_name = Option("deep-agent", val_type=str, bind_env="APP_NAME")
    model = Option("openai:gpt-4o", val_type=str, bind_env="MODEL")
    model_base_url = Option(
        None,
        val_type=str,
        bind_env="MODEL_BASE_URL",
        convertor=_strip_optional_str,
    )
    model_api_key = Option(
        None,
        val_type=str,
        protected=True,
        bind_env="MODEL_API_KEY",
        convertor=_strip_optional_str,
    )
    system_prompt = Option(
        "You are deep-agent, a helpful general-purpose assistant. "
        "Use the available tools when they help you answer accurately. "
        "Be concise and explain your reasoning when it is useful.",
        val_type=str,
        bind_env="SYSTEM_PROMPT",
    )
    max_requests = Option(50, val_type=int, bind_env="MAX_REQUESTS")
    max_total_tokens = Option(None, val_type=int, bind_env="MAX_TOTAL_TOKENS")
    request_retries = Option(2, val_type=int, bind_env="REQUEST_RETRIES")
    skills_dir = Option(
        _resolve_repo_path("skills"),
        val_type=str,
        bind_env="SKILLS_DIR",
        convertor=_resolve_repo_path,
    )
    cursor_skills_dir = Option(
        _resolve_repo_path(".cursor/skills"),
        val_type=str,
        bind_env="CURSOR_SKILLS_DIR",
        convertor=_resolve_repo_path,
    )
    mcp_forward_request_headers = Option(
        DEFAULT_MCP_FORWARD_HEADERS,
        val_type=frozenset,
        bind_env="MCP_FORWARD_REQUEST_HEADERS",
        convertor=_parse_forward_headers,
    )
    mcp_stdio_session_scope = Option(
        True,
        val_type=bool,
        bind_env="MCP_STDIO_SESSION_SCOPE",
    )
    mcp_init_timeout = Option(
        30,
        val_type=float,
        bind_env="MCP_INIT_TIMEOUT",
    )
    mcp_acquire_retries = Option(
        1,
        val_type=int,
        bind_env="MCP_ACQUIRE_RETRIES",
    )

    def __get__(self, instance, owner) -> "Agent":
        """defined to help ide"""
        return super().__get__(instance, owner)


class DeepAgentOptions(GlobalOptions):
    agent = Agent()
    dbms = DBMS()


OPTION = DeepAgentOptions()
