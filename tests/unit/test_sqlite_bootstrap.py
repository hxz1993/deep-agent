from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.db.dialect import DbDialect
from deep_agent.db.migrate import upgrade_database_sync
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.db.factory import create_engine


class SqliteBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_migrate_and_create_stores(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bootstrap.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    await asyncio.to_thread(upgrade_database_sync)

            engine, session_factory = create_engine(url)
            try:
                stores = create_data_stores(DbDialect.SQLITE, session_factory)
                self.assertIsNotNone(stores.agent)
                self.assertIsNotNone(stores.mcp)
                self.assertIsNotNone(stores.ui_tool)

                await stores.coordinator.append(
                    "sess-bootstrap",
                    [ModelRequest(parts=[UserPromptPart(content="hello")])],
                )
                history = await stores.coordinator.get_history("sess-bootstrap")
                self.assertEqual(len(history), 1)
            finally:
                await engine.dispose()


if __name__ == "__main__":
    unittest.main()
