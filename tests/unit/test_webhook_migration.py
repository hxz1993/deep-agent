from pathlib import Path
import sqlite3
import tempfile
import unittest
from alembic import command
from deep_agent.db import migrate


class WebhookMigrationTests(unittest.TestCase):
    def test_010_to_011_preserves_data_and_structure(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_webhook_") as directory:
            path = Path(directory) / "db.sqlite"
            config = migrate._build_alembic_config()
            config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
            command.upgrade(config, "010")
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO channel_accounts(id,name,channel_type,transport,bot_id,secret,agent_id,session_user_id,enabled,settings) VALUES ('c','legacy','wecom','websocket','b','s','a','u',0,'{}')")
                old = db.execute("SELECT * FROM channel_accounts").fetchone()
                cols = [r[1] for r in db.execute("PRAGMA table_info(channel_accounts)")]
                indices = db.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='channel_accounts' ORDER BY name").fetchall()
                tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            command.upgrade(config, "011")
            command.upgrade(config, "011")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT " + ','.join(cols) + " FROM channel_accounts").fetchone(), old)
                columns = {r[1]: r for r in db.execute("PRAGMA table_info(channel_accounts)")}
                self.assertEqual(set(columns) - set(cols), {"webhook_token", "webhook_aes_key"})
                for name in ("bot_id", "secret", "webhook_token", "webhook_aes_key"):
                    self.assertEqual(columns[name][3], 0)
                self.assertEqual(db.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='channel_accounts' ORDER BY name").fetchall(), indices)
                self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall(), tables)

    def test_old_development_010_is_completed_before_011(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_webhook_010_") as directory:
            path = Path(directory) / "db.sqlite"
            config = migrate._build_alembic_config()
            config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
            command.upgrade(config, "010")
            with sqlite3.connect(path) as db:
                db.execute("ALTER TABLE chat_sessions DROP COLUMN source_type")
                db.execute("ALTER TABLE chat_sessions DROP COLUMN source_id")
            command.upgrade(config, "011")
            with sqlite3.connect(path) as db:
                self.assertTrue({"source_type", "source_id"} <= {row[1] for row in db.execute("PRAGMA table_info(chat_sessions)")})

    def test_011_mysql_and_postgresql_ddl_compiles(self):
        """Offline SQL generation only; this does not execute either database."""
        import importlib.util
        import io
        from unittest.mock import patch
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        migration_path = Path(__file__).resolve().parents[2] / 'migrations/versions/011_channel_webhook_credentials.py'
        spec = importlib.util.spec_from_file_location('webhook_migration_011', migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        for dialect in ('mysql', 'postgresql'):
            with self.subTest(dialect=dialect):
                output = io.StringIO()
                context = MigrationContext.configure(dialect_name=dialect, opts={
                    'as_sql': True, 'output_buffer': output,
                })
                # The existing 010 repair requires online inspection and has its
                # own SQLite coverage; compile only the new 011 operations here.
                with Operations.context(context), patch.object(migration, 'ensure_session_source_columns') as repair:
                    migration.upgrade()
                repair.assert_called_once()
                sql = output.getvalue().lower()
                self.assertIn('alter table channel_accounts add column webhook_token text', sql)
                self.assertIn('alter table channel_accounts add column webhook_aes_key text', sql)
                if dialect == 'mysql':
                    self.assertIn('modify bot_id varchar(191) null', sql)
                    self.assertIn('modify secret text null', sql)
                else:
                    self.assertIn('alter column bot_id drop not null', sql)
                    self.assertIn('alter column secret drop not null', sql)
                self.assertNotIn('create table', sql)
                self.assertNotIn('create index', sql)
                self.assertNotIn('drop column', sql)
