from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from alembic.config import Config

from deep_agent.core import config as config_module
from deep_agent.core.paths import REPO_ROOT


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
        model="openai:gpt-4o",
        model_base_url=None,
        model_api_key=None,
        system_prompt="test",
        max_requests=12,
        max_total_tokens=None,
        request_retries=2,
        skills_dir="skills",
        cursor_skills_dir=".cursor/skills",
        mcp_forward_request_headers="authorization",
    )
    return SimpleNamespace(dbms=dbms, agent=agent)


class DatabaseUrlResolutionTests(unittest.TestCase):
    def test_builds_postgres_url_from_dbms(self) -> None:
        option = _build_option(dbms_name="postgres")
        option.dbms.port = 5432
        with patch.object(config_module, "OPTION", option):
            self.assertEqual(
                config_module.get_database_url(),
                "postgresql+asyncpg://user:p%40ss%3Aword@localhost:5432/deep_agent",
            )

    def test_builds_mysql_url_from_dbms(self) -> None:
        option = _build_option(dbms_name="mysql")
        with patch.object(config_module, "OPTION", option):
            self.assertEqual(
                config_module.get_database_url(),
                "mysql+asyncmy://user:p%40ss%3Aword@localhost:3306/deep_agent",
            )

    def test_returns_none_for_unsupported_dbms(self) -> None:
        option = _build_option(dbms_name="dameng")
        with patch.object(config_module, "OPTION", option):
            self.assertIsNone(config_module.get_database_url())

    def test_alembic_ini_accepts_percent_encoded_password(self) -> None:
        url = "mysql+asyncmy://deepfos:Okf%21S30Zjk@192.168.9.108:10307/deepagent"
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", config_module.escape_for_ini(url))
        self.assertEqual(cfg.get_main_option("sqlalchemy.url"), url)


if __name__ == "__main__":
    unittest.main()
