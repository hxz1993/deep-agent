from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.schema import CreateTable


_PATH = Path(__file__).resolve().parents[2] / "alembic/versions/008_session_events.py"
_SPEC = importlib.util.spec_from_file_location("deep_agent_migration_008", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


class SessionEventMigrationDialectTests(unittest.TestCase):
    @staticmethod
    def _bind(dialect):
        return SimpleNamespace(dialect=dialect)

    def test_new_tables_compile_for_all_supported_dialects(self) -> None:
        for dialect in (sqlite.dialect(), mysql.dialect(), postgresql.dialect()):
            with self.subTest(dialect=dialect.name):
                tables: dict[str, sa.Table] = {}
                metadata = sa.MetaData()
                sa.Table(
                    "agents",
                    metadata,
                    sa.Column("id", sa.String(36), primary_key=True),
                )

                def create_table(name: str, *elements, **_kwargs):
                    table = sa.Table(name, metadata, *elements)
                    tables[name] = table
                    return table

                with (
                    patch.object(migration, "_bind", return_value=self._bind(dialect)),
                    patch.object(migration.op, "drop_table"),
                    patch.object(migration.op, "drop_index"),
                    patch.object(migration.op, "create_index"),
                    patch.object(migration.op, "create_table", side_effect=create_table),
                ):
                    migration.upgrade()

                self.assertEqual(
                    set(tables),
                    {"chat_sessions", "chat_session_events"},
                )
                for table in tables.values():
                    ddl = str(CreateTable(table).compile(dialect=dialect))
                    self.assertIn("CREATE TABLE", ddl)
                event_payload = tables["chat_session_events"].c.payload.type
                if dialect.name == "mysql":
                    self.assertEqual(event_payload.__class__.__name__, "LONGTEXT")
                else:
                    self.assertIsInstance(event_payload, sa.Text)


if __name__ == "__main__":
    unittest.main()
