from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.schema import CreateTable


_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/009_remove_auxiliary_model_configs.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "deep_agent_migration_009",
    _PATH,
)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


class AuxiliaryModelCleanupMigrationDialectTests(unittest.TestCase):
    @staticmethod
    def _bind(dialect):
        return SimpleNamespace(dialect=dialect)

    def test_upgrade_drops_only_when_the_legacy_table_exists(self) -> None:
        for exists in (False, True):
            with self.subTest(exists=exists):
                with (
                    patch.object(migration, "_has_table", return_value=exists),
                    patch.object(migration.op, "drop_table") as drop_table,
                ):
                    migration.upgrade()
                self.assertEqual(drop_table.call_count, int(exists))

    def test_downgrade_recreates_the_legacy_table_for_all_dialects(self) -> None:
        for dialect in (sqlite.dialect(), mysql.dialect(), postgresql.dialect()):
            with self.subTest(dialect=dialect.name):
                metadata = sa.MetaData()
                sa.Table(
                    "llm_models",
                    metadata,
                    sa.Column("id", sa.String(36), primary_key=True),
                )
                created: dict[str, sa.Table] = {}

                def create_table(name: str, *elements, **_kwargs):
                    table = sa.Table(name, metadata, *elements)
                    created[name] = table
                    return table

                with (
                    patch.object(migration, "_has_table", return_value=False),
                    patch.object(
                        migration,
                        "_bind",
                        return_value=self._bind(dialect),
                    ),
                    patch.object(
                        migration.op,
                        "create_table",
                        side_effect=create_table,
                    ),
                ):
                    migration.downgrade()

                table = created["auxiliary_model_configs"]
                ddl = str(CreateTable(table).compile(dialect=dialect))
                self.assertIn("CREATE TABLE", ddl)
                self.assertEqual(
                    set(table.c.keys()),
                    {"use_case", "llm_model_id", "created_at", "updated_at"},
                )

    def test_downgrade_keeps_an_existing_legacy_table(self) -> None:
        with (
            patch.object(migration, "_has_table", return_value=True),
            patch.object(migration.op, "create_table") as create_table,
        ):
            migration.downgrade()
        create_table.assert_not_called()


if __name__ == "__main__":
    unittest.main()
