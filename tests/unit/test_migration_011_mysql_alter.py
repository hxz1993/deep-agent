from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_011 = REPO_ROOT / "alembic" / "versions" / "011_split_session_key.py"


def _load_migration_011():
    spec = importlib.util.spec_from_file_location("migration_011", MIGRATION_011)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Migration011MysqlAlterTests(unittest.TestCase):
    def test_alter_not_null_includes_existing_type_for_mysql(self) -> None:
        mod = _load_migration_011()
        bind = MagicMock()
        bind.dialect.name = "mysql"

        with patch.object(mod, "op") as mock_op:
            mod._alter_not_null(
                "chat_sessions",
                "session_id",
                column_type=sa.String(191),
                bind=bind,
            )

        mock_op.alter_column.assert_called_once()
        args, kwargs = mock_op.alter_column.call_args
        self.assertEqual(args, ("chat_sessions", "session_id"))
        self.assertEqual(kwargs["existing_nullable"], True)
        self.assertEqual(kwargs["nullable"], False)
        self.assertIsInstance(kwargs["existing_type"], sa.String)
        self.assertEqual(kwargs["existing_type"].length, 191)


if __name__ == "__main__":
    unittest.main()
