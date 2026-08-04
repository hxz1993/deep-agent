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
    "auxiliary_model_configs",
    "chat_context_checkpoints",
    "chat_messages",
    "chat_sessions",
    "llm_models",
    "mcp_servers",
    "model_catalog",
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
                self.assertIsNotNone(stores.context_checkpoint)

                await stores.coordinator.append(
                    "sess-bootstrap",
                    [ModelRequest(parts=[UserPromptPart(content="hello")])],
                )
                history = await stores.coordinator.get_history("sess-bootstrap")
                self.assertEqual(len(history), 1)
            finally:
                await engine.dispose()

    async def test_single_baseline_upgrade_downgrade_upgrade(self) -> None:
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

    async def test_002_groups_ui_tools_and_rewrites_agent_packages(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "tool-packages.db"
            url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
            with patch("deep_agent.db.url.get_database_url", return_value=url):
                with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                    config = migrate._build_alembic_config()
                    await asyncio.to_thread(command.upgrade, config, "001")
                    with sqlite3.connect(db_path) as connection:
                        for name in ("deep_cube__clarify", "deep_cube__config_fill"):
                            connection.execute(
                                "INSERT INTO ui_tool_definitions "
                                "(name, description, parameters_json_schema, source, enabled) "
                                "VALUES (?, 'DeepCube', '{}', 'manual', 1)",
                                (name,),
                            )
                        connection.execute(
                            "UPDATE agents SET capabilities = ? WHERE name = 'default'",
                            (
                                json.dumps(
                                    [
                                        "native",
                                        "python-sandbox",
                                        "attachment",
                                        "ui:deep_cube__clarify",
                                        "ui:builtin__confirm",
                                        "mcp:text_tools",
                                    ]
                                ),
                            ),
                        )
                        connection.commit()

                    await asyncio.to_thread(command.upgrade, config, "002")
                    self.assertEqual(self._revision(db_path), "002")
                    self.assertEqual(self._row_count(db_path, "ui_tool_packages"), 1)
                    self.assertEqual(
                        self._scalar(
                            db_path,
                            "SELECT package_code FROM ui_tool_definitions "
                            "WHERE name = 'deep_cube__clarify'",
                        ),
                        "ui:deepcube",
                    )
                    capabilities = json.loads(
                        str(
                            self._scalar(
                                db_path,
                                "SELECT capabilities FROM agents WHERE name = 'default'",
                            )
                        )
                    )
                    self.assertEqual(
                        capabilities,
                        [
                            "builtin:general",
                            "builtin:sandbox",
                            "builtin:attachment",
                            "ui:deepcube",
                            "ui:builtin",
                            "mcp:text_tools",
                        ],
                    )

                    await asyncio.to_thread(command.downgrade, config, "001")
                    downgraded = json.loads(
                        str(
                            self._scalar(
                                db_path,
                                "SELECT capabilities FROM agents WHERE name = 'default'",
                            )
                        )
                    )
                    self.assertEqual(
                        downgraded,
                        [
                            "native",
                            "python-sandbox",
                            "attachment",
                            "ui:deep_cube__clarify",
                            "ui:deep_cube__config_fill",
                            "ui:builtin__confirm",
                            "ui:builtin__choice",
                            "mcp:text_tools",
                        ],
                    )
                    await asyncio.to_thread(command.upgrade, config, "002")
                    self.assertEqual(self._revision(db_path), "002")

    async def test_002_rejects_unmapped_ui_data_before_ddl(self) -> None:
        cases = (
            ("unknown-tool", "INSERT INTO ui_tool_definitions "
             "(name, description, parameters_json_schema, source, enabled) "
             "VALUES ('other__tool', 'Other', '{}', 'manual', 1)"),
            ("unknown-reference", "UPDATE agents SET capabilities = '[\"ui:other__tool\"]' "
             "WHERE name = 'default'"),
            ("missing-deepcube-reference", "UPDATE agents SET capabilities = "
             "'[\"ui:deep_cube__clarify\"]' WHERE name = 'default'"),
        )
        for case_name, statement in cases:
            with self.subTest(case=case_name):
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                    db_path = Path(tmpdir) / f"{case_name}.db"
                    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
                    with patch("deep_agent.db.url.get_database_url", return_value=url):
                        with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                            config = migrate._build_alembic_config()
                            await asyncio.to_thread(command.upgrade, config, "001")
                            with sqlite3.connect(db_path) as connection:
                                connection.execute(statement)
                                connection.commit()
                            with self.assertRaises(RuntimeError):
                                await asyncio.to_thread(command.upgrade, config, "002")
                    self.assertEqual(self._revision(db_path), "001")
                    self.assertNotIn("ui_tool_packages", self._business_tables(db_path))
                    self.assertNotIn("package_code", self._columns(db_path, "ui_tool_definitions"))

    def _assert_current_baseline(self, db_path: Path) -> None:
        self.assertEqual(self._revision(db_path), "002")
        self.assertEqual(self._business_tables(db_path), EXPECTED_TABLES)
        self.assertNotIn("max_requests", self._columns(db_path, "agents"))
        self.assertNotIn("capabilities_source", self._columns(db_path, "llm_models"))
        self.assertNotIn("resource_capabilities", self._columns(db_path, "mcp_servers"))
        self.assertIn("package_code", self._columns(db_path, "ui_tool_definitions"))
        self.assertEqual(
            self._primary_key(db_path, "chat_context_checkpoints"),
            ["agent_id", "session_id"],
        )
        self.assertEqual(
            self._index_columns(db_path, "ix_chat_sessions_user_id_agent_id_updated_at"),
            ["user_id", "agent_id", "updated_at"],
        )
        self.assertEqual(self._row_count(db_path, "llm_models"), 1)
        self.assertEqual(self._row_count(db_path, "agents"), 1)
        self.assertEqual(self._row_count(db_path, "mcp_servers"), 3)
        self.assertEqual(self._row_count(db_path, "auxiliary_model_configs"), 1)
        self.assertEqual(
            self._scalar(
                db_path,
                "SELECT model_id FROM agents WHERE name = 'default'",
            ),
            self._scalar(
                db_path,
                "SELECT id FROM llm_models WHERE name = 'openai:gpt-4o'",
            ),
        )
        for name in ("deepcube", "doc_search", "text_tools"):
            raw = self._scalar(
                db_path,
                "SELECT inspection FROM mcp_servers WHERE name = ?",
                (name,),
            )
            self.assertEqual(json.loads(str(raw)), {"status": "not_applicable"})

    @staticmethod
    def _revision(db_path: Path) -> str:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
        assert row is not None
        return str(row[0])

    @staticmethod
    def _business_tables(db_path: Path) -> set[str]:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
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
    def _row_count(db_path: Path, table_name: str) -> int:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
        assert row is not None
        return int(row[0])

    @staticmethod
    def _scalar(
        db_path: Path,
        statement: str,
        params: tuple[object, ...] = (),
    ) -> object:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(statement, params).fetchone()
        assert row is not None
        return row[0]


if __name__ == "__main__":
    unittest.main()
