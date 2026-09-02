from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from pydantic_ai import FunctionToolset

from deep_agent.capabilities import build_registry
from deep_agent.capabilities.display_names import (
    build_tool_display_name_map,
    enrich_deferred_display_names,
    enrich_tool_event_display_name,
)
from deep_agent.capabilities.registry import ToolInfo, ToolPackage
from deep_agent.conversation.display_names import decorate_history_display_names
from deep_agent.conversation.models import (
    ConversationHistoryResponse,
    ConversationOrder,
    PlanConversationItem,
    ToolConversationItem,
)


def _mcp_row(
    name: str,
    *,
    tool_name: str,
    title: str | None,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        transport="url",
        inspection={
            "status": "healthy",
            "tools": {
                "status": "supported",
                "count": 1,
                "snapshot_status": "ready",
                "snapshot": {
                    "schema_version": "1.0",
                    "definitions": [
                        {
                            "name": tool_name,
                            "title": title,
                            "input_schema": {"type": "object"},
                        }
                    ],
                },
            },
        },
    )


class ToolDisplayNameMapTests(unittest.TestCase):
    def test_map_combines_builtin_system_ui_and_mcp_sources(self) -> None:
        ui_package = ToolPackage(
            code="ui:demo",
            category="ui",
            toolset=FunctionToolset([]),
            tools=[ToolInfo("demo__form", "填写信息")],
        )
        mapping = build_tool_display_name_map(
            build_registry(),
            capabilities=["builtin:ptc", "builtin:web", "ui:demo", "mcp:docs"],
            mcp_rows=[_mcp_row("docs", tool_name="lookup", title="查询文档")],
            ui_packages=[ui_package],
        )

        self.assertEqual(mapping["run_code"], "工具调用编排")
        self.assertEqual(mapping["web_search"], "网络搜索")
        self.assertEqual(mapping["web_fetch"], "获取网页内容")
        self.assertEqual(mapping["web_image_view"], "查看网络图片")
        self.assertEqual(mapping["search_tools"], "查找可用工具")
        self.assertEqual(mapping["demo__form"], "填写信息")
        self.assertEqual(mapping["docs_lookup"], "查询文档")

    def test_blank_or_missing_mcp_title_falls_back_to_prefixed_tool_name(self) -> None:
        mapping = build_tool_display_name_map(
            build_registry(),
            capabilities=["mcp:docs"],
            mcp_rows=[_mcp_row("docs", tool_name="lookup", title="  ")],
        )

        self.assertEqual(mapping["docs_lookup"], "docs_lookup")

    def test_sse_and_deferred_add_only_display_name(self) -> None:
        call = {
            "type": "tool_call",
            "tool": "lookup",
            "args": {"id": 1},
            "tool_call_id": "call-1",
        }
        enriched = enrich_tool_event_display_name(call, {"lookup": "查询"})

        self.assertEqual(enriched, {**call, "display_name": "查询"})
        self.assertEqual(call.get("display_name"), None)
        self.assertEqual(
            enrich_tool_event_display_name({"type": "text", "delta": "ok"}, {}),
            {"type": "text", "delta": "ok"},
        )

        deferred = {
            "calls": [{"tool": "lookup", "tool_call_id": "call-1"}],
            "approvals": [{"tool": "unknown", "tool_call_id": "call-2"}],
        }
        self.assertEqual(
            enrich_deferred_display_names(deferred, {"lookup": "查询"}),
            {
                "calls": [
                    {
                        "tool": "lookup",
                        "tool_call_id": "call-1",
                        "display_name": "查询",
                    }
                ],
                "approvals": [
                    {
                        "tool": "unknown",
                        "tool_call_id": "call-2",
                        "display_name": "unknown",
                    }
                ],
            },
        )


class HistoryDisplayNameTests(unittest.TestCase):
    def test_history_is_decorated_from_current_map_without_mutating_projection(self) -> None:
        now = datetime(2026, 9, 3, tzinfo=timezone.utc)
        history = ConversationHistoryResponse(
            session_id="session-1",
            items=[
                ToolConversationItem(
                    item_id="tool:call-1",
                    status="completed",
                    order=ConversationOrder(sequence=1),
                    source_event_ids=["event-1"],
                    created_at=now,
                    updated_at=now,
                    call_id="call-1",
                    tool_name="lookup",
                ),
                PlanConversationItem(
                    item_id="plan:call-2",
                    status="completed",
                    order=ConversationOrder(sequence=2),
                    source_event_ids=["event-2"],
                    created_at=now,
                    updated_at=now,
                    call_id="call-2",
                ),
            ],
        )

        first = decorate_history_display_names(
            history,
            {"lookup": "查询数据", "write_plan": "任务计划"},
        )
        second = decorate_history_display_names(
            history,
            {"lookup": "读取数据", "write_plan": "任务计划"},
        )

        self.assertIsNone(history.items[0].display_name)
        self.assertEqual(first.items[0].display_name, "查询数据")
        self.assertEqual(second.items[0].display_name, "读取数据")
        self.assertEqual(first.items[1].display_name, "任务计划")


if __name__ == "__main__":
    unittest.main()
