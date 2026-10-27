"""Upload-setting persistence and backup contract across isolated databases.

The two external URLs must name disposable loopback database servers. Every
server test creates and finally drops its own TMP-style database; no shared
application database is selected or modified.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from sqlalchemy import event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from deep_agent.db.factory import create_engine
from deep_agent.db.models import GlobalSetting
from deep_agent.db.stores.global_setting import GlobalSettingStore
from deep_agent.files.upload_settings import UPLOAD_SETTING_KEY, UploadSettings, load_upload_settings
from deep_agent.services.configuration_import import apply_configuration_import, build_configuration_import_plan
from maintenance.config_backup import _upgrade_target_schema, export_backup


class _NoFileWrites:
    async def upload(self, **kwargs):
        raise AssertionError("An upload-only configuration backup contains no Skill file")

    async def download(self, *args, **kwargs):
        raise AssertionError("An upload-only configuration backup contains no Skill file")

    async def delete(self, *args, **kwargs):
        raise AssertionError("An upload-only configuration backup contains no Skill file")


class AttachmentUploadDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def _exercise(self, url: str, directory: Path):
        first_engine, first_factory = create_engine(url)
        second_engine, second_factory = create_engine(url)
        try:
            # Use the released schema builder: metadata-only MySQL creation is
            # not equivalent to deployed migrations (notably Boolean defaults).
            await asyncio.to_thread(_upgrade_target_schema, url, "013")
            first, second = GlobalSettingStore(first_factory), GlobalSettingStore(second_factory)
            self.assertEqual(await load_upload_settings(first), UploadSettings(20, 10))
            async with second_engine.connect() as connection:
                self.assertEqual(await connection.scalar(select(func.count()).select_from(GlobalSetting)), 0)
            # The revision-row barrier also serializes concurrent first inserts
            # on MySQL/PostgreSQL, where locking a nonexistent settings row alone
            # would not provide a cross-worker critical section.
            candidates = ({"max_size_mb": 80, "max_count": 15}, {"max_size_mb": 120, "max_count": 20})
            await asyncio.gather(*(store.upsert(UPLOAD_SETTING_KEY, value=value, version=1)
                                   for store, value in zip((first, second), candidates)))
            stored = await first.get(UPLOAD_SETTING_KEY)
            self.assertIn(stored.value, candidates)
            async with second_engine.connect() as connection:
                self.assertEqual(await connection.scalar(select(func.count()).select_from(GlobalSetting)), 1)
            await first.upsert(UPLOAD_SETTING_KEY, value={"max_size_mb": 50, "max_count": 12}, version=1)
            self.assertEqual(await load_upload_settings(second), UploadSettings(50, 12))

            def fail_after_write(_connection, _cursor, statement, _parameters, _context, _many):
                if statement.lstrip().upper().startswith("UPDATE GLOBAL_SETTINGS"):
                    raise RuntimeError("fixture rollback after upload policy SQL")
            event.listen(first_engine.sync_engine, "after_cursor_execute", fail_after_write)
            try:
                with self.assertRaisesRegex(RuntimeError, "fixture rollback"):
                    await first.upsert(UPLOAD_SETTING_KEY, value={"max_size_mb": 100, "max_count": 20}, version=1)
            finally:
                event.remove(first_engine.sync_engine, "after_cursor_execute", fail_after_write)
            self.assertEqual(await load_upload_settings(second), UploadSettings(50, 12))

            before = await second.get(UPLOAD_SETTING_KEY)
            backup = await export_backup(url, environment="attachment-upload-fixture",
                output_root=directory / "backups", git_commit="fixture")
            after = await second.get(UPLOAD_SETTING_KEY)
            self.assertEqual(after.value, before.value)
            self.assertEqual(after.updated_at, before.updated_at)
            with sqlite3.connect(backup.path) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM global_settings").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0], "013")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM chat_session_events").fetchone()[0], 0)
            # Prove restore uses the existing generic global_settings path.
            await first.upsert(UPLOAD_SETTING_KEY, value={"max_size_mb": 1, "max_count": 5}, version=1)
            plan = await build_configuration_import_plan(backup.path, target_engine=first_engine)
            await apply_configuration_import(plan, target_engine=first_engine, file_store=_NoFileWrites(), request_headers={})
            self.assertEqual(await load_upload_settings(second), UploadSettings(50, 12))
            async with second_engine.connect() as connection:
                self.assertEqual(await connection.scalar(text("SELECT version_num FROM alembic_version")), "013")
                self.assertEqual(await connection.scalar(select(func.count()).select_from(GlobalSetting)), 1)
        finally:
            await first_engine.dispose()
            await second_engine.dispose()

    async def test_sqlite_atomic_hot_update_rollback_and_backup_restore(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_upload_sqlite_") as name:
            root = Path(name)
            await self._exercise(f"sqlite+aiosqlite:///{root / 'source.db'}", root)

    async def _server(self, env_name):
        raw = os.environ.get(env_name)
        if not raw:
            self.skipTest(f"{env_name} is not configured; real database not verified")
        url = make_url(raw)
        if url.host not in {"127.0.0.1", "localhost"}:
            self.fail("Only a disposable loopback database server is allowed")
        name = "tmp_upload_" + uuid4().hex[:12]
        admin_url = url.set(database="postgres" if url.get_backend_name() == "postgresql" else None)
        admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        created = False
        try:
            async with admin.connect() as connection:
                suffix = " CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" if url.get_backend_name() == "mysql" else ""
                await connection.execute(text(f"CREATE DATABASE {name}{suffix}"))
                created = True
            with tempfile.TemporaryDirectory(prefix="TMP_to_delete_upload_database_") as directory:
                await self._exercise(url.set(database=name).render_as_string(hide_password=False), Path(directory))
        finally:
            try:
                if created:
                    async with admin.connect() as connection:
                        await connection.execute(text(f"DROP DATABASE {name}"))
            finally:
                await admin.dispose()

    async def test_mysql_atomic_hot_update_rollback_and_backup_restore(self):
        await self._server("DEEP_AGENT_TEST_MYSQL_URL")

    async def test_postgres_atomic_hot_update_rollback_and_backup_restore(self):
        await self._server("DEEP_AGENT_TEST_POSTGRES_URL")


if __name__ == "__main__":
    unittest.main()
