from __future__ import annotations

import asyncio
import json
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

    async def test_migrations_remove_mcp_ui_discovery_and_add_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "migration-018.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    await asyncio.to_thread(upgrade_database_sync)
                    self.assertEqual(self._revision(db_path), "023")
                    self.assertTrue(
                        self._table_exists(db_path, "chat_context_checkpoints")
                    )
                    self.assertNotIn("agent_ids", self._columns(db_path, "ui_tool_definitions"))
                    self.assertIn("inspection", self._columns(db_path, "mcp_servers"))
                    self.assertNotIn("resource_capabilities", self._columns(db_path, "mcp_servers"))

                    await asyncio.to_thread(command.downgrade, migrate._build_alembic_config(), "018")
                    self.assertEqual(self._revision(db_path), "018")
                    self.assertIn("mcp_server_name", self._columns(db_path, "ui_tool_definitions"))
                    self.assertIn("override", self._columns(db_path, "ui_tool_definitions"))
                    self.assertIn("discovered_at", self._columns(db_path, "ui_tool_definitions"))
                    self.assertNotIn("resource_capabilities", self._columns(db_path, "mcp_servers"))
                    self.assertNotIn("inspection", self._columns(db_path, "mcp_servers"))

                    self._seed_legacy_mcp_ui_state(db_path)

                    await asyncio.to_thread(command.upgrade, migrate._build_alembic_config(), "head")
                    self.assertEqual(self._revision(db_path), "023")
                    columns = self._columns(db_path, "ui_tool_definitions")
                    self.assertNotIn("mcp_server_name", columns)
                    self.assertNotIn("override", columns)
                    self.assertNotIn("discovered_at", columns)
                    self.assertIn("inspection", self._columns(db_path, "mcp_servers"))
                    self.assertNotIn("resource_capabilities", self._columns(db_path, "mcp_servers"))
                    self.assertEqual(self._mcp_ui_tool_count(db_path), 0)
                    self.assertNotIn("ui_tools", self._deepcube_config(db_path))

    async def test_migration_021_removes_agent_request_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "migration-020.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    config = migrate._build_alembic_config()
                    await asyncio.to_thread(command.upgrade, config, "020")
                    self.assertIn("max_requests", self._columns(db_path, "agents"))

                    with sqlite3.connect(db_path) as conn:
                        conn.execute(
                            """
                            INSERT INTO llm_models (
                                id, name, model, created_at, updated_at
                            ) VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                            """,
                            ("model-021", "model-021", "test:model"),
                        )
                        conn.execute(
                            """
                            INSERT INTO agents (
                                id, name, model_id, max_requests, prompt,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                            """,
                            ("agent-021", "agent-021", "model-021", 12, "test"),
                        )
                        conn.commit()

                    await asyncio.to_thread(command.upgrade, config, "021")
                    self.assertEqual(self._revision(db_path), "021")
                    self.assertNotIn("max_requests", self._columns(db_path, "agents"))
                    self.assertIn(
                        "capabilities_source",
                        self._columns(db_path, "llm_models"),
                    )

                    await asyncio.to_thread(command.downgrade, config, "020")
                    self.assertEqual(self._revision(db_path), "020")
                    self.assertIn("max_requests", self._columns(db_path, "agents"))
                    with sqlite3.connect(db_path) as conn:
                        row = conn.execute(
                            "SELECT max_requests FROM agents WHERE id = 'agent-021'"
                        ).fetchone()
                    self.assertEqual(row, (None,))

                    await asyncio.to_thread(command.upgrade, config, "head")
                    self.assertEqual(self._revision(db_path), "023")
                    self.assertNotIn("max_requests", self._columns(db_path, "agents"))
                    llm_columns = self._columns(db_path, "llm_models")
                    self.assertIn("context_window_tokens", llm_columns)
                    self.assertIn("max_output_tokens", llm_columns)
                    self.assertIn("tags", llm_columns)
                    self.assertNotIn("capabilities_source", llm_columns)
                    self.assertTrue(self._table_exists(db_path, "model_catalog"))
                    self.assertTrue(
                        self._table_exists(db_path, "chat_context_checkpoints")
                    )
                    self.assertIn(
                        "release_date",
                        self._columns(db_path, "model_catalog"),
                    )

                    await asyncio.to_thread(command.downgrade, config, "021")
                    self.assertEqual(self._revision(db_path), "021")
                    llm_columns = self._columns(db_path, "llm_models")
                    self.assertNotIn("context_window_tokens", llm_columns)
                    self.assertNotIn("max_output_tokens", llm_columns)
                    self.assertNotIn("tags", llm_columns)
                    self.assertIn("capabilities_source", llm_columns)
                    self.assertFalse(self._table_exists(db_path, "model_catalog"))
                    self.assertFalse(
                        self._table_exists(db_path, "chat_context_checkpoints")
                    )

                    await asyncio.to_thread(command.upgrade, config, "head")
                    self.assertEqual(self._revision(db_path), "023")
                    self.assertNotIn(
                        "capabilities_source",
                        self._columns(db_path, "llm_models"),
                    )

    async def test_migration_020_preserves_resource_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "migration-019.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    config = migrate._build_alembic_config()
                    await asyncio.to_thread(command.upgrade, config, "019")
                    with sqlite3.connect(db_path) as conn:
                        conn.execute(
                            """
                            UPDATE mcp_servers
                            SET resource_capabilities = ?
                            WHERE name = 'deepcube'
                            """,
                            (
                                json.dumps(
                                    {
                                        "status": "supported",
                                        "resource_count": 2,
                                        "template_count": 1,
                                        "checked_at": "2026-07-23T10:00:00Z",
                                    }
                                ),
                            ),
                        )
                        conn.execute(
                            """
                            INSERT INTO mcp_servers (
                                name, transport, config, resource_capabilities,
                                enabled, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                            """,
                            (
                                "remote-docs",
                                "url",
                                '{"url":"http://example.test/mcp"}',
                                json.dumps(
                                    {
                                        "status": "supported",
                                        "resource_count": 2,
                                        "template_count": 1,
                                        "checked_at": "2026-07-23T10:00:00Z",
                                    }
                                ),
                                1,
                            ),
                        )
                        conn.commit()

                    await asyncio.to_thread(command.upgrade, config, "020")
                    inspection = self._mcp_inspection(db_path, "deepcube")
                    self.assertEqual(inspection["status"], "not_applicable")
                    inspection = self._mcp_inspection(db_path, "remote-docs")
                    self.assertEqual(inspection["status"], "unknown")
                    self.assertEqual(inspection["resources"]["status"], "supported")
                    self.assertEqual(inspection["resources"]["count"], 2)
                    self.assertEqual(inspection["resources"]["template_count"], 1)

                    await asyncio.to_thread(command.downgrade, config, "019")
                    with sqlite3.connect(db_path) as conn:
                        raw = conn.execute(
                            "SELECT resource_capabilities FROM mcp_servers "
                            "WHERE name = 'remote-docs'"
                        ).fetchone()
                    assert raw is not None
                    snapshot = json.loads(str(raw[0]))
                    self.assertEqual(snapshot["resource_count"], 2)
                    self.assertEqual(snapshot["template_count"], 1)

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

    @staticmethod
    def _table_exists(db_path: Path, table_name: str) -> bool:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
        return row is not None

    @staticmethod
    def _mcp_inspection(db_path: Path, name: str) -> dict:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT inspection FROM mcp_servers WHERE name = ?",
                (name,),
            ).fetchone()
        assert row is not None
        return json.loads(str(row[0]))

    @staticmethod
    def _seed_legacy_mcp_ui_state(db_path: Path) -> None:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT config FROM mcp_servers WHERE name = 'deepcube'"
            ).fetchone()
            assert row is not None
            config = json.loads(str(row[0]))
            config["ui_tools"] = {
                "enabled": True,
                "mode": "resource",
                "refresh_seconds": 300,
            }
            conn.execute(
                "UPDATE mcp_servers SET config = ? WHERE name = 'deepcube'",
                (json.dumps(config),),
            )
            conn.execute(
                """
                INSERT INTO ui_tool_definitions (
                    name, description, parameters_json_schema, source,
                    mcp_server_name, enabled, override, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    "deepcube__legacy_form",
                    "legacy",
                    '{"type":"object"}',
                    "mcp",
                    "deepcube",
                    1,
                    0,
                ),
            )
            conn.commit()

    @staticmethod
    def _mcp_ui_tool_count(db_path: Path) -> int:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM ui_tool_definitions WHERE source = 'mcp'"
            ).fetchone()
        assert row is not None
        return int(row[0])

    @staticmethod
    def _deepcube_config(db_path: Path) -> dict:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT config FROM mcp_servers WHERE name = 'deepcube'"
            ).fetchone()
        assert row is not None
        return json.loads(str(row[0]))


if __name__ == "__main__":
    unittest.main()
