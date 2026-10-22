from __future__ import annotations

from dataclasses import replace

from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.context.compaction import build_context_compaction
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.factory import build_agent
from deep_agent.agent.input_capabilities import augment_instructions_with_capabilities
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.tool_output_limits import build_tool_output_limits
from deep_agent.agent.history_tools import build_history_toolset
from deep_agent.agent.spec import DEFAULT_INSTRUCTIONS, AgentSpec
from deep_agent.models.context_window import ContextWindowModel
from deep_agent.models.provider_registry import (
    ProviderRuntimeError,
    effective_base_url,
    load_provider_for_model,
    validate_provider_config,
)
from deep_agent.capabilities.catalog import invalid_tool_package_codes
from deep_agent.capabilities.codes import canonical_tool_package_codes
from deep_agent.capabilities.display_names import build_tool_display_name_map
from deep_agent.capabilities.mcp.resolver import load_mcp_rows, resolve_mcp_toolsets
from deep_agent.capabilities.mcp.resources import build_mcp_resource_toolset
from deep_agent.capabilities.ui.registry import prepare_ui_tool_packages_for_spec
from deep_agent.skills.runtime import ResolvedSkills, resolve_skills_for_run
from deep_agent.skills.script_tool import build_skill_script_toolset
from deep_agent.skills.tools import build_skill_resource_toolset
from deep_agent.files import build_file_export_toolset, build_file_toolset
from deep_agent.documents.runtime import build_document_runtime
from deep_agent.documents.settings import DOCUMENT_PACKAGE_CODE, load_document_parsing_config
from deep_agent.core.context import AppContext
from deep_agent.channels.instructions import channel_instructions
from deep_agent.channels.outbound import ChannelRunContext
from deep_agent.core.exceptions import AgentModelNotConfiguredError, AgentNotFoundError, LlmModelNotFoundError
from deep_agent.options import OPTION
from deep_agent.web.runtime import build_web_runtime
from deep_agent.web.settings import (
    WEB_PACKAGE_CODE,
    WebSearchConfigurationError,
    load_web_search_config,
    WebSearchRuntimeConfig,
)


async def _provider_for_model(ctx: AppContext, llm_row):
    return await load_provider_for_model(llm_row, ctx.stores.llm_configuration)


def _model_settings_for_spec(spec: AgentSpec) -> ModelSettings | None:
    values: ModelSettings = {}
    if spec.max_output_tokens is not None:
        values["max_tokens"] = spec.max_output_tokens
    if not values:
        return None
    return values


def _skill_script_execution_available(
    spec: AgentSpec,
    ctx: AppContext,
    resolved_skills: ResolvedSkills,
) -> bool:
    sandbox_selected = any(
        package.code == "builtin:sandbox"
        for package in ctx.registry.selected(spec.capabilities)
    )
    return bool(
        sandbox_selected
        and ctx.sandbox_backend is not None
        and resolved_skills.scripts
    )


async def build_runner(
    ctx: AppContext,
    *,
    model: str | None = None,
    model_id: str | None = None,
    capabilities: list[str] | None = None,
    request_headers: dict[str, str] | None = None,
) -> AgentRunner:
    """Assemble a per-request AgentRunner from settings + registry + store."""
    if model_id is not None:
        llm_row = await ctx.stores.llm_model.get_by_id(model_id)
        if llm_row is None:
            raise LlmModelNotFoundError(model_id)
        provider = await _provider_for_model(ctx, llm_row)
        spec = AgentSpec.from_llm_model(
            llm_row,
            provider=provider,
        )
    elif model:
        spec = AgentSpec(
            name="default",
            model=model,
            instructions=DEFAULT_INSTRUCTIONS,
        )
    else:
        llm_rows = await ctx.stores.llm_model.list_all()
        if not llm_rows:
            raise LlmModelNotFoundError("default")
        provider = None
        llm_row = None
        last_error: ProviderRuntimeError | None = None
        for candidate in llm_rows:
            try:
                provider = await _provider_for_model(ctx, candidate)
            except ProviderRuntimeError as exc:
                last_error = exc
                continue
            llm_row = candidate
            break
        if llm_row is None or provider is None:
            if last_error is not None:
                raise last_error
            raise LlmModelNotFoundError("default")
        spec = AgentSpec.from_llm_model(
            llm_row,
            provider=provider,
        )
    if capabilities is not None:
        spec.capabilities = canonical_tool_package_codes(capabilities)
    return await _build_runner_from_spec(spec, ctx, request_headers=request_headers)


