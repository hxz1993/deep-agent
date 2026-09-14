import asyncio
import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from deep_agent.db import migrate
from maintenance.config_backup import export_backup


class WecomMigrationTests(unittest.TestCase):
    def config(self, path):
        url = f"sqlite+aiosqlite:///{path}"
        with patch("deep_agent.db.migrate.get_database_url", return_value=url):
            config = migrate._build_alembic_config()
        config.attributes["database_url"] = url
        return config

    def test_upgrade_preserves_data_and_refuses_destructive_downgrade(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-wecom-migration-") as directory:
            path = Path(directory)/"test.db"
            config = self.config(path)
            command.upgrade(config, "005")
            with sqlite3.connect(path) as db:
                before = db.execute("SELECT id,name FROM agents").fetchall()
            command.upgrade(config, "006")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT id,name FROM agents").fetchall(), before)
                self.assertEqual(db.execute("PRAGMA foreign_key_list(wecom_user_profiles)").fetchall(), [])
                db.execute("INSERT INTO wecom_applications (id,name,corp_id,wecom_agent_id,app_secret,settings,revision) VALUES ('app','fixture','corp','100','fixture-secret','{}',1)")
                db.execute("INSERT INTO wecom_user_profiles (id,wecom_application_id,userid,userid_key,open_userid,name) VALUES ('profile','app','User','user','opaque','姓名')")
            with self.assertRaisesRegex(RuntimeError, "Cannot downgrade"):
                command.downgrade(config, "005")
            result = asyncio.run(export_backup(f"sqlite+aiosqlite:///{path}", environment="fixture", output_root=Path(directory)/"backup"))
            with sqlite3.connect(result.path) as db:
                self.assertEqual(db.execute("SELECT app_secret FROM wecom_applications").fetchone(), ("fixture-secret",))
                self.assertEqual(db.execute("SELECT count(*) FROM wecom_user_profiles").fetchone(), (0,))
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM wecom_user_profiles").fetchone(), (1,))
                db.execute("DELETE FROM wecom_user_profiles")
                db.execute("DELETE FROM wecom_applications")
            command.downgrade(config, "005")
            command.upgrade(config, "006")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("006",))

    def test_ddl_all_three_dialects_without_foreign_keys(self):
        spec = importlib.util.spec_from_file_location("wecom_migration", "migrations/versions/006_wecom_applications.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for dialect in ("sqlite", "mysql", "postgresql"):
            output = io.StringIO()
            context = MigrationContext.configure(dialect_name=dialect, opts={"as_sql":True,"output_buffer":output})
            with Operations.context(context):
                module.upgrade()
            ddl = output.getvalue()
            self.assertIn("CREATE TABLE wecom_applications", ddl)
            self.assertIn("uq_wecom_profile_open_userid", ddl)
            self.assertNotIn("FOREIGN KEY", ddl)
            if dialect == "mysql":
                self.assertIn("utf8mb4_bin", ddl)
