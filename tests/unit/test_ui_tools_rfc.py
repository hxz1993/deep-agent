from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from pydantic import ValidationError
from pydantic_ai import Agent, DeferredToolRequests, ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deep_agent.agent.history import (
    HistoryMessage,
    HistoryToolCallPart,
    HistoryToolResultPart,
    enrich_history_messages,
)
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.registry import (
    build_external_toolset,
    build_tool_hints,
    enrich_deferred,
    enrich_tool_event,
    is_tool_visible,
    load_effective_ui_tools,
    load_selectable_ui_tools,
    merge_definitions,
    prepare_ui_tool_packages_for_spec,
)
from deep_agent.capabilities.ui.types import UIToolRecord
from deep_agent.db.models import Base, McpServer, UiToolDefinition, UiToolPackage
from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.db.stores.ui_tool import UiToolStoreBase
from deep_agent.schemas.ui_tools import (
    UiToolCreate,
    UiToolPackageCreate,
    UiToolPackageUpdate,
    UiToolUpdate,
)
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.catalog import invalid_tool_package_codes
from deep_agent.api.v1.tools import list_tool_packages
from deep_agent.api.v1.ui_tools import delete_ui_tool, get_ui_tool, list_ui_tools, update_ui_tool
from deep_agent.api.v1.ui_tool_packages import (
    create_ui_tool_package,
    delete_ui_tool_package,
    list_ui_tool_packages,
    update_ui_tool_package,
)


class MergeDefinitionsTests(unittest.TestCase):
    def test_builtin_wins_over_conflicting_manual_definition(self) -> None:
        manual = UIToolRecord(
            name="builtin__confirm",
            package_code="ui:demo",
            description="manual",
            parameters_json_schema={"type": "object"},
            source="manual",
        )
        merged, shadowed = merge_definitions(builtin_ui_records(), [manual])
        winner = next(item for item in merged if item.name == "builtin__confirm")
        self.assertEqual(winner.source, "builtin")
        self.assertEqual(shadowed["builtin__confirm"], "manual")


