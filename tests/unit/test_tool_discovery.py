from __future__ import annotations

import unittest
from unittest.mock import patch

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RunUsage

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_discovery import (
    MCP_SEARCH_RESULT_LIMIT,
    build_mcp_discovery_toolset,
    build_search_documents,
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

    async def test_more_than_three_tools_preselects_and_defers_globally(self) -> None:
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
        self.assertEqual(ctx.deps.tool_discovery.candidate_count, 4)
        self.assertGreater(
            ctx.deps.tool_discovery.estimated_saved_schema_tokens,
            0,
        )
        instructions = await wrapper.get_instructions(ctx)
        self.assertIn("deepfos_delete_financial_model", "\n".join(instructions or []))

    async def test_three_or_fewer_mcp_tools_are_all_direct(self) -> None:
        ctx = self._ctx("任意问题")
        small = FunctionToolset(
            tools=[search_financial_model, list_cube_dimensions]
        ).prefixed("small")
        wrapper = build_mcp_discovery_toolset(
            [small],
            server_names=["small"],
            estimator=TokenEstimator.conservative(),
        )
        assert wrapper is not None

        tools = await wrapper.get_tools(ctx)

        self.assertTrue(all(not item.tool_def.defer_loading for item in tools.values()))
        self.assertEqual(ctx.deps.tool_discovery.direct_count, 2)
        self.assertEqual(ctx.deps.tool_discovery.deferred_count, 0)

    async def test_multiple_mcp_servers_share_one_global_preselection_limit(self) -> None:
        ctx = self._ctx("查询维度成员")
        finance = FunctionToolset(
            tools=[search_financial_model, resolve_members]
        ).prefixed("finance")
        catalog = FunctionToolset(
            tools=[list_cube_dimensions, delete_financial_model]
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

    async def test_retriever_failure_discloses_the_authorized_catalog(self) -> None:
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

        self.assertTrue(all(not item.tool_def.defer_loading for item in tools.values()))
        self.assertEqual(ctx.deps.tool_discovery.direct_count, 4)
        self.assertEqual(ctx.deps.tool_discovery.fallback_reason, "RuntimeError")

    async def test_small_mcp_catalog_does_not_expose_search_tools(self) -> None:
        seen: list[str] = []

        def model_function(_messages, info: AgentInfo) -> ModelResponse:
            seen.extend(tool.name for tool in info.function_tools)
            return ModelResponse(parts=[TextPart(content="done")])

        small = FunctionToolset(
            tools=[search_financial_model, list_cube_dimensions]
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

        self.assertEqual(set(seen), {"small_search_financial_model", "small_list_cube_dimensions"})

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
            "查询财务模型预算",
            deps=AgentDeps(session_id="session", agent_id="agent"),
        )

        self.assertEqual(result.output, "done")
        self.assertEqual(calls, 3)
        self.assertIn("deepfos_delete_financial_model", seen_tools[1])


if __name__ == "__main__":
    unittest.main()
