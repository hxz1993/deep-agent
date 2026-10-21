from __future__ import annotations
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, String, create_engine, inspect, text, update, delete
from deep_agent.db.session_source_schema import SOURCE_COLUMNS
from deep_agent.db.models import ChatSession, ChannelAccount
from deep_agent.db.session_source_schema import ensure_session_source_columns
from tests.unit import test_channel_store_commands as fixtures

class SessionSourceMigrationTests(unittest.TestCase):
    def setUp(self):
        # Every test must explicitly select an isolated database; fail closed.
        self.guard = patch("deep_agent.db.url.get_database_url", side_effect=AssertionError("Implicit database target forbidden in migration tests"))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_explicit_main_option_never_uses_ambient_database(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-source-target-") as directory:
            path = Path(directory) / "isolated.sqlite"
            cfg = Config("alembic.ini")
            cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
            command.upgrade(cfg, "head")
            engine = create_engine(f"sqlite:///{path}")
            with engine.connect() as db:
                self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")), "012")
                self.assertIn("source_id", {c["name"] for c in inspect(db).get_columns("chat_sessions")})
            engine.dispose()

    def test_009_upgrade_and_old_010补齐(self):
        with tempfile.TemporaryDirectory(prefix='TMP_to_delete-source-') as directory:
            path = Path(directory) / 'db.sqlite'
            cfg = Config('alembic.ini')
            cfg.attributes['database_url'] = f'sqlite+aiosqlite:///{path}'
            command.upgrade(cfg, '009')
            engine = create_engine(f'sqlite:///{path}')
            with engine.begin() as db:
                db.execute(text("INSERT INTO chat_sessions(session_id,agent_id,user_id,title,next_sequence) VALUES ('s','a','u','历史标题',2)"))
                db.execute(text("INSERT INTO chat_session_events(event_id,session_id,sequence,schema_version,event_type,payload) VALUES ('e','s',1,1,'user/message','{}')"))
                db.execute(text("INSERT INTO channel_accounts(id,name,bot_id,secret,agent_id,session_user_id,settings) VALUES ('c','fixture','bot','fixture','a','u','{}')"))
                db.execute(text("INSERT INTO channel_session_bindings(id,account_id,peer_type,peer_id,peer_metadata,session_id) VALUES ('b','c','single','p','{}','s')"))
                db.execute(text("INSERT INTO channel_message_runs(id,account_id,binding_id,external_message_id,payload_hash,session_id) VALUES ('r','c','b','m','hash','s')"))
                db.execute(text("INSERT INTO channel_message_operations(id,account_id,channel_run_id,agent_run_id,tool_call_id,session_id,request_hash,results) VALUES ('o','c','r','ar','tc','s','hash','[]')"))
                before = {t: tuple(c['name'] for c in inspect(db).get_columns(t)) for t in inspect(db).get_table_names()}
                data_before = {table: db.execute(text(f'SELECT * FROM "{table}"')).all()
                               for table in before if table not in {'alembic_version', 'channel_message_operations'}}
                indexes_before = {table: inspect(db).get_indexes(table) for table in data_before}
            command.upgrade(cfg, '010')
            with engine.begin() as db:
                after = {t: tuple(c['name'] for c in inspect(db).get_columns(t)) for t in inspect(db).get_table_names()}
                self.assertNotIn('channel_message_operations', after)
                self.assertEqual(set(after['chat_sessions']) - set(before['chat_sessions']), {'source_type', 'source_id'})
                for table in set(after) - {'chat_sessions'}:
                    self.assertEqual(before[table], after[table])
                for table, rows in data_before.items():
                    columns = ','.join(f'"{column}"' for column in before[table])
                    self.assertEqual(rows, db.execute(text(f'SELECT {columns} FROM "{table}"')).all())
                    self.assertEqual(indexes_before[table], inspect(db).get_indexes(table))
                self.assertEqual(db.execute(text('SELECT source_type,source_id FROM chat_sessions')).one(), (None, None))
                db.execute(text('ALTER TABLE chat_sessions DROP COLUMN source_type'))
                ensure_session_source_columns(Operations(MigrationContext.configure(db)))
                ensure_session_source_columns(Operations(MigrationContext.configure(db)))
                self.assertEqual(db.scalar(text('SELECT version_num FROM alembic_version')), '010')
            engine.dispose()

    def test_source_ddl_compiles_mysql_postgresql_without_connections(self):
        for dialect in ('mysql', 'postgresql'):
            with self.subTest(dialect=dialect):
                output = io.StringIO()
                operations = Operations(MigrationContext.configure(dialect_name=dialect, opts={
                    'as_sql': True, 'output_buffer': output,
                }))
                for name, length in SOURCE_COLUMNS.items():
                    operations.add_column('chat_sessions', Column(name, String(length), nullable=True))
                sql = output.getvalue()
                self.assertIn('source_type VARCHAR(64)', sql)
                self.assertIn('source_id VARCHAR(255)', sql)
                self.assertNotIn('NOT NULL', sql)
                self.assertNotIn('CREATE INDEX', sql)

    def test_wrong_structure_rejected_before_ddl(self):
        engine = create_engine('sqlite://')
        with engine.begin() as db:
            db.execute(text('CREATE TABLE chat_sessions (source_id VARCHAR(10))'))
            with self.assertRaisesRegex(RuntimeError, 'schema mismatch'):
                ensure_session_source_columns(Operations(MigrationContext.configure(db)))
            self.assertEqual([c['name'] for c in inspect(db).get_columns('chat_sessions')], ['source_id'])
        engine.dispose()

class SessionSourceStorageTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ChannelStoreCommandTests.asyncSetUp
    asyncTearDown = fixtures.ChannelStoreCommandTests.asyncTearDown
    async def test_source_is_immutable_and_origin_uses_master(self):
        resolved = await self.store.resolve_binding(**self.peer)
        sid = resolved.session.session_id
        self.assertEqual(await self.stores.chat_session.get_source(self.agent_id, sid), ('channel:wecom', self.account.id))
        async with self.factory.begin() as db:
            await db.execute(update(ChatSession).where(ChatSession.session_id == sid).values(source_type=None, source_id=None))
        self.assertIsNone(await self.store.get_session_origin(sid))
        self.assertIsNone(await self.store.get_channel_session(self.account.id, sid))
        with self.assertRaisesRegex(PermissionError, 'source does not match'):
            await self.store.resolve_binding(**self.peer)
        async with self.factory.begin() as db:
            await db.execute(update(ChatSession).where(ChatSession.session_id == sid).values(source_type='channel:wecom', source_id=self.account.id))
            await db.execute(delete(ChannelAccount).where(ChannelAccount.id == self.account.id))
        origin = await self.store.get_session_origin(sid)
        self.assertTrue(origin['account_deleted'])
        self.assertEqual(origin['peer_id'], self.peer['peer_id'])
