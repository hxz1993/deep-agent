from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import String, Text
from sqlalchemy.dialects import mysql, postgresql, sqlite


_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "alembic/versions/002_tool_packages.py"
_SPEC = importlib.util.spec_from_file_location("deep_agent_migration_002", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


class ToolPackageMigrationDialectTests(unittest.TestCase):
    def _bind(self, dialect):
        return SimpleNamespace(dialect=dialect)

    def test_postgresql_uses_text_keys_and_native_boolean_default(self) -> None:
        with patch.object(migration, "_bind", return_value=self._bind(postgresql.dialect())):
            self.assertIsInstance(migration._key_type(), Text)
            self.assertEqual(str(migration._true_default()), "true")
            self.assertIsInstance(migration._json_type(), Text)

    def test_mysql_uses_bounded_keys_longtext_and_numeric_boolean_default(self) -> None:
        with patch.object(migration, "_bind", return_value=self._bind(mysql.dialect())):
            key = migration._key_type()
            self.assertIsInstance(key, String)
            self.assertEqual(key.length, 191)
            self.assertEqual(migration._json_type().__class__.__name__, "LONGTEXT")
            self.assertEqual(str(migration._true_default()), "1")

    def test_sqlite_uses_bounded_keys_text_and_numeric_boolean_default(self) -> None:
        with patch.object(migration, "_bind", return_value=self._bind(sqlite.dialect())):
            key = migration._key_type()
            self.assertIsInstance(key, String)
            self.assertEqual(key.length, 191)
            self.assertIsInstance(migration._json_type(), Text)
            self.assertEqual(str(migration._true_default()), "1")

    def test_package_codes_upgrade_and_downgrade_independently(self) -> None:
        cases = (
            (["native"], ["builtin:general"]),
            (["python-sandbox"], ["builtin:sandbox"]),
            (
                ["native", "python-sandbox"],
                ["builtin:general", "builtin:sandbox"],
            ),
            ([], []),
        )
        for legacy, expected in cases:
            with self.subTest(legacy=legacy):
                upgraded = migration._upgrade_capabilities(legacy)
                self.assertEqual(upgraded, expected)
                self.assertEqual(
                    migration._downgrade_capabilities(upgraded, {}),
                    legacy,
                )

    def test_upgrade_accepts_pre_release_package_codes_idempotently(self) -> None:
        self.assertEqual(
            migration._upgrade_capabilities(
                ["builtin:general", "builtin:sandbox", "ui:deepcube", "ui:builtin"]
            ),
            ["builtin:general", "builtin:sandbox", "ui:deepcube", "ui:builtin"],
        )

    def test_null_capabilities_remain_null(self) -> None:
        self.assertIsNone(migration._as_capabilities(None))


if __name__ == "__main__":
    unittest.main()
