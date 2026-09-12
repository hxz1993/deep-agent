"""Real isolated MySQL/PostgreSQL regression; URLs must name disposable TMP databases."""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from alembic import command
from alembic.operations import Operations

from deep_agent.db import migrate
from deep_agent.db.reference_schema import LOGICAL_REFERENCES, assert_logical_references


class ReferenceMigrationDatabasesTests(unittest.TestCase):
    def run_connection(self, url, callback):
        async def run():
            engine = create_async_engine(url)
            try:
                async with engine.begin() as connection:
                    return await connection.run_sync(callback)
            finally:
                await engine.dispose()
        return asyncio.run(run())

    def snapshot(self, url):
        def capture(db):
            inspector = sa.inspect(db)
            output = {}
            for table in inspector.get_table_names():
                if table == "alembic_version":
                    continue
                metadata = sa.MetaData()
                reflected = sa.Table(table, metadata, autoload_with=db)
                output[table] = {
                    "rows": sorted([tuple(row) for row in db.execute(sa.select(reflected))], key=repr),
                    "columns": [(col["name"], str(col["type"]), col["nullable"], col.get("default")) for col in inspector.get_columns(table)],
                    "pk": inspector.get_pk_constraint(table)["constrained_columns"],
                    "unique": sorted((tuple(item["column_names"]) for item in inspector.get_unique_constraints(table))),
                    "indexes": sorted((item["name"], tuple(item["column_names"]), item["unique"]) for item in inspector.get_indexes(table)),
                }
            return output
        return self.run_connection(url, capture)

    def fk_count(self, url):
        def count(db):
            inspector = sa.inspect(db)
            return sum(len(inspector.get_foreign_keys(table)) for table in inspector.get_table_names())
        return self.run_connection(url, count)

    def test_mysql(self):
        self.check_database("DEEP_AGENT_TEST_MYSQL_MIGRATION_URL")

    def test_postgresql(self):
        self.check_database("DEEP_AGENT_TEST_POSTGRES_MIGRATION_URL")

    def check_database(self, variable):
        url = os.environ.get(variable)
        if not url:
            self.skipTest(f"{variable} not configured; real database migration not verified")
        parsed = sa.make_url(url)
        if parsed.host not in {"127.0.0.1", "localhost"} or not (parsed.database or "").startswith("tmp_deepagent_migration"):
            self.fail("Real migration regression requires a localhost database named tmp_deepagent_migration*")
        config = migrate._build_alembic_config()
        config.attributes["database_url"] = url
        def reset(db):
            metadata = sa.MetaData()
            metadata.reflect(bind=db)
            metadata.drop_all(bind=db)
        self.run_connection(url, reset)
        command.upgrade(config, "002")
        def seed(db):
            agent = db.scalar(sa.text("SELECT id FROM agents LIMIT 1"))
            db.execute(sa.text("INSERT INTO ui_tool_packages(code,label,description,source) VALUES('ui:test','测试','说明','manual')"))
            db.execute(sa.text("INSERT INTO ui_tool_definitions(name,package_code,description,parameters_json_schema,source) VALUES('test','ui:test','工具','{}','manual')"))
            db.execute(sa.text("INSERT INTO chat_sessions(session_id,agent_id,user_id,title) VALUES('session',:agent,'owner','中文 👋📊🙂')"), {"agent": agent})
            db.execute(sa.text("INSERT INTO chat_session_events(event_id,session_id,sequence,schema_version,event_type,payload) VALUES('event','session',1,1,'user/message','中文 👋📊🙂')"))
            db.execute(sa.text("INSERT INTO channel_accounts(id,name,bot_id,secret,agent_id,session_user_id,settings) VALUES('account','测试','bot','test',:agent,'owner','{}')"), {"agent": agent})
            db.execute(sa.text("INSERT INTO channel_session_bindings(id,account_id,peer_type,peer_id,session_id,peer_metadata) VALUES('binding','account','single','peer','session','{}')"))
            db.execute(sa.text("INSERT INTO channel_message_runs(id,account_id,binding_id,external_message_id,payload_hash) VALUES('run','account','binding','message','hash')"))
        self.run_connection(url, seed)
        if parsed.get_backend_name() == "mysql":
            self.run_connection(url, lambda db: db.exec_driver_sql("ALTER TABLE agents RENAME INDEX agents_model_id_fkey TO custom_agent_model_reference"))
        before = self.snapshot(url)
        self.assertEqual(self.fk_count(url), 10)
        # Interrupt the third DROP: PostgreSQL rolls back, MySQL retains two DROP
        # operations. In both cases the version stays 002 and rerunning recovers.
        original = Operations.drop_constraint
        count = 0
        def interrupted(operations, *args, **kwargs):
            nonlocal count
            count += 1
            if count == 3:
                raise RuntimeError("simulated interrupted DDL")
            return original(operations, *args, **kwargs)
        with patch.object(Operations, "drop_constraint", interrupted):
            with self.assertRaisesRegex(RuntimeError, "simulated interrupted DDL"):
                command.upgrade(config, "003")
        self.assertEqual(self.snapshot(url), before)
        self.assertEqual(self.fk_count(url), 8 if parsed.get_backend_name() == "mysql" else 10)
        self.assertEqual(self.run_connection(url, lambda db: db.scalar(sa.text("SELECT version_num FROM alembic_version"))), "002")
        command.upgrade(config, "003")
        self.assertEqual(self.fk_count(url), 0)
        self.assertEqual(self.snapshot(url), before)
        command.upgrade(config, "003")
        command.stamp(config, "002")  # Full FK removal completed manually.
        command.upgrade(config, "003")
        command.downgrade(config, "002")
        self.assertEqual(self.fk_count(url), 10)
        def foreign_key_rules(db):
            inspector = sa.inspect(db)
            return {(table, fk["constrained_columns"][0], fk["referred_table"], fk["referred_columns"][0], fk["options"].get("ondelete", "RESTRICT"))
                    for table in inspector.get_table_names() for fk in inspector.get_foreign_keys(table)}
        self.assertEqual(self.run_connection(url, foreign_key_rules),
                         {(ref.table, ref.column, ref.parent_table, ref.parent_column, ref.ondelete) for ref in LOGICAL_REFERENCES})
        self.assertEqual(self.snapshot(url), before)
        command.upgrade(config, "003")
        self.run_connection(url, assert_logical_references)
        self.run_connection(url, lambda db: db.execute(sa.text("UPDATE channel_message_runs SET binding_id='missing'")))
        damaged = self.snapshot(url)
        with self.assertRaisesRegex(RuntimeError, "channel_message_runs.binding_id"):
            command.downgrade(config, "002")
        self.assertEqual(self.snapshot(url), damaged)
        command.stamp(config, "002")
        with self.assertRaisesRegex(RuntimeError, "channel_message_runs.binding_id"):
            command.upgrade(config, "003")
        self.assertEqual(self.snapshot(url), damaged)
        self.run_connection(url, lambda db: db.execute(sa.text("UPDATE channel_message_runs SET binding_id='binding'")))
        command.upgrade(config, "003")
        self.assertEqual(self.snapshot(url), before)
        # Fresh empty database -> head. Historical 002 downgrade is deliberately
        # untouched (on MySQL it drops indexes still needed by its foreign keys).
        self.run_connection(url, reset)
        command.upgrade(config, "003")
        self.assertEqual(self.fk_count(url), 0)
        self.run_connection(url, assert_logical_references)
