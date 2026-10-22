"""013 configuration, portable package and historical backup contracts."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.agent_packages import archive
from deep_agent.agent_packages.archive import InstallOptions, PackageConflict
from deep_agent.agent_packages.service import export_package, install_package, preview_package
from deep_agent.db import migrate
from deep_agent.db.models import Agent, AgentOutputConfig
from deep_agent.schemas.agent import AgentCreate, AgentUpdate, AgentResponse
from deep_agent.services.configuration_import import build_configuration_import_plan, apply_configuration_import
from maintenance.config_backup import export_backup, _upgrade_target_schema
from tests.unit import test_agent_output_configuration as fixture
from tests.unit import test_config_backup as backup_fixture
from tests.unit.test_configuration_import import _MemoryFileStore
from tests.unit.test_agent_packages import make_package


class AllowTextOutputInputTests(unittest.TestCase):
    def test_default_strict_bool_and_patch_omission(self):
        self.assertFalse(AgentCreate(name="plain", prompt="test").allow_text_output)
        self.assertNotIn("allow_text_output", AgentUpdate(prompt="updated").model_fields_set)
        for value in (None, 0, 1, "true", "false", [], {}):
            for cls in (AgentCreate, AgentUpdate):
                with self.subTest(value=value, cls=cls), self.assertRaises(ValueError):
                    cls.model_validate({"name": "plain", "prompt": "test", "allow_text_output": value})
        with self.assertRaises(ValueError):
            make_package(agent_changes={"allow_text_output": True})
        for value in (None, 0, "true"):
            with self.subTest(package=value), self.assertRaises(ValueError):
                make_package(agent_changes={"structured_output_enabled": True,
                    "output_config": fixture.OUTPUT_CONFIG, "allow_text_output": value})


class AllowTextOutputStorageTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.AgentOutputStorageTests.asyncSetUp
    asyncTearDown = fixture.AgentOutputStorageTests.asyncTearDown
    create = fixture.AgentOutputStorageTests.create

    async def test_physical_config_policy_and_boolean_only_list_read(self):
        mixed = await self.create(allow_text_output=True, output_config=fixture.OUTPUT_CONFIG)
        plain = await self.stores.agent.create_unique(AgentCreate(name="no config", prompt="synthetic"), user_id="creator")
        self.assertNotIn("allow_text_output", Agent.__table__.columns)
        self.assertIn("allow_text_output", AgentOutputConfig.__table__.columns)
        async with self.engine.connect() as db:
            self.assertTrue(await db.scalar(text("SELECT allow_text_output FROM agent_output_configs WHERE agent_id=:id"),
                {"id": mixed.id}))
            self.assertEqual(await db.scalar(text("SELECT COUNT(*) FROM agent_output_configs WHERE agent_id=:id"),
                {"id": plain.id}), 0)
        from sqlalchemy import event
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            rows = await self.stores.agent.list_all()
            self.assertEqual({row.id: row.allow_text_output for row in rows}, {mixed.id: True, plain.id: False})
            self.assertEqual({row.id: AgentResponse.from_row(row, None).allow_text_output for row in rows},
                {mixed.id: True, plain.id: False})
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
        self.assertEqual(len(statements), 1)
        self.assertIn("agent_output_configs.allow_text_output", statements[0])
        for full_definition_column in ("json_schema", "agent_output_configs.description", "agent_output_configs.name"):
            self.assertNotIn(full_definition_column, statements[0])

    async def test_draft_omission_explicit_false_and_independent_copy(self):
        row = await self.create(allow_text_output=True, output_config=fixture.OUTPUT_CONFIG)
        self.assertFalse(row.structured_output_enabled)
        self.assertTrue(AgentResponse.from_row(row, None).allow_text_output)
        await self.stores.agent.update_unique(row.id, AgentUpdate(prompt="preserve draft"), user_id="editor")
        self.assertTrue((await self.stores.agent.get_by_id(row.id)).allow_text_output)
        edited = {**fixture.OUTPUT_CONFIG, "description": "definition update keeps independent flag"}
        await self.stores.agent.update_unique(row.id, AgentUpdate(output_config=edited), user_id="schema-editor")
        self.assertTrue((await self.stores.agent.get_by_id(row.id)).allow_text_output)
        self.assertTrue((await self.stores.agent.get_output_config(row.id)).allow_text_output)
        await self.stores.agent.update_unique(row.id, AgentUpdate(structured_output_enabled=True), user_id="enable")
        await self.stores.agent.update_unique(row.id, AgentUpdate(structured_output_enabled=False), user_id="disable")
        self.assertTrue((await self.stores.agent.get_by_id(row.id)).allow_text_output)
        source, config = await self.stores.agent.get_output_configuration(row.id)
        copied = await self.stores.agent.create_unique(AgentCreate(name="copied", prompt=source.prompt,
            structured_output_enabled=source.structured_output_enabled, allow_text_output=source.allow_text_output,
            output_config=config), user_id="copier")
        await self.stores.agent.update_unique(copied.id, AgentUpdate(allow_text_output=False, output_config=None), user_id="copier")
        self.assertFalse((await self.stores.agent.get_by_id(copied.id)).allow_text_output)
        self.assertTrue((await self.stores.agent.get_by_id(row.id)).allow_text_output)
        self.assertIsNotNone(await self.stores.agent.get_output_config(row.id))

    async def test_flag_definition_actor_and_time_roll_back_together(self):
        row = await self.create(structured_output_enabled=True, output_config=fixture.OUTPUT_CONFIG)
        changed = deepcopy(fixture.OUTPUT_CONFIG)
        changed["description"] = "must roll back"
        with patch("deep_agent.db.stores.agent.base.save_output_config", side_effect=RuntimeError("injected config failure")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                await self.stores.agent.update_unique(row.id,
                    AgentUpdate(allow_text_output=True, output_config=changed), user_id="not-committed")
        after = await self.stores.agent.get_by_id(row.id)
        self.assertEqual((after.allow_text_output, after.updated_by, after.updated_at),
                         (False, row.updated_by, row.updated_at))
        self.assertEqual((await self.stores.agent.get_output_config(row.id)).description, fixture.OUTPUT_CONFIG["description"])

    async def test_formal_exports_omit_off_and_false_preserve_mixed_roundtrip_and_old_overwrite(self):
        row = await self.create(allow_text_output=True, output_config=fixture.OUTPUT_CONFIG)
        off = archive.json_read(archive.read_zip(await export_package(self.ctx, row.id, {}), [0, 0])["agent.json"])
        for key in ("structured_output_enabled", "output_config", "allow_text_output"):
            self.assertNotIn(key, off)
        await self.stores.agent.update_unique(row.id,
            AgentUpdate(structured_output_enabled=True, allow_text_output=False), user_id="strict")
        strict = archive.json_read(archive.read_zip(await export_package(self.ctx, row.id, {}), [0, 0])["agent.json"])
        self.assertTrue(strict["structured_output_enabled"])
        self.assertNotIn("allow_text_output", strict)
        await self.stores.agent.update_unique(row.id, AgentUpdate(allow_text_output=True), user_id="mixed")
        raw = await export_package(self.ctx, row.id, {})
        mixed = archive.parse_package(raw)
        self.assertTrue(mixed.agent.allow_text_output)
        self.assertIs(type(mixed.manifest.schema_version), int)
        preview = await preview_package(self.ctx, mixed)
        self.assertTrue(preview["structured_output"]["allow_text_output"])
        installed = await install_package(self.ctx, mixed, InstallOptions(digest=preview["digest"],
            name="mixed roundtrip", model_id=None), {}, user_id="installer")
        target = await self.stores.agent.get_by_id(installed["agent"].id)
        self.assertTrue(target.allow_text_output)
        self.assertEqual((await self.stores.agent.get_output_config(target.id)).json_schema, fixture.OUTPUT_CONFIG["json_schema"])
        self.assertTrue(archive.parse_package(await export_package(self.ctx, target.id, {})).agent.allow_text_output)
        legacy = make_package()
        preview = await preview_package(self.ctx, legacy, "overwrite", target.id)
        self.assertTrue(preview["structured_output"]["target_allow_text_output"])
        await install_package(self.ctx, legacy, InstallOptions(digest=preview["digest"], name=target.name,
            model_id=None, mode="overwrite", target_agent_id=target.id), {}, user_id="legacy-installer")
        after = await self.stores.agent.get_by_id(target.id)
        self.assertFalse(after.allow_text_output)
        self.assertFalse(after.structured_output_enabled)
        self.assertIsNone(await self.stores.agent.get_output_config(target.id))

    async def test_policy_only_preview_change_and_concurrent_mixed_install(self):
        row = await self.create(structured_output_enabled=True, output_config=fixture.OUTPUT_CONFIG)
        package = make_package(agent_changes={"structured_output_enabled": True,
            "output_config": fixture.OUTPUT_CONFIG, "allow_text_output": True})
        preview = await preview_package(self.ctx, package, "overwrite", row.id)
        await self.stores.agent.update_unique(row.id, AgentUpdate(allow_text_output=True), user_id="policy-editor")
        with self.assertRaises(PackageConflict):
            await install_package(self.ctx, package, InstallOptions(digest=preview["digest"], name=row.name,
                model_id=None, mode="overwrite", target_agent_id=row.id), {}, user_id="installer")
        create_preview = await preview_package(self.ctx, package)
        options = InstallOptions(digest=create_preview["digest"], name="concurrent mixed", model_id=None)
        outcomes = await asyncio.gather(*[install_package(self.ctx, package, options, {}, user_id=f"installer-{n}")
                                          for n in range(2)], return_exceptions=True)
        self.assertEqual(sum(isinstance(o, PackageConflict) for o in outcomes), 1, outcomes)
        self.assertEqual(sum(not isinstance(o, BaseException) for o in outcomes), 1, outcomes)
        installed = next(o for o in outcomes if not isinstance(o, BaseException))
        self.assertTrue((await self.stores.agent.get_by_id(installed["agent"].id)).allow_text_output)

    async def test_mixed_package_failed_commit_rolls_back_and_lost_ack_keeps_committed_policy(self):
        for lost_ack in (False, True):
            with self.subTest(lost_ack=lost_ack):
                package = make_package(resources=True, agent_changes={"structured_output_enabled": True,
                    "output_config": fixture.OUTPUT_CONFIG, "allow_text_output": True})
                preview = await preview_package(self.ctx, package)
                options = InstallOptions(digest=preview["digest"], name="mixed commit boundary", model_id=None)
                original = AsyncSession.commit
                commits = 0
                async def commit(db):
                    nonlocal commits
                    commits += 1
                    if commits == 1 and not lost_ack:
                        raise OSError("injected mixed commit failure")
                    await original(db)
                    if commits == 1:
                        raise OSError("injected lost mixed commit acknowledgement")
                with patch.object(AsyncSession, "commit", commit):
                    if lost_ack:
                        installed = await install_package(self.ctx, package, options, {}, user_id="installer")
                    else:
                        with self.assertRaises(archive.PackageError):
                            await install_package(self.ctx, package, options, {}, user_id="installer")
                rows = await self.stores.agent.list_all()
                self.assertEqual(len(rows), 1 if lost_ack else 0)
                with sqlite3.connect(self.files.metadata_path) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM files WHERE deleted_at IS NULL").fetchone()[0],
                                     1 if lost_ack else 0)
                if lost_ack:
                    row = await self.stores.agent.get_by_id(installed["agent"].id)
                    self.assertTrue(row.allow_text_output)
                    self.assertTrue(row.structured_output_enabled)
                    self.assertEqual(row.updated_by, "installer")
                    self.assertIsNotNone(await self.stores.agent.get_output_config(row.id))


class AllowTextOutputMigrationTests(unittest.TestCase):
    def test_later_012_or_011_refusal_is_preflighted_before_any_013_ddl(self):
        from alembic import op
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_allow_text_downgrade_preflight_") as temporary:
            path = Path(temporary) / "migration.db"
            url = f"sqlite+aiosqlite:///{path}"
            with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                config = migrate._build_alembic_config()
            config.attributes["database_url"] = url
            command.upgrade(config, "013")
            with sqlite3.connect(path) as db:
                aid = db.execute("SELECT id FROM agents LIMIT 1").fetchone()[0]
                db.execute("INSERT INTO agent_output_configs(agent_id,name,json_schema) VALUES(?,'strict_draft','{}')", (aid,))
            original_batch = op.batch_alter_table
            for target in ("011", "-2", "010", "base", "-3"):
                with self.subTest(target=target), patch.object(op, "batch_alter_table", wraps=original_batch) as ddl:
                    with self.assertRaisesRegex(RuntimeError, "preflight before 013 DDL|refused before 013 DDL"):
                        command.downgrade(config, target)
                    ddl.assert_not_called()
                with sqlite3.connect(path) as db:
                    self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone()[0], "013")
                    self.assertIn("allow_text_output", {c[1] for c in db.execute("PRAGMA table_info(agent_output_configs)")})
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM agent_output_configs").fetchone()[0], 1)
            # A strict 012-compatible draft must not be rejected when 012 is
            # the requested destination, because its data/table remain intact.
            command.downgrade(config, "012")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM agent_output_configs").fetchone()[0], 1)
                self.assertNotIn("allow_text_output", {c[1] for c in db.execute("PRAGMA table_info(agent_output_configs)")})
            command.upgrade(config, "013")
            with sqlite3.connect(path) as db:
                db.execute("DELETE FROM agent_output_configs")
            command.downgrade(config, "-2")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone()[0], "011")

    def test_012_upgrade_defaults_repeated_upgrade_and_guarded_downgrade(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_allow_text_migration_") as temporary:
            path = Path(temporary) / "migration.db"
            url = f"sqlite+aiosqlite:///{path}"
            with patch("deep_agent.db.migrate.get_database_url", return_value=url):
                config = migrate._build_alembic_config()
            config.attributes["database_url"] = url
            command.upgrade(config, "012")
            with sqlite3.connect(path) as db:
                aid = db.execute("SELECT id FROM agents LIMIT 1").fetchone()[0]
                db.execute("INSERT INTO agent_output_configs(agent_id,name,json_schema) VALUES(?,'existing_012','{}')", (aid,))
            command.upgrade(config, "013")
            command.upgrade(config, "013")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT DISTINCT allow_text_output FROM agent_output_configs").fetchall(), [(0,)])
                column = next(c for c in db.execute("PRAGMA table_info(agent_output_configs)") if c[1] == "allow_text_output")
                self.assertEqual(column[3], 1)
                db.execute("UPDATE agent_output_configs SET allow_text_output=1")
            with self.assertRaisesRegex(RuntimeError, "configured_output_configs="):
                command.downgrade(config, "012")
            with sqlite3.connect(path) as db:
                db.execute("UPDATE agent_output_configs SET allow_text_output=0")
                aid = db.execute("SELECT id FROM agents LIMIT 1").fetchone()[0]
                db.execute("INSERT INTO chat_sessions(session_id,agent_id,user_id,next_sequence) VALUES('s',?,'fixture',2)", (aid,))
                payload = {"message": {"metadata": {"deepagent_output_contract": {"allow_text_output": True}}}}
                db.execute("INSERT INTO chat_session_events(event_id,session_id,sequence,schema_version,event_type,payload) VALUES('e','s',1,1,'user/message',?)", (json.dumps(payload),))
            with self.assertRaisesRegex(RuntimeError, "frozen_events=1"):
                command.downgrade(config, "012")
            with sqlite3.connect(path) as db:
                db.execute("UPDATE chat_session_events SET payload=?", (json.dumps({"message": {"metadata": {
                    "deepagent_output_contract": {"allow_text_output": False, "unrelated": True}}}}),))
            command.downgrade(config, "012")
            with sqlite3.connect(path) as db:
                self.assertNotIn("allow_text_output", {c[1] for c in db.execute("PRAGMA table_info(agent_output_configs)")})
                self.assertEqual(db.execute("SELECT COUNT(*) FROM chat_session_events").fetchone()[0], 1)


class AllowTextOutputBackupTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_011_012_013_same_revision_backups_do_not_inject_new_orm_column(self):
        for revision in ("011", "012", "013"):
            with self.subTest(revision=revision), tempfile.TemporaryDirectory(prefix="TMP_to_delete_allow_text_backup_") as temporary:
                root = Path(temporary)
                source = root / "source.db"
                await backup_fixture.ConfigBackupTests()._create_source(source, revision=revision)
                with sqlite3.connect(source) as db:
                    db.execute("UPDATE agents SET skills='[]'")
                    db.execute("DELETE FROM skill_packages")
                    if revision in {"012", "013"}:
                        db.execute("UPDATE agents SET structured_output_enabled=0")
                        db.execute("INSERT INTO agent_output_configs(agent_id,name,json_schema) VALUES('agent-1','draft_result','{}')")
                    if revision == "013":
                        db.execute("UPDATE agent_output_configs SET allow_text_output=1")
                backup = await export_backup(f"sqlite+aiosqlite:///{source}", environment="fixture",
                    output_root=root / "backups", git_commit="synthetic")
                target_url = f"sqlite+aiosqlite:///{root / 'target.db'}"
                await asyncio.to_thread(_upgrade_target_schema, target_url, revision)
                target = create_async_engine(target_url)
                try:
                    plan = await build_configuration_import_plan(backup.path, target_engine=target)
                    row = plan.rows["agents"][0]
                    self.assertNotIn("allow_text_output", row)
                    if revision == "013":
                        self.assertTrue(plan.rows["agent_output_configs"][0]["allow_text_output"])
                        self.assertFalse(row["structured_output_enabled"])
                    elif revision == "012":
                        self.assertNotIn("allow_text_output", plan.rows["agent_output_configs"][0])
                    if revision == "011":
                        self.assertNotIn("structured_output_enabled", row)
                    await apply_configuration_import(plan, target_engine=target, file_store=_MemoryFileStore(), request_headers={})
                    async with target.connect() as db:
                        self.assertEqual(await db.scalar(text("SELECT version_num FROM alembic_version")), revision)
                        self.assertEqual(await db.scalar(text("SELECT COUNT(*) FROM agents")), 1)
                        if revision == "013":
                            self.assertTrue(await db.scalar(text("SELECT allow_text_output FROM agent_output_configs")))
                            self.assertEqual(await db.scalar(text("SELECT COUNT(*) FROM agent_output_configs")), 1)
                finally:
                    await target.dispose()
