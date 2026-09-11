from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

from deep_agent.channels.gateway.app import create_gateway_app


ROOT = Path(__file__).resolve().parents[2]


class GatewayStartupRevisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_gateway_starts_on_migrated_schema_without_revision_gate(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-gateway-startup-", dir=ROOT) as directory:
            path = Path(directory) / "gateway.db"
            url = f"sqlite+aiosqlite:///{path}"
            config = Config(str(ROOT / "alembic.ini"))
            config.attributes["database_url"] = url
            await asyncio.to_thread(command.upgrade, config, "head")
            with sqlite3.connect(path) as db:
                current = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            # Same physical schema with a later revision must not require another
            # edit to gateway code. This label change is only in this isolated fixture.
            for revision in (current, "future-compatible-fixture"):
                with self.subTest(revision=revision):
                    with sqlite3.connect(path) as db:
                        db.execute("UPDATE alembic_version SET version_num=?", (revision,))
                    with (
                        patch("deep_agent.channels.gateway.app.get_database_url", return_value=url),
                        patch.dict("os.environ", {"DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "fixture-token"}),
                    ):
                        app = create_gateway_app()
                        async with app.router.lifespan_context(app):
                            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway") as client:
                                denied = await client.get("/health")
                                health = await client.get("/health", headers={"Authorization": "Bearer fixture-token"})
                            self.assertEqual(denied.status_code, 401)
                            self.assertEqual(health.status_code, 200)
                            self.assertEqual(health.json(), {"status": "ok", "database_revision": revision})
                            runtime = app.state.gateway.runtime
                            self.assertIsNotNone(runtime._poll_task)
                        self.assertTrue(runtime._poll_task.done())
