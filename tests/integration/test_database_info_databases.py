"""Database summary/check separation against disposable servers and SQLite."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deep_agent.api.v1 import database_info
from deep_agent.db.charset import business_text_columns
from maintenance.config_backup import _upgrade_target_schema


class DatabaseInfoDialectTests(unittest.IsolatedAsyncioTestCase):
    async def _check(self, url):
        engine = create_async_engine(url)
        statements = []
        event.listen(engine.sync_engine, "before_cursor_execute", lambda _c, _u, query, _p, _x, _m: statements.append(query))
        try:
            ctx = SimpleNamespace(db_session_factory=async_sessionmaker(engine))
            with patch.object(database_info, "get_database_url", return_value=url):
                light = await database_info.get_database_info(_user=None, ctx=ctx, include_charset=False)
                self.assertEqual(light.schema_version, "013")
                self.assertIsNone(light.charset)
                self.assertEqual(len(statements), 1)
                self.assertIn("alembic_version", statements[0])
                statements.clear()
                checked = await database_info.get_database_info(_user=None, ctx=ctx, include_charset=True)
                self.assertEqual(checked.charset.status, "ok")
                self.assertGreater(len(statements), 1)
                if engine.dialect.name == "mysql":
                    self.assertEqual(checked.charset.checked_columns, len(business_text_columns()))
                    self.assertTrue(any("information_schema.COLUMNS" in statement for statement in statements))
                else:
                    self.assertTrue(any("encoding" in statement.lower() for statement in statements))
        finally:
            await engine.dispose()

    async def test_sqlite(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-console-info-") as directory:
            url = f"sqlite+aiosqlite:///{Path(directory) / 'database.db'}"
            await asyncio.to_thread(_upgrade_target_schema, url, "013")
            await self._check(url)

    async def _server(self, env):
        if not os.environ.get(env):
            self.skipTest(f"{env} not configured; real database not verified")
        url = make_url(os.environ[env])
        self.assertIn(url.host, {"localhost", "127.0.0.1"})
        name = "console_info_test_" + uuid4().hex[:10]
        mysql = url.get_backend_name() == "mysql"
        admin = create_async_engine(url.set(database=None if mysql else "postgres"), isolation_level="AUTOCOMMIT")
        created = False
        try:
            async with admin.connect() as db:
                await db.execute(text(f"CREATE DATABASE {name}" + (" CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" if mysql else "")))
                created = True
            target = url.set(database=name).render_as_string(hide_password=False)
            await asyncio.to_thread(_upgrade_target_schema, target, "013")
            await self._check(target)
        finally:
            if created:
                async with admin.connect() as db:
                    await db.execute(text(f"DROP DATABASE {name}"))
            await admin.dispose()

    async def test_mysql(self):
        await self._server("DEEP_AGENT_TEST_MYSQL_URL")

    async def test_postgresql(self):
        await self._server("DEEP_AGENT_TEST_POSTGRES_URL")
