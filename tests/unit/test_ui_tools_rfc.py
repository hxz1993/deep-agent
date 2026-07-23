from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError
from pydantic_ai import Agent, DeferredToolRequests, ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deep_agent.agent.history import HistoryMessage, HistoryToolCallPart, enrich_history_messages
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
    prepare_ui_toolset_for_spec,
)
from deep_agent.capabilities.ui.types import UIToolRecord
from deep_agent.db.models import Base, UiToolDefinition
from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.db.stores.ui_tool import UiToolStoreBase
from deep_agent.schemas.ui_tools import UiToolCreate, UiToolUpdate
from deep_agent.api.v1.tools import list_capabilities
from deep_agent.api.v1.ui_tools import get_ui_tool, list_ui_tools


class MergeDefinitionsTests(unittest.TestCase):
    def test_builtin_wins_over_conflicting_manual_definition(self) -> None:
        manual = UIToolRecord(
            name="confirm_action",
            description="manual",
            parameters_json_schema={"type": "object"},
            source="manual",
        )
        merged, shadowed = merge_definitions(builtin_ui_records(), [manual])
        winner = next(item for item in merged if item.name == "confirm_action")
        self.assertEqual(winner.source, "builtin")
        self.assertEqual(shadowed["confirm_action"], "manual")


