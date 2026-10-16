from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import os
import time

from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic_ai import ToolFailed
from pydantic import BaseModel

from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.core.context import AppContextDep
from deep_agent.options import OPTION, DeepAgentOptions
from deep_agent.schemas.llm_provider import mask_api_key
from deep_agent.services.codex_provider import (
    LEGACY_CODEX_PROVIDER_ENV,
    SUBSCRIPTION_PROVIDER_ENV,
    codex_enabled,
)
from deep_agent.schemas.web_search import (
    WebSearchSettingsResponse,
    WebSearchModelTestRequest,
    WebSearchSettingsWrite,
    WebSearchTestResponse,
    WebSearchValueResponse,
)
from deep_agent.web.runtime import probe_web_search_config
from deep_agent.web.settings import (
    WEB_SEARCH_SCHEMA_VERSION,
    WEB_SEARCH_SETTING_KEY,
    WebSearchConfigurationError,
    WebSearchRuntimeConfig,
    resolve_web_search_config,
    resolve_web_search_credentials,
    read_web_search_config,
)


router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES)

SettingValue = str | int | float | bool | list[str] | None


class GlobalSettingItem(BaseModel):
    key: str
    label: str
    env_name: str
    value: SettingValue
    display_value: str
    source: Literal["environment", "default"]
    unit: str | None = None
    description: str
    restart_required: bool = True


class GlobalSettingGroup(BaseModel):
    key: str
    label: str
    items: list[GlobalSettingItem]


class GlobalSettingsResponse(BaseModel):
    groups: list[GlobalSettingGroup]


@dataclass(frozen=True)
class _SettingDefinition:
    key: str
    label: str
    env_name: str
    category: str
    option: str
    description: str
    unit: str | None = None
    empty_display: str = "未配置"


@dataclass(frozen=True)
class _SettingGroupDefinition:
    key: str
    label: str
    items: tuple[_SettingDefinition, ...]