async def build_runner_for_agent(
    ctx: AppContext,
    agent_id: str,
    *,
    model_id: str | None = None,
    capabilities: list[str] | None = None,
    request_headers: dict[str, str] | None = None,
    channel_type: str | None = None,
    channel_context: ChannelRunContext | None = None,
) -> AgentRunner:
    """Assemble a per-request AgentRunner from a database agent configuration."""
    row = await ctx.stores.agent.get_by_id(agent_id)
    if row is None:
        raise AgentNotFoundError(agent_id)

    effective_model_id = model_id if model_id is not None else row.model_id
    if effective_model_id is None:
        raise AgentModelNotConfiguredError(agent_id)
    llm_row = await ctx.stores.llm_model.get_by_id(effective_model_id)
    if llm_row is None:
        raise LlmModelNotFoundError(effective_model_id)

    provider = await _provider_for_model(ctx, llm_row)
    spec = AgentSpec.from_row(row, llm_row, provider=provider)
    entry_instructions = channel_instructions(channel_type)
    if entry_instructions:
        spec = replace(spec, instructions=f"{spec.instructions}\n\n{entry_instructions}")
    if capabilities is not None:
        spec.capabilities = canonical_tool_package_codes(capabilities)
    runner = await _build_runner_from_spec(spec, ctx, request_headers=request_headers, channel_context=channel_context)
    async def load_output_config():
        current = await ctx.stores.agent.get_by_id(agent_id)
        if current is None:
            raise AgentNotFoundError(agent_id)
        if not current.structured_output_enabled:
            return None
        current, config = await ctx.stores.agent.get_output_configuration(agent_id)
        if current is None:
            raise AgentNotFoundError(agent_id)
        if not current.structured_output_enabled:
            return None
        if config is None:
            raise ValueError("Enabled structured output has no definition")
        return {"name": config.name, "description": config.description, "json_schema": config.json_schema,
                "allow_text_output": bool(config.allow_text_output)}
    runner._output_config_loader = load_output_config
    return runner


