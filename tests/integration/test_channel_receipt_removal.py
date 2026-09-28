"""Real isolated SQLite persistence and migration checks; no platform sends."""
from __future__ import annotations

import importlib.util
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

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
                before = {name: db.execute(f'SELECT * FROM "{name}"').fetchall() for name in preserved}
                schema = {name: db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() for name in preserved}
            command.upgrade(config, "010")
            with sqlite3.connect(path) as db:
                after_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertEqual(after_tables, tables - {"channel_message_operations"})
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("010",))
                for name in preserved:
                    self.assertEqual(db.execute(f'SELECT * FROM "{name}"').fetchall(), before[name], name)
                    self.assertEqual(db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone(), schema[name], name)
            command.upgrade(config, "head")
            with self.assertRaisesRegex(RuntimeError, "010 cannot be downgraded"):
                command.downgrade(config, "009")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("010",))

    def test_010_ddl_only_drops_receipt_table_for_supported_dialects(self):
        spec = importlib.util.spec_from_file_location("receipt_removal", REPO_ROOT / "migrations/versions/010_remove_channel_message_operations.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for dialect in ("sqlite", "mysql", "postgresql"):
            output = io.StringIO()
            context = MigrationContext.configure(dialect_name=dialect, opts={"as_sql": True, "output_buffer": output})
            with Operations.context(context):
                module.upgrade()
            self.assertEqual(output.getvalue().strip(), "DROP TABLE channel_message_operations;")
