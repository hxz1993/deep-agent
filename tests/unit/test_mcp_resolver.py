from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.mcp.resolver import (
    effective_mcp_capability_names,
    load_mcp_tool_packages,
    resolve_mcp_toolsets,
    select_mcp_tool_packages,
)
from deep_agent.capabilities.registry import ToolPackage
from deep_agent.db.stores.bundle import DataStores


def _package(code: str, *, source: str | None = None) -> ToolPackage:
    return ToolPackage(
        code=code,
        category="mcp",
        toolset=object(),
        source=source,
    )


class McpResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_mcp_packages_reads_from_store(self) -> None:
        row = MagicMock(name="mcp-row")
        row.name = "echo"
        mcp_store = MagicMock()
        mcp_store.list_all = AsyncMock(return_value=[row])
        stores = DataStores(
            chat_session=MagicMock(),
            events=MagicMock(),
            coordinator=MagicMock(),
            agent=MagicMock(),
            llm_model=MagicMock(),
            llm_configuration=MagicMock(),
            model_catalog=MagicMock(),
            mcp=mcp_store,
            ui_tool=MagicMock(),
            ui_tool_package=MagicMock(),
            skill_package=MagicMock(),
        )
        with patch(
            "deep_agent.capabilities.mcp.resolver.tool_packages_from_rows",
            return_value=[_package("mcp:echo")],
        ) as from_rows, patch(
            "deep_agent.capabilities.mcp.resolver.load_builtin_mcp_tool_packages",
            return_value=[],
        ):
            packages = await load_mcp_tool_packages(stores)
        from_rows.assert_called_once_with([row])
        self.assertEqual([package.code for package in packages], ["mcp:echo"])

    def test_explicit_selection(self) -> None:
        packages = [_package("mcp:echo"), _package("mcp:crm")]
        spec = AgentSpec(
            name="t", model="test", instructions="", capabilities=["mcp:echo"]
        )
        selected = select_mcp_tool_packages(packages, spec)
        self.assertEqual([package.code for package in selected], ["mcp:echo"])

    def test_empty_selection_excludes_mcp(self) -> None:
        spec = AgentSpec(name="t", model="test", instructions="", capabilities=[])
        self.assertEqual(select_mcp_tool_packages([_package("mcp:echo")], spec), [])

    def test_effective_names_use_package_codes(self) -> None:
        packages = [_package("mcp:echo"), _package("mcp:crm")]
        spec = AgentSpec(
            name="t",
            model="test",
            instructions="",
            capabilities=["mcp:echo", "builtin:general"],
        )
        self.assertEqual(effective_mcp_capability_names(spec, packages), {"echo"})

    async def test_runtime_hides_url_mcp_without_snapshot(self) -> None:
        package = _package("mcp:echo", source="database")
        spec = AgentSpec(
            name="t", model="test", instructions="", capabilities=["mcp:echo"]
        )
        row = SimpleNamespace(
            name="echo",
            transport="url",
            inspection={"tools": {"status": "supported", "count": 2}},
        )
        with patch(
            "deep_agent.capabilities.mcp.resolver.load_mcp_tool_packages",
            new=AsyncMock(return_value=[package]),
        ):
            resolved = await resolve_mcp_toolsets(
                MagicMock(),
                spec,
                mcp_rows=[row],
            )
        self.assertEqual(resolved, [])

    async def test_runtime_uses_url_mcp_with_ready_snapshot(self) -> None:
        package = _package("mcp:echo", source="database")
        spec = AgentSpec(
            name="t", model="test", instructions="", capabilities=["mcp:echo"]
        )
        row = SimpleNamespace(
            name="echo",
            transport="url",
            inspection={
                "tools": {
                    "status": "supported",
                    "count": 1,
                    "snapshot_status": "ready",
                    "snapshot": {
                        "schema_version": "1.0",
                        "definitions": [
                            {"name": "query", "input_schema": {"type": "object"}}
                        ],
                    },
                }
            },
        )
        with patch(
            "deep_agent.capabilities.mcp.resolver.load_mcp_tool_packages",
            new=AsyncMock(return_value=[package]),
        ):
            resolved = await resolve_mcp_toolsets(
                MagicMock(),
                spec,
                mcp_rows=[row],
            )
        self.assertEqual(resolved, [package.toolset])


if __name__ == "__main__":
    unittest.main()
