from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations

from deep_agent.db import migrate
from deep_agent.db.reference_schema import LOGICAL_REFERENCES, Relation, assert_logical_references


class ApplicationReferenceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="TMP_to_delete_reference_migration_")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "db.sqlite"
        self.config = migrate._build_alembic_config()
        self.config.attributes["database_url"] = f"sqlite+aiosqlite:///{self.path}"
        command.upgrade(self.config, "002")
        self.engine = sa.create_engine(f"sqlite:///{self.path}")
        self.addCleanup(self.engine.dispose)
        @sa.event.listens_for(self.engine, "connect")
        def enable_fks(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        module_path = Path(__file__).resolve().parents[2] / "migrations/versions/003_application_references.py"
        spec = importlib.util.spec_from_file_location("migration003", module_path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)
        with self.engine.begin() as db:
            agent = db.scalar(sa.text("SELECT id FROM agents LIMIT 1"))
            db.execute(sa.text("INSERT INTO ui_tool_packages(code,label,description,source) VALUES('ui:test','测试','说明','manual')"))
            db.execute(sa.text("INSERT INTO ui_tool_definitions(name,package_code,description,parameters_json_schema,source) VALUES('test','ui:test','工具','{}','manual')"))
            db.execute(sa.text("INSERT INTO chat_sessions(session_id,agent_id,user_id,title) VALUES('session',:agent,'owner','中文 👋📊🙂')"), {"agent": agent})
            db.execute(sa.text("INSERT INTO chat_session_events(event_id,session_id,sequence,schema_version,event_type,payload) VALUES('event','session',1,1,'user/message','中文 👋📊🙂')"))
            db.execute(sa.text("INSERT INTO channel_accounts(id,name,bot_id,secret,agent_id,session_user_id,settings) VALUES('account','测试','bot','test',:agent,'owner','{}')"), {"agent": agent})
            db.execute(sa.text("INSERT INTO channel_session_bindings(id,account_id,peer_type,peer_id,session_id,peer_metadata) VALUES('binding','account','single','peer','session','{}')"))
            db.execute(sa.text("INSERT INTO channel_message_runs(id,account_id,binding_id,external_message_id,payload_hash) VALUES('run','account','binding','message','hash')"))
            db.exec_driver_sql("CREATE INDEX custom_session_title ON chat_sessions(title)")
            db.exec_driver_sql("CREATE TRIGGER keep_trigger AFTER UPDATE OF title ON chat_sessions BEGIN SELECT 1; END")

    def snapshot(self):
        with self.engine.connect() as db:
            inspector = sa.inspect(db)
            result = {}
            for table in inspector.get_table_names():
                if table == "alembic_version":
                    continue
                result[table] = {
                    "rows": sorted(db.exec_driver_sql(f'SELECT * FROM "{table}"').all(), key=repr),
                    "columns": db.exec_driver_sql(f'PRAGMA table_info("{table}")').all(),
                    "indexes": sorted((index[1], index[2], index[3], tuple(db.exec_driver_sql(f'PRAGMA index_info("{index[1]}")').all())) for index in db.exec_driver_sql(f'PRAGMA index_list("{table}")').all()),
                }
            result["triggers"] = db.exec_driver_sql("SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name").all()
            return result

    def fk_count(self):
        with self.engine.connect() as db:
            inspector = sa.inspect(db)
            return sum(len(inspector.get_foreign_keys(table)) for table in inspector.get_table_names())

    def apply_direct(self, restore=False):
        with self.engine.begin() as db:
            db.exec_driver_sql("BEGIN IMMEDIATE")
            operations = Operations(MigrationContext.configure(db))
            with patch.object(self.migration, "op", operations), patch.object(self.migration.context, "is_offline_mode", return_value=False):
                self.migration._alter(restore=restore)
            self.assertEqual(db.exec_driver_sql("PRAGMA foreign_keys").scalar(), 1)

    def test_roundtrip_preserves_all_records_columns_indexes_and_triggers(self):
        before = self.snapshot()
        self.assertEqual(self.fk_count(), 10)
        self.apply_direct()
        self.assertEqual(self.fk_count(), 0)
        self.assertEqual(before, self.snapshot())
        self.apply_direct()  # Rerunning a partially/recently completed operation is safe.
        self.apply_direct(restore=True)
        self.assertEqual(self.fk_count(), 10)
        with self.engine.connect() as db:
            actual = {(table, fk["constrained_columns"][0], fk["referred_table"], fk["referred_columns"][0], fk["options"].get("ondelete"))
                      for table in sa.inspect(db).get_table_names() for fk in sa.inspect(db).get_foreign_keys(table)}
        self.assertEqual(actual, set(self.migration._REFERENCES))
        self.assertEqual(before, self.snapshot())
        self.apply_direct()
        self.assertEqual(before, self.snapshot())
        with self.engine.connect() as db:
            assert_logical_references(db)

    def test_partial_removal_and_already_removed_constraints(self):
        with self.engine.begin() as db:
            db.exec_driver_sql("BEGIN IMMEDIATE")
            operations = Operations(MigrationContext.configure(db))
            with operations.batch_alter_table("channel_message_runs", naming_convention=self.migration._NAMING) as batch:
                batch.drop_constraint("fk_channel_message_runs_account_id_channel_accounts", type_="foreignkey")
        self.assertEqual(self.fk_count(), 9)
        before = self.snapshot()
        command.upgrade(self.config, "003")
        self.assertEqual(self.fk_count(), 0)
        self.assertEqual(before, self.snapshot())
        command.stamp(self.config, "002")  # Model an ops-completed full FK removal.
        command.upgrade(self.config, "003")
        self.assertEqual(before, self.snapshot())
        command.downgrade(self.config, "002")
        self.assertEqual(self.fk_count(), 10)
        command.upgrade(self.config, "003")
        self.assertEqual(before, self.snapshot())

    def test_all_ten_orphan_relations_refuse_upgrade_and_downgrade_without_changes(self):
        # This fixture is revision 002: exercise exactly the ten references
        # governed by the frozen 003 migration, not later additions to runtime.
        historical = tuple(Relation(*value) for value in self.migration._REFERENCES)
        self.assertEqual(len(historical), 10)
        self.assertTrue(set(historical).issubset(set(LOGICAL_REFERENCES)))
        for relation in historical:
            with self.subTest(relation=relation):
                with sqlite3.connect(self.path) as db:
                    original = db.execute(f'SELECT "{relation.column}" FROM "{relation.table}" LIMIT 1').fetchone()[0]
                    db.execute(f'UPDATE "{relation.table}" SET "{relation.column}"=?', ("missing",))
                before = self.snapshot()
                with self.assertRaisesRegex(RuntimeError, relation.table + "." + relation.column):
                    command.upgrade(self.config, "003")
                self.assertEqual(before, self.snapshot())
                self.assertEqual(self.fk_count(), 10)
                with sqlite3.connect(self.path) as db:
                    db.execute(f'UPDATE "{relation.table}" SET "{relation.column}"=?', (original,))
        command.upgrade(self.config, "003")
        with self.engine.begin() as db:
            db.execute(sa.text("UPDATE channel_message_runs SET binding_id='missing'"))
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "channel_message_runs.binding_id"):
            command.downgrade(self.config, "002")
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.fk_count(), 0)

    def test_current_wecom_orphan_is_detected_and_cannot_be_downgraded_away(self):
        command.upgrade(self.config, "006")
        with self.engine.begin() as db:
            db.execute(sa.text("INSERT INTO wecom_user_profiles(id,wecom_application_id,userid,userid_key) VALUES('profile','missing','User','user')"))
        before = self.snapshot()
        with self.engine.connect() as db:
            with self.assertRaisesRegex(RuntimeError, "wecom_user_profiles.wecom_application_id"):
                assert_logical_references(db)
        self.assertEqual(before, self.snapshot())
        with self.assertRaisesRegex(RuntimeError, "Cannot downgrade"):
            command.downgrade(self.config, "005")
        self.assertEqual(before, self.snapshot())
        with self.engine.connect() as db:
            self.assertEqual(db.scalar(sa.text("SELECT version_num FROM alembic_version")), "006")

    def test_sqlite_mid_rebuild_failure_rolls_back_and_can_retry(self):
        before = self.snapshot()
        original = Operations.batch_alter_table
        count = 0
        def interrupted(operations, *args, **kwargs):
            nonlocal count
            count += 1
            if count == 3:
                raise RuntimeError("simulated interruption")
            return original(operations, *args, **kwargs)
        with patch.object(Operations, "batch_alter_table", interrupted):
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                command.upgrade(self.config, "003")
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.fk_count(), 10)
        command.upgrade(self.config, "003")
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.fk_count(), 0)
