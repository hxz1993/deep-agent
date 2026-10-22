"""012 Agent configuration invariants, atomic writes and portable packages."""
from __future__ import annotations

import copy
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from alembic import command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.agent_packages import archive
from deep_agent.agent_packages.archive import InstallOptions, PackageConflict
from deep_agent.agent_packages.service import export_package, install_package, preview_package
from deep_agent.db import migrate
from deep_agent.db.configuration_transfer import configuration_table_names
from deep_agent.db.models import Agent, AgentOutputConfig
from deep_agent.db.stores.agent.base import InvalidAgentOutputConfig
from deep_agent.files.storage import LocalFileStore
from deep_agent.schemas.agent import AgentCreate, AgentUpdate
from deep_agent.skills.runtime import SkillRuntimeLibrary
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.unit.test_agent_packages import make_package


OUTPUT_CONFIG = {
    "name": "lease_result", "description": "原生结果 😀",
    "json_schema": {"type": "object", "properties": {
        "contract": {"type": "object", "properties": {"name": {"type": "string"}},
                     "required": ["name"], "additionalProperties": False},
        "text": {"type": "string"},
    }, "required": [], "additionalProperties": False},
}


class AgentOutputInputTests(unittest.TestCase):
    def test_missing_fields_keep_legacy_defaults(self):
        data = AgentCreate(name="legacy", prompt="test")
        self.assertFalse(data.structured_output_enabled)
        self.assertIsNone(data.output_config)
        self.assertNotIn("output_config", AgentUpdate(prompt="updated").model_fields_set)

    def test_strict_switch_and_read_only_actor(self):
        for bad in (None, "false", "true", 1, 0):
            for schema in (AgentCreate, AgentUpdate):
                values = {"name": "test", "prompt": "test", "structured_output_enabled": bad}
                with self.subTest(bad=bad, schema=schema), self.assertRaises(ValueError):
                    schema.model_validate(values)
        for key in ("updated_by", "updated_by_user"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                AgentCreate.model_validate({"name": "test", "prompt": "test", key: "forged"})
        with self.assertRaises(ValueError):
            AgentCreate(name="test", prompt="test", structured_output_enabled=True)

    def test_package_switch_definition_consistency_and_unknown_actor(self):
        for fields in ({"structured_output_enabled": True},
                       {"output_config": OUTPUT_CONFIG},
                       {"structured_output_enabled": False, "output_config": OUTPUT_CONFIG}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                make_package(agent_changes=fields)
        package = make_package(agent_changes={"updated_by": "forged", "structured_output_enabled": True,
                                               "output_config": OUTPUT_CONFIG})
        self.assertNotIn("updated_by", package.agent.model_dump())
        self.assertEqual(package.agent.output_config.json_schema["properties"]["contract"]["required"], ["name"])


class AgentOutputStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="TMP_to_delete_output_config_")
        self.root = Path(self.temp.name)
        self.stores, self.engine, self.factory = await make_sqlite_stores(f'sqlite+aiosqlite:///{self.root / "db.sqlite"}')
        self.files = LocalFileStore(self.root / "files")
        await self.files.initialize()
        self.ctx = SimpleNamespace(stores=self.stores, file_store=self.files,
            skill_runtime=SkillRuntimeLibrary(self.root / "runtime", file_store=self.files),
            registry=SimpleNamespace(codes=lambda: []))

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.temp.cleanup()

    async def create(self, **kwargs):
        return await self.stores.agent.create_unique(AgentCreate(name="output fixture", prompt="test", **kwargs), user_id="creator")

    async def test_patch_merged_state_and_delete_rules(self):
        row = await self.create(structured_output_enabled=True, output_config=OUTPUT_CONFIG)
        first = await self.stores.agent.get_output_config(row.id)
        for data in (AgentUpdate(output_config=None), AgentUpdate(structured_output_enabled=True, output_config=None)):
            with self.assertRaises(InvalidAgentOutputConfig):
                await self.stores.agent.update_unique(row.id, data, user_id="not-committed")
        unchanged = await self.stores.agent.get_by_id(row.id)
        self.assertTrue(unchanged.structured_output_enabled)
        self.assertEqual(unchanged.updated_by, "creator")
        self.assertEqual(unchanged.updated_at, row.updated_at)
        await self.stores.agent.update_unique(row.id, AgentUpdate(prompt="changed"), user_id="editor")
        self.assertEqual((await self.stores.agent.get_output_config(row.id)).json_schema, first.json_schema)
        await self.stores.agent.update_unique(row.id, AgentUpdate(structured_output_enabled=False), user_id="disabler")
        self.assertIsNotNone(await self.stores.agent.get_output_config(row.id))
        await self.stores.agent.update_unique(row.id, AgentUpdate(output_config=None), user_id="remover")
        self.assertIsNone(await self.stores.agent.get_output_config(row.id))
        with self.assertRaises(InvalidAgentOutputConfig):
            await self.stores.agent.update_unique(row.id, AgentUpdate(structured_output_enabled=True), user_id="editor")

    async def test_config_only_write_touches_actor_time_and_atomic_failure_rolls_back(self):
        row = await self.create(output_config=OUTPUT_CONFIG)
        modified = copy.deepcopy(OUTPUT_CONFIG)
        modified["json_schema"]["properties"]["text"]["maxLength"] = 32
        current = await self.stores.agent.update_unique(row.id, AgentUpdate(output_config=modified), user_id="schema-editor")
        self.assertEqual(current.updated_by, "schema-editor")
        self.assertGreater(current.updated_at, row.updated_at)
        from deep_agent.db.stores.agent import base
        original = base.save_output_config
        async def fail_after_write(db, agent_id, config, **kwargs):
            await original(db, agent_id, config, **kwargs)
            await db.flush()
            raise RuntimeError("injected transaction failure")
        with patch.object(base, "save_output_config", fail_after_write), self.assertRaises(RuntimeError):
            await self.stores.agent.update_unique(row.id, AgentUpdate(
                name="should rollback", structured_output_enabled=True, output_config=OUTPUT_CONFIG), user_id="failed")
        reread = await self.stores.agent.get_by_id(row.id)
        self.assertEqual((reread.name, reread.structured_output_enabled, reread.updated_by, reread.updated_at),
                         (current.name, False, current.updated_by, current.updated_at))
        self.assertEqual((await self.stores.agent.get_output_config(row.id)).json_schema, modified["json_schema"])

    async def test_independent_copy_and_parent_cleanup(self):
        row = await self.create(structured_output_enabled=True, output_config=OUTPUT_CONFIG)
        copied = await self.stores.agent.create_unique(AgentCreate(name="copied", prompt=row.prompt,
            structured_output_enabled=True, output_config=OUTPUT_CONFIG), user_id="copier")
        await self.stores.agent.update_unique(copied.id, AgentUpdate(structured_output_enabled=False, output_config=None), user_id="copier")
        self.assertIsNotNone(await self.stores.agent.get_output_config(row.id))
        self.assertEqual((await self.stores.agent.get_by_id(copied.id)).updated_by, "copier")
        self.assertTrue(await self.stores.agent.delete(row.id))
        self.assertIsNone(await self.stores.agent.get_output_config(row.id))

    async def test_deadlock_or_non_unique_driver_error_is_not_renamed_to_duplicate(self):
        from sqlalchemy.exc import IntegrityError, OperationalError
        from deep_agent.db.stores.agent import base
        row = await self.create(output_config=OUTPUT_CONFIG)
        original = base.save_output_config
        for error_type, code in ((OperationalError, 1213), (IntegrityError, 1213),
                                 (OperationalError, "40P01"), (IntegrityError, "40001")):
            with self.subTest(error_type=error_type, code=code):
                driver = Exception(code, "synthetic deadlock")
                if isinstance(code, str):
                    driver.sqlstate = code
                injected = error_type("synthetic update", {}, driver)
                async def fail_after_write(db, agent_id, config, **kwargs):
                    await original(db, agent_id, config, **kwargs)
                    await db.flush()
                    raise injected
                with patch.object(base, "save_output_config", fail_after_write), self.assertRaises(error_type) as caught:
                    await self.stores.agent.update_unique(row.id, AgentUpdate(
                        name="must roll back", structured_output_enabled=True,
                        output_config={**OUTPUT_CONFIG, "description": "uncommitted"}), user_id="uncommitted")
                self.assertIs(caught.exception, injected)
                reread = await self.stores.agent.get_by_id(row.id)
                self.assertEqual((reread.name, reread.structured_output_enabled, reread.updated_by, reread.updated_at),
                                 (row.name, False, row.updated_by, row.updated_at))
                self.assertEqual((await self.stores.agent.get_output_config(row.id)).description, OUTPUT_CONFIG["description"])

    async def test_corrupt_disabled_draft_does_not_block_legacy_patch_but_reenable_fails(self):
        row = await self.create(output_config=OUTPUT_CONFIG)
        async with self.stores.agent._write() as db:
            config = await db.get(AgentOutputConfig, row.id)
            config.json_schema = {"$ref": "https://invalid.example/schema"}
        await self.stores.agent.update_unique(row.id, AgentUpdate(prompt="legacy still works"), user_id="editor")
        with self.assertRaises(InvalidAgentOutputConfig):
            await self.stores.agent.update_unique(row.id, AgentUpdate(structured_output_enabled=True), user_id="editor")

    async def test_formal_export_omits_disabled_draft_then_enabled_roundtrip(self):
        row = await self.create(output_config=OUTPUT_CONFIG)
        raw = await export_package(self.ctx, row.id, {})
        files = archive.read_zip(raw, [0, 0])
        exported = archive.json_read(files["agent.json"])
        self.assertNotIn("structured_output_enabled", exported)
        self.assertNotIn("output_config", exported)
        self.assertNotIn("updated_by", exported)
        self.assertEqual(set(files), {"manifest.json", "agent.json", "ui-tools.json"})
        await self.stores.agent.update_unique(row.id, AgentUpdate(structured_output_enabled=True), user_id="enabler")
        enabled = archive.parse_package(await export_package(self.ctx, row.id, {}))
        preview = await preview_package(self.ctx, enabled)
        installed = await install_package(self.ctx, enabled, InstallOptions(
            digest=preview["digest"], name="imported output", model_id=None), {}, user_id="installer")
        actual = await self.stores.agent.get_by_id(installed["agent"].id)
        self.assertTrue(actual.structured_output_enabled)
        self.assertEqual(actual.updated_by, "installer")
        self.assertEqual((await self.stores.agent.get_output_config(actual.id)).json_schema, OUTPUT_CONFIG["json_schema"])

    async def test_old_package_overwrite_clears_output_and_snapshot_tracks_only_schema_changes(self):
        row = await self.create(structured_output_enabled=True, output_config=OUTPUT_CONFIG)
        package = make_package()
        preview = await preview_package(self.ctx, package, "overwrite", row.id)
        options = InstallOptions(digest=preview["digest"], name=row.name, model_id=None,
                                 mode="overwrite", target_agent_id=row.id)
        changed = copy.deepcopy(OUTPUT_CONFIG)
        changed["json_schema"]["properties"]["text"]["minLength"] = 1
        await self.stores.agent.update_unique(row.id, AgentUpdate(output_config=changed), user_id="schema-editor")
        with self.assertRaises(PackageConflict):
            await install_package(self.ctx, package, options, {}, user_id="installer")
        new_preview = await preview_package(self.ctx, package, "overwrite", row.id)
        self.assertFalse(new_preview["structured_output"]["enabled"])
        self.assertTrue(new_preview["structured_output"]["target_enabled"])
        options = options.model_copy(update={"digest": new_preview["digest"]})
        await install_package(self.ctx, package, options, {}, user_id="installer")
        actual = await self.stores.agent.get_by_id(row.id)
        self.assertFalse(actual.structured_output_enabled)
        self.assertEqual(actual.updated_by, "installer")
        self.assertIsNone(await self.stores.agent.get_output_config(row.id))

    async def test_list_only_queries_config_boolean_and_backup_order_includes_drafts(self):
        await self.create(output_config=OUTPUT_CONFIG)
        statements = []
        from sqlalchemy import event
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            self.assertEqual(len(await self.stores.agent.list_all()), 1)
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
        self.assertEqual(len(statements), 1)
        self.assertIn("agent_output_configs.allow_text_output", statements[0])
        self.assertNotIn("json_schema", statements[0])
        self.assertNotIn("agent_output_configs.description", statements[0])
        order = configuration_table_names()
        self.assertEqual(order.index("agent_output_configs"), order.index("agents") + 1)

    async def test_enabled_install_commit_failure_and_lost_ack_include_config_and_files(self):
        for lost_ack in (False, True):
            with self.subTest(lost_ack=lost_ack):
                package = make_package(resources=True, agent_changes={
                    "structured_output_enabled": True, "output_config": OUTPUT_CONFIG})
                preview = await preview_package(self.ctx, package)
                options = InstallOptions(digest=preview["digest"], name=f"commit boundary {lost_ack}", model_id=None)
                original = AsyncSession.commit
                commits = 0
                async def failing_commit(db):
                    nonlocal commits
                    commits += 1
                    if commits == 1 and not lost_ack:
                        raise OSError("injected output config commit failure")
                    await original(db)
                    if commits == 1:
                        raise OSError("injected output config commit acknowledgment loss")
                with patch.object(AsyncSession, "commit", failing_commit):
                    if lost_ack:
                        installed = await install_package(self.ctx, package, options, {}, user_id="target-installer")
                    else:
                        with self.assertRaises(archive.PackageError):
                            await install_package(self.ctx, package, options, {}, user_id="target-installer")
                async with self.stores.agent._read() as db:
                    configs = list((await db.execute(select(AgentOutputConfig))).scalars())
                with sqlite3.connect(self.files.metadata_path) as db:
                    file_count = db.execute("SELECT COUNT(*) FROM files WHERE deleted_at IS NULL").fetchone()[0]
                if lost_ack:
                    self.assertEqual(len(configs), 1)
                    self.assertEqual(configs[0].agent_id, installed["agent"].id)
                    self.assertEqual(configs[0].json_schema, OUTPUT_CONFIG["json_schema"])
                    self.assertEqual(installed["agent"].updated_by, "target-installer")
                    self.assertEqual(file_count, 1)
                else:
                    self.assertEqual(configs, [])
                    self.assertEqual(await self.stores.agent.list_all(), [])
                    self.assertEqual(file_count, 0)

    async def test_export_detects_only_schema_or_switch_change_during_skill_download(self):
        """PK-13: the second snapshot covers the new table and the Agent switch."""
        package = make_package(resources=True, agent_changes={
            "structured_output_enabled": True, "output_config": OUTPUT_CONFIG})
        preview = await preview_package(self.ctx, package)
        installed = await install_package(self.ctx, package,
            InstallOptions(digest=preview["digest"], name="export race", model_id=None), {}, user_id="installer")
        agent_id = installed["agent"].id
        original_download = self.ctx.skill_runtime.download_package
        for field in ("schema", "switch", "allow_text_output"):
            with self.subTest(field=field):
                before = await self.stores.agent.get_by_id(agent_id)
                async def change_during_download(row, **kwargs):
                    raw = await original_download(row, **kwargs)
                    async with self.stores.agent._write() as db:
                        if field == "schema":
                            config = await db.get(AgentOutputConfig, agent_id)
                            config.json_schema = {**config.json_schema, "description": "concurrent schema-only change"}
                        elif field == "switch":
                            (await db.get(Agent, agent_id)).structured_output_enabled = False
                        else:
                            (await db.get(AgentOutputConfig, agent_id)).allow_text_output = True
                    return raw
                with patch.object(self.ctx.skill_runtime, "download_package", side_effect=change_during_download):
                    with self.assertRaisesRegex(PackageConflict, "导出期间配置发生变化"):
                        await export_package(self.ctx, agent_id, {})
                after = await self.stores.agent.get_by_id(agent_id)
                if field == "schema":
                    self.assertEqual(after.updated_at, before.updated_at)
                self.assertEqual(after.updated_by, before.updated_by)

    async def test_concurrent_enabled_package_install_has_one_complete_config_winner(self):
        """PK-20: a losing identical package install leaves no orphan output config."""
        package = make_package(resources=True, agent_changes={
            "structured_output_enabled": True, "output_config": OUTPUT_CONFIG})
        preview = await preview_package(self.ctx, package)
        options = InstallOptions(digest=preview["digest"], name="same package", model_id=None)
        outcomes = await asyncio.gather(*[
            install_package(self.ctx, package, options, {}, user_id=f"installer-{n}") for n in range(2)
        ], return_exceptions=True)
        self.assertEqual(sum(not isinstance(o, BaseException) for o in outcomes), 1, outcomes)
        self.assertEqual(sum(isinstance(o, PackageConflict) for o in outcomes), 1, outcomes)
        agents = await self.stores.agent.list_all()
        self.assertEqual(len(agents), 1)
        async with self.stores.agent._read() as db:
            configs = list((await db.execute(select(AgentOutputConfig))).scalars())
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].agent_id, agents[0].id)
        self.assertEqual(configs[0].json_schema, OUTPUT_CONFIG["json_schema"])
        with sqlite3.connect(self.files.metadata_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM files WHERE deleted_at IS NULL").fetchone()[0], 1)

    async def test_deleted_overwrite_target_rejects_package_without_new_resources_or_config(self):
        """PK-20: target removal after preview fails before resource/file installation."""
        target = await self.create(structured_output_enabled=True, output_config=OUTPUT_CONFIG)
        package = make_package(resources=True, agent_changes={
            "structured_output_enabled": True, "output_config": OUTPUT_CONFIG})
        preview = await preview_package(self.ctx, package, "overwrite", target.id)
        options = InstallOptions(digest=preview["digest"], name=target.name, model_id=None,
                                 mode="overwrite", target_agent_id=target.id)
        await self.stores.agent.delete(target.id)
        with self.assertRaises(PackageConflict):
            await install_package(self.ctx, package, options, {}, user_id="installer")
        self.assertEqual(await self.stores.agent.list_all(), [])
        async with self.stores.agent._read() as db:
            self.assertEqual(list((await db.execute(select(AgentOutputConfig))).scalars()), [])
        with sqlite3.connect(self.files.metadata_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM files WHERE deleted_at IS NULL").fetchone()[0], 0)


class AgentOutputMigrationTests(unittest.TestCase):
    def config(self, path):
        url = f"sqlite+aiosqlite:///{path}"
        with patch("deep_agent.db.migrate.get_database_url", return_value=url):
            config = migrate._build_alembic_config()
        config.attributes["database_url"] = url
        return config

    def test_011_upgrade_defaults_unicode_and_downgrade_guards(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_output_migration_") as temp:
            path = Path(temp) / "db.sqlite"
            config = self.config(path)
            command.upgrade(config, "011")
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO agents (id,name,prompt,capabilities,skills,ui_tool_selection) VALUES ('legacy','legacy','test','[]','[]','{}')")
            command.upgrade(config, "012")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT structured_output_enabled,updated_by FROM agents").fetchone(), (0, None))
                self.assertEqual(db.execute("PRAGMA foreign_key_list(agent_output_configs)").fetchall(), [])
                db.execute("INSERT INTO agent_output_configs (agent_id,name,description,json_schema) VALUES (?,?,?,?)",
                           ("legacy", "lease_result", "😀 合同", json.dumps(OUTPUT_CONFIG["json_schema"], ensure_ascii=False)))
            with self.assertRaisesRegex(RuntimeError, "output_configs=1"):
                command.downgrade(config, "011")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT description FROM agent_output_configs").fetchone(), ("😀 合同",))
                db.execute("DELETE FROM agent_output_configs")
                db.execute("UPDATE agents SET updated_by='trusted-user' WHERE id='legacy'")
            with self.assertRaisesRegex(RuntimeError, "updated_agents=1"):
                command.downgrade(config, "011")
            with sqlite3.connect(path) as db:
                db.execute("UPDATE agents SET updated_by=NULL")
            command.downgrade(config, "011")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT name,prompt FROM agents WHERE id='legacy'").fetchone(), ("legacy", "test"))
                self.assertNotIn("agent_output_configs", {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
