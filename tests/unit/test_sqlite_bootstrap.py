from __future__ import annotations

import asyncio
import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch

from alembic import command
from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.db.dialect import DbDialect
from deep_agent.db.migrate import upgrade_database_sync
from deep_agent.db import migrate
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
                self.assertIsNotNone(stores.llm_model)
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

    async def test_migration_018_upgrade_and_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "migration-018.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    await asyncio.to_thread(upgrade_database_sync)
                    self.assertEqual(self._revision(db_path), "018")
                    self.assertNotIn("agent_ids", self._columns(db_path, "ui_tool_definitions"))

                    await asyncio.to_thread(command.downgrade, migrate._build_alembic_config(), "017")
                    self.assertEqual(self._revision(db_path), "017")
                    self.assertIn("agent_ids", self._columns(db_path, "ui_tool_definitions"))

                    await asyncio.to_thread(command.upgrade, migrate._build_alembic_config(), "head")
                    self.assertEqual(self._revision(db_path), "018")
                    self.assertNotIn("agent_ids", self._columns(db_path, "ui_tool_definitions"))

    @staticmethod
    def _revision(db_path: Path) -> str:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert row is not None
        return str(row[0])

    @staticmethod
    def _columns(db_path: Path, table_name: str) -> set[str]:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row[1]) for row in rows}


if __name__ == "__main__":
    unittest.main()
