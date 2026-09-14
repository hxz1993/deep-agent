from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from deep_agent.db.factory import create_engine
from deep_agent.db.migrate import _build_alembic_config
from deep_agent.local_dev import LocalIdentity, LocalIdentityAdapter, _parser
from deep_agent.local_preview import enforce_read_only, preview_lifespan


class PreviewHttpTests(unittest.TestCase):
    def test_write_methods_and_websockets_rejected_before_handler(self):
        app = FastAPI()
        calls = []

        @app.api_route("/probe", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        async def handler():
            calls.append(True)
            return {"ok": True}

        wrapped = LocalIdentityAdapter(app, LocalIdentity(user="test"), read_only_preview=True)
        with TestClient(wrapped) as client:
            for method in ["post", "put", "patch", "delete"]:
                response = getattr(client, method)("/probe")
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.headers["X-Deep-Agent-Read-Only"], "1")
                self.assertIn("只读预览", response.json()["detail"])
            self.assertEqual(calls, [])
            for method in ["get", "head", "options"]:
                self.assertEqual(getattr(client, method)("/probe").status_code, 200)
            from starlette.websockets import WebSocketDisconnect
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect("/probe"):
                    pass

    def test_default_entry_does_not_enable_preview(self):
        self.assertFalse(_parser().parse_args([]).read_only_preview)
        self.assertTrue(_parser().parse_args(["--read-only-preview"]).read_only_preview)
        app = FastAPI()

        @app.post("/probe")
        async def handler():
            return {"ok": True}

        with TestClient(LocalIdentityAdapter(app, LocalIdentity(user="test"))) as client:
            self.assertEqual(client.post("/probe").status_code, 200)


class PreviewDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="preview-tests-TMP_to_delete-")
        self.path = Path(self.temp.name) / "database.sqlite3"
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE alembic_version(version_num VARCHAR(32))")
            head = ScriptDirectory.from_config(_build_alembic_config()).get_current_head()
            db.execute("INSERT INTO alembic_version VALUES(?)", (head,))
            db.execute("CREATE TABLE preview_probe(status TEXT)")
            db.execute("INSERT INTO preview_probe VALUES('running')")
        self.url = f"sqlite+aiosqlite:///{self.path}"

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_database_guard_blocks_incidental_get_writes_and_new_connections(self):
        engine, factory = create_engine(self.url)
        enforce_read_only(engine)
        try:
            for _ in range(2):
                async with factory() as session:
                    self.assertEqual((await session.execute(text("SELECT status FROM preview_probe"))).scalar(), "running")
                    with self.assertRaises(DBAPIError):
                        await session.execute(text("UPDATE preview_probe SET status='interrupted'"))
                    await session.rollback()
                    with self.assertRaises(DBAPIError):
                        await session.execute(text("CREATE TABLE forbidden(id INTEGER)"))
                await engine.dispose()
        finally:
            await engine.dispose()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT status FROM preview_probe").fetchone()[0], "running")

    async def test_preview_startup_skips_all_normal_bootstrap_work(self):
        from deep_agent.core.context import AppContext
        with patch("deep_agent.local_preview.local_runtime_enabled", return_value=True), \
             patch("deep_agent.local_preview.get_database_url", return_value=self.url), \
             patch.object(AppContext, "bootstrap", new_callable=AsyncMock) as bootstrap, \
             patch("deep_agent.db.factory.upgrade_database", new_callable=AsyncMock) as migrate, \
             patch("deep_agent.skills.runtime.SkillRuntimeLibrary.reconcile", new_callable=AsyncMock) as reconcile, \
             patch("deep_agent.agent.event_persister.EventPersister.start", new_callable=AsyncMock) as start:
            async with preview_lifespan(None):
                pass
            bootstrap.assert_not_awaited()
            migrate.assert_not_awaited()
            reconcile.assert_not_awaited()
            start.assert_not_awaited()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT status FROM preview_probe").fetchone()[0], "running")

    async def test_revision_mismatch_fails_without_migration(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE alembic_version SET version_num='old'")
        with patch("deep_agent.local_preview.local_runtime_enabled", return_value=True), \
             patch("deep_agent.local_preview.get_database_url", return_value=self.url):
            with self.assertRaisesRegex(RuntimeError, "migration head"):
                async with preview_lifespan(None):
                    self.fail("must not start")
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone()[0], "old")

    async def test_deployment_cannot_enter_preview(self):
        with patch("deep_agent.local_preview.local_runtime_enabled", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "local_dev"):
                async with preview_lifespan(None):
                    self.fail("must not start")
