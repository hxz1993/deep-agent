from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import IncludeToolReturnSchemas, ToolSearch
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolSearchCallPart,
    ToolSearchReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_discovery import (
    MCP_DIRECT_DISCLOSURE_LIMIT,
    MCP_SEARCH_RESULT_LIMIT,
    TOOL_SEARCH_DESCRIPTION,
    TOOL_SEARCH_PARAMETER_DESCRIPTION,
    ToolDiscoveryTelemetry,
    build_mcp_discovery_toolset,
    build_return_schema_selector,
    build_search_documents,
    mcp_original_tool_name,
    rank_tool_documents,
    search_deferred_tools,
    search_text_for_run,
)


def search_financial_model(model_name: str, year: int = 2026) -> str:
    """查询财务模型及预算工作版。"""
    return f"{model_name}:{year}"


def list_cube_dimensions(cube_name: str) -> list[str]:
    """列出 Cube 的维度。"""
    return [cube_name]


def resolve_members(dimension: str, keywords: str) -> list[str]:
    """搜索维度成员。"""
    return [dimension, keywords]


def delete_financial_model(model_name: str) -> bool:
    """永久删除财务模型。"""
    return True


def refresh_financial_model_cache(model_name: str) -> bool:
    """刷新财务模型缓存。"""
    return True


def generate_financial_model_report(model_name: str) -> str:
    """生成财务模型报表。"""
    return model_name


def upload_financial_model_template(model_name: str) -> str:
    """上传财务模型模板。"""
    return model_name


def deeptable_workbook_get(workbook_name: str) -> str:
    """读取电子表格输入表和工作簿。"""
    return workbook_name


def cube_query_data(cube: str) -> dict[str, list[dict[str, int]]]:
    """查询 Cube 数据。"""
    return {"rows": []}


def dimension_query_members(dimension: str) -> dict[str, list[str]]:
    """查询维度成员。"""
    return {"members": []}


def cube_describe(cube: str) -> dict[str, str]:
    """查看 Cube 定义。"""
    return {"name": cube}


def platform_folder_list(path: str = "/") -> list[str]:
    """List child folders under a platform directory."""
    return [path]


def platform_element_search(query: str) -> list[str]:
    """Search platform elements by keyword."""
    return [query]


class ToolDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def _ctx(self, prompt: str) -> RunContext[AgentDeps]:
        return RunContext(
            deps=AgentDeps(session_id="session", agent_id="agent"),
            model=TestModel(),
            usage=RunUsage(),
            prompt=prompt,
        )

    def _toolset(self):
        return FunctionToolset(
            tools=[
                search_financial_model,
                list_cube_dimensions,
                resolve_members,
                delete_financial_model,
                refresh_financial_model_cache,
                generate_financial_model_report,
            ]
        ).prefixed("deepfos")

    async def test_lexical_rank_uses_generic_mcp_fields(self) -> None:
        ctx = self._ctx("查询 2026 年财务模型预算工作版")
        tools = list((await self._toolset().get_tools(ctx)).values())
        documents = build_search_documents(
            [item.tool_def for item in tools],
            server_names=["deepfos"],
        )

        ranked = rank_tool_documents(ctx.prompt or "", documents, limit=3)

        self.assertEqual(ranked[0], "deepfos_search_financial_model")
        self.assertNotIn("deepfos_delete_financial_model", ranked)

    def test_chinese_query_does_not_invent_cross_language_synonyms(self) -> None:
        tools = [
            ToolDefinition(
                name="deepfos_platform_folder_list",
                description=(
                    "List compact shared fields for the direct child folders and elements "
                    "in one DeepFOS folder by folder_id or path."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "folder_id": {"type": "string", "description": "Optional folder ID."},
                        "path": {"type": "string", "description": "Optional folder path."},
                    },
                },
            ),
            ToolDefinition(
                name="deepfos_platform_element_search",
                description="Search DeepFOS elements by name, type, path, or folder.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            ),
            ToolDefinition(
                name="deepfos_dimension_get",
                description="Get a dimension definition.",
                parameters_json_schema={"type": "object", "properties": {}},
            ),
        ]
        documents = build_search_documents(tools, server_names=["deepfos"])

        self.assertEqual(rank_tool_documents("列出平台文件夹结构", documents, limit=3), [])

    def test_explicit_protocol_identifier_returns_only_exact_tool(self) -> None:
        tools = [
            ToolDefinition(
                name="deepfos_platform_folder_list",
                description="List folders and elements.",
                parameters_json_schema={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="deepfos_platform_element_search",
                description="Search platform elements.",
                parameters_json_schema={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="deepfos_platform_element_get",
                description="Get platform element details.",
                parameters_json_schema={"type": "object", "properties": {}},
            ),
        ]

        ranked = rank_tool_documents(
            "platform_folder_list 平台文件夹列表",
            build_search_documents(tools, server_names=["deepfos"]),
            limit=5,
        )

        self.assertEqual(ranked, ["deepfos_platform_folder_list"])

    def test_mcp_standard_title_is_used_as_alias(self) -> None:
        tool = ToolDefinition(
            name="deepfos_platform_folder_list",
            description="List folders and elements.",
            parameters_json_schema={"type": "object", "properties": {}},
            metadata={"title": "平台文件夹列表"},
        )

        ranked = rank_tool_documents(
            "平台文件夹列表",
            build_search_documents([tool], server_names=["deepfos"]),
            limit=3,
        )

        self.assertEqual(ranked, ["deepfos_platform_folder_list"])

    async def test_return_schema_allowlist_uses_original_mcp_name(self) -> None:
        ctx = self._ctx("查询财务模型")
        selector = build_return_schema_selector(
            server_names=["renamed_server"],
        )
        selected = ToolDefinition(
            name="renamed_server_cube_query_data",
            parameters_json_schema={"type": "object", "properties": {}},
            return_schema={"type": "object", "properties": {"rows": {"type": "array"}}},
        )
        other = ToolDefinition(
            name="renamed_server_cube_describe",
            parameters_json_schema={"type": "object", "properties": {}},
            return_schema={"type": "object"},
        )

        self.assertTrue(await selector(ctx, selected))
        self.assertFalse(await selector(ctx, other))
        self.assertEqual(
            mcp_original_tool_name(selected.name, ["renamed_server"]),
            "cube_query_data",
        )

    async def test_return_schema_allowlist_does_not_invent_missing_schema(self) -> None:
        ctx = self._ctx("查询成员")
        selector = build_return_schema_selector(
            server_names=["deepfos"],
        )
        missing = ToolDefinition(
            name="deepfos_dimension_query_members",
            parameters_json_schema={"type": "object", "properties": {}},
        )

        self.assertFalse(await selector(ctx, missing))

    async def test_return_schema_is_only_injected_for_disclosed_allowlist_tool(self) -> None:
        seen: dict[str, str] = {}

        def model_function(_messages, info: AgentInfo) -> ModelResponse:
            seen.update({tool.name: tool.description or "" for tool in info.function_tools})
            return ModelResponse(parts=[TextPart(content="done")])

        toolset = FunctionToolset(
            tools=[
                cube_query_data,
                dimension_query_members,
                cube_describe,
                search_financial_model,
                platform_folder_list,
                platform_element_search,
            ]
        ).prefixed("renamed_server")
        wrapper = build_mcp_discovery_toolset(
            [toolset],
            server_names=["renamed_server"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None
        agent = Agent(
            FunctionModel(model_function),
            deps_type=AgentDeps,
            toolsets=[wrapper],
            capabilities=[
                ToolSearch(strategy=search_deferred_tools, max_results=MCP_SEARCH_RESULT_LIMIT),
                IncludeToolReturnSchemas(
                    tools=build_return_schema_selector(
                        server_names=["renamed_server"],
                    )
                ),
            ],
        )
        deps = AgentDeps(
            session_id="schema-session",
            agent_id="agent",
            tool_discovery_inputs_explicit=True,
            tool_discovery_primary_query="renamed_server_cube_query_data",
        )

        await agent.run("renamed_server_cube_query_data", deps=deps)

        self.assertIn("Return schema:", seen["renamed_server_cube_query_data"])
        self.assertNotIn("renamed_server_cube_describe", seen)
        self.assertNotIn("renamed_server_dimension_query_members", seen)
        self.assertEqual(
            deps.tool_discovery.return_schema_tools,
            ["renamed_server_cube_query_data"],
        )
        self.assertGreater(deps.tool_discovery.estimated_return_schema_tokens, 0)

    async def test_read_only_negations_do_not_preselect_side_effect_tools(self) -> None:
        ctx = self._ctx("查询当前财务模型，只读即可，不要刷新、生成或上传任何内容")
        toolset = FunctionToolset(
            tools=[
                search_financial_model,
                refresh_financial_model_cache,
                generate_financial_model_report,
                upload_financial_model_template,
                list_cube_dimensions,
                resolve_members,
            ]
        ).prefixed("deepfos")
        tools = list((await toolset.get_tools(ctx)).values())

        ranked = rank_tool_documents(
            ctx.prompt or "",
            build_search_documents([item.tool_def for item in tools]),
            limit=5,
        )

        self.assertEqual(ranked, ["deepfos_search_financial_model"])

    async def test_run_read_only_policy_filters_later_side_effect_searches(self) -> None:
        ctx = self._ctx(
            "对比当前财务模型，不要创建、保存、刷新缓存、上传、发布、同步、导入或修改"
        )
        toolset = FunctionToolset(
            tools=[
                search_financial_model,
                refresh_financial_model_cache,
                generate_financial_model_report,
                upload_financial_model_template,
                list_cube_dimensions,
                resolve_members,
            ]
        ).prefixed("deepfos")
        wrapper = build_mcp_discovery_toolset(
            [toolset],
            server_names=["deepfos"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None

        tools = await wrapper.get_tools(ctx)
        search_result = search_deferred_tools(
            ctx,
            ["上传模板、创建报表、刷新缓存"],
            [item.tool_def for item in tools.values()],
        )

        self.assertTrue(ctx.deps.tool_discovery.read_only_intent)
        direct = {
            name for name, item in tools.items() if not item.tool_def.defer_loading
        }
        self.assertEqual(direct, {"deepfos_search_financial_model"})
        self.assertEqual(search_result, [])

    async def test_context_reference_search_uses_only_type_and_label(self) -> None:
        ctx = self._ctx("")
        ctx.prompt = [
            {
                "kind": "context-ref",
                "key": "selection",
                "type": "deeptable.selection",
                "schema_version": "2.0",
                "label": "销售预算 B2:C4",
                "payload": {"secret_fact": "must-not-enter-retriever"},
                "metadata": {"token": "must-not-enter-retriever"},
            }
        ]

        visible = search_text_for_run(ctx)

        self.assertIn("deeptable.selection", visible)
        self.assertIn("销售预算 B2:C4", visible)
        self.assertNotIn("secret_fact", visible)
        self.assertNotIn("token", visible)

    async def test_user_question_precedes_context_reference_labels_for_preselection(self) -> None:
        ctx = self._ctx("")
        ctx.prompt = [
            {"kind": "text-content", "content": "查询财务模型的维度成员和预算工作版"},
            {
                "kind": "context-ref",
                "key": "page",
                "type": "deeptable.selection",
                "schema_version": "2.0",
                "label": "电子表格输入表 B020101_copy",
                "payload": {},
            },
        ]
        toolset = FunctionToolset(
            tools=[
                search_financial_model,
                list_cube_dimensions,
                resolve_members,
                deeptable_workbook_get,
                cube_describe,
                platform_folder_list,
            ]
        ).prefixed("deepfos")
        wrapper = build_mcp_discovery_toolset(
            [toolset],
            server_names=["deepfos"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None

        await wrapper.get_tools(ctx)

        self.assertNotIn(
            "deepfos_deeptable_workbook_get",
            ctx.deps.tool_discovery.preselected_tools,
        )
        self.assertIn(
            "deepfos_search_financial_model",
            ctx.deps.tool_discovery.preselected_tools,
        )

    async def test_context_only_prompt_uses_reference_label_as_fallback(self) -> None:
        ctx = self._ctx("")
        ctx.prompt = [
            {
                "kind": "context-ref",
                "key": "page",
                "type": "deeptable.selection",
                "schema_version": "2.0",
                "label": "电子表格输入表 B020101_copy",
                "payload": {},
            }
        ]
        toolset = FunctionToolset(
            tools=[
                search_financial_model,
                list_cube_dimensions,
                resolve_members,
                deeptable_workbook_get,
                cube_describe,
                platform_folder_list,
            ]
        ).prefixed("deepfos")
        wrapper = build_mcp_discovery_toolset(
            [toolset],
            server_names=["deepfos"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None

        await wrapper.get_tools(ctx)

        self.assertIn(
            "deepfos_deeptable_workbook_get",
            ctx.deps.tool_discovery.preselected_tools,
        )

    async def test_nonempty_unmatched_question_does_not_fallback_to_context_label(self) -> None:
        ctx = self._ctx("")
        ctx.prompt = [
            {"kind": "text-content", "content": "对比 Actual 与 Budget 的差异"},
            {
                "kind": "context-ref",
                "key": "page",
                "type": "deeptable.selection",
                "schema_version": "2.0",
                "label": "电子表格输入表 B020101_copy",
                "payload": {},
            },
        ]
        toolset = FunctionToolset(
            tools=[
                search_financial_model,
                list_cube_dimensions,
                resolve_members,
                deeptable_workbook_get,
                cube_describe,
                platform_folder_list,
            ]
        ).prefixed("deepfos")
        wrapper = build_mcp_discovery_toolset(
            [toolset],
            server_names=["deepfos"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None

        await wrapper.get_tools(ctx)

        self.assertEqual(ctx.deps.tool_discovery.preselected_tools, [])

    async def test_more_than_five_tools_preselects_and_defers_globally(self) -> None:
        ctx = self._ctx("查询财务模型和预算")
        wrapper = build_mcp_discovery_toolset(
            [self._toolset()],
            server_names=["deepfos"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None

        tools = await wrapper.get_tools(ctx)
        direct = [name for name, item in tools.items() if not item.tool_def.defer_loading]
        deferred = [name for name, item in tools.items() if item.tool_def.defer_loading]

        self.assertLessEqual(len(direct), 3)
        self.assertIn("deepfos_search_financial_model", direct)
        self.assertIn("deepfos_delete_financial_model", deferred)
        self.assertEqual(ctx.deps.tool_discovery.candidate_count, 6)
        self.assertGreater(
            ctx.deps.tool_discovery.estimated_saved_schema_tokens,
            0,
        )
        instructions = await wrapper.get_instructions(ctx)
        instruction_text = "\n".join(instructions or [])
        self.assertIn("deepfos_delete_financial_model", instruction_text)
        self.assertIn("用户原始语言和简洁英文能力描述", instruction_text)
        self.assertIn("协议名必须原样复制", instruction_text)

    async def test_five_or_fewer_mcp_tools_are_all_direct(self) -> None:
        ctx = self._ctx("任意问题")
        small = FunctionToolset(
            tools=[
                search_financial_model,
                list_cube_dimensions,
                resolve_members,
                cube_describe,
                platform_folder_list,
            ]
        ).prefixed("small")
        wrapper = build_mcp_discovery_toolset(
            [small],
            server_names=["small"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None

        tools = await wrapper.get_tools(ctx)

        self.assertTrue(all(not item.tool_def.defer_loading for item in tools.values()))
        self.assertEqual(ctx.deps.tool_discovery.direct_count, MCP_DIRECT_DISCLOSURE_LIMIT)
        self.assertEqual(ctx.deps.tool_discovery.deferred_count, 0)

    async def test_multiple_mcp_servers_share_one_global_preselection_limit(self) -> None:
        ctx = self._ctx("搜索维度成员并更新配置")
        finance = FunctionToolset(
            tools=[search_financial_model, resolve_members, cube_query_data]
        ).prefixed("finance")
        catalog = FunctionToolset(
            tools=[
                list_cube_dimensions,
                delete_financial_model,
                refresh_financial_model_cache,
            ]
        ).prefixed("catalog")
        wrapper = build_mcp_discovery_toolset(
            [finance, catalog],
            server_names=["finance", "catalog"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None

        tools = await wrapper.get_tools(ctx)
        direct = [name for name, item in tools.items() if not item.tool_def.defer_loading]

        self.assertLessEqual(len(direct), 3)
        self.assertIn("finance_resolve_members", direct)
        instructions = "\n".join(await wrapper.get_instructions(ctx) or [])
        self.assertIn("- catalog:", instructions)
        self.assertNotIn("finance_resolve_members", instructions)

    async def test_no_positive_score_does_not_preselect_arbitrary_mcp_tool(self) -> None:
        ctx = self._ctx("火星气象温度")
        wrapper = build_mcp_discovery_toolset(
            [self._toolset()],
            server_names=["deepfos"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None

        tools = await wrapper.get_tools(ctx)

        self.assertTrue(all(item.tool_def.defer_loading for item in tools.values()))
        self.assertEqual(ctx.deps.tool_discovery.preselected_tools, [])

    async def test_retriever_failure_preserves_read_only_policy(self) -> None:
        ctx = self._ctx("查询财务模型")
        wrapper = build_mcp_discovery_toolset(
            [self._toolset()],
            server_names=["deepfos"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None

        with patch(
            "deep_agent.agent.tool_discovery.rank_tool_documents",
            side_effect=RuntimeError("retriever unavailable"),
        ):
            tools = await wrapper.get_tools(ctx)

        self.assertNotIn("deepfos_delete_financial_model", tools)
        self.assertTrue(all(not item.tool_def.defer_loading for item in tools.values()))
        self.assertEqual(ctx.deps.tool_discovery.direct_count, 3)
        self.assertEqual(ctx.deps.tool_discovery.fallback_reason, "RuntimeError")

    async def test_small_mcp_catalog_does_not_expose_search_tools(self) -> None:
        seen: list[str] = []

        def model_function(_messages, info: AgentInfo) -> ModelResponse:
            seen.extend(tool.name for tool in info.function_tools)
            return ModelResponse(parts=[TextPart(content="done")])

        small = FunctionToolset(
            tools=[
                search_financial_model,
                list_cube_dimensions,
                resolve_members,
                cube_describe,
                platform_folder_list,
            ]
        ).prefixed("small")
        wrapper = build_mcp_discovery_toolset(
            [small],
            server_names=["small"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None
        agent = Agent(
            FunctionModel(model_function),
            deps_type=AgentDeps,
            toolsets=[wrapper],
            capabilities=[
                ToolSearch(
                    strategy=search_deferred_tools,
                    max_results=MCP_SEARCH_RESULT_LIMIT,
                )
            ],
        )

        await agent.run(
            "查询财务模型",
            deps=AgentDeps(session_id="session", agent_id="agent"),
        )

        self.assertEqual(
            set(seen),
            {
                "small_search_financial_model",
                "small_list_cube_dimensions",
                "small_resolve_members",
                "small_cube_describe",
                "small_platform_folder_list",
            },
        )

    async def test_search_callback_returns_at_most_five_and_updates_telemetry(self) -> None:
        ctx = self._ctx("")
        tools = [
            item.tool_def
            for item in (await self._toolset().get_tools(ctx)).values()
        ]

        result = search_deferred_tools(ctx, ["维度成员搜索"], tools)

        self.assertLessEqual(len(result), MCP_SEARCH_RESULT_LIMIT)
        self.assertIn("deepfos_resolve_members", result)
        self.assertEqual(ctx.deps.tool_discovery.search_calls, 1)
        self.assertEqual(ctx.deps.tool_discovery.discovered_tools, result)

    async def test_complementary_queries_bridge_language_with_protocol_name(self) -> None:
        ctx = self._ctx("")
        toolset = FunctionToolset(
            tools=[platform_folder_list, platform_element_search]
        ).prefixed("deepfos")
        tools = [item.tool_def for item in (await toolset.get_tools(ctx)).values()]
        ctx.deps.tool_discovery = ToolDiscoveryTelemetry(server_names=("deepfos",))

        chinese_only = search_deferred_tools(
            ctx,
            ["列出平台根目录文件夹"],
            tools,
        )
        result = search_deferred_tools(
            ctx,
            [
                "列出平台根目录文件夹",
                "list folders in the platform root directory",
                "platform_folder_list",
            ],
            tools,
        )

        self.assertEqual(chinese_only, [])
        self.assertEqual(result, ["deepfos_platform_folder_list"])
        self.assertEqual(ctx.deps.tool_discovery.search_calls, 2)

    async def test_tool_search_definition_requests_complementary_queries(self) -> None:
        seen: dict[str, ToolDefinition] = {}

        def model_function(_messages, info: AgentInfo) -> ModelResponse:
            seen.update({tool.name: tool for tool in info.function_tools})
            return ModelResponse(parts=[TextPart(content="done")])

        wrapper = build_mcp_discovery_toolset(
            [self._toolset()],
            server_names=["deepfos"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None
        agent = Agent(
            FunctionModel(model_function),
            deps_type=AgentDeps,
            toolsets=[wrapper],
            capabilities=[
                ToolSearch(
                    strategy=search_deferred_tools,
                    max_results=MCP_SEARCH_RESULT_LIMIT,
                    tool_description=TOOL_SEARCH_DESCRIPTION,
                    parameter_description=TOOL_SEARCH_PARAMETER_DESCRIPTION,
                )
            ],
        )

        await agent.run(
            "查询财务模型",
            deps=AgentDeps(session_id="session", agent_id="agent"),
        )

        search_tool = seen["search_tools"]
        self.assertIn("用户原始语言和简洁英文能力描述", search_tool.description or "")
        query_description = search_tool.parameters_json_schema["properties"]["queries"][
            "description"
        ]
        self.assertIn("2～3 个互补查询", query_description)
        self.assertIn("目录协议名", query_description)

    async def test_tool_search_uses_at_most_three_queries(self) -> None:
        ctx = self._ctx("")
        toolset = FunctionToolset(
            tools=[platform_folder_list, platform_element_search]
        ).prefixed("deepfos")
        tools = [item.tool_def for item in (await toolset.get_tools(ctx)).values()]
        ctx.deps.tool_discovery = ToolDiscoveryTelemetry(server_names=("deepfos",))

        result = search_deferred_tools(
            ctx,
            [
                "unmatched-first",
                "unmatched-second",
                "platform_folder_list",
                "platform_element_search",
            ],
            tools,
        )

        self.assertEqual(result, ["deepfos_platform_folder_list"])

    async def test_search_excludes_preselected_and_already_discovered_tools(self) -> None:
        ctx = self._ctx("")
        ctx.discovered_tool_names.add("deepfos_resolve_members")
        ctx.deps.tool_discovery = None
        tools = [
            item.tool_def
            for item in (await self._toolset().get_tools(ctx)).values()
        ]
        ctx.deps.tool_discovery = ToolDiscoveryTelemetry(
            preselected_tools=["deepfos_list_cube_dimensions"]
        )

        result = search_deferred_tools(ctx, ["查询维度成员"], tools)

        self.assertNotIn("deepfos_resolve_members", result)
        self.assertNotIn("deepfos_list_cube_dimensions", result)

    async def test_repeated_equivalent_search_query_is_deduplicated_per_run(self) -> None:
        ctx = self._ctx("")
        tools = [
            item.tool_def
            for item in (await self._toolset().get_tools(ctx)).values()
        ]

        first = search_deferred_tools(ctx, ["搜索维度成员"], tools)
        second = search_deferred_tools(ctx, ["搜索维度成员"], tools)

        self.assertTrue(first)
        self.assertEqual(second, [])
        self.assertEqual(ctx.deps.tool_discovery.search_calls, 2)

    async def test_pydantic_tool_search_reveals_schema_before_execution(self) -> None:
        seen_tools: list[list[str]] = []
        calls = 0

        def model_function(_messages, info: AgentInfo) -> ModelResponse:
            nonlocal calls
            calls += 1
            names = [tool.name for tool in info.function_tools]
            seen_tools.append(names)
            if calls == 1:
                self.assertIn("search_tools", names)
                self.assertNotIn("deepfos_delete_financial_model", names)
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "search_tools",
                            {"queries": ["永久删除财务模型"]},
                            "search-1",
                        )
                    ]
                )
            if calls == 2:
                self.assertIn("deepfos_delete_financial_model", names)
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "deepfos_delete_financial_model",
                            {"model_name": "demo"},
                            "delete-1",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart(content="done")])

        wrapper = build_mcp_discovery_toolset(
            [self._toolset()],
            server_names=["deepfos"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None
        agent = Agent(
            FunctionModel(model_function),
            deps_type=AgentDeps,
            toolsets=[wrapper],
            capabilities=[
                ToolSearch(
                    strategy=search_deferred_tools,
                    max_results=MCP_SEARCH_RESULT_LIMIT,
                )
            ],
        )

        result = await agent.run(
            "搜索维度成员并更新配置",
            deps=AgentDeps(session_id="session", agent_id="agent"),
        )

        self.assertEqual(result.output, "done")
        self.assertEqual(calls, 3)
        self.assertIn("deepfos_delete_financial_model", seen_tools[1])

    async def test_hidden_tool_name_cannot_be_guessed_before_search(self) -> None:
        executions = 0
        model_calls = 0

        def hidden_action(model_name: str) -> str:
            nonlocal executions
            executions += 1
            return model_name

        def model_function(_messages, info: AgentInfo) -> ModelResponse:
            nonlocal model_calls
            model_calls += 1
            names = {tool.name for tool in info.function_tools}
            self.assertIn("search_tools", names)
            self.assertNotIn("deepfos_hidden_action", names)
            if model_calls == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "deepfos_hidden_action",
                            {"model_name": "demo"},
                            "guessed-hidden-1",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart(content="done")])

        toolset = FunctionToolset(
            tools=[
                hidden_action,
                search_financial_model,
                list_cube_dimensions,
                resolve_members,
                cube_describe,
                platform_folder_list,
            ]
        ).prefixed("deepfos")
        wrapper = build_mcp_discovery_toolset(
            [toolset],
            server_names=["deepfos"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None
        agent = Agent(
            FunctionModel(model_function),
            deps_type=AgentDeps,
            toolsets=[wrapper],
            capabilities=[
                ToolSearch(
                    strategy=search_deferred_tools,
                    max_results=MCP_SEARCH_RESULT_LIMIT,
                )
            ],
        )

        result = await agent.run(
            "火星天气",
            deps=AgentDeps(session_id="hidden-guess", agent_id="agent"),
        )

        self.assertEqual(result.output, "done")
        self.assertEqual(executions, 0)
        retries = [
            part
            for message in result.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, RetryPromptPart)
        ]
        self.assertEqual(len(retries), 1)
        self.assertIn("search for it first", str(retries[0].content))

    async def test_removed_discovery_evidence_requires_search_again(self) -> None:
        first_calls = 0

        def first_model(_messages, _info: AgentInfo) -> ModelResponse:
            nonlocal first_calls
            first_calls += 1
            if first_calls == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "search_tools",
                            {"queries": ["永久删除财务模型"]},
                            "search-before-compaction",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart(content="searched")])

        wrapper = build_mcp_discovery_toolset(
            [self._toolset()],
            server_names=["deepfos"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None
        capability = ToolSearch(
            strategy=search_deferred_tools,
            max_results=MCP_SEARCH_RESULT_LIMIT,
        )
        first = Agent(
            FunctionModel(first_model),
            deps_type=AgentDeps,
            toolsets=[wrapper],
            capabilities=[capability],
        )
        discovered = await first.run(
            "搜索维度成员并更新配置",
            deps=AgentDeps(session_id="before-compaction", agent_id="agent"),
        )
        self.assertTrue(
            any(
                isinstance(part, ToolSearchReturnPart)
                for message in discovered.all_messages()
                for part in message.parts
            )
        )

        compacted_history = [
            message
            if not isinstance(message, ModelRequest)
            else replace(
                message,
                parts=[
                    part
                    for part in message.parts
                    if not isinstance(part, ToolSearchReturnPart)
                ],
            )
            for message in discovered.all_messages()
            if not (
                isinstance(message, ModelResponse)
                and any(isinstance(part, ToolSearchCallPart) for part in message.parts)
            )
        ]
        compacted_history = [
            message
            for message in compacted_history
            if not isinstance(message, ModelRequest) or message.parts
        ]
        second_calls = 0

        def second_model(_messages, info: AgentInfo) -> ModelResponse:
            nonlocal second_calls
            second_calls += 1
            self.assertNotIn(
                "deepfos_delete_financial_model",
                {tool.name for tool in info.function_tools},
            )
            if second_calls == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "deepfos_delete_financial_model",
                            {"model_name": "demo"},
                            "guess-after-compaction",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart(content="search required")])

        second = Agent(
            FunctionModel(second_model),
            deps_type=AgentDeps,
            toolsets=[wrapper],
            capabilities=[capability],
        )
        result = await second.run(
            "继续",
            message_history=compacted_history,
            deps=AgentDeps(session_id="after-compaction", agent_id="agent"),
        )

        self.assertEqual(result.output, "search required")
        self.assertTrue(
            any(
                isinstance(part, RetryPromptPart)
                and "search for it first" in str(part.content)
                for message in result.new_messages()
                if isinstance(message, ModelRequest)
                for part in message.parts
            )
        )


if __name__ == "__main__":
    unittest.main()
