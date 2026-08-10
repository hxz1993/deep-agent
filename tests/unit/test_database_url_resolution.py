from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from alembic.config import Config

from deep_agent.core.paths import REPO_ROOT
from deep_agent.db import url as db_url_module


def _build_option(*, dbms_name: str) -> SimpleNamespace:
    dbms = SimpleNamespace(
        name=dbms_name,
        username="user",
        password="p@ss:word",
        hostname="localhost",
        port=3306,
        db_name="deep_agent",
    )
    agent = SimpleNamespace(
        app_name="deep-agent",
        max_requests=12,
        max_total_tokens=None,
        request_retries=2,
        mcp_forward_request_headers="authorization",
        sqlite_path="data/deep-agent.db",
    )
    return SimpleNamespace(dbms=dbms, agent=agent)


class DatabaseUrlResolutionTests(unittest.TestCase):
    def test_builds_postgres_url_from_dbms(self) -> None:
        option = _build_option(dbms_name="postgres")
        option.dbms.port = 5432
        with patch.object(db_url_module, "OPTION", option):
            self.assertEqual(
                db_url_module.get_database_url(),
                "postgresql+asyncpg://user:p%40ss%3Aword@localhost:5432/deep_agent",
            )

    def test_builds_mysql_url_from_dbms(self) -> None:
        option = _build_option(dbms_name="mysql")
        with patch.object(db_url_module, "OPTION", option):
            self.assertEqual(
                db_url_module.get_database_url(),
                "mysql+asyncmy://user:p%40ss%3Aword@localhost:3306/deep_agent",
            )

    def test_falls_back_to_sqlite_for_unsupported_dbms(self) -> None:
        option = _build_option(dbms_name="dameng")
        with patch.object(db_url_module, "OPTION", option):
            self.assertTrue(db_url_module.get_database_url().startswith("sqlite+aiosqlite:///"))

    def test_alembic_ini_accepts_percent_encoded_password(self) -> None:
        url = "mysql+asyncmy://deepfos:Okf%21S30Zjk@192.168.9.108:10307/deepagent"
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", db_url_module.escape_for_ini(url))
        self.assertEqual(cfg.get_main_option("sqlalchemy.url"), url)


if __name__ == "__main__":
    unittest.main()
