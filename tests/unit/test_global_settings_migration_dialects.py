from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import sqlalchemy as sa
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.schema import CreateTable


_PATH = Path(__file__).resolve().parents[2] / "alembic/versions/012_global_settings.py"
_SPEC = importlib.util.spec_from_file_location("deep_agent_migration_012", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


class GlobalSettingsMigrationDialectTests(unittest.TestCase):
    def test_table_and_seed_compile_for_all_supported_dialects(self) -> None:
        for dialect in (sqlite.dialect(), mysql.dialect(), postgresql.dialect()):
            with self.subTest(dialect=dialect.name):
                bind = SimpleNamespace(dialect=dialect, execute=MagicMock())
                metadata = sa.MetaData()
                created: list[sa.Table] = []

                def create_table(name: str, *elements, **_kwargs):
                    table = sa.Table(name, metadata, *elements)
                    created.append(table)
                    return table

                with (
                    patch.object(migration.op, "get_bind", return_value=bind),
                    patch.object(
                        migration.op,
                        "create_table",
                        side_effect=create_table,
                    ),
                ):
                    migration.upgrade()

                self.assertEqual(len(created), 1)
                table = created[0]
                ddl = str(CreateTable(table).compile(dialect=dialect))
                self.assertIn("CREATE TABLE global_settings", ddl)
                self.assertEqual(
                    set(table.c.keys()),
                    {"key", "value", "version", "created_at", "updated_at"},
                )
                if dialect.name == "postgresql":
                    self.assertIsInstance(table.c.key.type, sa.Text)
                else:
                    self.assertEqual(table.c.key.type.length, 191)
                if dialect.name == "mysql":
                    self.assertEqual(table.c.value.type.__class__.__name__, "LONGTEXT")
                else:
                    self.assertIsInstance(table.c.value.type, sa.Text)
                bind.execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
