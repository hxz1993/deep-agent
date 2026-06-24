from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.mcp.resolver import (
    effective_mcp_capability_names,
    effective_mcp_names_for_ui,
    effective_mcp_rows_for_discovery,
    load_mcp_capabilities,
    select_mcp_capabilities,
)
from deep_agent.capabilities.registry import Capability
from deep_agent.db.stores.bundle import DataStores


class McpResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_mcp_capabilities_returns_empty_when_no_store(self) -> None:
        stores = DataStores(
            chat_session=None,
            message=None,
            coordinator=MagicMock(),
            agent=None,
            mcp=None,
            ui_tool=None,
        )
        self.assertEqual(await load_mcp_capabilities(stores), [])

    async def test_load_mcp_capabilities_reads_from_store(self) -> None:
        row = MagicMock()
        row.name = "echo"
        row.enabled = True
        mcp_store = MagicMock()
        mcp_store.list_all = AsyncMock(return_value=[row])
        stores = DataStores(
            chat_session=None,
            message=None,
            coordinator=MagicMock(),
            agent=None,
            mcp=mcp_store,
            ui_tool=None,
        )
        with unittest.mock.patch(
            "deep_agent.capabilities.mcp.resolver.capabilities_from_rows",
            return_value=[
                Capability(name="mcp:echo", kind="mcp", toolset=object(), default_enabled=False)
            ],
        ) as from_rows, unittest.mock.patch(
            "deep_agent.capabilities.mcp.resolver.load_builtin_mcp_capabilities",
            return_value=[],
        ):
            caps = await load_mcp_capabilities(stores)
        from_rows.assert_called_once_with([row])
        self.assertEqual(len(caps), 1)
        self.assertEqual(caps[0].name, "mcp:echo")

    def test_select_mcp_capabilities_honors_explicit_names(self) -> None:
        caps = [
            Capability(name="mcp:echo", kind="mcp", toolset=object(), default_enabled=False),
            Capability(name="mcp:crm", kind="mcp", toolset=object(), default_enabled=False),
        ]
        spec = AgentSpec(name="t", model="test", instructions="", capabilities=["mcp:echo"])
        selected = select_mcp_capabilities(caps, spec)
        self.assertEqual([c.name for c in selected], ["mcp:echo"])

    def test_select_mcp_capabilities_excludes_non_default_when_capabilities_none(self) -> None:
        caps = [
            Capability(name="mcp:echo", kind="mcp", toolset=object(), default_enabled=False),
        ]
        spec = AgentSpec(name="t", model="test", instructions="", capabilities=None)
        self.assertEqual(select_mcp_capabilities(caps, spec), [])

    def test_effective_mcp_capability_names_scopes_explicit_capabilities(self) -> None:
        caps = [
            Capability(name="mcp:echo", kind="mcp", toolset=object(), default_enabled=False),
            Capability(name="mcp:crm", kind="mcp", toolset=object(), default_enabled=False),
        ]
        spec = AgentSpec(name="t", model="test", instructions="", capabilities=["mcp:echo", "native"])
        self.assertEqual(effective_mcp_capability_names(spec, caps), {"echo"})

    def test_effective_mcp_names_for_ui_uses_all_enabled_when_capabilities_none(self) -> None:
        caps = [
            Capability(name="mcp:echo", kind="mcp", toolset=object(), default_enabled=False),
        ]
        rows = [unittest.mock.MagicMock(name="echo", enabled=True)]
        rows[0].name = "echo"
        rows[0].enabled = True
        spec = AgentSpec(name="t", model="test", instructions="", capabilities=None)
        self.assertEqual(effective_mcp_names_for_ui(spec, caps, rows), {"echo"})

    def test_effective_mcp_rows_for_discovery_only_explicit_mcp_capabilities(self) -> None:
        row_acme = unittest.mock.MagicMock()
        row_acme.name = "acme"
        row_acme.enabled = True
        row_crm = unittest.mock.MagicMock()
        row_crm.name = "crm"
        row_crm.enabled = True
        spec = AgentSpec(name="t", model="test", instructions="", capabilities=["mcp:acme"])
        scoped = effective_mcp_rows_for_discovery(spec, [row_acme, row_crm])
        self.assertEqual([row.name for row in scoped], ["acme"])


if __name__ == "__main__":
    unittest.main()
