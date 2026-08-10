from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import String, Text
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.schema import CreateColumn


_PATH = Path(__file__).resolve().parents[2] / "alembic/versions/003_skill_packages.py"
_SPEC = importlib.util.spec_from_file_location("deep_agent_migration_003", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


class SkillMigrationDialectTests(unittest.TestCase):
    @staticmethod
    def _bind(dialect):
        return SimpleNamespace(dialect=dialect)

    def test_postgresql_uses_text_keys_and_text_json(self) -> None:
        with patch.object(migration, "_bind", return_value=self._bind(postgresql.dialect())):
            self.assertIsInstance(migration._key_type(), Text)
            self.assertIsInstance(migration._json_type(), Text)

    def test_mysql_uses_bounded_keys_and_longtext_json(self) -> None:
        with patch.object(migration, "_bind", return_value=self._bind(mysql.dialect())):
            key = migration._key_type()
            self.assertIsInstance(key, String)
            self.assertEqual(key.length, 191)
            self.assertEqual(migration._json_type().__class__.__name__, "LONGTEXT")
            ddl = str(CreateColumn(migration._skills_column(nullable=True)).compile(dialect=mysql.dialect()))
            self.assertIn("skills LONGTEXT", ddl)
            self.assertNotIn("DEFAULT", ddl.upper())

    def test_sqlite_uses_bounded_keys_and_text_json(self) -> None:
        with patch.object(migration, "_bind", return_value=self._bind(sqlite.dialect())):
            key = migration._key_type()
            self.assertIsInstance(key, String)
            self.assertEqual(key.length, 191)
            self.assertIsInstance(migration._json_type(), Text)

    def test_string_list_decoder_preserves_null_and_values(self) -> None:
        self.assertIsNone(migration._as_string_list(None, field="x"))
        self.assertEqual(migration._as_string_list('["a","b"]', field="x"), ["a", "b"])
        with self.assertRaises(RuntimeError):
            migration._as_string_list('{"a":1}', field="x")


if __name__ == "__main__":
    unittest.main()
