"""Real isolated SQLite persistence and migration checks; no platform sends."""
from __future__ import annotations

import importlib.util
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

from deep_agent.core.paths import REPO_ROOT


class RemovedOutboundReceiptMigrationTests(unittest.TestCase):
    def test_008_009_roundtrip_and_nonempty_downgrade_guard(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-channel-receipts-") as directory:
            path = Path(directory) / "test.db"
            config = Config(str(REPO_ROOT / "alembic.ini"))
            config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
            command.upgrade(config, "008")
            with sqlite3.connect(path) as db:
                before = db.execute("SELECT id,name FROM agents").fetchall()
            command.upgrade(config, "009")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT id,name FROM agents").fetchall(), before)
                self.assertEqual(db.execute("PRAGMA foreign_key_list(channel_message_operations)").fetchall(), [])
                actual = {row[1] for row in db.execute("PRAGMA table_info(channel_message_operations)")}
                self.assertEqual(actual, {"id", "account_id", "channel_run_id", "agent_run_id", "tool_call_id", "session_id", "request_hash", "state", "results", "created_at", "updated_at"})
                db.execute("INSERT INTO channel_message_operations (id,account_id,channel_run_id,agent_run_id,tool_call_id,session_id,request_hash,results) VALUES ('op','account','channel-run','agent-run','call','session','hash','[]')")
            with self.assertRaisesRegex(RuntimeError, "Cannot downgrade"):
                command.downgrade(config, "008")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("009",))
                db.execute("DELETE FROM channel_message_operations")
            command.downgrade(config, "008")
            command.upgrade(config, "009")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("009",))
                self.assertEqual(db.execute("SELECT id,name FROM agents").fetchall(), before)

    def test_all_dialect_ddl_preserves_idempotency_constraint(self):
        spec = importlib.util.spec_from_file_location("outbound_migration", REPO_ROOT / "migrations/versions/009_channel_message_operations.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for dialect in ("sqlite", "mysql", "postgresql"):
            output = io.StringIO()
            context = MigrationContext.configure(dialect_name=dialect, opts={"as_sql": True, "output_buffer": output})
            with Operations.context(context):
                module.upgrade()
            ddl = output.getvalue()
            self.assertIn("CREATE TABLE channel_message_operations", ddl)
            self.assertIn("uq_channel_operations_run_tool", ddl)
            self.assertNotIn("FOREIGN KEY", ddl)

    def test_010_removes_nonempty_receipts_and_preserves_every_other_table(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-remove-receipts-") as directory:
            path = Path(directory) / "test.db"
            config = Config(str(REPO_ROOT / "alembic.ini"))
            config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
            command.upgrade(config, "009")
            with sqlite3.connect(path) as db:
                agent = db.execute("SELECT id FROM agents LIMIT 1").fetchone()[0]
                db.execute("INSERT INTO channel_accounts (id,name,bot_id,secret,agent_id,session_user_id,settings) VALUES ('a','fixture','bot','secret',?,'owner','{}')", (agent,))
                db.execute("INSERT INTO chat_sessions (session_id,agent_id,user_id,next_sequence) VALUES ('s',?,'owner',2)", (agent,))
                db.execute("INSERT INTO chat_session_events (event_id,session_id,sequence,schema_version,event_type,payload,created_at,updated_at) VALUES ('e','s',1,1,'user/message','{\"file_id\":\"existing-file\"}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
                db.execute("INSERT INTO channel_session_bindings (id,account_id,peer_type,peer_id,session_id,peer_metadata) VALUES ('b','a','single','peer','s','{}')")
                db.execute("INSERT INTO channel_message_runs (id,account_id,binding_id,external_message_id,session_id,payload_hash) VALUES ('r','a','b','m','s','hash')")
                db.execute("INSERT INTO channel_message_operations (id,account_id,channel_run_id,agent_run_id,tool_call_id,session_id,request_hash,results) VALUES ('op','a','r','ar','call','s','hash','[]')")
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                preserved = tables - {"channel_message_operations", "alembic_version"}
                columns = {name: [row[1] for row in db.execute(f'PRAGMA table_info("{name}")')] for name in preserved}
                before = {name: db.execute(f'SELECT * FROM "{name}"').fetchall() for name in preserved}
                schema = {name: db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() for name in preserved}
            command.upgrade(config, "010")
            with sqlite3.connect(path) as db:
                after_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertEqual(after_tables, tables - {"channel_message_operations"})
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("010",))
                for name in preserved:
                    projection = ",".join(f'"{column}"' for column in columns[name])
                    self.assertEqual(db.execute(f'SELECT {projection} FROM "{name}"').fetchall(), before[name], name)
                    if name != "chat_sessions":
                        self.assertEqual(db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone(), schema[name], name)
                source_columns = {row[1]: row for row in db.execute("PRAGMA table_info(chat_sessions)")}
                self.assertEqual(set(source_columns) - set(columns["chat_sessions"]), {"source_type", "source_id"})
                for column, length in (("source_type", 64), ("source_id", 255)):
                    self.assertEqual(source_columns[column][2].upper(), f"VARCHAR({length})")
                    self.assertEqual(source_columns[column][3], 0)
                    self.assertIsNone(source_columns[column][4])
                self.assertEqual(db.execute("SELECT source_type,source_id FROM chat_sessions").fetchall(), [(None, None)])
            command.upgrade(config, "010")
            with self.assertRaisesRegex(RuntimeError, "010 cannot be downgraded"):
                command.downgrade(config, "009")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("010",))

    def test_010_ddl_adds_source_columns_and_drops_receipt_table(self):
        spec = importlib.util.spec_from_file_location("receipt_removal", REPO_ROOT / "migrations/versions/010_remove_channel_message_operations.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for dialect in ("sqlite", "mysql", "postgresql"):
            output = io.StringIO()
            context = MigrationContext.configure(dialect_name=dialect, opts={"as_sql": True, "output_buffer": output})
            # Physical column inspection needs a real database; the online
            # SQLite case above verifies it. Compile the known 009 shape here.
            inspector = Mock()
            inspector.get_columns.return_value = []
            with Operations.context(context), patch("deep_agent.db.session_source_schema.sa.inspect", return_value=inspector):
                module.upgrade()
            ddl = output.getvalue().strip()
            self.assertIn("ALTER TABLE chat_sessions ADD COLUMN source_type VARCHAR(64)", ddl)
            self.assertIn("ALTER TABLE chat_sessions ADD COLUMN source_id VARCHAR(255)", ddl)
            self.assertIn("DROP TABLE channel_message_operations;", ddl)
            self.assertEqual(ddl.count(";"), 3)
