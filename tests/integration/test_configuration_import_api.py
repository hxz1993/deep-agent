from __future__ import annotations

import sqlite3
import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.files.storage import StoredFile
from maintenance.config_backup import export_backup
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.unit import test_config_backup as config_backup_tests
from deep_agent.schemas.channel import ChannelAccountCreate
from deep_agent.services.wecom_client import WecomError
from tests.helpers.sqlite_stores import seed_default_agent


class _RejectingFileStore:
    async def upload(self, **kwargs: object) -> StoredFile:
        del kwargs
        raise AssertionError("A Skill-free configuration import must not upload files")

    async def download(
        self,
        file_id: str,
        request_headers: Mapping[str, str],
    ) -> bytes:
        del file_id, request_headers
        raise AssertionError("A Skill-free configuration import must not download files")

    async def delete(
        self,
        file_id: str,
        request_headers: Mapping[str, str],
    ) -> None:
        del file_id, request_headers
        raise AssertionError("A Skill-free configuration import must not delete files")

    async def register_download(self, **kwargs: object) -> str:
        del kwargs
        raise AssertionError("Configuration backup must not enter the file store")


class ConfigurationImportApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self._tmp.name)
        source = root / "source.db"
        await config_backup_tests.ConfigBackupTests()._create_source(source, revision="008")
        with sqlite3.connect(source) as connection:
            connection.execute("UPDATE agents SET skills='[]'")
            connection.execute("DELETE FROM skill_packages")
        backup = await export_backup(
            f"sqlite+aiosqlite:///{source.as_posix()}",
            environment="test",
            output_root=root / "backups",
            exported_at=datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc),
            git_commit="api-test",
        )
        self.backup_path = backup.path

        self.stores, self.engine, session_factory = await make_sqlite_stores()
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            await connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES ('008')")
            )
        self.app = FastAPI()
        self.app.include_router(api_router)
        allow_console_admin(self.app)
        init_context(
            AppContext.for_testing(
                stores=self.stores,
                db_engine=self.engine,
                db_session_factory=session_factory,
                file_store=_RejectingFileStore(),
            )
        )
        self.transport = ASGITransport(app=self.app, raise_app_exceptions=False)

    async def asyncTearDown(self) -> None:
        await get_context().event_persister.aclose()
        reset_context()
        await self.engine.dispose()
        self._tmp.cleanup()

    async def _post(
        self,
        *,
        apply: bool,
        confirm: bool,
    ):
        async with AsyncClient(
            transport=self.transport,
            base_url="http://test",
        ) as client:
            return await client.post(
                "/api/v1/configuration/import",
                data={
                    "apply": str(apply).lower(),
                    "confirm": str(confirm).lower(),
                },
                files={
                    "database_file": (
                        self.backup_path.name,
                        self.backup_path.read_bytes(),
                        "application/vnd.sqlite3",
                    )
                },
            )

    async def test_preflight_then_apply_preserves_agent_id(self) -> None:
        validated = await self._post(apply=False, confirm=False)
        self.assertEqual(validated.status_code, 200, validated.text)
        self.assertEqual(validated.json()["status"], "validated")
        self.assertEqual(validated.json()["agent_ids"], ["agent-1"])

        imported = await self._post(apply=True, confirm=True)
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["status"], "imported")
        self.assertTrue(imported.json()["database_primary_keys_preserved"])
        self.assertEqual(imported.json()["session_rows_imported"], 0)
        self.assertEqual(
            [row.id for row in await self.stores.agent.list_all()],
            ["agent-1"],
        )

    async def test_apply_requires_explicit_confirmation(self) -> None:
        response = await self._post(apply=True, confirm=False)
        self.assertEqual(response.status_code, 422)
        self.assertIn("confirm=true", response.json()["detail"])

    async def test_import_closes_old_identity_jobs_and_tokens_before_reusing_application_revision(self):
        from deep_agent.api.v1 import configuration_import as import_api

        ctx = get_context()
        agent_id = await seed_default_agent(self.stores)
        app = await self.stores.wecom.create_application(name="旧应用", corp_id="test-corp",
            wecom_agent_id="identity-agent", app_secret="old-test-secret")
        account = await self.stores.channel.create_account(ChannelAccountCreate(name="旧机器人",
            bot_id="old-bot", secret="test-only", agent_id=agent_id, session_user_id="test-owner", enabled=True,
            settings={"wecom": {"user_details_enabled": True, "application_id": app.id}}))
        with sqlite3.connect(self.backup_path) as db:
            db.execute("""INSERT INTO wecom_applications
                (id,name,corp_id,wecom_agent_id,app_secret,settings,revision)
                VALUES (?, '恢复后的应用', 'test-corp', 'identity-agent', 'new-test-secret', '{}', ?)""",
                (app.id, app.revision))
        runtime = ctx.get_wecom_profiles()
        self.addAsyncCleanup(runtime.close)
        member_started, member_cancelled = asyncio.Event(), asyncio.Event()

        async def old_wire(method, path, **kwargs):
            if path == "/gettoken":
                return {"access_token": "old-test-token", "expires_in": 7200}
            if path == "/batch/openuserid_to_userid":
                return {"errcode": 0, "userid_list": [{"open_userid": "opaque", "userid": "Member"}]}
            member_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                member_cancelled.set()

        runtime.client._request = AsyncMock(side_effect=old_wire)
        old_query = asyncio.create_task(runtime.resolve(account, "opaque", peer_type="group"))
        async def close_query():
            old_query.cancel()
            await asyncio.gather(old_query, return_exceptions=True)
        self.addAsyncCleanup(close_query)
        await asyncio.wait_for(member_started.wait(), 2)
        apply_original = import_api.apply_configuration_import

        async def apply_with_runtime_check(*args, **kwargs):
            self.assertTrue(member_cancelled.is_set())
            self.assertIs(ctx.get_wecom_profiles(), runtime)
            self.assertEqual((await runtime.resolve(account, "opaque"))["status"], "disabled")
            with self.assertRaises(WecomError):
                await runtime.client.token(app)
            return await apply_original(*args, **kwargs)

        with patch.object(import_api, "apply_configuration_import", side_effect=apply_with_runtime_check):
            response = await self._post(apply=True, confirm=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await old_query)["status"], "disabled")
        self.assertIsNone(ctx.wecom_profiles)
        self.assertEqual((await self.stores.wecom.cache_stats(app.id))["total"], 0)
        restored = await self.stores.wecom.get_application(app.id)
        self.assertEqual((restored.revision, restored.app_secret), (app.revision, "new-test-secret"))
        new_runtime = ctx.get_wecom_profiles()
        self.addAsyncCleanup(new_runtime.close)
        self.assertIsNot(new_runtime, runtime)
        new_runtime.client._request = AsyncMock(return_value={"access_token": "new-test-token", "expires_in": 7200})
        self.assertEqual(await new_runtime.client.token(restored), "new-test-token")
        self.assertEqual(new_runtime.client._request.await_args.kwargs["params"]["corpsecret"], "new-test-secret")


if __name__ == "__main__":
    unittest.main()
