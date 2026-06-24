from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.manifest import parse_manifest_payload
from deep_agent.capabilities.ui.registry import (
    build_external_toolset,
    is_tool_visible,
    load_effective_ui_tools,
    merge_definitions,
    prepare_ui_toolset_for_spec,
)
from deep_agent.capabilities.ui.types import UIToolRecord
from deep_agent.db.models import Base, McpServer, UiToolDefinition
from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.db.stores.ui_tool import UiToolStoreBase, create_ui_tool_store
from deep_agent.schemas.ui_tools import UiToolCreate


class MergeDefinitionsTests(unittest.TestCase):
    def test_manual_wins_over_mcp(self) -> None:
        manual = UIToolRecord(
            name="acme__pick_date",
            description="manual",
            parameters_json_schema={"type": "object"},
            source="manual",
        )
        mcp = UIToolRecord(
            name="acme__pick_date",
            description="mcp",
            parameters_json_schema={"type": "object"},
            source="mcp",
            mcp_server_name="acme",
        )
        merged, shadowed = merge_definitions(builtin_ui_records(), [mcp], [manual])
        self.assertEqual(len(merged), 3)
        winner = next(item for item in merged if item.name == "acme__pick_date")
        self.assertEqual(winner.source, "manual")
        self.assertEqual(shadowed["acme__pick_date"], "mcp")


class ManifestParseTests(unittest.TestCase):
    def test_parse_manifest_assigns_canonical_names(self) -> None:
        payload = {
            "protocol_version": "1.0",
            "tools": [
                {
                    "name": "pick_date",
                    "description": "Pick a date",
                    "parameters_json_schema": {"type": "object", "properties": {}},
                    "widget": "date_picker",
                }
            ],
        }
        rows = parse_manifest_payload(payload, mcp_server_name="acme")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "acme__pick_date")
        self.assertEqual(rows[0].source, "mcp")


class UiToolStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.stores = create_data_stores(DbDialect.SQLITE, self.session_factory)
        self.store: UiToolStoreBase = self.stores.ui_tool

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_list_all_scopes_mcp_server_names(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            db.add_all(
                [
                    UiToolDefinition(
                        name="acme__tool",
                        description="acme",
                        parameters_json_schema={"type": "object"},
                        source="mcp",
                        mcp_server_name="acme",
                        enabled=True,
                        created_at=now,
                        updated_at=now,
                    ),
                    UiToolDefinition(
                        name="crm__tool",
                        description="crm",
                        parameters_json_schema={"type": "object"},
                        source="mcp",
                        mcp_server_name="crm",
                        enabled=True,
                        created_at=now,
                        updated_at=now,
                    ),
                    UiToolDefinition(
                        name="demo__manual",
                        description="manual",
                        parameters_json_schema={"type": "object"},
                        source="manual",
                        enabled=True,
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            await db.commit()

        rows = await self.store.list_all(enabled=True, mcp_server_names={"acme"})
        names = {row.name for row in rows}
        self.assertEqual(names, {"acme__tool", "demo__manual"})

    async def test_list_all_filters_by_agent_id(self) -> None:
        agent_a = "00000000-0000-4000-8000-000000000001"
        agent_b = "00000000-0000-4000-8000-000000000002"
        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            db.add_all(
                [
                    UiToolDefinition(
                        name="global__tool",
                        description="all agents",
                        parameters_json_schema={"type": "object"},
                        source="manual",
                        enabled=True,
                        created_at=now,
                        updated_at=now,
                    ),
                    UiToolDefinition(
                        name="scoped__tool",
                        description="agent a only",
                        parameters_json_schema={"type": "object"},
                        source="manual",
                        enabled=True,
                        agent_ids=[agent_a],
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            await db.commit()

        rows = await self.store.list_all(enabled=True, agent_id=agent_a)
        names = {row.name for row in rows}
        self.assertEqual(names, {"global__tool", "scoped__tool"})

        rows_b = await self.store.list_all(enabled=True, agent_id=agent_b)
        self.assertEqual({row.name for row in rows_b}, {"global__tool"})

    async def test_create_manual_and_list(self) -> None:
        row = await self.store.create_manual(
            UiToolCreate(
                name="myapp__custom_form",
                description="Custom form",
                parameters_json_schema={"type": "object", "properties": {}},
                widget="custom",
            ),
        )
        rows = await self.store.list_all(source="manual")
        self.assertEqual(row.name, "myapp__custom_form")
        self.assertEqual(len(rows), 1)

    async def test_effective_tools_include_manual_for_agent(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            db.add(
                UiToolDefinition(
                    name="demo__pick_color",
                    description="Pick color",
                    parameters_json_schema={"type": "object", "properties": {}},
                    source="manual",
                    enabled=True,
                    override=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()

        spec = AgentSpec(
            name="demo",
            model="test",
            instructions="hi",
            capabilities=["ui"],
            agent_id="00000000-0000-4000-8000-000000000001",
        )
        effective = await load_effective_ui_tools(self.stores, spec, mcp_enabled_names=set())
        toolset, hints = await prepare_ui_toolset_for_spec(self.stores, spec)

        names = {item.name for item in effective.definitions}
        self.assertIn("confirm_action", names)
        self.assertIn("demo__pick_color", names)
        self.assertTrue(effective.has_dynamic)
        self.assertIsNotNone(toolset)
        self.assertIn("demo__pick_color", hints)


class DiscoveryPolicyTests(unittest.TestCase):
    def test_non_builtin_mcp_names_from_capabilities(self) -> None:
        from deep_agent.capabilities.mcp.ui_discovery import non_builtin_mcp_names_for_spec

        self.assertEqual(non_builtin_mcp_names_for_spec(None), set())
        self.assertEqual(non_builtin_mcp_names_for_spec([]), set())
        self.assertEqual(
            non_builtin_mcp_names_for_spec(["native", "mcp:acme", "mcp:crm"]),
            {"acme", "crm"},
        )

    def test_builtin_mcp_row_skips_discovery(self) -> None:
        from deep_agent.capabilities.mcp.builtin_config import is_builtin_mcp_row

        self.assertTrue(is_builtin_mcp_row("text_tools", "builtin", {}))
        self.assertFalse(is_builtin_mcp_row("acme", "url", {"url": "http://localhost/mcp"}))


class VisibilityTests(unittest.TestCase):
    def test_mcp_tool_hidden_without_capability(self) -> None:
        record = UIToolRecord(
            name="acme__pick_date",
            description="x",
            parameters_json_schema={},
            source="mcp",
            mcp_server_name="acme",
            enabled=True,
        )
        self.assertFalse(
            is_tool_visible(
                record,
                agent_id=None,
                capabilities=["native"],
                mcp_enabled_names={"acme"},
            )
        )
        self.assertTrue(
            is_tool_visible(
                record,
                agent_id=None,
                capabilities=["mcp:acme"],
                mcp_enabled_names={"acme"},
            )
        )


class ExternalToolsetTests(unittest.TestCase):
    def test_build_external_toolset_preserves_names(self) -> None:
        toolset = build_external_toolset(builtin_ui_records())
        self.assertEqual(toolset.id, "ui-dynamic")


if __name__ == "__main__":
    unittest.main()
