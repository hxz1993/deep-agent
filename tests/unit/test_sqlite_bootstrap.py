from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.db import migrate
from deep_agent.db.dialect import DbDialect
from deep_agent.db.factory import create_engine
from deep_agent.db.migrate import upgrade_database_sync
from deep_agent.db.stores.bundle import create_data_stores


EXPECTED_TABLES = {
    "agents",
    "chat_session_events",
    "chat_sessions",
    "channel_accounts",
    "channel_message_runs",
    "channel_session_bindings",
    "global_settings",
    "llm_models",
    "llm_providers",
    "mcp_servers",
    "model_catalog",
    "skill_packages",
    "ui_tool_definitions",
    "ui_tool_packages",
}


class SqliteBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_migrate_and_create_stores(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
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
                self.assertIsNotNone(stores.model_catalog)
                self.assertIsNotNone(stores.mcp)
                self.assertIsNotNone(stores.ui_tool)
                self.assertIsNotNone(stores.events)
                self.assertIsNotNone(stores.skill_package)
                self.assertIsNotNone(stores.channel)

                await stores.coordinator.append(
                    "sess-bootstrap",
                    [ModelRequest(parts=[UserPromptPart(content="hello")])],
                    agent_id=(await stores.agent.list_all())[0].id,
                    user_id="local-user",
                    turn_id="1" * 32,
                    run_id="run-bootstrap",
                )
                history = await stores.coordinator.get_history(
                    "sess-bootstrap",
                    agent_id=(await stores.agent.list_all())[0].id,
                )
                self.assertEqual(len(history), 1)
            finally:
                await engine.dispose()

    async def test_single_production_baseline_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "baseline.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    config = migrate._build_alembic_config()
                    await asyncio.to_thread(command.upgrade, config, "head")
                    self._assert_current_baseline(db_path)

                    await asyncio.to_thread(command.downgrade, config, "base")
                    self.assertEqual(self._business_tables(db_path), set())

                    await asyncio.to_thread(command.upgrade, config, "head")
                    self._assert_current_baseline(db_path)

    def _assert_current_baseline(self, db_path: Path) -> None:
        self.assertEqual(self._revision(db_path), "002")
        self.assertEqual(self._business_tables(db_path), EXPECTED_TABLES)
        self.assertEqual(self._primary_key(db_path, "chat_sessions"), ["session_id"])
        self.assertEqual(self._primary_key(db_path, "chat_session_events"), ["event_id"])
        self.assertEqual(
            self._columns(db_path, "chat_session_events"),
            {
                "event_id", "session_id", "sequence", "schema_version",
                "event_type", "status", "turn_id", "run_id", "search_text",
                "payload", "created_at", "updated_at",
            },
        )
        self.assertEqual(
            self._index_columns(db_path, "ix_chat_sessions_agent_user_updated"),
            ["agent_id", "user_id", "updated_at"],
        )
        self.assertEqual(
            self._index_columns(db_path, "ix_llm_models_provider_id"),
            ["provider_id"],
        )
        self.assertIn(
            ("provider_id", "llm_providers", "id", "RESTRICT"),
            self._foreign_keys(db_path, "llm_models"),
        )
        self.assertIn("skills", self._columns(db_path, "agents"))
        self.assertIn("enabled", self._columns(db_path, "llm_models"))
        for table in (
            "llm_providers",
            "skill_packages",
            "mcp_servers",
            "ui_tool_packages",
            "ui_tool_definitions",
        ):
            self.assertNotIn("enabled", self._columns(db_path, table))

        self.assertEqual(self._row_count(db_path, "llm_providers"), 1)
        self.assertEqual(self._row_count(db_path, "llm_models"), 1)
        self.assertEqual(self._row_count(db_path, "agents"), 1)
        self.assertEqual(self._row_count(db_path, "mcp_servers"), 1)
        self.assertEqual(
            self._scalar(db_path, "SELECT value FROM global_settings WHERE key='web_search'"),
            '{"mode":"native"}',
        )
        self.assertEqual(
            json.loads(str(self._scalar(db_path, "SELECT capabilities FROM agents WHERE name='default'"))),
            [],
        )
        self.assertEqual(
            json.loads(str(self._scalar(db_path, "SELECT skills FROM agents WHERE name='default'"))),
            [],
        )
        provider_id = str(
            self._scalar(db_path, "SELECT provider_id FROM llm_models WHERE name='openai:gpt-4o'")
        )
        self.assertEqual(len(provider_id), 32)
        self.assertNotIn("-", provider_id)
        self.assertEqual(
            self._scalar(db_path, "SELECT model_id FROM agents WHERE name='default'"),
            self._scalar(db_path, "SELECT id FROM llm_models WHERE name='openai:gpt-4o'"),
        )
        self.assertEqual(
            json.loads(
                str(
                    self._scalar(
                        db_path,
                        "SELECT inspection FROM mcp_servers WHERE name='deepcube'",
                    )
                )
            ),
            {"status": "not_applicable"},
        )

    @staticmethod
    def _revision(db_path: Path) -> str:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        assert row is not None
        return str(row[0])

    @staticmethod
    def _business_tables(db_path: Path) -> set[str]:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name <> 'alembic_version'"
            ).fetchall()
        return {str(row[0]) for row in rows}

    @staticmethod
    def _columns(db_path: Path, table_name: str) -> set[str]:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row[1]) for row in rows}

    @staticmethod
    def _primary_key(db_path: Path, table_name: str) -> list[str]:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [str(row[1]) for row in sorted(rows, key=lambda item: item[5]) if row[5]]

    @staticmethod
    def _index_columns(db_path: Path, index_name: str) -> list[str]:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(f"PRAGMA index_info({index_name})").fetchall()
        return [str(row[2]) for row in rows]

    @staticmethod
    def _foreign_keys(db_path: Path, table_name: str) -> set[tuple[str, str, str, str]]:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(f"PRAGMA foreign_key_list({table_name})").fetchall()
        return {
            (str(row[3]), str(row[2]), str(row[4]), str(row[6]).upper())
            for row in rows
        }

    @staticmethod
    def _row_count(db_path: Path, table_name: str) -> int:
        return int(SqliteBootstrapTests._scalar(db_path, f"SELECT COUNT(*) FROM {table_name}"))

    @staticmethod
    def _scalar(db_path: Path, query: str):
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(query).fetchone()
        assert row is not None
        return row[0]


if __name__ == "__main__":
    unittest.main()
