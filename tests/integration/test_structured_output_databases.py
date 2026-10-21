"""012 acceptance on isolated SQLite, real MySQL and real PostgreSQL.

The two server URLs are credentials for disposable localhost servers only. Each
case creates and drops separate source and restore databases in finally blocks.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deep_agent.db import migrate
from deep_agent.db.charset import read_database_charset
from deep_agent.db.dialect import resolve_dialect
from deep_agent.db.models import Agent, AgentOutputConfig
from deep_agent.db.reference_schema import assert_logical_references
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.schemas.agent import AgentCreate, AgentUpdate
from deep_agent.services.configuration_import import ConfigurationImportConflictError, apply_configuration_import, build_configuration_import_plan
from maintenance.config_backup import export_backup
from tests.integration.test_configuration_import_api import _RejectingFileStore


CONFIG = {"name": "lease_result", "description": "结构定义 🧪 中文",
          "json_schema": {"type": "object", "properties": {
              "正文": {"type": "string"}, "contract": {"type": "object", "properties": {
                  "name": {"type": "string"}}, "required": ["name"]},
              "nothing": {"type": ["null", "boolean", "integer"]}}, "required": []}}


class StructuredOutputDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def _migrate(self, url, target, *, downgrade=False):
        def run():
            with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                config = migrate._build_alembic_config()
            config.attributes["database_url"] = url
            (command.downgrade if downgrade else command.upgrade)(config, target)
        await asyncio.to_thread(run)

    async def _exercise(self, url, restore_url):
        await self._migrate(url, "011")
        source = create_async_engine(url)
        restored = None
        try:
            async with source.begin() as db:
                await db.execute(sa.text("INSERT INTO agents (id,name,prompt,capabilities,skills,ui_tool_selection) VALUES ('legacy','历史 Agent 👋','原正文 📊','[]','[]','{}')"))
                await db.execute(sa.text("INSERT INTO chat_sessions(session_id,agent_id,user_id,title) VALUES ('legacy-session','legacy','fixture-user','旧会话 👋')"))
                await db.execute(sa.text("INSERT INTO chat_session_events(event_id,session_id,sequence,schema_version,event_type,payload) VALUES ('legacy-event','legacy-session',1,1,'user/message','原消息 🧪')"))
                columns_011 = await db.run_sync(lambda conn: {c["name"] for c in sa.inspect(conn).get_columns("agents")})
                self.assertNotIn("structured_output_enabled", columns_011)
            with tempfile.TemporaryDirectory(prefix="TMP_to_delete_legacy_011_backup_") as temp:
                old_backup = await export_backup(url, environment="fixture", output_root=Path(temp), git_commit="fixture")
                self.assertNotIn("agent_output_configs", old_backup.row_counts)
                await self._migrate(restore_url, "011")
                legacy_restore = create_async_engine(restore_url)
                try:
                    old_plan = await build_configuration_import_plan(old_backup.path, target_engine=legacy_restore)
                    self.assertEqual(old_plan.source.revision, "011")
                    self.assertNotIn("structured_output_enabled", old_plan.rows["agents"][0])
                    await apply_configuration_import(old_plan, target_engine=legacy_restore,
                        file_store=_RejectingFileStore(), request_headers={})
                    async with legacy_restore.connect() as db:
                        self.assertEqual(await db.scalar(sa.text("SELECT prompt FROM agents WHERE id='legacy'")), "原正文 📊")
                        self.assertEqual(await db.scalar(sa.text("SELECT version_num FROM alembic_version")), "011")
                finally:
                    await legacy_restore.dispose()
                await self._migrate(url, "012")
                with self.assertRaisesRegex(ConfigurationImportConflictError, "revision does not match"):
                    await build_configuration_import_plan(old_backup.path, target_engine=source)
            async with source.begin() as db:
                self.assertEqual((await db.execute(sa.text("SELECT structured_output_enabled,updated_by FROM agents WHERE id='legacy'"))).one(), (False, None))
                self.assertEqual(await db.scalar(sa.text("SELECT payload FROM chat_session_events WHERE event_id='legacy-event'")), "原消息 🧪")
                self.assertEqual(await db.scalar(sa.text("SELECT COUNT(*) FROM agent_output_configs")), 0)
                await db.execute(sa.text("INSERT INTO agents (id,name,prompt,capabilities,skills,ui_tool_selection) VALUES ('raw-new','raw default','text','[]','[]','{}')"))
                self.assertEqual((await db.execute(sa.text("SELECT structured_output_enabled,updated_by FROM agents WHERE id='raw-new'"))).one(), (False, None))
                inspected = await db.run_sync(lambda conn: sa.inspect(conn).get_columns("agents"))
                self.assertFalse(next(c for c in inspected if c["name"] == "structured_output_enabled")["nullable"])
                self.assertEqual(await db.run_sync(lambda conn: sa.inspect(conn).get_pk_constraint("agent_output_configs")["constrained_columns"]), ["agent_id"])
                output_columns = await db.run_sync(lambda conn: {column["name"]: column for column in sa.inspect(conn).get_columns("agent_output_configs")})
                self.assertEqual(output_columns["agent_id"]["type"].length, 36)
                self.assertEqual(output_columns["name"]["type"].length, 64)
                self.assertFalse(output_columns["json_schema"]["nullable"])
                self.assertEqual(next(c for c in inspected if c["name"] == "updated_by")["type"].length, 128)
                self.assertEqual(await db.run_sync(lambda conn: sa.inspect(conn).get_foreign_keys("agent_output_configs")), [])
                self.assertEqual(await db.run_sync(lambda conn: sum(len(sa.inspect(conn).get_foreign_keys(t)) for t in sa.inspect(conn).get_table_names())), 0)
            factory = async_sessionmaker(source, expire_on_commit=False)
            stores = create_data_stores(resolve_dialect(url), factory)
            row = await stores.agent.create_unique(AgentCreate(name="新 Agent 🧪", prompt="内容 👋📊🙂", output_config=CONFIG), user_id="trusted-user")
            draft = await stores.agent.get_output_config(row.id)
            self.assertEqual((draft.description, draft.json_schema), (CONFIG["description"], CONFIG["json_schema"]))
            self.assertEqual(draft.created_at.utcoffset().total_seconds(), 0)
            self.assertEqual(row.updated_by, "trusted-user")
            self.assertFalse(row.structured_output_enabled)
            async with factory() as db:
                charset = await read_database_charset(db, source.dialect.name)
            self.assertEqual(charset.status, "ok", charset.model_dump())

            # Real transactional rollback after the child row is flushed.
            from deep_agent.db.stores.agent import base
            save_original = base.save_output_config
            async def fail_after_write(db, agent_id, config):
                await save_original(db, agent_id, config)
                await db.flush()
                raise RuntimeError("injected output config transaction failure")
            changed = copy.deepcopy(CONFIG); changed["description"] = "不应提交"
            with patch.object(base, "save_output_config", fail_after_write), self.assertRaisesRegex(RuntimeError, "injected"):
                await stores.agent.update_unique(row.id, AgentUpdate(structured_output_enabled=True, output_config=changed), user_id="failed-user")
            fresh = create_data_stores(resolve_dialect(url), factory)
            reread = await fresh.agent.get_by_id(row.id)
            self.assertEqual((reread.structured_output_enabled, reread.updated_by, reread.updated_at), (False, row.updated_by, row.updated_at))
            self.assertEqual((await fresh.agent.get_output_config(row.id)).description, CONFIG["description"])
            # Same output code on different Agents is allowed; the one-row-per-
            # Agent primary key is the only new uniqueness requirement.
            enabled = await stores.agent.create_unique(AgentCreate(name="enabled fixture", prompt="test", structured_output_enabled=True, output_config=CONFIG), user_id="second-user")
            async with source.begin() as db:
                with self.assertRaises(sa.exc.IntegrityError):
                    async with db.begin_nested():
                        await db.execute(sa.insert(AgentOutputConfig).values(agent_id=row.id, name="duplicate", json_schema={}))
            # Concurrent output replacement and parent deletion use the same
            # logical lock order. Either update may win first; deletion must
            # finish and must never leave a child row behind or leak a deadlock.
            for iteration in range(3):
                raced = await stores.agent.create_unique(AgentCreate(name=f"raced-{iteration}", prompt="test", output_config=CONFIG), user_id="race-creator")
                release = asyncio.Event()
                async def update_raced():
                    await release.wait()
                    return await stores.agent.update_unique(raced.id, AgentUpdate(structured_output_enabled=True, output_config=CONFIG), user_id="race-editor")
                async def delete_raced():
                    await release.wait()
                    return await stores.agent.delete(raced.id, user_id="race-deleter")
                operations = [update_raced(), delete_raced()] if iteration % 2 else [delete_raced(), update_raced()]
                tasks = [asyncio.create_task(operation) for operation in operations]
                try:
                    release.set()
                    await asyncio.wait_for(asyncio.gather(*tasks), 5)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                self.assertIsNone(await stores.agent.get_by_id(raced.id))
                self.assertIsNone(await stores.agent.get_output_config(raced.id))

            # Logical references replace physical foreign keys, including 012.
            async with source.begin() as db:
                await db.run_sync(assert_logical_references)
                await db.execute(sa.insert(AgentOutputConfig).values(agent_id="orphan", name="orphan_output", json_schema={}))
                with self.assertRaisesRegex(RuntimeError, "agent_output_configs.agent_id"):
                    await db.run_sync(assert_logical_references)
                await db.execute(sa.delete(AgentOutputConfig).where(AgentOutputConfig.agent_id == "orphan"))

            # Whole-configuration backup preserves disabled drafts and actors;
            # restoration uses the real production import transaction boundary.
            with tempfile.TemporaryDirectory(prefix="TMP_to_delete_output_backup_") as temp:
                backup = await export_backup(url, environment="fixture", output_root=Path(temp), git_commit="fixture")
                self.assertEqual(backup.revision, "012")
                self.assertEqual(backup.row_counts["agent_output_configs"], 2)
                self.assertEqual(backup.row_counts["chat_sessions"], 0)
                self.assertEqual(backup.row_counts["chat_session_events"], 0)
                await self._migrate(restore_url, "012")
                restored = create_async_engine(restore_url)
                plan = await build_configuration_import_plan(backup.path, target_engine=restored)
                self.assertEqual(next(item for item in plan.rows["agent_output_configs"] if item["agent_id"] == row.id)["json_schema"], CONFIG["json_schema"])
                await apply_configuration_import(plan, target_engine=restored, file_store=_RejectingFileStore(), request_headers={})
                restore_factory = async_sessionmaker(restored, expire_on_commit=False)
                restore_stores = create_data_stores(resolve_dialect(restore_url), restore_factory)
                restored_row = await restore_stores.agent.get_by_id(row.id)
                self.assertFalse(restored_row.structured_output_enabled)
                self.assertEqual(restored_row.updated_by, "trusted-user")
                self.assertEqual((await restore_stores.agent.get_output_config(row.id)).json_schema, CONFIG["json_schema"])
                self.assertTrue((await restore_stores.agent.get_by_id(enabled.id)).structured_output_enabled)

            # Each guard is independently exercised without deleting user data:
            # only this test's rows are cleared between the disposable fixtures.
            with self.assertRaisesRegex(RuntimeError, "output_configs=2"):
                await self._migrate(url, "011", downgrade=True)
            async with source.begin() as db:
                await db.execute(sa.delete(AgentOutputConfig))
                await db.execute(sa.update(Agent).values(updated_by=None, structured_output_enabled=True))
            with self.assertRaisesRegex(RuntimeError, "enabled_agents="):
                await self._migrate(url, "011", downgrade=True)
            async with source.begin() as db:
                await db.execute(sa.update(Agent).values(structured_output_enabled=False, updated_by="trusted-user"))
            with self.assertRaisesRegex(RuntimeError, "updated_agents="):
                await self._migrate(url, "011", downgrade=True)
            async with source.begin() as db:
                await db.execute(sa.update(Agent).values(updated_by=None))
                await db.execute(sa.text("INSERT INTO chat_sessions(session_id,agent_id,user_id,title) VALUES ('fixture-session','legacy','fixture-user','消息 🧪')"))
                await db.execute(sa.text("INSERT INTO chat_session_events(event_id,session_id,sequence,schema_version,event_type,payload) VALUES ('fixture-event','fixture-session',1,1,'output/attempt','{}')"))
            with self.assertRaisesRegex(RuntimeError, "output_events=1"):
                await self._migrate(url, "011", downgrade=True)
            async with source.begin() as db:
                await db.execute(sa.text("UPDATE chat_session_events SET event_type='user/message', payload=:payload WHERE event_id='fixture-event'"), {"payload": json.dumps({"deepagent_output_contract": {"name": "fixture"}})})
            with self.assertRaisesRegex(RuntimeError, "output_snapshots=1"):
                await self._migrate(url, "011", downgrade=True)
            async with source.begin() as db:
                await db.execute(sa.text("DELETE FROM chat_session_events WHERE event_id='fixture-event'"))
            await self._migrate(url, "011", downgrade=True)
            async with source.connect() as db:
                self.assertEqual(await db.scalar(sa.text("SELECT version_num FROM alembic_version")), "011")
                self.assertEqual(await db.scalar(sa.text("SELECT prompt FROM agents WHERE id='legacy'")), "原正文 📊")
                self.assertEqual(await db.scalar(sa.text("SELECT payload FROM chat_session_events WHERE event_id='legacy-event'")), "原消息 🧪")
                self.assertNotIn("agent_output_configs", await db.run_sync(lambda conn: sa.inspect(conn).get_table_names()))
            await self._migrate(url, "012")
            async with source.connect() as db:
                await db.run_sync(assert_logical_references)
        finally:
            if restored is not None:
                await restored.dispose()
            await source.dispose()

    async def _server_case(self, env_name):
        raw = os.environ.get(env_name)
        if not raw:
            self.skipTest(f"{env_name} not configured; real database not verified")
        url = sa.make_url(raw)
        if url.host not in {"localhost", "127.0.0.1"}:
            self.fail("Only disposable localhost database servers are allowed")
        admin = create_async_engine(url.set(database="postgres" if url.get_backend_name() == "postgresql" else None), isolation_level="AUTOCOMMIT")
        created = []
        try:
            names = ["tmp_deepagent_output_" + uuid4().hex[:12] for _ in range(2)]
            async with admin.connect() as db:
                for name in names:
                    suffix = " CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" if url.get_backend_name() == "mysql" else " ENCODING 'UTF8'"
                    await db.execute(sa.text(f"CREATE DATABASE {name}{suffix}")); created.append(name)
            await self._exercise(*(url.set(database=name).render_as_string(hide_password=False) for name in names))
        finally:
            async with admin.connect() as db:
                for name in reversed(created):
                    await db.execute(sa.text(f"DROP DATABASE {name}"))
            await admin.dispose()

    async def test_sqlite_012_full_acceptance(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_output_sqlite_") as temp:
            await self._exercise(*(f"sqlite+aiosqlite:///{Path(temp) / name}" for name in ("source.db", "restored.db")))

    async def test_mysql_012_full_acceptance(self):
        await self._server_case("DEEP_AGENT_TEST_MYSQL_URL")

    async def test_postgresql_012_full_acceptance(self):
        await self._server_case("DEEP_AGENT_TEST_POSTGRES_URL")


if __name__ == "__main__":
    unittest.main()