# Only settings that an operator might reasonably tune for a running Agent belong here.
# Database, deployment wiring, subprocess hand-off and local_dev settings live in their
# respective deployment configuration; listing every environment variable is not useful.
_SETTING_GROUPS = (
    _SettingGroupDefinition("runtime", "对话与模型", (
        _SettingDefinition("max_requests", "最大模型请求次数", "DEEP_AGENT_MAX_REQUESTS", "agent", "max_requests", "单轮回复的模型请求上限，工具回传续跑共用额度。", "次/轮"),
        _SettingDefinition("max_total_tokens", "最大 Token 数", "DEEP_AGENT_MAX_TOTAL_TOKENS", "agent", "max_total_tokens", "单轮对话累计 Token 上限。", "Token/轮", "未限制"),
        _SettingDefinition("request_retries", "模型请求重试次数", "DEEP_AGENT_REQUEST_RETRIES", "agent", "request_retries", "模型请求失败后允许的重试次数。", "次"),
        _SettingDefinition("subscription_provider_enabled", "订阅 Provider 接入", SUBSCRIPTION_PROVIDER_ENV, "", "", "允许创建、授权和调用订阅供应商；目前仅支持 ChatGPT，默认关闭；开启后仍需完成账号授权。"),
    )),
    _SettingGroupDefinition("context_compaction", "上下文压缩", (
        _SettingDefinition("context_compaction_trigger_ratio", "压缩触发比例", "DEEP_AGENT_CONTEXT_COMPACTION_TRIGGER_RATIO", "agent", "context_compaction_trigger_ratio", "完整请求的预计输入达到可用输入预算的该比例后触发压缩。"),
        _SettingDefinition("context_summary_timeout", "摘要超时", "DEEP_AGENT_CONTEXT_SUMMARY_TIMEOUT", "agent", "context_summary_timeout", "生成压缩摘要的等待上限。", "秒"),
    )),
    _SettingGroupDefinition("mcp", "MCP 连接", (
        _SettingDefinition("mcp_init_timeout", "初始化超时", "DEEP_AGENT_MCP_INIT_TIMEOUT", "agent", "mcp_init_timeout", "MCP 服务连接及能力握手的等待上限。", "秒"),
        _SettingDefinition("mcp_acquire_retries", "连接重试次数", "DEEP_AGENT_MCP_ACQUIRE_RETRIES", "agent", "mcp_acquire_retries", "获取共享 MCP 运行实例失败后的重试次数。", "次"),
        _SettingDefinition("mcp_forward_request_headers", "转发请求头白名单", "DEEP_AGENT_MCP_FORWARD_REQUEST_HEADERS", "agent", "mcp_forward_request_headers", "仅将白名单内的请求头转发给 MCP 服务；影响 App、Space 等上下文传递。"),
    )),
    _SettingGroupDefinition("sandbox", "Python 沙箱", (
        _SettingDefinition("sandbox_enabled", "启用沙箱", "DEEP_AGENT_SANDBOX_ENABLED", "agent", "sandbox_enabled", "关闭后 execute_python 不可用。"),
        _SettingDefinition("sandbox_timeout", "执行超时", "DEEP_AGENT_SANDBOX_TIMEOUT", "agent", "sandbox_timeout", "单次 Python 执行的等待上限。", "秒"),
        _SettingDefinition("sandbox_max_concurrency", "最大并发", "DEEP_AGENT_SANDBOX_MAX_CONCURRENCY", "agent", "sandbox_max_concurrency", "同时执行的沙箱请求上限。"),
        _SettingDefinition("sandbox_memory_mb", "内存预算", "DEEP_AGENT_SANDBOX_MEMORY_MB", "agent", "sandbox_memory_mb", "单次沙箱执行的内存预算。", "MB"),
        _SettingDefinition("sandbox_cpu_seconds", "CPU 时间上限", "DEEP_AGENT_SANDBOX_CPU_SECONDS", "agent", "sandbox_cpu_seconds", "单次执行消耗 CPU 时间的上限，与等待超时分别生效。", "秒"),
    )),
    _SettingGroupDefinition("uploads", "文件上传", (
        _SettingDefinition("upload_max_size_mb", "单文件大小上限", "DEEP_AGENT_UPLOAD_MAX_SIZE_MB", "agent", "upload_max_size_mb", "单个上传文件允许的最大体积。", "MB"),
        _SettingDefinition("upload_max_count", "单次文件数量", "DEEP_AGENT_UPLOAD_MAX_COUNT", "agent", "upload_max_count", "一次请求允许上传的最大文件数量。", "个"),
        _SettingDefinition("upload_allowed_mimes", "允许的 MIME", "DEEP_AGENT_UPLOAD_ALLOWED_MIMES", "agent", "upload_allowed_mimes", "允许上传的 MIME 类型；留空使用内置白名单。", empty_display="使用内置白名单"),
    )),
    _SettingGroupDefinition("images", "图片输入", (
        _SettingDefinition("image_max_dimension", "图片最大边长", "DEEP_AGENT_IMAGE_MAX_DIMENSION", "agent", "image_max_dimension", "发给模型前图片缩放后的最大边长。", "px"),
        _SettingDefinition("image_max_bytes", "单图大小上限", "DEEP_AGENT_IMAGE_MAX_BYTES", "agent", "image_max_bytes", "发给模型前单张图片允许的最大体积。", "字节"),
    )),
)


