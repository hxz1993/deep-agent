from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.script import ScriptDirectory

from deep_agent.db import migrate
from deep_agent.db.models import ChannelAccount, ChannelMessageRun, ChannelSessionBinding


class ChannelMigrationTests(unittest.TestCase):
    def test_corrected_002_schema_and_existing_data_survive_upgrade_head(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_channel_migration_") as directory:
            path = Path(directory) / "db.sqlite"
            url = f"sqlite+aiosqlite:///{path}"
            with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                config = migrate._build_alembic_config()
            config.attributes["database_url"] = url
            self.assertEqual(ScriptDirectory.from_config(config).get_heads(), ["002"])
            command.upgrade(config, "head")
            with sqlite3.connect(path) as db:
                db.execute("PRAGMA foreign_keys=ON")
                agent_id = db.execute("SELECT id FROM agents LIMIT 1").fetchone()[0]
                db.execute("""INSERT INTO channel_accounts
                    (id,name,bot_id,secret,agent_id,session_user_id,settings)
                    VALUES ('account','test','test-bot','test-secret',?,'operator','{}')""", (agent_id,))
                db.execute("""INSERT INTO chat_sessions
                    (session_id,agent_id,user_id,next_sequence) VALUES ('session',?,'operator',1)""", (agent_id,))
                db.execute("""INSERT INTO channel_session_bindings
                    (id,account_id,peer_type,peer_id,session_id,peer_metadata)
                    VALUES ('binding','account','single','u','session','{}')""")
                db.execute("""INSERT INTO channel_message_runs
                    (id,account_id,binding_id,external_message_id,session_id,payload_hash,
                     processing_status,delivery_status,finished_at)
                    VALUES ('run','account','binding','message','session','hash',
                            'completed','sent',CURRENT_TIMESTAMP)""")
            command.upgrade(config, "head")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("002",))
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(db.execute("SELECT session_user_id,secret FROM channel_accounts").fetchone(),
                                 ("operator", "test-secret"))
                self.assertEqual(db.execute("SELECT peer_type,peer_id,session_id FROM channel_session_bindings").fetchone(),
                                 ("single", "u", "session"))
                self.assertEqual(db.execute("SELECT external_message_id,session_id,processing_status FROM channel_message_runs").fetchone(),
                                 ("message", "session", "completed"))
                for model in (ChannelAccount, ChannelSessionBinding, ChannelMessageRun):
                    table = model.__table__
                    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table.name})")}
                    self.assertEqual(columns, set(table.columns.keys()))
            # Destructive downgrade is tested only on this disposable database.
            command.downgrade(config, "001")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT user_id FROM chat_sessions WHERE session_id='session'").fetchone(),
                                 ("operator",))
            command.upgrade(config, "head")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("002",))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM channel_accounts").fetchone(), (0,))
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
