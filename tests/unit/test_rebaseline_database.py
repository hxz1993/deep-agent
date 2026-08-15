from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command

from deep_agent.db import migrate
from maintenance.rebaseline_database import (
    SchemaValidationError,
    capture_database_state,
    rebaseline_database,
)
from sqlalchemy.ext.asyncio import create_async_engine


class RebaselineDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_stamp_legacy_revision_preserves_schema_and_rows(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "legacy-023.db"
            url = self._url(db_path)
            await self._upgrade_head(url)
            with sqlite3.connect(db_path) as connection:
                connection.execute("UPDATE alembic_version SET version_num = '023'")
                connection.execute(
                    "INSERT INTO ui_tool_definitions "
                    "(name, description, parameters_json_schema, source, enabled) "
                    "VALUES ('manual_tool', 'manual', '{}', 'manual', 1)"
                )
                connection.commit()

            before, after = await rebaseline_database(
                url,
                expected_revision="023",
                stamp=True,
            )

            self.assertIsNotNone(after)
            assert after is not None
            self.assertEqual(before.revision, "023")
            self.assertEqual(after.revision, "001")
            self.assertEqual(before.schema, after.schema)
            self.assertEqual(before.row_counts, after.row_counts)
            self.assertEqual(before.content_hashes, after.content_hashes)
            self.assertEqual(after.row_counts["ui_tool_definitions"], 1)

    async def test_rejects_missing_or_legacy_columns(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            missing_path = Path(tmpdir) / "missing.db"
            missing_url = self._url(missing_path)
            await self._upgrade_head(missing_url)
            with sqlite3.connect(missing_path) as connection:
                connection.execute("ALTER TABLE agents DROP COLUMN temperature")
                connection.commit()
            with self.assertRaisesRegex(SchemaValidationError, "missing columns temperature"):
                await self._capture(missing_url)

            legacy_path = Path(tmpdir) / "legacy-column.db"
            legacy_url = self._url(legacy_path)
            await self._upgrade_head(legacy_url)
            with sqlite3.connect(legacy_path) as connection:
                connection.execute("ALTER TABLE agents ADD COLUMN max_requests INTEGER")
                connection.commit()
            with self.assertRaisesRegex(SchemaValidationError, "unexpected columns max_requests"):
                await self._capture(legacy_url)

    async def _upgrade_head(self, url: str) -> None:
        with patch("deep_agent.db.url.get_database_url", return_value=url):
            with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                await asyncio.to_thread(
                    command.upgrade,
                    migrate._build_alembic_config(),
                    "001",
                )

    @staticmethod
    async def _capture(url: str):
        engine = create_async_engine(url)
        try:
            return await capture_database_state(engine)
        finally:
            await engine.dispose()

    @staticmethod
    def _url(path: Path) -> str:
        return f"sqlite+aiosqlite:///{path.as_posix()}"


if __name__ == "__main__":
    unittest.main()
