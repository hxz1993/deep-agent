from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command

from deep_agent.db import migrate


class ChannelSourceMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_legacy_runs_and_downgrade_refuses_source_loss(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_feishu_migration_") as directory:
            path = Path(directory) / "db.sqlite"
            url = f"sqlite+aiosqlite:///{path}"
            with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                config = migrate._build_alembic_config()
            config.attributes["database_url"] = url
            command.upgrade(config, "007")
            with sqlite3.connect(path) as db:
                db.execute("""INSERT INTO channel_message_runs
                    (id,account_id,binding_id,external_message_id,sender_id,payload_hash)
                    VALUES ('legacy','account','binding','message','opaque-user','hash')""")
                tables_before = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            command.upgrade(config, "008")
            with sqlite3.connect(path) as db:
                tables_after = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertEqual(tables_before, tables_after)
                self.assertEqual(db.execute("SELECT sender_id,sender_name,tenant_id,thread_id,source_metadata FROM channel_message_runs").fetchone(), ("opaque-user", None, None, None, None))
                db.execute("UPDATE channel_message_runs SET sender_name='测试发送者',tenant_id='tenant',thread_id='thread',source_metadata='{}'")
            with self.assertRaisesRegex(RuntimeError, "channel source facts exist"):
                command.downgrade(config, "007")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone()[0], "008")
                self.assertEqual(db.execute("SELECT sender_name FROM channel_message_runs").fetchone()[0], "测试发送者")
                db.execute("UPDATE channel_message_runs SET sender_name=NULL,tenant_id=NULL,thread_id=NULL,source_metadata=NULL")
            command.downgrade(config, "007")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT sender_id FROM channel_message_runs").fetchone()[0], "opaque-user")
                self.assertNotIn("sender_name", {r[1] for r in db.execute("PRAGMA table_info(channel_message_runs)")})
