"""013 acceptance using deployed migrations and disposable real databases."""
from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from pydantic_ai.messages import ModelRequest, UserPromptPart
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deep_agent.db import migrate
from deep_agent.db.dialect import resolve_dialect
from deep_agent.db.models import Agent, AgentOutputConfig
from deep_agent.db.reference_schema import assert_logical_references
from deep_agent.db.serialization import serialize_message
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.schemas.agent import AgentCreate, AgentUpdate
from deep_agent.services.configuration_import import (
    ConfigurationImportConflictError, apply_configuration_import, build_configuration_import_plan,
)
from maintenance.config_backup import export_backup
from tests.integration.test_configuration_import_api import _RejectingFileStore


CONFIG = {"name": "portable_result", "description": "013原子草稿 👋📊🙂",
          "json_schema": {"type": "object", "properties": {"text": {"type": "string"},
              "record": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
              "additionalProperties": False}}


class AllowTextOutputDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def policy(self, stores, agent_id):
        config = await stores.agent.get_output_config(agent_id)
        return bool(config.allow_text_output) if config is not None else False

    async def concurrent_policy_merge(self, source, stores):
        if source.dialect.name == "mysql":
            async with source.connect() as db:
                self.assertEqual(await db.get_isolation_level(), "REPEATABLE READ")
        for edit in ("schema", "prompt"):
            with self.subTest(concurrent_edit=edit):
                row = await stores.agent.create_unique(AgentCreate(name=f"policy merge {edit}", prompt="original prompt",
                    output_config=CONFIG), user_id="race-creator")
                original_read = stores.agent._get_by_id
                initial_read = asyncio.Event()
                continue_edit = asyncio.Event()
                later_task = None
                changed = copy.deepcopy(CONFIG)
                changed["json_schema"]["properties"]["new_field"] = {"type": "integer"}

                async def pause_first_read(db, agent_id):
                    value = await original_read(db, agent_id)
                    if agent_id == row.id and asyncio.current_task() is later_task and not initial_read.is_set():
                        initial_read.set()
                        await continue_edit.wait()
                    return value

                try:
                    with patch.object(stores.agent, "_get_by_id", pause_first_read):
                        patch_data = AgentUpdate(output_config=changed) if edit == "schema" else AgentUpdate(prompt="updated prompt")
                        later_task = asyncio.create_task(stores.agent.update_unique(
                            row.id, patch_data, user_id=f"{edit}-editor"))
                        try:
                            await asyncio.wait_for(initial_read.wait(), 5)
                            policy_result = await asyncio.wait_for(stores.agent.update_unique(
                                row.id, AgentUpdate(allow_text_output=True), user_id="policy-writer"), 5)
                            self.assertTrue(policy_result.allow_text_output)
                            continue_edit.set()
                            result = await asyncio.wait_for(later_task, 10)
                        finally:
                            continue_edit.set()
                            if not later_task.done():
                                later_task.cancel()
                            await asyncio.gather(later_task, return_exceptions=True)
                    fresh = await stores.agent.get_by_id(row.id)
                    definition = await stores.agent.get_output_config(row.id)
                    self.assertTrue(definition.allow_text_output, "omitting policy must preserve the already committed true")
                    self.assertTrue(fresh.allow_text_output)
                    self.assertEqual(result.allow_text_output, fresh.allow_text_output,
                                     "the write response must not report the stale repeatable-read policy")
                    self.assertEqual(fresh.updated_by, f"{edit}-editor")
                    self.assertEqual(definition.json_schema, changed["json_schema"] if edit == "schema" else CONFIG["json_schema"])
                    self.assertEqual(fresh.prompt, "updated prompt" if edit == "prompt" else "original prompt")
                finally:
                    await stores.agent.delete(row.id)

    async def migrate(self, url, target, *, downgrade=False):
        def run():
            config = migrate._build_alembic_config()
            config.attributes["database_url"] = url
            (command.downgrade if downgrade else command.upgrade)(config, target)
        await asyncio.to_thread(run)

    async def exercise(self, source_url, restored_url):
        await self.migrate(source_url, "012")
        source = create_async_engine(source_url)
        restored = create_async_engine(restored_url)
        try:
            async with source.begin() as db:
                tables_012 = await db.run_sync(lambda conn: set(sa.inspect(conn).get_table_names()))
                columns_012 = await db.run_sync(lambda conn: {column["name"] for column in sa.inspect(conn).get_columns("agents")})
                output_columns_012 = await db.run_sync(lambda conn: {column["name"] for column in sa.inspect(conn).get_columns("agent_output_configs")})
                self.assertNotIn("allow_text_output", columns_012)
                self.assertNotIn("allow_text_output", output_columns_012)
                await db.execute(sa.text("INSERT INTO agents(id,name,prompt,capabilities,skills,ui_tool_selection) "
                    "VALUES('legacy','真实012历史中文🧪','原正文👋','[]','[]','{}')"))
                await db.execute(sa.text("INSERT INTO agent_output_configs(agent_id,name,description,json_schema) "
                    "VALUES('legacy','legacy_result','012真实草稿🧪',:schema)"), {"schema": json.dumps(CONFIG["json_schema"])})
                await db.execute(sa.text("INSERT INTO chat_sessions(session_id,agent_id,user_id,title) "
                    "VALUES('legacy-session','legacy','fixture-owner','旧会话🙂')"))
                await db.execute(sa.text("INSERT INTO chat_session_events(event_id,session_id,sequence,schema_version,event_type,payload) "
                    "VALUES('old-event','legacy-session',1,1,'user/message','原012消息📊')"))
            # A genuine 012 backup remains recognizable and restorable at 012.
            with tempfile.TemporaryDirectory(prefix="TMP_to_delete_allow_text_012_backup_") as directory:
                backup = await export_backup(source_url, environment="fixture", output_root=Path(directory), git_commit="fixture")
                self.assertEqual(backup.revision, "012")
                await self.migrate(restored_url, "012")
                plan = await build_configuration_import_plan(backup.path, target_engine=restored)
                self.assertTrue(plan.rows["agents"])
                self.assertTrue(all("allow_text_output" not in row for row in plan.rows["agents"]))
                self.assertTrue(plan.rows["agent_output_configs"])
                self.assertTrue(all("allow_text_output" not in row for row in plan.rows["agent_output_configs"]))
                await apply_configuration_import(plan, target_engine=restored, file_store=_RejectingFileStore(), request_headers={})
                async with restored.connect() as db:
                    self.assertEqual(await db.scalar(sa.text("SELECT prompt FROM agents WHERE id='legacy'")), "原正文👋")
                    self.assertEqual(await db.scalar(sa.text("SELECT version_num FROM alembic_version")), "012")
                await self.migrate(source_url, "013")
                with self.assertRaises(ConfigurationImportConflictError):
                    await build_configuration_import_plan(backup.path, target_engine=source)

            async with source.begin() as db:
                columns = await db.run_sync(lambda conn: sa.inspect(conn).get_columns("agent_output_configs"))
                self.assertEqual({column["name"] for column in columns} - output_columns_012, {"allow_text_output"})
                self.assertEqual(await db.run_sync(lambda conn: {column["name"] for column in sa.inspect(conn).get_columns("agents")}), columns_012)
                self.assertEqual(await db.run_sync(lambda conn: set(sa.inspect(conn).get_table_names())), tables_012)
                self.assertFalse(next(column for column in columns if column["name"] == "allow_text_output")["nullable"])
                self.assertIs(AgentOutputConfig.__table__.c.allow_text_output.default.arg, False)
                self.assertEqual(await db.scalar(sa.text("SELECT COUNT(*) FROM agent_output_configs WHERE allow_text_output <> :disabled"), {"disabled": False}), 0)
                self.assertEqual(await db.scalar(sa.text("SELECT payload FROM chat_session_events WHERE event_id='old-event'")), "原012消息📊")
                await db.execute(sa.text("INSERT INTO agents(id,name,prompt,capabilities,skills,ui_tool_selection) "
                    "VALUES('raw-default','raw database default','raw','[]','[]','{}')"))
                await db.execute(sa.text("INSERT INTO agent_output_configs(agent_id,name,json_schema) "
                    "VALUES('raw-default','raw_result',:schema)"), {"schema": json.dumps(CONFIG["json_schema"])})
                self.assertEqual(await db.scalar(sa.text("SELECT allow_text_output FROM agent_output_configs WHERE agent_id='raw-default'")), False)
                await db.run_sync(assert_logical_references)
            factory = async_sessionmaker(source, expire_on_commit=False)
            stores = create_data_stores(resolve_dialect(source_url), factory)
            legacy = await stores.agent.get_by_id("legacy")
            self.assertFalse(await self.policy(stores, legacy.id))
            empty = await stores.agent.create_unique(AgentCreate(name="no output config", prompt="synthetic"), user_id="empty-operator")
            self.assertIsNone(await stores.agent.get_output_config(empty.id))
            self.assertFalse(await self.policy(stores, empty.id))
            self.assertFalse(empty.allow_text_output)
            with self.assertRaises(ValueError):
                await stores.agent.create_unique(AgentCreate(name="must not create", prompt="synthetic", allow_text_output=True), user_id="invalid-operator")
            self.assertIsNone(await stores.agent.get_by_name("must not create"))
            with self.assertRaises(ValueError):
                await stores.agent.update_unique(empty.id, AgentUpdate(allow_text_output=True), user_id="invalid-operator")
            empty_after = await stores.agent.get_by_id(empty.id)
            self.assertEqual((empty_after.updated_by, empty_after.updated_at), (empty.updated_by, empty.updated_at))
            self.assertIsNone(await stores.agent.get_output_config(empty.id))
            deletable = await stores.agent.create_unique(AgentCreate(name="deletable draft", prompt="synthetic",
                allow_text_output=True, output_config=CONFIG), user_id="draft-operator")
            with self.assertRaises(ValueError):
                await stores.agent.update_unique(deletable.id, AgentUpdate(output_config=None,
                    allow_text_output=True), user_id="invalid-operator")
            self.assertTrue(await self.policy(stores, deletable.id))
            await stores.agent.update_unique(deletable.id, AgentUpdate(output_config=None), user_id="draft-remover")
            self.assertIsNone(await stores.agent.get_output_config(deletable.id))
            self.assertFalse(await self.policy(stores, deletable.id))
            draft = await stores.agent.create_unique(AgentCreate(name="关闭但保留纯文本草稿", prompt="合成🧪",
                allow_text_output=True, output_config=CONFIG), user_id="draft-operator")
            strict = await stores.agent.create_unique(AgentCreate(name="strict", prompt="synthetic",
                structured_output_enabled=True, output_config=CONFIG), user_id="strict-operator")
            mixed = await stores.agent.create_unique(AgentCreate(name="mixed", prompt="synthetic",
                structured_output_enabled=True, allow_text_output=True, output_config=CONFIG), user_id="mixed-operator")
            self.assertEqual((draft.structured_output_enabled, await self.policy(stores, draft.id)), (False, True))
            self.assertEqual((strict.structured_output_enabled, await self.policy(stores, strict.id)), (True, False))
            self.assertEqual((mixed.structured_output_enabled, await self.policy(stores, mixed.id)), (True, True))
            ordinary_patch = await stores.agent.update_unique(draft.id, AgentUpdate(prompt="新的合成正文🙂"), user_id="ordinary-editor")
            self.assertEqual((ordinary_patch.structured_output_enabled, await self.policy(stores, draft.id)), (False, True))
            # Parent switch/actor/time and child policy/definition roll back together.
            from deep_agent.db.stores.agent import base
            original_save = base.save_output_config
            async def fail_after_child(*args, **kwargs):
                await original_save(*args, **kwargs)
                await args[0].flush()
                raise RuntimeError("injected allow-text transaction failure")
            changed = copy.deepcopy(CONFIG)
            changed["description"] = "不应保存"
            with patch.object(base, "save_output_config", fail_after_child), self.assertRaisesRegex(RuntimeError, "injected"):
                await stores.agent.update_unique(draft.id, AgentUpdate(structured_output_enabled=True,
                    allow_text_output=False, output_config=changed), user_id="failed-operator")
            current = await stores.agent.get_by_id(draft.id)
            self.assertEqual((current.structured_output_enabled, await self.policy(stores, current.id), current.updated_by, current.updated_at),
                (False, True, ordinary_patch.updated_by, ordinary_patch.updated_at))
            self.assertEqual((await stores.agent.get_output_config(draft.id)).description, CONFIG["description"])
            # The published MySQL DATETIME type has second precision. Seed a
            # known previous time rather than depending on crossing a wall-
            # clock second between two otherwise successful writes.
            previous_time = datetime(2020, 1, 1, tzinfo=UTC)
            async with source.begin() as db:
                await db.execute(sa.update(Agent).where(Agent.id == strict.id).values(updated_at=previous_time))
                await db.execute(sa.update(AgentOutputConfig).where(AgentOutputConfig.agent_id == strict.id).values(updated_at=previous_time))
            strict = await stores.agent.get_by_id(strict.id)
            self.assertEqual(strict.updated_at, previous_time)
            self.assertEqual((await stores.agent.get_output_config(strict.id)).updated_at, previous_time)
            flipped = await stores.agent.update_unique(strict.id, AgentUpdate(allow_text_output=True), user_id="policy-editor")
            self.assertTrue(flipped.structured_output_enabled)
            self.assertTrue(await self.policy(stores, flipped.id))
            self.assertEqual(flipped.updated_by, "policy-editor")
            self.assertGreater(flipped.updated_at, strict.updated_at)
            persisted_flip = await stores.agent.get_by_id(strict.id)
            self.assertEqual((await self.policy(stores, persisted_flip.id), persisted_flip.updated_by, persisted_flip.updated_at),
                             (True, "policy-editor", flipped.updated_at))
            self.assertGreater((await stores.agent.get_output_config(strict.id)).updated_at, previous_time)
            await stores.agent.update_unique(strict.id, AgentUpdate(allow_text_output=False), user_id="strict-restored")
            if source.dialect.name in {"mysql", "postgresql"}:
                # SQLite serializes the entire writer session and cannot model
                # two independently committed writers or MySQL RR snapshots.
                await self.concurrent_policy_merge(source, stores)

            with tempfile.TemporaryDirectory(prefix="TMP_to_delete_allow_text_013_backup_") as directory:
                backup = await export_backup(source_url, environment="fixture", output_root=Path(directory), git_commit="fixture")
                self.assertEqual(backup.revision, "013")
                self.assertEqual(backup.row_counts["chat_sessions"], 0)
                self.assertEqual(backup.row_counts["chat_session_events"], 0)
                await self.migrate(restored_url, "013")
                plan = await build_configuration_import_plan(backup.path, target_engine=restored)
                self.assertTrue(all("allow_text_output" not in row for row in plan.rows["agents"]))
                self.assertIs(next(row for row in plan.rows["agent_output_configs"] if row["agent_id"] == draft.id)["allow_text_output"], True)
                await apply_configuration_import(plan, target_engine=restored, file_store=_RejectingFileStore(), request_headers={})
                restored_stores = create_data_stores(resolve_dialect(restored_url), async_sessionmaker(restored, expire_on_commit=False))
                for before in (current, await stores.agent.get_by_id(strict.id), mixed):
                    after = await restored_stores.agent.get_by_id(before.id)
                    self.assertEqual((after.structured_output_enabled, await self.policy(restored_stores, after.id), after.updated_by),
                                     (before.structured_output_enabled, await self.policy(stores, before.id), before.updated_by))
                    self.assertEqual((await restored_stores.agent.get_output_config(before.id)).json_schema, CONFIG["json_schema"])

            with self.assertRaisesRegex(RuntimeError, "configured_.*="):
                await self.migrate(source_url, "012", downgrade=True)
            async with source.begin() as db:
                await db.execute(sa.update(AgentOutputConfig).values(allow_text_output=False))
                payload = {"message": serialize_message(ModelRequest(parts=[UserPromptPart("synthetic mixed turn")],
                    metadata={"deepagent_output_contract": {**CONFIG, "allow_text_output": True}}))}
                await db.execute(sa.text("INSERT INTO chat_session_events(event_id,session_id,sequence,schema_version,event_type,payload) "
                    "VALUES('frozen-mixed','legacy-session',2,1,'user/message',:payload)"), {"payload": json.dumps(payload)})
            async with source.connect() as db:
                frozen = json.loads(await db.scalar(sa.text(
                    "SELECT payload FROM chat_session_events WHERE event_id='frozen-mixed'")))
                self.assertIs(frozen["message"]["metadata"]["deepagent_output_contract"]["allow_text_output"], True)
            with self.assertRaisesRegex(RuntimeError, "frozen_events=1"):
                await self.migrate(source_url, "012", downgrade=True)
            async with source.begin() as db:
                await db.execute(sa.text("DELETE FROM chat_session_events WHERE event_id='frozen-mixed'"))
                # A customer's unrelated business key is not a platform policy.
                payload = {"message": serialize_message(ModelRequest(parts=[UserPromptPart("synthetic business data")],
                    metadata={"client_context": {"allow_text_output": True}}))}
                await db.execute(sa.text("INSERT INTO chat_session_events(event_id,session_id,sequence,schema_version,event_type,payload) "
                    "VALUES('business-only','legacy-session',3,1,'user/message',:payload)"), {"payload": json.dumps(payload)})
            await self.migrate(source_url, "012", downgrade=True)
            async with source.connect() as db:
                self.assertNotIn("allow_text_output", await db.run_sync(lambda conn: {column["name"] for column in sa.inspect(conn).get_columns("agent_output_configs")}))
                self.assertEqual(await db.scalar(sa.text("SELECT payload FROM chat_session_events WHERE event_id='old-event'")), "原012消息📊")
                self.assertEqual(await db.scalar(sa.text("SELECT COUNT(*) FROM agent_output_configs")), 5)
            await self.migrate(source_url, "013")
            async with source.connect() as db:
                self.assertEqual(await db.scalar(sa.text("SELECT COUNT(*) FROM agent_output_configs WHERE allow_text_output <> :disabled"), {"disabled": False}), 0)
                await db.run_sync(assert_logical_references)
        finally:
            await restored.dispose()
            await source.dispose()

    async def server_case(self, variable):
        raw = os.environ.get(variable)
        if not raw:
            self.skipTest(f"{variable} missing; actual database not tested")
        url = sa.make_url(raw)
        if url.host not in {"127.0.0.1", "localhost"}:
            self.fail("Destructive tests require a disposable loopback server")
        admin = create_async_engine(url.set(database="postgres" if url.get_backend_name() == "postgresql" else None), isolation_level="AUTOCOMMIT")
        names = ["tmp_allow_text_" + uuid4().hex[:12] for _ in range(2)]
        created = []
        try:
            async with admin.connect() as db:
                for name in names:
                    suffix = " CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" if url.get_backend_name() == "mysql" else " ENCODING 'UTF8'"
                    await db.execute(sa.text(f"CREATE DATABASE {name}{suffix}"))
                    created.append(name)
            await self.exercise(*(url.set(database=name).render_as_string(hide_password=False) for name in names))
        finally:
            async with admin.connect() as db:
                for name in reversed(created):
                    await db.execute(sa.text(f"DROP DATABASE {name}"))
            await admin.dispose()

    async def test_sqlite_013_allow_text_output(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_allow_text_sqlite_") as directory:
            await self.exercise(*(f"sqlite+aiosqlite:///{Path(directory) / filename}" for filename in ("source.sqlite", "restored.sqlite")))

    async def test_mysql_013_allow_text_output(self):
        await self.server_case("DEEP_AGENT_TEST_MYSQL_URL")

    async def test_postgresql_013_allow_text_output(self):
        await self.server_case("DEEP_AGENT_TEST_POSTGRES_URL")
