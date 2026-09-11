from __future__ import annotations

import unittest
from contextlib import redirect_stdout
from io import StringIO

from alembic import command

from deep_agent.db import migrate


class BaselineMigrationDialectTests(unittest.TestCase):
    def test_production_baseline_compiles_for_all_supported_dialects(self) -> None:
        urls = {
            "sqlite": "sqlite+aiosqlite:///baseline.db",
            "mysql": "mysql+asyncmy://user:pass@127.0.0.1:3306/deep_agent",
            "postgresql": "postgresql+asyncpg://user:pass@127.0.0.1:5432/deep_agent",
        }
        for dialect, url in urls.items():
            with self.subTest(dialect=dialect):
                config = migrate._build_alembic_config()
                config.attributes["database_url"] = url
                output = StringIO()
                with redirect_stdout(output):
                    command.upgrade(config, "002", sql=True)
                sql = output.getvalue().upper()
                self.assertEqual(sql.count("CREATE TABLE"), 15)
                self.assertIn("CREATE TABLE GLOBAL_SETTINGS", sql)
                self.assertIn("CREATE TABLE LLM_PROVIDERS", sql)
                self.assertIn("CREATE TABLE CHAT_SESSION_EVENTS", sql)
                self.assertIn("CREATE TABLE CHANNEL_ACCOUNTS", sql)
                self.assertIn("CREATE TABLE CHANNEL_SESSION_BINDINGS", sql)
                self.assertIn("CREATE TABLE CHANNEL_MESSAGE_RUNS", sql)
                self.assertIn("INSERT INTO ALEMBIC_VERSION", sql)
                self.assertIn("002", sql)
                self.assertNotIn("CHAT_MESSAGES", sql)
                self.assertNotIn("AUXILIARY_MODEL_CONFIGS", sql)
                # Historical baseline SQL stays available; current defaults run only
                # after an online upgrade to the current head.
                with redirect_stdout(StringIO()):
                    with self.assertRaisesRegex(RuntimeError, "online database"):
                        command.upgrade(config, "002:head", sql=True)


if __name__ == "__main__":
    unittest.main()