class UiToolSchemaValidationTests(unittest.TestCase):
    def test_create_and_update_reject_invalid_json_schema(self) -> None:
        invalid_schema = {"type": 123}

        with self.assertRaises(ValidationError):
            UiToolCreate(
                name="demo__invalid",
                package_code="ui:demo",
                description="invalid",
                parameters_json_schema=invalid_schema,
            )
        with self.assertRaises(ValidationError):
            UiToolUpdate(parameters_json_schema=invalid_schema)
    def test_ui_tool_api_schemas_do_not_expose_agent_ids(self) -> None:
        from deep_agent.schemas.ui_tools import UiToolResponse

        self.assertNotIn("agent_ids", UiToolCreate.model_fields)
        self.assertNotIn("agent_ids", UiToolUpdate.model_fields)
        self.assertNotIn("agent_ids", UiToolResponse.model_fields)

    def test_new_builtin_names_are_reserved_but_legacy_names_are_available(self) -> None:
        for name in ("builtin__confirm", "builtin__choice"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                UiToolCreate(
                    name=name,
                    package_code="ui:demo",
                    description="reserved",
                    parameters_json_schema={"type": "object"},
                )
        for name in ("confirm_action", "ask_user_choice"):
            with self.subTest(name=name):
                created = UiToolCreate(
                    name=name,
                    package_code="ui:demo",
                    description="legacy name is no longer builtin",
                    parameters_json_schema={"type": "object"},
                )
                self.assertEqual(created.name, name)


class UiToolStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.stores = create_data_stores(DbDialect.SQLITE, self.session_factory)
        self.store: UiToolStoreBase = self.stores.ui_tool
        async with self.session_factory() as db:
            db.add(
                UiToolPackage(
                    code="ui:demo",
                    label="Demo",
                    description="Demo UI package",
                    source="manual",
                    enabled=True,
                )
            )
            await db.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_create_manual_and_list(self) -> None:
        row = await self.store.create_manual(
            UiToolCreate(
                name="myapp__custom_form",
                package_code="ui:demo",
                description="Custom form",
                parameters_json_schema={"type": "object", "properties": {}},
                widget="custom",
            ),
        )
        rows = await self.store.list_all(source="manual")
        self.assertEqual(row.name, "myapp__custom_form")
        self.assertEqual(len(rows), 1)

    async def test_ui_tool_package_crud_and_builtin_protection(self) -> None:
        listed = await list_ui_tool_packages(store=self.stores.ui_tool_package)
        self.assertEqual([item.code for item in listed.items], ["ui:builtin", "ui:demo"])

        created = await create_ui_tool_package(
            UiToolPackageCreate(
                code="ui:spreadsheet",
                label="Spreadsheet",
                description="Spreadsheet interactions",
            ),
            store=self.stores.ui_tool_package,
        )
        self.assertEqual(created.code, "ui:spreadsheet")
        updated = await update_ui_tool_package(
            "ui:spreadsheet",
            UiToolPackageUpdate(label="电子表格", enabled=False),
            store=self.stores.ui_tool_package,
        )
        self.assertEqual(updated.label, "电子表格")
        self.assertFalse(updated.enabled)
        await delete_ui_tool_package(
            "ui:spreadsheet",
            store=self.stores.ui_tool_package,
            agent_store=self.stores.agent,
        )
        self.assertIsNone(await self.stores.ui_tool_package.get_by_code("ui:spreadsheet"))

        with self.assertRaisesRegex(Exception, "cannot be modified"):
            await update_ui_tool_package(
                "ui:builtin",
                UiToolPackageUpdate(label="changed"),
                store=self.stores.ui_tool_package,
            )
        with self.assertRaisesRegex(Exception, "cannot be deleted"):
            await delete_ui_tool_package(
                "ui:builtin",
                store=self.stores.ui_tool_package,
                agent_store=self.stores.agent,
            )

    async def test_ui_tool_package_with_tools_cannot_be_deleted(self) -> None:
        await self.store.create_manual(
            UiToolCreate(
                name="demo__protected",
                package_code="ui:demo",
                description="Protected",
                parameters_json_schema={"type": "object"},
            )
        )
        with self.assertRaisesRegex(Exception, "contains tools|包含工具"):
            await delete_ui_tool_package(
                "ui:demo",
                store=self.stores.ui_tool_package,
                agent_store=self.stores.agent,
            )

    async def test_management_list_includes_read_only_builtin_tools(self) -> None:
        response = await list_ui_tools(
            store=self.store,
            source=None,
            enabled=None,
        )

        by_name = {item.name: item for item in response.items}
        self.assertEqual(set(by_name), {"builtin__choice", "builtin__confirm"})
        self.assertTrue(all(item.source == "builtin" for item in by_name.values()))
        self.assertTrue(all(item.enabled for item in by_name.values()))
        self.assertTrue(all(item.created_at is None for item in by_name.values()))
        self.assertTrue(all(item.updated_at is None for item in by_name.values()))

    async def test_management_list_filters_builtin_tools(self) -> None:
        builtin_response = await list_ui_tools(
            store=self.store,
            source="builtin",
            enabled=True,
        )
        disabled_response = await list_ui_tools(
            store=self.store,
            source=None,
            enabled=False,
        )

        self.assertEqual({item.name for item in builtin_response.items}, {"builtin__choice", "builtin__confirm"})
        self.assertEqual(disabled_response.items, [])

    async def test_management_detail_reads_builtin_without_database_row(self) -> None:
        response = await get_ui_tool("builtin__confirm", store=self.store)

        self.assertEqual(response.name, "builtin__confirm")
        self.assertEqual(response.source, "builtin")
        self.assertEqual(response.widget, "confirm")
        self.assertEqual(response.result_schema["required"], ["confirmed"])
        self.assertIsNone(response.metadata)
        self.assertIsNone(response.created_at)
        self.assertIsNone(response.updated_at)

    async def test_builtin_tools_cannot_be_updated_or_deleted_without_database_rows(self) -> None:
        for name in ("builtin__confirm", "builtin__choice"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(Exception, "cannot be modified"):
                    await update_ui_tool(
                        name,
                        UiToolUpdate(description="changed"),
                        store=self.store,
                        package_store=self.stores.ui_tool_package,
                    )
                with self.assertRaisesRegex(Exception, "cannot be deleted"):
                    await delete_ui_tool(
                        name,
                        store=self.store,
                        agent_store=self.stores.agent,
                    )

    async def test_effective_tools_include_manual_for_agent(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            db.add(
                    UiToolDefinition(
                        name="demo__pick_color",
                        package_code="ui:demo",
                    description="Pick color",
                    parameters_json_schema={"type": "object", "properties": {}},
                    source="manual",
                    enabled=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()

        spec = AgentSpec(
            name="demo",
            model="test",
            instructions="hi",
            capabilities=["ui:builtin", "ui:demo"],
            agent_id="00000000-0000-4000-8000-000000000001",
        )
        effective = await load_effective_ui_tools(self.stores, spec)
        packages, hints = await prepare_ui_tool_packages_for_spec(self.stores, spec)

        names = {item.name for item in effective.definitions}
        self.assertIn("builtin__confirm", names)
        self.assertIn("builtin__choice", names)
        self.assertIn("demo__pick_color", names)
        self.assertTrue(effective.has_dynamic)
        self.assertTrue(packages)
        self.assertEqual({package.code for package in packages}, {"ui:builtin", "ui:demo"})
        self.assertIn("demo__pick_color", hints)

    async def test_effective_tools_require_exact_ui_capability(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            db.add_all(
                [
                    UiToolDefinition(
                        name="demo__first",
                        package_code="ui:demo",
                        description="first",
                        parameters_json_schema={"type": "object"},
                        source="manual",
                        enabled=True,
                        created_at=now,
                        updated_at=now,
                    ),
                    UiToolDefinition(
                        name="demo__second",
                        package_code="ui:demo",
                        description="second",
                        parameters_json_schema={"type": "object"},
                        source="manual",
                        enabled=True,
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            await db.commit()

        spec = AgentSpec(
            name="demo",
            model="test",
            instructions="hi",
            capabilities=["ui:builtin", "ui:demo", "ui:missing"],
        )
        effective = await load_effective_ui_tools(self.stores, spec)
        names = {item.name for item in effective.definitions}
        self.assertIn("builtin__confirm", names)
        self.assertIn("builtin__choice", names)
        self.assertIn("demo__second", names)
        self.assertIn("demo__first", names)
        self.assertNotIn("missing__tool", names)

    async def test_null_capabilities_do_not_enable_extension_ui_tools(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            db.add(
                UiToolDefinition(
                    name="demo__hidden_by_default",
                    package_code="ui:demo",
                    description="hidden",
                    parameters_json_schema={"type": "object"},
                    source="manual",
                    enabled=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()

        spec = AgentSpec(name="demo", model="test", instructions="hi", capabilities=None)
        effective = await load_effective_ui_tools(self.stores, spec)
        names = {item.name for item in effective.definitions}
        self.assertEqual(names, set())
        self.assertFalse(effective.has_dynamic)

        empty_spec = AgentSpec(name="empty", model="test", instructions="hi", capabilities=[])
        empty_effective = await load_effective_ui_tools(self.stores, empty_spec)
        self.assertEqual(empty_effective.definitions, [])

        legacy_spec = AgentSpec(
            name="legacy",
            model="test",
            instructions="hi",
            capabilities=["ui-builtin:confirm_action", "ui-builtin:ask_user_choice"],
        )
        legacy_effective = await load_effective_ui_tools(self.stores, legacy_spec)
        self.assertEqual(legacy_effective.definitions, [])

    async def test_builtin_model_tool_list_matches_exact_agent_selection(self) -> None:
        cases = (
            (None, set()),
            ([], set()),
            (["ui:builtin"], {"builtin__confirm", "builtin__choice"}),
        )
        for capabilities, expected in cases:
            with self.subTest(capabilities=capabilities):
                spec = AgentSpec(
                    name="selection",
                    model="test",
                    instructions="hi",
                    capabilities=capabilities,
                )
                packages, hints = await prepare_ui_tool_packages_for_spec(self.stores, spec)
                self.assertEqual(set(hints), expected)
                if not expected:
                    self.assertEqual(packages, [])
                    continue
                self.assertEqual(len(packages), 1)
                tools = await packages[0].toolset.get_tools(None)
                self.assertEqual(set(tools), expected)

    def test_builtin_choice_schema_supports_general_options_and_custom_input(self) -> None:
        choice = next(item for item in builtin_ui_records() if item.name == "builtin__choice")
        properties = choice.parameters_json_schema["properties"]
        self.assertEqual(choice.parameters_json_schema["required"], ["prompt", "options"])
        self.assertEqual(properties["options"]["minItems"], 1)
        self.assertIn("oneOf", properties["options"]["items"])
        self.assertEqual(properties["custom_label"]["default"], "其他（自行输入）")
        self.assertEqual(properties["custom_placeholder"]["default"], "请输入其他内容")
        self.assertEqual(choice.result_schema["required"], ["selected", "custom_input"])

    async def test_disabled_selected_tool_is_ignored(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            db.add(
                UiToolDefinition(
                    name="demo__disabled",
                    package_code="ui:demo",
                    description="disabled",
                    parameters_json_schema={"type": "object"},
                    source="manual",
                    enabled=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()

        spec = AgentSpec(
            name="demo",
            model="test",
            instructions="hi",
            capabilities=["ui:demo"],
        )
        effective = await load_effective_ui_tools(self.stores, spec)
        self.assertNotIn("demo__disabled", {item.name for item in effective.definitions})

    async def test_tool_package_directory_groups_ui_and_reports_mcp_count_only(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            db.add_all(
                [
                    UiToolDefinition(
                        name="demo__first",
                        package_code="ui:demo",
                        description="First form",
                        parameters_json_schema={"type": "object"},
                        source="manual",
                        enabled=True,
                        created_at=now,
                        updated_at=now,
                    ),
                    UiToolDefinition(
                        name="demo__disabled",
                        package_code="ui:demo",
                        description="Disabled form",
                        parameters_json_schema={"type": "object"},
                        source="manual",
                        enabled=False,
                        created_at=now,
                        updated_at=now,
                    ),
                    McpServer(
                        name="remote_catalog",
                        transport="url",
                        config={"url": "http://127.0.0.1:8765/mcp"},
                        inspection={
                            "status": "healthy",
                            "tools": {"status": "supported", "count": 7},
                        },
                        enabled=True,
                    ),
                ]
            )
            await db.commit()

        selectable = await load_selectable_ui_tools(self.stores)
        self.assertEqual([record.name for record in selectable], ["demo__first"])

        registry = build_registry(sandbox_backend=MagicMock())
        ctx = SimpleNamespace(stores=self.stores, registry=registry)
        capabilities = await list_tool_packages(ctx)
        general_package = next(
            item for item in capabilities if item.code == "builtin:general"
        )
        sandbox_package = next(
            item for item in capabilities if item.code == "builtin:sandbox"
        )
        self.assertEqual(
            [tool.name for tool in general_package.tools],
            ["get_current_time"],
        )
        self.assertEqual(
            [tool.name for tool in sandbox_package.tools],
            ["execute_python"],
        )
        self.assertFalse(sandbox_package.default_enabled)
        attachment_capability = next(
            item for item in capabilities if item.code == "builtin:attachment"
        )
        self.assertEqual(attachment_capability.category, "builtin")
        self.assertEqual(attachment_capability.label, "附件读取")
        self.assertEqual(attachment_capability.source, "builtin")
        self.assertFalse(attachment_capability.default_enabled)
        self.assertEqual(attachment_capability.disclosure_mode, "conditional")
        self.assertEqual(
            attachment_capability.disclosure_condition,
            "session_has_attachment",
        )
        self.assertEqual(
            [tool.name for tool in attachment_capability.tools],
            ["read_text_attachment", "read_docx", "read_xlsx"],
        )
        ui_packages = [item for item in capabilities if item.category == "ui"]
        self.assertEqual([item.code for item in ui_packages], ["ui:builtin", "ui:demo"])
        self.assertEqual([item.source for item in ui_packages], ["builtin", "manual"])
        self.assertEqual([tool.name for tool in ui_packages[-1].tools], ["demo__disabled", "demo__first"])
        mcp_package = next(
            item for item in capabilities if item.code == "mcp:remote_catalog"
        )
        self.assertEqual(mcp_package.category, "mcp")
        self.assertEqual(mcp_package.tool_count, 7)
        self.assertEqual(mcp_package.tool_catalog_status, "missing")
        self.assertEqual(mcp_package.disclosure_mode, "search")
        self.assertEqual(mcp_package.preselect_limit, 3)
        self.assertEqual(mcp_package.search_result_limit, 5)
        self.assertEqual(mcp_package.tools, [])

    async def test_prepare_toolset_skips_historical_invalid_schema_only(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            db.add(
                UiToolDefinition(
                    name="demo__invalid",
                    package_code="ui:demo",
                    description="invalid",
                    parameters_json_schema={"type": 123},
                    source="manual",
                    enabled=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()

        spec = AgentSpec(
            name="demo",
            model="test",
            instructions="hi",
            capabilities=["ui:demo"],
            agent_id="00000000-0000-4000-8000-000000000001",
        )
        packages, hints = await prepare_ui_tool_packages_for_spec(self.stores, spec)

        self.assertEqual(packages, [])
        self.assertNotIn("demo__invalid", hints)

        registry = build_registry()
        self.assertEqual(
            await invalid_tool_package_codes(
                ["ui:demo"],
                registry,
                self.stores,
            ),
            ["ui:demo"],
        )

    async def test_empty_ui_package_is_visible_but_not_selectable(self) -> None:
        registry = build_registry()
        ctx = SimpleNamespace(stores=self.stores, registry=registry)
        catalog = await list_tool_packages(ctx)
        package = next(item for item in catalog if item.code == "ui:demo")
        self.assertEqual(package.tool_catalog_status, "empty")
        self.assertEqual(package.tool_count, 0)
        self.assertEqual(
            await invalid_tool_package_codes(
                ["ui:demo"],
                registry,
                self.stores,
            ),
            ["ui:demo"],
        )


class VisibilityTests(unittest.TestCase):
    def test_manual_tool_requires_exact_ui_capability(self) -> None:
        record = UIToolRecord(
            name="demo__form",
            package_code="ui:demo",
            description="x",
            parameters_json_schema={},
            source="manual",
            enabled=True,
        )
        self.assertFalse(
            is_tool_visible(record, capabilities=None)
        )
        self.assertFalse(
            is_tool_visible(record, capabilities=["ui"])
        )
        self.assertTrue(
            is_tool_visible(record, capabilities=["ui:demo"])
        )

    def test_builtin_tool_uses_the_same_exact_ui_capability_rule(self) -> None:
        record = next(item for item in builtin_ui_records() if item.name == "builtin__confirm")
        self.assertFalse(is_tool_visible(record, capabilities=None))
        self.assertFalse(is_tool_visible(record, capabilities=[]))
        self.assertTrue(
            is_tool_visible(record, capabilities=["ui:builtin"])
        )


class ToolHintsTests(unittest.TestCase):
    def test_build_tool_hints_includes_metadata(self) -> None:
        record = UIToolRecord(
            name="myapp__form",
            package_code="ui:demo",
            description="form",
            parameters_json_schema={"type": "object"},
            widget="text",
            metadata={"form_version": "1"},
            source="manual",
        )
        hints = build_tool_hints([record])
        self.assertEqual(hints["myapp__form"]["widget"], "text")
        self.assertEqual(hints["myapp__form"]["metadata"], {"form_version": "1"})
        self.assertEqual(hints["myapp__form"]["source"], "manual")

    def test_enrich_tool_event_merges_hints(self) -> None:
        event = {"type": "tool_call", "tool": "myapp__form", "args": {}, "tool_call_id": "c1"}
        hints = {"myapp__form": {"widget": "text", "metadata": {"v": 1}}}
        enriched = enrich_tool_event(event, hints)
        self.assertEqual(enriched["widget"], "text")
        self.assertEqual(enriched["metadata"], {"v": 1})

    def test_enrich_tool_event_builtin_without_hints(self) -> None:
        event = {"type": "tool_call", "tool": "builtin__confirm", "args": {"message": "ok"}, "tool_call_id": "c1"}
        enriched = enrich_tool_event(event, {})
        self.assertEqual(enriched["widget"], "confirm")

    def test_legacy_builtin_name_is_not_enriched(self) -> None:
        event = {
            "type": "tool_call",
            "tool": "confirm_action",
            "args": {"message": "old"},
            "tool_call_id": "legacy",
        }
        self.assertEqual(enrich_tool_event(event, {}), event)

    def test_enrich_tool_event_ignores_non_tool_call(self) -> None:
        event = {"type": "text", "delta": "hi"}
        self.assertIs(enrich_tool_event(event, {"x": {}}), event)

    def test_enrich_deferred_merges_calls(self) -> None:
        deferred = {"calls": [{"tool": "myapp__form", "args": {}, "tool_call_id": "c1"}], "approvals": []}
        hints = {"myapp__form": {"widget": "text", "metadata": {"v": 1}}}
        enriched = enrich_deferred(deferred, hints)
        self.assertEqual(enriched["calls"][0]["metadata"], {"v": 1})
        self.assertEqual(enriched["calls"][0]["widget"], "text")


class HistoryEnrichmentTests(unittest.TestCase):
    def test_enrich_history_messages_adds_metadata(self) -> None:
        messages = [
            HistoryMessage(
                role="assistant",
                parts=[
                    HistoryToolCallPart(
                        tool="myapp__form",
                        args={"prompt": "hi"},
                        tool_call_id="c1",
                    )
                ],
            )
        ]
        hints = {"myapp__form": {"widget": "text", "metadata": {"form_version": "1"}}}
        enriched = enrich_history_messages(messages, hints)
        part = enriched[0].parts[0]
        assert isinstance(part, HistoryToolCallPart)
        self.assertEqual(part.widget, "text")
        self.assertEqual(part.metadata, {"form_version": "1"})

    def test_dangling_function_tool_is_projected_as_interrupted(self) -> None:
        messages = [
            HistoryMessage(
                role="assistant",
                status="completed",
                parts=[
                    HistoryToolCallPart(
                        tool="execute_python",
                        args={"code": "sleep(30)"},
                        tool_call_id="function-call",
                    )
                ],
            )
        ]

        enriched = enrich_history_messages(messages, {})

        self.assertEqual(enriched[0].status, "interrupted")
        self.assertEqual(messages[0].status, "completed")

    def test_completed_function_tool_stays_completed(self) -> None:
        messages = [
            HistoryMessage(
                role="assistant",
                status="completed",
                parts=[
                    HistoryToolCallPart(
                        tool="execute_python",
                        tool_call_id="function-call",
                    ),
                    HistoryToolResultPart(
                        tool="execute_python",
                        tool_call_id="function-call",
                        result={"ok": True},
                    ),
                ],
            )
        ]

        enriched = enrich_history_messages(messages, {})

        self.assertEqual(enriched[0].status, "completed")

    def test_pending_ui_tool_stays_pending_not_interrupted(self) -> None:
        messages = [
            HistoryMessage(
                role="assistant",
                status="completed",
                parts=[
                    HistoryToolCallPart(
                        tool="myapp__form",
                        tool_call_id="ui-call",
                    )
                ],
            )
        ]

        enriched = enrich_history_messages(
            messages,
            {"myapp__form": {"widget": "text"}},
        )

        self.assertEqual(enriched[0].status, "completed")


class ExternalToolsetTests(unittest.IsolatedAsyncioTestCase):
    def test_build_external_toolset_preserves_names(self) -> None:
        toolset = build_external_toolset(builtin_ui_records())
        self.assertEqual(toolset.id, "ui-dynamic")

    async def test_external_ui_tool_allows_one_model_retry(self) -> None:
        toolset = build_external_toolset(builtin_ui_records())
        tools = await toolset.get_tools(None)
        self.assertEqual(tools["builtin__confirm"].max_retries, 1)

    async def test_invalid_json_is_retried_once_and_then_deferred(self) -> None:
        calls = 0

        def model_function(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
            nonlocal calls
            calls += 1
            args: str | dict[str, str]
            args = '{"message":' if calls == 1 else {"message": "继续？"}
            return ModelResponse(
                parts=[ToolCallPart("builtin__confirm", args, tool_call_id=f"call-{calls}")]
            )

        agent = Agent(
            FunctionModel(model_function),
            toolsets=[build_external_toolset(builtin_ui_records())],
            output_type=[str, DeferredToolRequests],
        )
        result = await agent.run("请确认")

        self.assertEqual(calls, 2)
        self.assertIsInstance(result.output, DeferredToolRequests)
        assert isinstance(result.output, DeferredToolRequests)
        self.assertEqual(result.output.calls[0].args, {"message": "继续？"})

    async def test_invalid_args_are_retried_once_and_then_deferred(self) -> None:
        calls = 0

        def model_function(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
            nonlocal calls
            calls += 1
            args = {} if calls == 1 else {"message": "继续？"}
            return ModelResponse(
                parts=[ToolCallPart("builtin__confirm", args, tool_call_id=f"call-{calls}")]
            )

        agent = Agent(
            FunctionModel(model_function),
            toolsets=[build_external_toolset(builtin_ui_records())],
            output_type=[str, DeferredToolRequests],
        )
        result = await agent.run("请确认")

        self.assertEqual(calls, 2)
        self.assertIsInstance(result.output, DeferredToolRequests)

    def test_invalid_tool_schema_fails_when_toolset_is_built(self) -> None:
        invalid = UIToolRecord(
            name="invalid_form",
            package_code="ui:demo",
            description="invalid",
            parameters_json_schema={"type": 123},
            widget="text",
            source="manual",
        )

        with self.assertRaises(ValueError):
            build_external_toolset([invalid])

    async def test_invalid_clarify_semantics_are_retried_once(self) -> None:
        clarify = UIToolRecord(
            name="deep_cube__clarify",
            package_code="ui:deepcube",
            description="clarify",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "fields": {"type": "array"},
                },
            },
            widget="clarify",
            source="manual",
        )
        calls = 0

        def model_function(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
            nonlocal calls
            calls += 1
            args = {} if calls == 1 else {
                "fields": [{"field": "account", "type": "text"}],
            }
            return ModelResponse(
                parts=[ToolCallPart("deep_cube__clarify", args, tool_call_id=f"call-{calls}")]
            )

        agent = Agent(
            FunctionModel(model_function),
            toolsets=[build_external_toolset([clarify])],
            output_type=[str, DeferredToolRequests],
        )
        result = await agent.run("请澄清")

        self.assertEqual(calls, 2)
        self.assertIsInstance(result.output, DeferredToolRequests)

    async def test_clarify_validator_rejects_select_without_options(self) -> None:
        clarify = UIToolRecord(
            name="deep_cube__clarify",
            package_code="ui:deepcube",
            description="clarify",
            parameters_json_schema={"type": "object"},
            widget="clarify",
            source="manual",
        )
        toolset = build_external_toolset([clarify])
        tools = await toolset.get_tools(None)
        validator = tools["deep_cube__clarify"].args_validator_func
        assert validator is not None
        with self.assertRaises(ModelRetry):
            validator(None, fields=[{"field": "account", "type": "select", "options": []}])

    async def test_clarify_validator_rejects_empty_option_object(self) -> None:
        clarify = UIToolRecord(
            name="deep_cube__clarify",
            package_code="ui:deepcube",
            description="clarify",
            parameters_json_schema={"type": "object"},
            widget="clarify",
            source="manual",
        )
        tools = await build_external_toolset([clarify]).get_tools(None)
        validator = tools["deep_cube__clarify"].args_validator_func
        assert validator is not None

        with self.assertRaises(ModelRetry):
            validator(
                None,
                fields=[{"field": "account", "type": "select", "options": [{}]}],
            )

    async def test_clarify_validator_rejects_missing_type_and_duplicate_id(self) -> None:
        clarify = UIToolRecord(
            name="deep_cube__clarify",
            package_code="ui:deepcube",
            description="clarify",
            parameters_json_schema={"type": "object"},
            widget="clarify",
            source="manual",
        )
        tools = await build_external_toolset([clarify]).get_tools(None)
        validator = tools["deep_cube__clarify"].args_validator_func
        assert validator is not None

        with self.assertRaises(ModelRetry):
            validator(None, fields=[{"field": "account"}])
        with self.assertRaises(ModelRetry):
            validator(
                None,
                fields=[
                    {"field": "account", "type": "text"},
                    {"field": "account", "type": "textarea"},
                ],
            )

    async def test_clarify_validator_rejects_object_identifiers(self) -> None:
        clarify = UIToolRecord(
            name="deep_cube__clarify",
            package_code="ui:deepcube",
            description="clarify",
            parameters_json_schema={"type": "object"},
            widget="clarify",
            source="manual",
        )
        tools = await build_external_toolset([clarify]).get_tools(None)
        validator = tools["deep_cube__clarify"].args_validator_func
        assert validator is not None

        with self.assertRaises(ModelRetry):
            validator(None, fields=[{"field": {}, "type": "textarea"}])
        with self.assertRaises(ModelRetry):
            validator(
                None,
                fields=[
                    {
                        "field": "account",
                        "type": "select",
                        "options": [{"value": {}}],
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
