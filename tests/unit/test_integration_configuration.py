from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from alembic import command
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.db import migrate
from deep_agent.db.models import IntegrationSettings
from deep_agent.schemas.integration import IntegrationSettingsWrite
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores


class IntegrationSchemaTests(unittest.TestCase):
    def test_rejects_invalid_types_budgets_and_non_json_numbers(self):
        deep = {}
        for _ in range(10):
            deep = {"next": deep}
        for value in ([], None, {"": 1}, {"a": float("inf")}, {"a": "中" * 22000}, {"a": [0] * 2048}, deep, {"a": "\ud800"}):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValidationError):
                    IntegrationSettingsWrite(settings=value)
        with self.assertRaises(ValidationError):
            IntegrationSettingsWrite(settings={}, group="injected")


class IntegrationConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, factory = await make_sqlite_stores()
        self.app = FastAPI()
        self.app.include_router(api_router)
        allow_console_admin(self.app)
        init_context(AppContext.for_testing(stores=self.stores, db_engine=self.engine, db_session_factory=factory))
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test", headers={"user": "fixture"})

    async def asyncTearDown(self):
        await self.client.aclose()
        await get_context().event_persister.aclose()
        reset_context()
        await self.engine.dispose()

    async def test_catalog_reads_never_seed_and_saved_values_keep_json_types(self):
        for _ in range(2):
            response = await self.client.get("/api/v1/integrations")
            self.assertEqual(response.status_code, 200, response.text)
            groups = response.json()["groups"]
            self.assertEqual([item["code"] for item in groups], ["deeptable", "python-server"])
            self.assertTrue(all(item["integrations"][0]["settings"] == {} for item in groups))
            self.assertTrue(all(item["integrations"][0]["created_at"] is None for item in groups))
        self.assertEqual(await self.stores.integration_settings.list_all(), [])
        settings = {"agent_id": "not-a-foreign-key", "boolean": True, "count": 12, "ratio": 1.5, "nested": {"text": "中文|=\\\n", "list": [1, False, None]}}
        path = "/api/v1/integrations/deeptable-assistant"
        saved = await self.client.put(path, json={"settings": settings})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["settings"], settings)
        self.assertIsNotNone(saved.json()["created_at"])
        created_at = saved.json()["created_at"]
        changed = await self.client.put(path, json={"settings": {"mode": "new"}})
        self.assertEqual(changed.json()["settings"], {"mode": "new"})
        self.assertEqual(changed.json()["created_at"], created_at)
        self.assertEqual(len(await self.stores.integration_settings.list_all()), 1)
        empty = await self.client.put(path, json={"settings": {}})
        self.assertEqual(empty.json()["settings"], {})
        self.assertIsNone(empty.json()["created_at"])
        self.assertEqual(await self.stores.integration_settings.list_all(), [])
        for _ in range(2):
            cleared = await self.client.delete(path)
            self.assertEqual(cleared.status_code, 200)
            self.assertIsNone(cleared.json()["updated_at"])

    async def test_unknown_codes_and_invalid_writes_do_not_create_rows(self):
        for method in ("PUT", "DELETE"):
            response = await self.client.request(method, "/api/v1/integrations/unknown", json={"settings": {}} if method == "PUT" else None)
            self.assertEqual(response.status_code, 404, response.text)
        response = await self.client.put("/api/v1/integrations/deeptable-assistant", json={"settings": []})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(await self.stores.integration_settings.list_all(), [])

    async def test_concurrent_saves_are_atomic_single_row(self):
        store = self.stores.integration_settings
        results = await asyncio.gather(*(store.save("deepcube-assistant", {"index": index}) for index in range(12)))
        self.assertEqual(len(results), 12)
        rows = await store.list_all()
        self.assertEqual(len(rows), 1)
        self.assertIn(rows[0].settings["index"], range(12))


class IntegrationMigrationTests(unittest.TestCase):
    def test_incremental_004_ddl_compiles_for_supported_dialects(self):
        for url in ("sqlite+aiosqlite:///unused.db", "mysql+asyncmy://test:test@localhost/unused", "postgresql+asyncpg://test:test@localhost/unused"):
            config = migrate._build_alembic_config()
            config.attributes["database_url"] = url
            output = StringIO()
            with redirect_stdout(output):
                command.upgrade(config, "003:004", sql=True)
            sql = output.getvalue().upper()
            self.assertIn("CREATE TABLE INTEGRATION_SETTINGS", sql)
            self.assertNotIn("FOREIGN KEY", sql)
            self.assertNotIn("INSERT INTO INTEGRATION_SETTINGS", sql)

    def test_004_empty_upgrade_preserves_existing_data_and_round_trip(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_integration_migration_") as directory:
            path = Path(directory) / "db.sqlite"
            config = migrate._build_alembic_config()
            config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
            command.upgrade(config, "003")
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO global_settings (key,value,version) VALUES ('fixture','{\"keep\":true}',1)")
            command.upgrade(config, "004")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("004",))
                self.assertEqual(db.execute("SELECT count(*) FROM integration_settings").fetchone(), (0,))
                self.assertEqual({row[1] for row in db.execute("PRAGMA table_info(integration_settings)")}, set(IntegrationSettings.__table__.columns.keys()))
                self.assertEqual(db.execute("PRAGMA foreign_key_list(integration_settings)").fetchall(), [])
                db.execute("INSERT INTO integration_settings(code,settings) VALUES ('deeptable-assistant','{\"mode\":\"fixture\"}')")
            command.upgrade(config, "004")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM integration_settings").fetchone(), (1,))
            command.downgrade(config, "003")
            command.upgrade(config, "004")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM integration_settings").fetchone(), (0,))
                self.assertEqual(db.execute("SELECT value FROM global_settings WHERE key='fixture'").fetchone(), ('{"keep":true}',))