async def _build_runner_from_spec(
    spec: AgentSpec,
    ctx: AppContext,
    *,
    request_headers: dict[str, str] | None = None,
    channel_context: ChannelRunContext | None = None,
) -> AgentRunner:
    spec = replace(
        spec,
        capabilities=canonical_tool_package_codes(spec.capabilities),
    )
    if spec.provider_config is not None:
        validate_provider_config(spec.provider_config)
    invalid_packages = await invalid_tool_package_codes(
        spec.capabilities, ctx.registry, ctx.stores
    )
    if invalid_packages:
        from deep_agent.core.exceptions import ToolPackageError

        raise ToolPackageError(
            "Unknown or unavailable tool packages: " + ", ".join(invalid_packages)
        )
    mcp_rows = await load_mcp_rows(ctx.stores)

    dynamic_ui_packages, ui_tool_hints = await prepare_ui_tool_packages_for_spec(
        ctx.stores,
        spec,
    )
    tool_display_names = build_tool_display_name_map(
        ctx.registry,
        capabilities=spec.capabilities,
        mcp_rows=mcp_rows,
        ui_packages=dynamic_ui_packages,
    )

    mcp_toolsets = await resolve_mcp_toolsets(ctx.stores, spec, mcp_rows=mcp_rows)
    mcp_resource_toolset = build_mcp_resource_toolset(spec, mcp_rows)
    resolved_skills = await resolve_skills_for_run(
        ctx.stores,
        spec.skills,
        runtime=ctx.skill_runtime,
        request_headers=request_headers or {},
    )
    script_execution_available = _skill_script_execution_available(
        spec,
        ctx,
        resolved_skills,
    )
    skill_resource_toolset = build_skill_resource_toolset(resolved_skills.resources)
    file_export_toolset = build_file_export_toolset()
    skill_script_toolset = (
        build_skill_script_toolset(
            resolved_skills.scripts,
            backend=ctx.sandbox_backend,
            timeout_sec=float(OPTION.agent.sandbox_timeout),
        )
        if script_execution_available and ctx.sandbox_backend is not None
        else None
    )

    document_config = await load_document_parsing_config(getattr(ctx.stores, "global_setting", None))
    document_selected = DOCUMENT_PACKAGE_CODE in spec.capabilities
    spec = replace(spec, instructions=augment_instructions_with_capabilities(
        spec, document_config=document_config, document_available=document_selected,
    ))
    if resolved_skills.unavailable:
        unavailable = ", ".join(item.code for item in resolved_skills.unavailable)
        spec = replace(
            spec,
            instructions=(
                f"{spec.instructions}\n\n"
                "The following configured Skills are temporarily unavailable in this "
                f"runtime: {unavailable}. Do not claim that you loaded or used them. "
                "Continue with the available Skills and tools."
            ),
        )
    estimator = TokenEstimator.for_model(
        spec.model,
        (
            effective_base_url(spec.provider_config)
            if spec.provider_config is not None
            else spec.base_url
        ),
        provider_type=(
            spec.provider_config.provider_type if spec.provider_config else None
        ),
    )
    file_toolset = build_file_toolset(
        resolved_skills.resources,
        context_window_tokens=spec.context_window_tokens,
        estimator=estimator,
        document_available=document_selected,
    )
    document_runtime = (build_document_runtime(
        document_config, resolved_skills.resources,
        context_window_tokens=spec.context_window_tokens, estimator=estimator,
    ) if document_selected else None)
    context_compaction = build_context_compaction(
        context_window_tokens=spec.context_window_tokens,
        max_output_tokens=spec.max_output_tokens,
        estimator=estimator,
    )
    tool_output_limits, tool_output_store = build_tool_output_limits(
        request_headers=request_headers or {},
        context_window_tokens=spec.context_window_tokens,
        max_output_tokens=spec.max_output_tokens,
        estimator=estimator,
        file_store=getattr(ctx, "file_store", None),
    )
    history_toolset = build_history_toolset(ctx.store)
    channel_toolset = None
    if channel_context is not None:
        from deep_agent.channels.message_tool import build_channel_message_toolset
        from deep_agent.channels.registry import default_registry
        from deep_agent.services.channel_gateway_client import ChannelGatewayClient
        account = await ctx.stores.channel.get_account(channel_context.account_id)
        caps = default_registry().message_capabilities(account.channel_type)
        channel_toolset = build_channel_message_toolset(ctx.stores.channel, ChannelGatewayClient(), channel_context, caps)
        spec = replace(spec, instructions=spec.instructions + "\n本轮已提供channel_message：向当前聊天发图片、文件和已有音视频使用此内置工具，无需MCP；媒体使用会话file_id。普通回答由渠道自动投递。工具已确认发送的内容不要重复贴出；发送结果未知时不得盲目补发。")

    web_runtime = None
    if WEB_PACKAGE_CODE in spec.capabilities:
        try:
            global_setting_store = ctx.stores.global_setting
            if global_setting_store is None:
                raise WebSearchConfigurationError(
                    "Global settings store is unavailable"
                )
            web_config = await load_web_search_config(
                global_setting_store,
                ctx.stores.llm_configuration,
            )
            web_runtime = build_web_runtime(
                web_config,
                image_capable="image" in {
                    str(value).strip().lower()
                    for value in (spec.input_modalities or [])
                },
            )
        except WebSearchConfigurationError as exc:
            web_runtime = build_web_runtime(
                WebSearchRuntimeConfig(mode="unconfigured", configuration_error=str(exc)),
                image_capable="image" in (spec.input_modalities or []),
            )
    agent = build_agent(
        spec,
        ctx.registry,
        request_retries=OPTION.agent.request_retries,
        mcp_toolsets=mcp_toolsets,
        dynamic_ui_packages=dynamic_ui_packages,
        mcp_resource_toolset=mcp_resource_toolset,
        file_toolset=file_toolset,
        file_export_toolset=file_export_toolset,
        skill_resource_toolset=skill_resource_toolset,
        skill_script_toolset=skill_script_toolset,
        history_toolset=history_toolset,
        channel_toolset=channel_toolset,
        skill_capability=resolved_skills.capability,
        context_compaction=context_compaction,
        tool_output_limits=tool_output_limits,
        mcp_server_names=[
            code.removeprefix("mcp:")
            for code in (spec.capabilities or [])
            if code.startswith("mcp:")
        ],
        estimator=estimator,
        web_toolset=(web_runtime.toolset if web_runtime is not None else None),
        document_toolset=(document_runtime.toolset if document_runtime else None),
        document_lifecycle=(document_runtime.lifecycle if document_runtime else None),
    )
    if spec.context_window_tokens is not None and agent.model is not None:
        agent.model = ContextWindowModel(agent.model, spec.context_window_tokens)
    limits = UsageLimits(
        request_limit=OPTION.agent.max_requests,
        total_tokens_limit=OPTION.agent.max_total_tokens,
    )
    return AgentRunner(
        agent,
        ctx.store,
        limits,
        ui_tool_hints=ui_tool_hints,
        tool_display_names=tool_display_names,
        model_settings=_model_settings_for_spec(spec),
        context_window_tokens=spec.context_window_tokens,
        max_output_tokens=spec.max_output_tokens,
        model_tags=spec.tags,
        context_compaction=context_compaction,
        model_id=spec.llm_model_id or spec.model,
        model_name=spec.model,
        agent_name=spec.name,
        provider_id=(spec.provider_config.id if spec.provider_config else None),
        provider_type=(
            spec.provider_config.provider_type if spec.provider_config else None
        ),
        estimator=estimator,
        active_runs=getattr(ctx, "active_runs", None),
        skill_runtime_context=resolved_skills.as_context(),
        tool_output_store=tool_output_store,
    )
