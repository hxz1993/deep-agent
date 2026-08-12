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
            ["runtime:skill", "runtime:file", "runtime:mcp-tool-search", "runtime:mcp-resource"],
        )
        self.assertEqual(
            [[tool.name for tool in domain.tools] for domain in domains],
            [
                ["load_capability", "list_skill_resources", "execute_skill_script"],
                ["read_file"],
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


if __name__ == "__main__":
    unittest.main()
