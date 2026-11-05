from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import ValidationError

from deep_agent.agent.spec import AgentSpec
from deep_agent.agent.structured_output import StructuredOutputState
from deep_agent.agent_packages.archive import build_zip, parse_package
from deep_agent.agent_packages.service import preview_from_snapshot
from deep_agent.api.v1.agents import _validate_output_name
from deep_agent.api.v1.ui_tools import update_ui_tool
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.ui.registry import prepare_ui_tool_packages_for_spec
from deep_agent.core.exceptions import ToolPackageError
from deep_agent.db.models import (
    Agent, AgentOutputConfig as AgentOutputConfigRow, LlmModel, LlmProvider, McpServer,
    SkillPackage, UiToolDefinition, UiToolPackage,
)
from deep_agent.schemas.agent import AgentOutputConfig
from deep_agent.schemas.ui_tools import UiToolCreate, UiToolPackageCreate, UiToolUpdate
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.unit.test_agent_packages import make_package, package_files


CONTRACT = {"name": "get_current_time", "description": "fixture", "json_schema": {"type": "object", "properties": {}}}


class RuntimeTimeCollisionSchemaTests(unittest.TestCase):
    def test_new_ui_and_uploaded_agent_package_reject_default_time_name(self):
        with self.assertRaisesRegex(ValidationError, "默认运行时工具冲突"):
            UiToolCreate(name="get_current_time", package_code="ui:collision", description="fixture", parameters_json_schema={"type": "object"})
        files = package_files(resources=True)
        ui = json.loads(files["ui-tools.json"])
        ui[0]["tools"][0]["name"] = "get_current_time"
        files["ui-tools.json"] = json.dumps(ui).encode()
        with self.assertRaisesRegex(ValueError, "默认运行时工具冲突"):
            parse_package(build_zip(files))

    def test_legacy_output_contract_fails_clearly_before_sdk_dispatch(self):
        with self.assertRaisesRegex(ValueError, "get_current_time.*默认运行时工具冲突"):
            StructuredOutputState(CONTRACT)

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

    async def test_legacy_ui_tool_update_is_rejected_without_overwriting_record(self):
        with self.assertRaises(HTTPException) as failure:
            await update_ui_tool("get_current_time", UiToolUpdate(description="changed"), self.stores.ui_tool, self.stores.ui_tool_package)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertIn("默认运行时工具冲突", failure.exception.detail)
        self.assertEqual((await self.stores.ui_tool.get_by_name("get_current_time")).description, "legacy custom tool")

    async def test_selected_legacy_ui_fails_clearly_unselected_record_does_not_affect_agent(self):
        spec = AgentSpec(name="fixture", model="test", instructions="", capabilities=["ui:collision"])
        with self.assertRaisesRegex(ToolPackageError, "get_current_time.*默认运行时工具冲突"):
            await prepare_ui_tool_packages_for_spec(self.stores, spec)
        spec.capabilities = []
        packages, _ = await prepare_ui_tool_packages_for_spec(self.stores, spec)
        self.assertEqual(packages, [])

    async def test_output_save_and_agent_package_preview_reject_default_time_name(self):
        ctx = SimpleNamespace(registry=build_registry(), stores=self.stores)
        with self.assertRaises(HTTPException) as failure:
            await _validate_output_name(AgentOutputConfig(**CONTRACT), [], ctx)
        self.assertEqual(failure.exception.status_code, 422)
        self.assertIn("get_current_time", failure.exception.detail)
        package = make_package(agent_changes={"structured_output_enabled": True, "output_config": CONTRACT})
        data = {model: [] for model in (Agent, AgentOutputConfigRow, LlmModel, LlmProvider, McpServer, SkillPackage, UiToolDefinition, UiToolPackage)}
        preview = preview_from_snapshot(ctx, package, data, "create", None)
        self.assertTrue(any("get_current_time" in message and "默认运行时工具冲突" in message for message in preview["blocked"]))


if __name__ == "__main__":
    unittest.main()
