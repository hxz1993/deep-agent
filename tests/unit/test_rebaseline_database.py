from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from sqlalchemy.ext.asyncio import create_async_engine

from deep_agent.db import migrate
from maintenance.rebaseline_database import (
    SchemaValidationError,
    capture_database_state,
    rebaseline_database,
)


class RebaselineDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_stamp_revision_012_preserves_schema_and_all_rows(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "legacy-012.db"
            url = self._url(db_path)
            await self._upgrade_baseline(url)
            with sqlite3.connect(db_path) as connection:
                connection.execute("UPDATE alembic_version SET version_num='012'")
                connection.execute(
                    "INSERT INTO chat_sessions "
                    "(session_id,agent_id,user_id,title,next_sequence) "
                    "VALUES ('session-1',"
                    "'00000000-0000-0000-0000-000000000001','user-1','保留会话',2)"
                )
                connection.execute(
                    "INSERT INTO chat_session_events "
                    "(event_id,session_id,sequence,schema_version,event_type,payload) "
                    "VALUES ('11111111111111111111111111111111','session-1',1,1,"
                    "'message','{\"content\":\"保留事件\"}')"
                )
                connection.commit()

            before, after = await rebaseline_database(
                url,
                expected_revision="012",
                stamp=True,
            )

            self.assertIsNotNone(after)
            assert after is not None
            self.assertEqual(before.revision, "012")
            self.assertEqual(after.revision, "001")
            self.assertEqual(before.schema, after.schema)
            self.assertEqual(before.row_counts, after.row_counts)
            self.assertEqual(before.content_hashes, after.content_hashes)
            self.assertEqual(after.row_counts["chat_sessions"], 1)
            self.assertEqual(after.row_counts["chat_session_events"], 1)

    async def test_audit_does_not_stamp_without_explicit_request(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "audit-012.db"
            url = self._url(db_path)
            await self._upgrade_baseline(url)
            with sqlite3.connect(db_path) as connection:
                connection.execute("UPDATE alembic_version SET version_num='012'")
                connection.commit()

            before, after = await rebaseline_database(url, expected_revision="012")
            self.assertEqual(before.revision, "012")
            self.assertIsNone(after)
            with sqlite3.connect(db_path) as connection:
                revision = connection.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchone()
            self.assertEqual(revision, ("012",))

    async def test_rejects_unexpected_table_and_missing_column(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            extra_path = Path(tmpdir) / "extra.db"
            extra_url = self._url(extra_path)
            await self._upgrade_baseline(extra_url)
            with sqlite3.connect(extra_path) as connection:
                connection.execute("CREATE TABLE stale_table (id INTEGER)")
                connection.commit()
            with self.assertRaisesRegex(SchemaValidationError, "unexpected tables: stale_table"):
                await self._capture(extra_url)

            missing_path = Path(tmpdir) / "missing.db"
            missing_url = self._url(missing_path)
            await self._upgrade_baseline(missing_url)
            with sqlite3.connect(missing_path) as connection:
                connection.execute("ALTER TABLE agents DROP COLUMN temperature")
                connection.commit()
            with self.assertRaisesRegex(SchemaValidationError, "missing columns temperature"):
                await self._capture(missing_url)

    async def test_rejects_revision_other_than_012(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "already-001.db"
            url = self._url(db_path)
            await self._upgrade_baseline(url)
            with self.assertRaisesRegex(
                SchemaValidationError,
                "Database revision is 001, expected 012",
            ):
                await rebaseline_database(url, expected_revision="012", stamp=True)

    async def _upgrade_baseline(self, url: str) -> None:
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
