from __future__ import annotations

import os
import sys
from pathlib import Path

from deepfos_platform.conf.option import GlobalOptions, Category, Option, Action
from deepfos_platform.lib.cipher import maybe_decrypt_pwd
from deepfos_platform.lib.constants import BOOLEAN_STATES
from deep_agent.core.paths import DEEP_AGENT_ROOT, REPO_ROOT
from deep_agent.sandbox.profile import (
    DEFAULT_SANDBOX_PROFILE,
    ProfileNotFoundError,
    ProfileValidationError,
    load_sandbox_profile,
)

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


def _ensure_positive_int(value: int) -> None:
    if value < 1:
        raise ValueError("must be a positive integer")


def _ensure_non_negative_int(value: int) -> None:
    if value < 0:
        raise ValueError("must be a non-negative integer")


def _ensure_ratio(value: float) -> None:
    if not 0 < value < 1:
        raise ValueError("must be greater than 0 and less than 1")


def _resolve_repo_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path.resolve())
    return str((REPO_ROOT / value).resolve())


def _default_sandbox_python() -> str:
    if sys.platform == "win32":
        rel = ".venv-sandbox/Scripts/python.exe"
    else:
        rel = ".venv-sandbox/bin/python"
    # Keep venv symlinks intact; resolve() would collapse to the base interpreter
    # and lose pyvenv.cfg / site-packages isolation.
    return str((REPO_ROOT / rel).absolute())


