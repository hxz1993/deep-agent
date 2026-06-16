from deepfos_platform.conf.option import GlobalOptions, Category, Option, Action
from deepfos_platform.lib.cipher import maybe_decrypt_pwd


def _ensure_db_module(value):
    if value == 'dameng':
        try:
            from sqlalchemy_dm.base import DMCompiler
            from sqlalchemy_dm.dmPython import DMDialect_dmPython
        except (ModuleNotFoundError, AttributeError):
            raise RuntimeError(f"sqlalchemy_dm模块未安装")


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
    model_base_url = Option(None, val_type=str, bind_env="MODEL_BASE_URL")
    model_api_key = Option(None, val_type=str, protected=True, bind_env="MODEL_API_KEY")
    system_prompt = Option(
        "You are deep-agent, a helpful general-purpose assistant. "
        "Use the available tools when they help you answer accurately. "
        "Be concise and explain your reasoning when it is useful.",
        val_type=str,
        bind_env="SYSTEM_PROMPT",
    )
    max_requests = Option(12, val_type=int, bind_env="MAX_REQUESTS")
    max_total_tokens = Option(None, val_type=int, bind_env="MAX_TOTAL_TOKENS")
    request_retries = Option(2, val_type=int, bind_env="REQUEST_RETRIES")
    skills_dir = Option("skills", val_type=str, bind_env="SKILLS_DIR")
    cursor_skills_dir = Option(".cursor/skills", val_type=str, bind_env="CURSOR_SKILLS_DIR")
    mcp_forward_request_headers = Option(
        "authorization,x-tenant-id,x-user-token",
        val_type=str,
        bind_env="MCP_FORWARD_REQUEST_HEADERS",
    )
    mcp_stdio_session_scope = Option(
        True,
        val_type=bool,
        bind_env="MCP_STDIO_SESSION_SCOPE",
    )

    def __get__(self, instance, owner) -> "Agent":
        """defined to help ide"""
        return super().__get__(instance, owner)


class DeepAgentOptions(GlobalOptions):
    agent = Agent()
    dbms = DBMS()


OPTION = DeepAgentOptions()
