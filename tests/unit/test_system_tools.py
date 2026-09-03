from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from deep_agent.api.v1.system_tools import list_system_tools
from deep_agent.api.v1.tools import list_tool_packages
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.system_tools import list_system_tool_domains


class SystemToolCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_returns_stable_domain_and_tool_order(self) -> None:
        domains = await list_system_tools()

        self.assertEqual(
            [domain.code for domain in domains],
            [
                "runtime:planning",
                "runtime:conversation-history",
                "runtime:skill",
                "runtime:file",
                "runtime:tool-result",
                "runtime:mcp-tool-search",
                "runtime:mcp-resource",
            ],
        )
        self.assertEqual(
            [[tool.name for tool in domain.tools] for domain in domains],
            [
                ["write_plan"],
                ["search_conversation_history", "read_conversation_event"],
                ["load_capability", "list_skill_resources", "execute_skill_script"],
                ["read_file", "export_file_to_download_center"],
                ["read_tool_result"],
                ["search_tools"],
                [
                    "list_mcp_resources",
                    "list_mcp_resource_templates",
                    "read_mcp_resource",
                ],
            ],
        )

    async def test_catalog_explains_source_condition_and_description(self) -> None:
        for domain in list_system_tool_domains():
            self.assertTrue(domain.label)
            self.assertTrue(domain.description)
            for tool in domain.tools:
                self.assertTrue(tool.display_name)
                self.assertTrue(tool.source)
                self.assertTrue(tool.injection_condition)
                self.assertTrue(tool.description)

    async def test_runtime_catalog_is_not_agent_capability_registry(self) -> None:
        registry = build_registry()
        self.assertFalse(any(code.startswith("runtime:") for code in registry.codes()))

    async def test_runtime_catalog_is_not_tool_package_api(self) -> None:
        ctx = SimpleNamespace(
            registry=build_registry(),
            stores=SimpleNamespace(
                mcp=SimpleNamespace(list_all=AsyncMock(return_value=[])),
                ui_tool=SimpleNamespace(list_all=AsyncMock(return_value=[])),
                ui_tool_package=SimpleNamespace(list_all=AsyncMock(return_value=[])),
            ),
        )

        packages = await list_tool_packages(ctx)

        self.assertFalse(any(item.code.startswith("runtime:") for item in packages))

    async def test_tool_package_api_exposes_only_current_product_builtin_mcp(self) -> None:
        ctx = SimpleNamespace(
            registry=build_registry(),
            stores=SimpleNamespace(
                mcp=SimpleNamespace(list_all=AsyncMock(return_value=[])),
                ui_tool=SimpleNamespace(list_all=AsyncMock(return_value=[])),
                ui_tool_package=SimpleNamespace(list_all=AsyncMock(return_value=[])),
            ),
        )

        packages = await list_tool_packages(ctx)
        mcp_codes = [item.code for item in packages if item.category == "mcp"]
        retired_code = "mcp:" + "_".join(("doc", "search"))

        self.assertEqual(mcp_codes, ["mcp:deepcube"])
        self.assertNotIn(retired_code, mcp_codes)

    async def test_ten_tool_mcp_is_reported_as_direct_disclosure(self) -> None:
        row = SimpleNamespace(
            name="small_docs",
            transport="url",
            inspection={
                "status": "healthy",
                "tools": {
                    "status": "supported",
                    "count": 10,
                    "snapshot_status": "ready",
                },
            },
            description="Small documentation MCP",
        )
        ctx = SimpleNamespace(
            registry=build_registry(),
            stores=SimpleNamespace(
                mcp=SimpleNamespace(list_all=AsyncMock(return_value=[row])),
                ui_tool=SimpleNamespace(list_all=AsyncMock(return_value=[])),
                ui_tool_package=SimpleNamespace(list_all=AsyncMock(return_value=[])),
            ),
        )

        packages = await list_tool_packages(ctx)
        package = next(item for item in packages if item.code == "mcp:small_docs")

        self.assertEqual(package.disclosure_mode, "direct")
        self.assertIsNone(package.preselect_limit)
        self.assertIsNone(package.search_result_limit)


if __name__ == "__main__":
    unittest.main()