def _json_value(value) -> SettingValue:
    if isinstance(value, (set, frozenset, tuple, list)):
        return sorted(str(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _display_value(value: SettingValue, *, empty_display: str) -> str:
    if value is None or value == "" or value == []:
        return empty_display
    if isinstance(value, list):
        return ", ".join(value)
    return str(value)


def build_global_settings(
    options: DeepAgentOptions = OPTION,
) -> GlobalSettingsResponse:
    groups: list[GlobalSettingGroup] = []
    for group_definition in _SETTING_GROUPS:
        items: list[GlobalSettingItem] = []
        for definition in group_definition.items:
            if definition.key == "subscription_provider_enabled":
                value = codex_enabled()
                display_value = "开启" if value else "关闭"
                source: Literal["environment", "default"] = (
                    "environment"
                    if SUBSCRIPTION_PROVIDER_ENV in os.environ or LEGACY_CODEX_PROVIDER_ENV in os.environ
                    else "default"
                )
            else:
                category = getattr(options, definition.category)
                raw_value = getattr(category, definition.option)
                value = _json_value(raw_value)
                display_value = _display_value(
                    value,
                    empty_display=definition.empty_display,
                )
                descriptor = category._options[definition.option]
                source = "environment" if descriptor.set_by else "default"
            items.append(
                GlobalSettingItem(
                    key=definition.key,
                    label=definition.label,
                    env_name=definition.env_name,
                    value=value,
                    display_value=display_value,
                    source=source,
                    unit=definition.unit,
                    description=definition.description,
                )
            )
        groups.append(
            GlobalSettingGroup(
                key=group_definition.key,
                label=group_definition.label,
                items=items,
            )
        )
    return GlobalSettingsResponse(groups=groups)


@router.get("/global-settings", response_model=GlobalSettingsResponse)
async def get_global_settings() -> GlobalSettingsResponse:
    """Return the Console's safe, read-only runtime settings."""
    return build_global_settings()


def _web_search_response(row, config: WebSearchRuntimeConfig) -> WebSearchSettingsResponse:
    return WebSearchSettingsResponse(
        version=WEB_SEARCH_SCHEMA_VERSION,
        value=WebSearchValueResponse(
            mode=config.mode,
            provider=config.provider,
            model=None,
            fetch_mode=config.fetch_mode,
            api_key_set=bool(config.api_key),
            api_key_masked=(
                mask_api_key(config.api_key)
                if config.credential_source == "api_key"
                else None
            ),
            credential_source=config.credential_source,
            llm_provider_id=config.llm_provider_id,
            llm_provider_name=config.llm_provider_name,
            configuration_error=config.configuration_error,
        ),
        created_at=getattr(row, "created_at", None),
        updated_at=getattr(row, "updated_at", None),
    )


async def _current_web_search(ctx: AppContextDep, *, for_write: bool = False):
    store = ctx.stores.global_setting
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Global settings store is unavailable",
        )
    row = await store.get(WEB_SEARCH_SETTING_KEY)
    if not for_write:
        return row, await read_web_search_config(row, ctx.stores.llm_configuration)
    try:
        if row is not None and row.version == WEB_SEARCH_SCHEMA_VERSION:
            # Preserve same-provider credentials, without resolving a stale reference.
            return row, resolve_web_search_config(row.value, require_credentials=False)
    except WebSearchConfigurationError:
        pass
    return row, WebSearchRuntimeConfig(mode="unconfigured")


async def _resolve_web_search_write(
    body: WebSearchSettingsWrite,
    *,
    existing: WebSearchRuntimeConfig,
    ctx: AppContextDep,
) -> WebSearchRuntimeConfig:
    try:
        config = resolve_web_search_config(
            body.value.model_dump(exclude_none=True, exclude_unset=True),
            existing=existing,
        )
        return await resolve_web_search_credentials(
            config,
            ctx.stores.llm_configuration,
        )
    except WebSearchConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get(
    "/global-settings/web-search",
    response_model=WebSearchSettingsResponse,
)
async def get_web_search_settings(ctx: AppContextDep) -> WebSearchSettingsResponse:
    row, config = await _current_web_search(ctx)
    return _web_search_response(row, config)


@router.put(
    "/global-settings/web-search",
    response_model=WebSearchSettingsResponse,
)
async def put_web_search_settings(
    body: WebSearchSettingsWrite,
    ctx: AppContextDep,
) -> WebSearchSettingsResponse:
    _row, existing = await _current_web_search(ctx, for_write=True)
    config = await _resolve_web_search_write(body, existing=existing, ctx=ctx)
    store = ctx.stores.global_setting
    if store is None:  # pragma: no cover - guarded by _current_web_search
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Global settings store is unavailable",
        )
    row = await store.upsert(
        WEB_SEARCH_SETTING_KEY,
        value=config.to_stored_value(),
        version=WEB_SEARCH_SCHEMA_VERSION,
    )
    logger.info(
        "Global Web Search configuration updated mode={} provider={}",
        config.mode,
        config.provider or "unconfigured",
    )
    return _web_search_response(row, config)


@router.post(
    "/global-settings/web-search/test",
    response_model=WebSearchTestResponse,
)
async def test_web_search_settings(
    body: WebSearchSettingsWrite,
    ctx: AppContextDep,
) -> WebSearchTestResponse:
    _row, existing = await _current_web_search(ctx, for_write=True)
    config = await _resolve_web_search_write(body, existing=existing, ctx=ctx)
    started = time.monotonic()
    provider = config.provider or "unconfigured"
    try:
        await probe_web_search_config(config)
    except ToolFailed as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "Global Web Search connectivity test failed provider={} elapsed_ms={}",
            provider,
            elapsed_ms,
        )
        return WebSearchTestResponse(
            ok=False,
            provider=provider,
            message=str(exc),
            elapsed_ms=elapsed_ms,
        )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "Global Web Search connectivity test succeeded provider={} elapsed_ms={}",
        provider,
        elapsed_ms,
    )
    return WebSearchTestResponse(
        ok=True,
        provider=provider,
        message="连接成功",
        elapsed_ms=elapsed_ms,
    )


@router.post("/global-settings/web-search/test-model", response_model=WebSearchTestResponse)
async def test_web_search_model(body: WebSearchModelTestRequest, ctx: AppContextDep) -> WebSearchTestResponse:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="会话模型原生搜索测试已移除，请配置搜索供应商后测试。",
    )