def _resolve_sandbox_python(value: str) -> str:
    path = Path(str(value).strip())
    if not path.is_absolute():
        path = REPO_ROOT / path
    return str(path.absolute())


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
    max_requests = Option(
        30,
        val_type=int,
        bind_env="MAX_REQUESTS",
        extra_check=_ensure_positive_int,
    )
    max_total_tokens = Option(None, val_type=int, bind_env="MAX_TOTAL_TOKENS")
    request_retries = Option(2, val_type=int, bind_env="REQUEST_RETRIES")
    context_compaction_trigger_ratio = Option(
        0.80,
        val_type=float,
        bind_env="CONTEXT_COMPACTION_TRIGGER_RATIO",
        extra_check=_ensure_ratio,
    )
    context_compaction_target_ratio = Option(
        0.55,
        val_type=float,
        bind_env="CONTEXT_COMPACTION_TARGET_RATIO",
        extra_check=_ensure_ratio,
    )
    context_keep_tool_pairs = Option(
        3,
        val_type=int,
        bind_env="CONTEXT_KEEP_TOOL_PAIRS",
        extra_check=_ensure_non_negative_int,
    )
    context_min_clear_tokens = Option(
        2000,
        val_type=int,
        bind_env="CONTEXT_MIN_CLEAR_TOKENS",
        extra_check=_ensure_non_negative_int,
    )
    context_summary_timeout = Option(
        30.0,
        val_type=float,
        bind_env="CONTEXT_SUMMARY_TIMEOUT",
    )
    skills_dir = Option(
        _resolve_repo_path("skills"),
        val_type=str,
        bind_env="SKILLS_DIR",
        convertor=_resolve_repo_path,
    )
    mcp_forward_request_headers = Option(
        DEFAULT_MCP_FORWARD_HEADERS,
        val_type=frozenset,
        bind_env="MCP_FORWARD_REQUEST_HEADERS",
        convertor=_parse_forward_headers,
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
    mcp_runtime_dir = Option(
        "/tmp/deep-agent-mcp",
        val_type=str,
        bind_env="MCP_RUNTIME_DIR",
    )
    mcp_shared_server_ttl = Option(
        300,
        val_type=float,
        bind_env="MCP_SHARED_SERVER_TTL",
    )
    mcp_shared_lock_timeout = Option(
        30,
        val_type=float,
        bind_env="MCP_SHARED_LOCK_TIMEOUT",
    )
    sqlite_path = Option(
        _resolve_repo_path("data/deep-agent.db"),
        val_type=str,
        bind_env="SQLITE_PATH",
        convertor=_resolve_repo_path,
    )
    upload_max_size_mb = Option(
        20,
        val_type=int,
        bind_env="UPLOAD_MAX_SIZE_MB",
        extra_check=_ensure_positive_int,
    )
    upload_max_count = Option(
        10,
        val_type=int,
        bind_env="UPLOAD_MAX_COUNT",
        extra_check=_ensure_positive_int,
    )
    upload_allowed_mimes = Option(
        "",
        val_type=str,
        bind_env="UPLOAD_ALLOWED_MIMES",
        convertor=_strip_optional_str,
    )
    image_max_dimension = Option(1568, val_type=int, bind_env="IMAGE_MAX_DIMENSION")
    image_max_bytes = Option(4_194_304, val_type=int, bind_env="IMAGE_MAX_BYTES")
    image_jpeg_quality = Option(85, val_type=int, bind_env="IMAGE_JPEG_QUALITY")
    gemini_proxy = Option(
        "",
        val_type=str,
        bind_env="GEMINI_PROXY",
        convertor=_strip_optional_str,
    )
    title_model = Option(
        "",  # 标题生成专用模型名，空=不启用 AI 标题生成
        val_type=str,
        bind_env="TITLE_MODEL",
        convertor=_strip_optional_str,
    )
    title_model_base_url = Option(
        "",  # 标题模型 API 基础 URL
        val_type=str,
        bind_env="TITLE_MODEL_BASE_URL",
        convertor=_strip_optional_str,
    )
    title_model_api_key = Option(
        "",  # 标题模型 API Key
        val_type=str,
        protected=True,  # 敏感信息不暴露到日志
        bind_env="TITLE_MODEL_API_KEY",
        convertor=_strip_optional_str,
    )
    title_generation_timeout = Option(
        30.0,  # 标题生成超时（秒）；慢速 OpenAI 兼容端点可能需要更久
        val_type=float,
        bind_env="TITLE_GENERATION_TIMEOUT",
    )
    sandbox_enabled = Option(
        True,
        val_type=bool,
        bind_env="SANDBOX_ENABLED",
    )
    sandbox_backend = Option(
        "subprocess-dev",
        val_type=str,
        bind_env="SANDBOX_BACKEND",
    )
    sandbox_timeout = Option(
        30,
        val_type=int,
        bind_env="SANDBOX_TIMEOUT",
        extra_check=_ensure_positive_int,
    )
    sandbox_max_concurrency = Option(
        4,
        val_type=int,
        bind_env="SANDBOX_MAX_CONCURRENCY",
        extra_check=_ensure_positive_int,
    )
    sandbox_python = Option(
        _default_sandbox_python(),
        val_type=str,
        bind_env="SANDBOX_PYTHON",
        convertor=_resolve_sandbox_python,
    )
    sandbox_ensure_venv = Option(
        False,
        val_type=bool,
        bind_env="SANDBOX_ENSURE_VENV",
    )
    sandbox_memory_mb = Option(
        # RSS product budget in megabytes (same semantics as stage-C cgroup memory.max).
        # subprocess-dev translates to RLIMIT_AS via RLIMIT_AS_PER_RSS_RATIO.
        512,
        val_type=int,
        bind_env="SANDBOX_MEMORY_MB",
        extra_check=_ensure_positive_int,
    )
    sandbox_cpu_seconds = Option(
        30,
        val_type=int,
        bind_env="SANDBOX_CPU_SECONDS",
        extra_check=_ensure_positive_int,
    )
    sandbox_max_pids = Option(
        # 0 = do not apply RLIMIT_NPROC in subprocess-dev (recommended).
        # Per-UID NPROC ≠ stage-C cgroup pids.max; enabling can break OpenBLAS / host threads.
        0,
        val_type=int,
        bind_env="SANDBOX_MAX_PIDS",
        extra_check=_ensure_non_negative_int,
    )

    def __get__(self, instance, owner) -> "Agent":
        """defined to help ide"""
        return super().__get__(instance, owner)


class DeepAgentOptions(GlobalOptions):
    agent = Agent()
    dbms = DBMS()


OPTION = DeepAgentOptions()


def validate_runtime_options(options: DeepAgentOptions = OPTION) -> None:
    """Resolve startup-critical options so invalid environment values fail fast."""
    _ = options.agent.max_requests
    _ = options.agent.upload_max_size_mb
    _ = options.agent.upload_max_count
    trigger_ratio = options.agent.context_compaction_trigger_ratio
    target_ratio = options.agent.context_compaction_target_ratio
    if target_ratio >= trigger_ratio:
        raise ValueError(
            "DEEP_AGENT_CONTEXT_COMPACTION_TARGET_RATIO must be less than "
            "DEEP_AGENT_CONTEXT_COMPACTION_TRIGGER_RATIO"
        )
    if options.agent.context_summary_timeout <= 0:
        raise ValueError("DEEP_AGENT_CONTEXT_SUMMARY_TIMEOUT must be positive")
    if options.agent.sandbox_enabled:
        try:
            load_sandbox_profile(DEFAULT_SANDBOX_PROFILE)
        except (ProfileNotFoundError, ProfileValidationError) as exc:
            raise ValueError(f"default sandbox profile is invalid: {exc}") from exc
