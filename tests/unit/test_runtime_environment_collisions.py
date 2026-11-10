from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from deep_agent.agent.spec import AgentSpec
from deep_agent.agent.structured_output import StructuredOutputState
from deep_agent.agent_packages.archive import build_zip, parse_package
from deep_agent.agent_packages.service import preview_from_snapshot
from deep_agent.api.v1.ui_tools import update_ui_tool
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.ui.registry import prepare_ui_tool_packages_for_spec
from deep_agent.db.models import (
    Agent, AgentOutputConfig as AgentOutputConfigRow, LlmModel, LlmProvider, McpServer,
    SkillPackage, UiToolDefinition, UiToolPackage,
)
from deep_agent.schemas.ui_tools import UiToolCreate, UiToolPackageCreate, UiToolUpdate
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.unit.test_agent_packages import make_package, package_files


CONTRACT = {"name": "get_current_time", "description": "fixture", "json_schema": {"type": "object", "properties": {}}}


class RuntimeTimeCollisionSchemaTests(unittest.TestCase):
    def test_new_ui_and_agent_package_keep_default_time_business_name(self):
        tool = UiToolCreate(name="get_current_time", package_code="ui:collision", description="fixture",
                            parameters_json_schema={"type": "object"})
        self.assertEqual(tool.name, "get_current_time")
        files = package_files(resources=True)
        ui = json.loads(files["ui-tools.json"])
        ui[0]["tools"][0]["name"] = "get_current_time"
        files["ui-tools.json"] = json.dumps(ui).encode()
        package = parse_package(build_zip(files))
        self.assertEqual(package.ui[0].tools[0].name, "get_current_time")

    def test_legacy_output_contract_keeps_name_and_uses_an_output_alias(self):
        state = StructuredOutputState(CONTRACT)
        self.assertEqual(state.contract["name"], "get_current_time")
        self.assertEqual(state.tool_name.wire, "output__get_current_time")

    def test_new_reservation_does_not_expand_other_runtime_name_policy(self):
        tool = UiToolCreate(name="fixture_time", package_code="ui:collision", description="fixture", parameters_json_schema={"type": "object"})
        self.assertEqual(tool.name, "fixture_time")
        # Existing unrelated naming policy is unchanged by this migration.
        self.assertEqual(UiToolCreate(name="read_file", package_code="ui:collision", description="fixture", parameters_json_schema={"type": "object"}).name, "read_file")


class RuntimeTimeCollisionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, self.factory = await make_sqlite_stores()
        await self.stores.ui_tool_package.create_manual(UiToolPackageCreate(code="ui:collision", label="fixture"))
        async with self.factory() as db:
            db.add(UiToolDefinition(name="get_current_time", package_code="ui:collision", description="legacy custom tool", parameters_json_schema={"type": "object"}, source="manual"))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_legacy_ui_tool_update_preserves_business_identity(self):
        await update_ui_tool("get_current_time", UiToolUpdate(description="changed"),
                             self.stores.ui_tool, self.stores.ui_tool_package)
        row = await self.stores.ui_tool.get_by_name("get_current_time")
        self.assertEqual((row.name, row.description), ("get_current_time", "changed"))

    async def test_legacy_ui_is_aliased_only_when_selected(self):
        spec = AgentSpec(name="fixture", model="test", instructions="", capabilities=["ui:collision"])
        packages, hints = await prepare_ui_tool_packages_for_spec(self.stores, spec)
        self.assertEqual(packages[0].tools[0].name, "get_current_time")
        self.assertEqual(hints["get_current_time"]["wire_name"], "ui__collision__get_current_time")
        spec.capabilities = []
        packages, _ = await prepare_ui_tool_packages_for_spec(self.stores, spec)
        self.assertEqual(packages, [])

    async def test_agent_package_preview_allows_output_with_default_time_business_name(self):
        ctx = SimpleNamespace(registry=build_registry(), stores=self.stores)
        package = make_package(agent_changes={"structured_output_enabled": True, "output_config": CONTRACT})
        data = {model: [] for model in (Agent, AgentOutputConfigRow, LlmModel, LlmProvider, McpServer, SkillPackage, UiToolDefinition, UiToolPackage)}
        preview = preview_from_snapshot(ctx, package, data, "create", None)
        self.assertEqual(preview["blocked"], [])


if __name__ == "__main__":
    unittest.main()
