from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import Text
from sqlalchemy.dialects import mysql, postgresql, sqlite


_PATH = Path(__file__).resolve().parents[2] / "alembic/versions/005_remove_text_tools.py"
_SPEC = importlib.util.spec_from_file_location("deep_agent_migration_005", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


class TextToolsMigrationDialectTests(unittest.TestCase):
    @staticmethod
    def _bind(dialect):
        return SimpleNamespace(dialect=dialect)

    def test_json_storage_type_matches_supported_dialects(self) -> None:
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            with self.subTest(dialect=dialect.name):
                with patch.object(migration, "_bind", return_value=self._bind(dialect)):
                    self.assertIsInstance(migration._json_type(), Text)
        with patch.object(migration, "_bind", return_value=self._bind(mysql.dialect())):
            self.assertEqual(migration._json_type().__class__.__name__, "LONGTEXT")

    def test_decoder_and_filter_preserve_null_empty_and_order(self) -> None:
        self.assertIsNone(migration._as_capabilities(None))
        self.assertEqual(migration._as_capabilities("[]"), [])
        self.assertEqual(
            migration._remove_capability(
                ["mcp:first", "mcp:text_tools", "ui:last", "mcp:text_tools"]
            ),
            ["mcp:first", "ui:last"],
        )
        with self.assertRaises(RuntimeError):
            migration._as_capabilities('{"not":"a-list"}')


if __name__ == "__main__":
    unittest.main()
