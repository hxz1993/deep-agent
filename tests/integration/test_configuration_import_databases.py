"""Real database import regression. URLs must point at disposable local servers.

DEEP_AGENT_TEST_MYSQL_URL / DEEP_AGENT_TEST_POSTGRES_URL provide credentials;
each test creates and finally drops its own import_fk_test_<random> database.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from pydantic_ai import Agent as ModelAgent
from pydantic_ai.models.test import TestModel

from deep_agent.db.models import Base
from deep_agent.db.dialect import resolve_dialect
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.db.reference_integrity import acquire_write_barrier
from deep_agent.services.configuration_import import (
    ConfigurationImportError, apply_configuration_import, build_configuration_import_plan,
)
from maintenance.config_backup import _upgrade_target_schema, export_backup
from tests.unit import test_config_backup as config_backup_tests
from tests.integration.test_configuration_import_api import _RejectingFileStore


class ConfigurationImportDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def _exercise(self, env_name: str) -> None:
        raw = os.environ.get(env_name)
        if not raw:
            self.skipTest(f"{env_name} is not configured; real database not verified")
        url = make_url(raw)
        if url.host not in {"127.0.0.1", "localhost"}:
            self.fail("Only a disposable local database server is allowed")
        name = "import_fk_test_" + uuid4().hex[:10]
        admin_url = url.set(database="postgres" if url.get_backend_name() == "postgresql" else None)
        admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        target = None
        created = False
        try:
            async with admin.connect() as db:
                suffix = " CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" if url.get_backend_name() == "mysql" else ""
                await db.execute(text(f"CREATE DATABASE {name}{suffix}"))
                created = True
            target_url = url.set(database=name).render_as_string(hide_password=False)
            await asyncio.to_thread(_upgrade_target_schema, target_url, "009")
            target = create_async_engine(target_url)
            with tempfile.TemporaryDirectory(prefix="TMP_to_delete-import-") as directory:
                root = Path(directory)
                source = root / "source.db"
                await config_backup_tests.ConfigBackupTests()._create_source(source, revision="009")
                with sqlite3.connect(source) as db:
                    db.execute("UPDATE agents SET skills='[]', prompt='中文 👋📊🙂'")
                    db.execute("DELETE FROM skill_packages")
                backup = await export_backup(f"sqlite+aiosqlite:///{source}", environment="fixture",
                                             output_root=root / "backup", git_commit="fixture")
                plan = await build_configuration_import_plan(backup.path, target_engine=target)
                async with target.connect() as db:
                    before = {name: [dict(r) for r in (await db.execute(select(table))).mappings()]
                              for name, table in Base.metadata.tables.items()}
                bad_rows = {name: [dict(row) for row in rows] for name, rows in plan.rows.items()}
                bad_rows["channel_accounts"][0]["agent_id"] = "missing-agent"
                with self.assertRaisesRegex(ConfigurationImportError, "channel_accounts.agent_id"):
                    await apply_configuration_import(replace(plan, rows=bad_rows), target_engine=target,
                                                     file_store=_RejectingFileStore(), request_headers={})
                async with target.connect() as db:
                    after = {name: [dict(r) for r in (await db.execute(select(table))).mappings()]
                             for name, table in Base.metadata.tables.items()}
                self.assertEqual(before, after, "failed import must roll back every table")

                # An actual shared database row lock held by another connection
                # blocks replacement, then releases it when the writer commits.
                async with target.connect() as writer:
                    await acquire_write_barrier(writer)
                    pending = asyncio.create_task(apply_configuration_import(
                        plan, target_engine=target, file_store=_RejectingFileStore(), request_headers={}))
                    try:
                        with self.assertRaises(asyncio.TimeoutError):
                            await asyncio.wait_for(asyncio.shield(pending), timeout=0.3)
                        await writer.commit()
                        await asyncio.wait_for(pending, timeout=10)
                    finally:
                        await writer.rollback()
                        if not pending.done():
                            pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                async with target.connect() as db:
                    self.assertEqual(await db.scalar(text("SELECT prompt FROM agents WHERE id='agent-1'")), "中文 👋📊🙂")
                    self.assertEqual(await db.scalar(text("SELECT agent_id FROM channel_accounts WHERE id='channel-1'")), "agent-1")
                    self.assertEqual(await db.scalar(text("SELECT version_num FROM alembic_version")), "006")
                    self.assertEqual(await db.run_sync(lambda c: sum(len(inspect(c).get_foreign_keys(t))
                                                                     for t in Base.metadata.tables)), 0)
                await self._verify_imported_agent_chat(target, target_url)
                # Chat cleanup restores the no-conversation import precondition.
                await build_configuration_import_plan(backup.path, target_engine=target)
                # Reverse direction: the importer blocks ordinary writers too.
                async with target.connect() as importer, target.connect() as writer:
                    await acquire_write_barrier(importer, exclusive=True)
                    pending = asyncio.create_task(acquire_write_barrier(writer))
                    try:
                        with self.assertRaises(asyncio.TimeoutError):
                            await asyncio.wait_for(asyncio.shield(pending), timeout=0.3)
                        await importer.commit()
                        await asyncio.wait_for(pending, timeout=10)
                    finally:
                        await importer.rollback()
                        if not pending.done():
                            pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                        await writer.rollback()
        finally:
            if target is not None:
                await target.dispose()
            if created:
                async with admin.connect() as db:
                    await db.execute(text(f"DROP DATABASE {name}"))
            await admin.dispose()

    async def _verify_imported_agent_chat(self, target, target_url: str) -> None:
        factory = async_sessionmaker(target, expire_on_commit=False)
        stores = create_data_stores(resolve_dialect(target_url), factory)
        agent = await stores.agent.get_by_id("agent-1")
        self.assertIsNotNone(agent)
        self.assertEqual(agent.llm_model.id, "model-1")
        self.assertEqual(agent.prompt, "中文 👋📊🙂")
        # Execute the real model SDK with a controlled model and no tool/network
        # side effects, then persist through the production conversation boundary.
        model = ModelAgent(TestModel(call_tools=[], custom_output_text="已收到 👋📊🙂"),
                           instructions=agent.prompt)
        session_id = uuid4().hex
        try:
            result = await model.run("你好 👋")
            await stores.coordinator.append(session_id, result.new_messages(),
                                            agent_id=agent.id, user_id="fixture-user",
                                            turn_id=uuid4().hex, run_id=result.response.run_id,
                                            terminal_status="completed")
            # A fresh store/session reads committed history, not a cached result.
            reloaded = create_data_stores(resolve_dialect(target_url), factory)
            history = await reloaded.coordinator.get_history(session_id, agent_id=agent.id)
            self.assertEqual(history, result.new_messages())
            continued = await model.run("继续看看 📊", message_history=history)
            await reloaded.coordinator.append(session_id, continued.new_messages(),
                                              agent_id=agent.id, user_id="fixture-user",
                                              turn_id=uuid4().hex, run_id=continued.response.run_id,
                                              terminal_status="completed")
            persisted = await stores.coordinator.get_history(session_id, agent_id=agent.id)
            self.assertEqual(persisted, [*history, *continued.new_messages()])
            self.assertEqual(len(persisted), 4)
            events = await stores.coordinator.list_all_events(session_id)
            self.assertEqual(len(events), 4)
        finally:
            await stores.coordinator.delete(session_id, agent_id=agent.id)
        async with target.connect() as db:
            self.assertEqual(await db.scalar(text("SELECT COUNT(*) FROM chat_sessions")), 0)
            self.assertEqual(await db.scalar(text("SELECT COUNT(*) FROM chat_session_events")), 0)

    async def test_mysql_import_rollback_unicode_and_writer_barrier(self) -> None:
        await self._exercise("DEEP_AGENT_TEST_MYSQL_URL")

    async def test_postgres_import_rollback_unicode_and_writer_barrier(self) -> None:
        await self._exercise("DEEP_AGENT_TEST_POSTGRES_URL")
