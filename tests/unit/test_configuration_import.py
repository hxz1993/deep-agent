from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from deep_agent.db.models import Agent, Base, GlobalSetting, SkillPackage, IntegrationSettings
from deep_agent.files.storage import StoredFile
from deep_agent.services.configuration_import import (
    ConfigurationImportConflictError,
    ConfigurationImportError,
    _normalize_ondelete,
    _type_family,
    apply_configuration_import,
    build_configuration_import_plan,
)
from deep_agent.skills.package import parse_skill_zip
from maintenance.config_backup import export_backup
from tests.unit import test_config_backup as config_backup_tests
from tests.unit.test_skill_packages import skill_zip


class _MemoryFileStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.deleted: list[str] = []

    async def upload(
        self,
        *,
        file_type: str,
        file_name: str,
        mime: str | None,
        data: bytes,
        request_headers: Mapping[str, str],
    ) -> StoredFile:
        del mime, request_headers
        file_id = f"target-file-{len(self.blobs) + 1}"
        self.blobs[file_id] = bytes(data)
        return StoredFile(
            id=file_id,
            file_name=file_name,
            file_size=len(data),
            file_type=file_type,
        )

    async def download(
        self,
        file_id: str,
        request_headers: Mapping[str, str],
    ) -> bytes:
        del request_headers
        return self.blobs[file_id]

    async def delete(
        self,
        file_id: str,
        request_headers: Mapping[str, str],
    ) -> None:
        del request_headers
        self.deleted.append(file_id)
        self.blobs.pop(file_id, None)

    async def register_download(self, **kwargs: object) -> str:
        del kwargs
        raise AssertionError("Configuration import must not register downloads")


class ConfigurationImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def test_mysql_reflection_equivalences_are_normalized(self) -> None:
        self.assertEqual(_type_family(TINYINT()), "boolean")
        self.assertIsNone(_normalize_ondelete("RESTRICT"))
        self.assertIsNone(_normalize_ondelete(None))
        self.assertEqual(_normalize_ondelete("CASCADE"), "CASCADE")

    @staticmethod
    def _url(path: Path) -> str:
        return f"sqlite+aiosqlite:///{path.as_posix()}"

    async def _target_engine(
        self,
        *,
        revision: str = "007",
        baseline: bool = False,
    ) -> AsyncEngine:
        target = self.root / f"target-{revision}-{baseline}.db"
        engine = create_async_engine(self._url(target))
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(
                text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            await connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": revision},
            )
            if baseline:
                await connection.execute(
                    insert(GlobalSetting.__table__),
                    {
                        "key": "target-only",
                        "value": {"keep": True},
                        "version": 1,
                    },
                )
        return engine

    async def _backup(
        self,
        *,
        include_skill: bool = False,
        revision: str = "007",
    ) -> tuple[Path, Path | None, bytes | None]:
        source = self.root / f"source-{include_skill}-{revision}.db"
        await config_backup_tests.ConfigBackupTests()._create_source(source, revision="007")
        raw_skill: bytes | None = None
        if include_skill:
            raw_skill = skill_zip(name="uploaded-skill")
            parsed = parse_skill_zip(raw_skill)
            try:
                engine = create_async_engine(self._url(source))
                try:
                    async with engine.begin() as connection:
                        await connection.execute(
                            update(SkillPackage.__table__)
                            .where(SkillPackage.code == "uploaded-skill")
                            .values(
                                label=parsed.label,
                                description=parsed.description,
                                version=parsed.version,
                                schema_version=parsed.schema_version,
                                file_size=len(raw_skill),
                                content_hash=parsed.content_hash,
                                manifest=parsed.manifest,
                                resource_index=parsed.resource_index,
                                contains_scripts=parsed.contains_scripts,
                            )
                        )
                finally:
                    await engine.dispose()
            finally:
                shutil.rmtree(parsed.package_root.parent, ignore_errors=True)
        else:
            with sqlite3.connect(source) as connection:
                connection.execute("UPDATE agents SET skills='[]'")
                connection.execute("DELETE FROM skill_packages")

        result = await export_backup(
            self._url(source),
            environment="test",
            output_root=self.root / f"backup-{include_skill}-{revision}",
            exported_at=datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc),
            git_commit="test-commit",
        )
        if revision != "007":
            with sqlite3.connect(result.path) as connection:
                connection.execute(
                    "UPDATE alembic_version SET version_num = ?",
                    (revision,),
                )
                connection.execute(
                    "UPDATE _backup_info SET alembic_revision = ?",
                    (revision,),
                )

        bundle_path: Path | None = None
        if raw_skill is not None:
            bundle_path = self.root / f"skills-{revision}.zip"
            manifest = {
                "format": "deepagent-skill-export",
                "schema_version": "1.0",
                "exported_at": "2026-09-01T08:00:00+00:00",
                "skills": [
                    {
                        "code": "uploaded-skill",
                        "label": "example-skill",
                        "description": "Example deferred Skill.",
                        "version": None,
                        "schema_version": "1.0",
                        "package_file": "skills/uploaded-skill.zip",
                        "content_hash": self._skill_hash(result.path),
                        "original_file_name": "uploaded-skill.zip",
                    }
                ],
            }
            with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_STORED) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest).encode("utf-8"),
                )
                archive.writestr("skills/uploaded-skill.zip", raw_skill)
        return result.path, bundle_path, raw_skill

    @staticmethod
    def _skill_hash(backup_path: Path) -> str:
        with sqlite3.connect(backup_path) as connection:
            row = connection.execute(
                "SELECT content_hash FROM skill_packages "
                "WHERE code = 'uploaded-skill'"
            ).fetchone()
        assert row is not None
        return str(row[0])

    async def test_wecom_import_restores_application_reference_and_discards_target_cache(self):
        backup_path, _, _ = await self._backup()
        with sqlite3.connect(backup_path) as db:
            db.execute("INSERT INTO wecom_applications (id,name,corp_id,wecom_agent_id,app_secret,settings,revision) VALUES ('app','fixture','corp','100','fixture-secret','{}',1)")
            db.execute("INSERT INTO channel_accounts (id,name,bot_id,secret,agent_id,session_user_id,settings) VALUES ('channel','fixture','bot','fixture','agent-1','fixture',?)", (json.dumps({"other": 1, "wecom": {"user_details_enabled": True, "application_id": "app"}}),))
        engine = await self._target_engine()
        try:
            async with engine.begin() as db:
                await db.execute(text("INSERT INTO wecom_applications (id,name,corp_id,wecom_agent_id,app_secret,settings,revision) VALUES ('old','old','old','1','old','{}',1)"))
                await db.execute(text("INSERT INTO wecom_user_profiles (id,wecom_application_id,userid,userid_key) VALUES ('cached','old','User','user')"))
            plan = await build_configuration_import_plan(backup_path, target_engine=engine)
            await apply_configuration_import(plan, target_engine=engine, file_store=_MemoryFileStore(), request_headers={})
            async with engine.connect() as db:
                self.assertEqual(await db.scalar(text("SELECT app_secret FROM wecom_applications")), "fixture-secret")
                self.assertEqual(await db.scalar(text("SELECT count(*) FROM wecom_user_profiles")), 0)
                settings = json.loads(await db.scalar(text("SELECT settings FROM channel_accounts WHERE id='channel'")))
                self.assertEqual(settings["wecom"]["application_id"], "app")
        finally:
            await engine.dispose()

    async def test_same_dynamic_revision_import_preserves_ids_and_revision(self) -> None:
        backup_path, _, _ = await self._backup(revision="007")
        engine = await self._target_engine(revision="007", baseline=True)
        try:
            plan = await build_configuration_import_plan(
                backup_path,
                target_engine=engine,
            )
            result = await apply_configuration_import(
                plan,
                target_engine=engine,
                file_store=_MemoryFileStore(),
                request_headers={},
            )
            self.assertEqual(result.plan.agent_ids, ("agent-1",))
            async with engine.connect() as connection:
                agent_ids = (
                    await connection.execute(select(Agent.id))
                ).scalars().all()
                provider = (
                    await connection.execute(
                        text("SELECT id, api_key FROM llm_providers")
                    )
                ).one()
                revision = await connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                integrations = (await connection.execute(select(IntegrationSettings.__table__))).mappings().all()
                sessions = await connection.scalar(
                    text("SELECT COUNT(*) FROM chat_sessions")
                )
            self.assertEqual(agent_ids, ["agent-1"])
            self.assertEqual(
                tuple(provider),
                ("0123456789abcdef0123456789abcdef", "plain-api-key"),
            )
            self.assertEqual(revision, "007")
            self.assertEqual(sessions, 0)
            self.assertEqual(integrations[0]["code"], "deeptable-assistant")
            self.assertEqual(integrations[0]["settings"], {"enabled": True, "options": [1, None, "中文"]})
        finally:
            await engine.dispose()

    async def test_nullable_agent_model_survives_configuration_import(self) -> None:
        backup_path, _, _ = await self._backup()
        with sqlite3.connect(backup_path) as connection:
            connection.execute("UPDATE agents SET model_id=NULL")
        engine = await self._target_engine()
        try:
            plan = await build_configuration_import_plan(backup_path, target_engine=engine)
            await apply_configuration_import(plan, target_engine=engine,
                file_store=_MemoryFileStore(), request_headers={})
            async with engine.connect() as connection:
                self.assertEqual(await connection.scalar(text("SELECT COUNT(*) FROM agents WHERE model_id IS NULL")), 1)
        finally:
            await engine.dispose()

    async def test_invalid_integration_rows_cannot_bypass_import_validation(self) -> None:
        backup_path, _, _ = await self._backup()
        engine = await self._target_engine(baseline=True)
        try:
            plan = await build_configuration_import_plan(backup_path, target_engine=engine)
            for value, code in (([], "deeptable-assistant"), ({}, "deeptable-assistant"), ({"a": "x"}, "unknown")):
                rows = copy.deepcopy(plan.rows)
                rows["integration_settings"][0].update(code=code, settings=value)
                with self.assertRaisesRegex(ConfigurationImportError, "integration_settings"):
                    await apply_configuration_import(replace(plan, rows=rows), target_engine=engine, file_store=_MemoryFileStore(), request_headers={})
            async with engine.connect() as connection:
                self.assertEqual(await connection.scalar(select(GlobalSetting.key)), "target-only")
        finally:
            await engine.dispose()

    async def test_different_revision_is_rejected(self) -> None:
        backup_path, _, _ = await self._backup(revision="007")
        engine = await self._target_engine(revision="001")
        try:
            with self.assertRaisesRegex(
                ConfigurationImportConflictError,
                "revision does not match",
            ):
                await build_configuration_import_plan(
                    backup_path,
                    target_engine=engine,
                )
        finally:
            await engine.dispose()

    async def test_import_plan_canonicalizes_legacy_ptc_capability(self) -> None:
        backup_path, _, _ = await self._backup()
        with sqlite3.connect(backup_path) as connection:
            connection.execute(
                "UPDATE agents SET capabilities = ? WHERE id = 'agent-1'",
                ('["builtin:code-mode","mcp:remote"]',),
            )
        engine = await self._target_engine()
        try:
            plan = await build_configuration_import_plan(
                backup_path,
                target_engine=engine,
            )
            self.assertEqual(
                plan.rows["agents"][0]["capabilities"],
                ["builtin:ptc", "mcp:remote"],
            )
        finally:
            await engine.dispose()

    async def test_active_run_is_rejected_before_database_access(self) -> None:
        backup_path, _, _ = await self._backup()
        engine = await self._target_engine()
        try:
            with self.assertRaisesRegex(
                ConfigurationImportConflictError,
                "active runs",
            ):
                await build_configuration_import_plan(
                    backup_path,
                    target_engine=engine,
                    active_run_count=1,
                )
        finally:
            await engine.dispose()

    async def test_corrupt_or_unexpected_backup_schema_is_rejected(self) -> None:
        target = await self._target_engine()
        corrupt = self.root / "corrupt.db"
        corrupt.write_bytes(b"not a sqlite database")
        try:
            with self.assertRaisesRegex(
                ConfigurationImportError,
                "Unable to read configuration backup",
            ):
                await build_configuration_import_plan(
                    corrupt,
                    target_engine=target,
                )

            backup_path, _, _ = await self._backup()
            with sqlite3.connect(backup_path) as connection:
                connection.execute("CREATE TABLE unexpected_table (id INTEGER)")
            with self.assertRaisesRegex(
                ConfigurationImportError,
                "table scope",
            ):
                await build_configuration_import_plan(
                    backup_path,
                    target_engine=target,
                )
        finally:
            await target.dispose()

    async def test_each_configuration_reference_is_checked_without_foreign_keys(self) -> None:
        backup_path, _, _ = await self._backup()
        mutations = (
            ("llm_models", "provider_id"), ("agents", "model_id"),
            ("ui_tool_definitions", "package_code"), ("channel_accounts", "agent_id"),
        )
        target = await self._target_engine()
        try:
            for table, column in mutations:
                with self.subTest(table=table):
                    candidate = self.root / f"orphan-{table}.db"
                    shutil.copy2(backup_path, candidate)
                    with sqlite3.connect(candidate) as db:
                        self.assertEqual(db.execute(f"PRAGMA foreign_key_list({table})").fetchall(), [])
                        db.execute(f"UPDATE {table} SET {column} = 'missing'")
                        self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
                    with self.assertRaisesRegex(ConfigurationImportError, table + r"\." + column):
                        await build_configuration_import_plan(candidate, target_engine=target)
        finally:
            await target.dispose()

    async def test_import_revalidates_channel_agent_before_commit_and_rolls_back(self) -> None:
        backup_path, _, _ = await self._backup()
        target = await self._target_engine(baseline=True)
        try:
            plan = await build_configuration_import_plan(backup_path, target_engine=target)
            rows = {name: [dict(row) for row in data] for name, data in plan.rows.items()}
            rows["channel_accounts"][0]["agent_id"] = "missing"
            with self.assertRaisesRegex(ConfigurationImportError, r"channel_accounts.agent_id"):
                await apply_configuration_import(replace(plan, rows=rows), target_engine=target,
                                                  file_store=_MemoryFileStore(), request_headers={})
            async with target.connect() as db:
                self.assertEqual(await db.scalar(text("SELECT COUNT(*) FROM agents")), 0)
                self.assertEqual(await db.scalar(text("SELECT COUNT(*) FROM channel_accounts")), 0)
                self.assertEqual(await db.scalar(text("SELECT COUNT(*) FROM global_settings WHERE key='target-only'")), 1)
        finally:
            await target.dispose()

    async def test_orphan_reference_and_missing_skill_bundle_are_rejected(self) -> None:
        target = await self._target_engine()
        try:
            backup_path, _, _ = await self._backup()
            with sqlite3.connect(backup_path) as connection:
                connection.execute(
                    "UPDATE agents SET model_id = 'missing-model'"
                )
            with self.assertRaisesRegex(
                ConfigurationImportError,
                "invalid logical references",
            ):
                await build_configuration_import_plan(
                    backup_path,
                    target_engine=target,
                )

            skill_backup, _, _ = await self._backup(include_skill=True)
            with self.assertRaisesRegex(
                ConfigurationImportError,
                "provide the matching Skills export bundle",
            ):
                await build_configuration_import_plan(
                    skill_backup,
                    target_engine=target,
                )
        finally:
            await target.dispose()

    async def test_target_with_conversation_data_is_rejected(self) -> None:
        backup_path, _, _ = await self._backup()
        engine = await self._target_engine()
        store = _MemoryFileStore()
        try:
            plan = await build_configuration_import_plan(
                backup_path,
                target_engine=engine,
            )
            await apply_configuration_import(
                plan,
                target_engine=engine,
                file_store=store,
                request_headers={},
            )
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO chat_sessions "
                        "(session_id, agent_id, user_id, next_sequence) "
                        "VALUES ('session-after-import', 'agent-1', 'user-1', 1)"
                    )
                )
            with self.assertRaisesRegex(
                ConfigurationImportConflictError,
                "contains conversation data",
            ):
                await build_configuration_import_plan(
                    backup_path,
                    target_engine=engine,
                )
        finally:
            await engine.dispose()

    async def test_database_failure_rolls_back_existing_configuration(self) -> None:
        backup_path, _, _ = await self._backup()
        engine = await self._target_engine(baseline=True)
        try:
            plan = await build_configuration_import_plan(
                backup_path,
                target_engine=engine,
            )
            bad_rows = copy.deepcopy(plan.rows)
            bad_rows["agents"][0]["model_id"] = "missing-model"
            invalid_plan = replace(plan, rows=bad_rows)
            with self.assertRaises(ConfigurationImportError):
                await apply_configuration_import(
                    invalid_plan,
                    target_engine=engine,
                    file_store=_MemoryFileStore(),
                    request_headers={},
                )
            async with engine.connect() as connection:
                rows = (
                    await connection.execute(
                        select(GlobalSetting.key, GlobalSetting.value)
                    )
                ).all()
            self.assertEqual(rows, [("target-only", {"keep": True})])
        finally:
            await engine.dispose()

    async def test_skill_bundle_upload_rewrites_only_file_location(self) -> None:
        backup_path, bundle_path, raw_skill = await self._backup(include_skill=True)
        assert bundle_path is not None
        assert raw_skill is not None
        engine = await self._target_engine()
        store = _MemoryFileStore()
        try:
            plan = await build_configuration_import_plan(
                backup_path,
                target_engine=engine,
                skills_bundle_path=bundle_path,
            )
            result = await apply_configuration_import(
                plan,
                target_engine=engine,
                file_store=store,
                request_headers={"user": "admin"},
            )
            self.assertEqual(result.skill_files_uploaded, 1)
            self.assertEqual(store.blobs, {"target-file-1": raw_skill})
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        select(
                            SkillPackage.code,
                            SkillPackage.platform_file_id,
                            SkillPackage.content_hash,
                        )
                    )
                ).one()
                agent = (
                    await connection.execute(select(Agent.id, Agent.skills))
                ).one()
            self.assertEqual(row.code, "uploaded-skill")
            self.assertEqual(row.platform_file_id, "target-file-1")
            self.assertEqual(row.content_hash, self._skill_hash(backup_path))
            self.assertEqual(agent.id, "agent-1")
            self.assertEqual(agent.skills, ["uploaded-skill"])
        finally:
            await engine.dispose()

    async def test_database_failure_compensates_uploaded_skill(self) -> None:
        backup_path, bundle_path, _ = await self._backup(include_skill=True)
        assert bundle_path is not None
        engine = await self._target_engine(baseline=True)
        store = _MemoryFileStore()
        try:
            plan = await build_configuration_import_plan(
                backup_path,
                target_engine=engine,
                skills_bundle_path=bundle_path,
            )
            bad_rows = copy.deepcopy(plan.rows)
            bad_rows["agents"][0]["model_id"] = "missing-model"
            with self.assertRaises(ConfigurationImportError):
                await apply_configuration_import(
                    replace(plan, rows=bad_rows),
                    target_engine=engine,
                    file_store=store,
                    request_headers={},
                )
            self.assertEqual(store.blobs, {})
            self.assertEqual(store.deleted, ["target-file-1"])
            async with engine.connect() as connection:
                settings = (
                    await connection.execute(select(GlobalSetting.key))
                ).scalars().all()
            self.assertEqual(settings, ["target-only"])
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