class UiToolSchemaValidationTests(unittest.TestCase):
    def test_create_and_update_reject_invalid_json_schema(self) -> None:
        invalid_schema = {"type": 123}

        with self.assertRaises(ValidationError):
            UiToolCreate(
                name="demo__invalid",
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

    async def test_management_list_includes_read_only_builtin_tools(self) -> None:
        response = await list_ui_tools(
            store=self.store,
            source=None,
            enabled=None,
        )

        by_name = {item.name: item for item in response.items}
        self.assertEqual(set(by_name), {"ask_user_choice", "confirm_action"})
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

        self.assertEqual({item.name for item in builtin_response.items}, {"ask_user_choice", "confirm_action"})
        self.assertEqual(disabled_response.items, [])

    async def test_management_detail_reads_builtin_without_database_row(self) -> None:
        response = await get_ui_tool("confirm_action", store=self.store)

        self.assertEqual(response.name, "confirm_action")
        self.assertEqual(response.source, "builtin")
        self.assertEqual(response.widget, "confirm")
        self.assertIsNone(response.result_schema)
        self.assertIsNone(response.metadata)
        self.assertIsNone(response.created_at)
        self.assertIsNone(response.updated_at)

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
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()

        spec = AgentSpec(
            name="demo",
            model="test",
            instructions="hi",
            capabilities=["ui:demo__pick_color"],
            agent_id="00000000-0000-4000-8000-000000000001",
        )
        effective = await load_effective_ui_tools(self.stores, spec)
        toolset, hints = await prepare_ui_toolset_for_spec(self.stores, spec)

        names = {item.name for item in effective.definitions}
        self.assertIn("confirm_action", names)
        self.assertIn("demo__pick_color", names)
        self.assertTrue(effective.has_dynamic)
        self.assertIsNotNone(toolset)
        self.assertIn("demo__pick_color", hints)

    async def test_effective_tools_require_exact_ui_capability(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            db.add_all(
                [
                    UiToolDefinition(
                        name="demo__first",
                        description="first",
                        parameters_json_schema={"type": "object"},
                        source="manual",
                        enabled=True,
                        created_at=now,
                        updated_at=now,
                    ),
                    UiToolDefinition(
                        name="demo__second",
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
            capabilities=["ui:demo__second", "ui:missing__tool"],
        )
        effective = await load_effective_ui_tools(self.stores, spec)
        names = {item.name for item in effective.definitions}
        self.assertIn("confirm_action", names)
        self.assertIn("ask_user_choice", names)
        self.assertIn("demo__second", names)
        self.assertNotIn("demo__first", names)
        self.assertNotIn("missing__tool", names)

    async def test_null_capabilities_do_not_enable_extension_ui_tools(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            db.add(
                UiToolDefinition(
                    name="demo__hidden_by_default",
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
        self.assertEqual(names, {"confirm_action", "ask_user_choice"})
        self.assertFalse(effective.has_dynamic)

    async def test_disabled_selected_tool_is_ignored(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            db.add(
                UiToolDefinition(
                    name="demo__disabled",
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
            capabilities=["ui:demo__disabled"],
        )
        effective = await load_effective_ui_tools(self.stores, spec)
        self.assertNotIn("demo__disabled", {item.name for item in effective.definitions})

    async def test_selectable_tools_and_capability_api_are_one_entry_per_tool(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            db.add_all(
                [
                    UiToolDefinition(
                        name="demo__first",
                        description="First form",
                        parameters_json_schema={"type": "object"},
                        source="manual",
                        enabled=True,
                        created_at=now,
                        updated_at=now,
                    ),
                    UiToolDefinition(
                        name="demo__disabled",
                        description="Disabled form",
                        parameters_json_schema={"type": "object"},
                        source="manual",
                        enabled=False,
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            await db.commit()

        selectable = await load_selectable_ui_tools(self.stores)
        self.assertEqual([record.name for record in selectable], ["demo__first"])

        registry = SimpleNamespace(describe=lambda: [])
        ctx = SimpleNamespace(stores=self.stores, registry=registry)
        capabilities = await list_capabilities(ctx)
        ui_capabilities = [item for item in capabilities if item.kind == "ui-client"]
        self.assertEqual(
            [item.name for item in ui_capabilities],
            ["ui-builtin:confirm_action", "ui-builtin:ask_user_choice", "ui:demo__first"],
        )
        self.assertEqual([item.source for item in ui_capabilities], ["builtin", "builtin", "manual"])
        self.assertEqual([item.locked for item in ui_capabilities], [True, True, False])
        self.assertEqual([item.default_enabled for item in ui_capabilities], [True, True, False])
        self.assertEqual([tool.name for tool in ui_capabilities[-1].tools], ["demo__first"])

    async def test_prepare_toolset_skips_historical_invalid_schema_only(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            db.add(
                UiToolDefinition(
                    name="demo__invalid",
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
            capabilities=["ui:demo__invalid"],
            agent_id="00000000-0000-4000-8000-000000000001",
        )
        toolset, hints = await prepare_ui_toolset_for_spec(self.stores, spec)

        self.assertIsNotNone(toolset)
        self.assertNotIn("demo__invalid", hints)
        assert toolset is not None
        tools = await toolset.get_tools(None)
        self.assertNotIn("demo__invalid", tools)
        self.assertIn("confirm_action", tools)


class VisibilityTests(unittest.TestCase):
    def test_manual_tool_requires_exact_ui_capability(self) -> None:
        record = UIToolRecord(
            name="demo__form",
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
            is_tool_visible(record, capabilities=["ui:demo__form"])
        )


class ToolHintsTests(unittest.TestCase):
    def test_build_tool_hints_includes_metadata(self) -> None:
        record = UIToolRecord(
            name="myapp__form",
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
        event = {"type": "tool_call", "tool": "confirm_action", "args": {"message": "ok"}, "tool_call_id": "c1"}
        enriched = enrich_tool_event(event, {})
        self.assertEqual(enriched["widget"], "confirm")

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


class ExternalToolsetTests(unittest.IsolatedAsyncioTestCase):
    def test_build_external_toolset_preserves_names(self) -> None:
        toolset = build_external_toolset(builtin_ui_records())
        self.assertEqual(toolset.id, "ui-dynamic")

    async def test_external_ui_tool_allows_one_model_retry(self) -> None:
        toolset = build_external_toolset(builtin_ui_records())
        tools = await toolset.get_tools(None)
        self.assertEqual(tools["confirm_action"].max_retries, 1)

    async def test_invalid_json_is_retried_once_and_then_deferred(self) -> None:
        calls = 0

        def model_function(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
            nonlocal calls
            calls += 1
            args: str | dict[str, str]
            args = '{"message":' if calls == 1 else {"message": "继续？"}
            return ModelResponse(
                parts=[ToolCallPart("confirm_action", args, tool_call_id=f"call-{calls}")]
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
                parts=[ToolCallPart("confirm_action", args, tool_call_id=f"call-{calls}")]
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
