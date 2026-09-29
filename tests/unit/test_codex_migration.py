"""The unpublished 007 marker must not hide newly added Codex columns."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from deep_agent.db.migrate import _repair_unpublished_schema


class CodexMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_007_is_repaired_repeatedly_without_changing_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_codex_migration_") as directory:
            path = Path(directory) / "old-007.db"
            with sqlite3.connect(path) as db:
                db.executescript("""
                    CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL);
                    INSERT INTO alembic_version VALUES ('007');
                    CREATE TABLE llm_providers (id VARCHAR(36) PRIMARY KEY, name VARCHAR(191));
                    INSERT INTO llm_providers VALUES ('provider-1', 'existing');
                    CREATE TABLE llm_models (id VARCHAR(36) PRIMARY KEY, provider_id VARCHAR(36));
                    INSERT INTO llm_models VALUES ('model-1', 'provider-1');
                """)
            url = f"sqlite+aiosqlite:///{path}"
            await _repair_unpublished_schema(url)
            await _repair_unpublished_schema(url)
            with sqlite3.connect(path) as db:
                provider_columns = {row[1] for row in db.execute("PRAGMA table_info(llm_providers)")}
                model_columns = {row[1] for row in db.execute("PRAGMA table_info(llm_models)")}
                self.assertEqual(db.execute("SELECT COUNT(*) FROM llm_providers").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM llm_models").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT auth_method, auth_revision, auth_status FROM llm_providers").fetchone(), ("api_key", 0, "unconfigured"))
                self.assertEqual(db.execute("SELECT purpose FROM llm_models").fetchone()[0], "chat")
                self.assertTrue({"auth_method", "credential_json", "auth_revision", "auth_status"} <= provider_columns)
                self.assertIn("purpose", model_columns)
