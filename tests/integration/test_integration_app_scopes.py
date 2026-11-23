"""Application configuration through ASGI, SQLite, local files and the real Harness."""

from __future__ import annotations

import asyncio
import io
import json
import tempfile
import unittest
import zipfile
from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import delete, select, text, update

from maintenance.app_skill_ownership import convert_app_skill_ownership
from maintenance.config_backup import export_backup

from deep_agent.api.v1 import api_router
from deep_agent.api.v1.skills import _upload_file
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.db.models import IntegrationScopedSettings, SkillPackage
from deep_agent.files.storage import LocalFileStore
from deep_agent.integrations.app_scope import INTEGRATION, app_skill_scope, app_skill_storage_key
from deep_agent.integrations.app_skill_data import AppSkillDataError
from deep_agent.schemas.agent import AgentUpdate
from deep_agent.security import ConsoleAdminAuthorizer
from deep_agent.services.integration_app_access import IntegrationAppAccessError, ManagedApp
from deep_agent.services.scope_names import PlatformScopeDirectoryFetcher, ScopeDirectory
from deep_agent.skills.runtime import SkillRuntimeLibrary
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
from tests.unit.test_skill_packages import skill_zip
from tests.unit.test_skill_script_execution import RecordingBackend

PATH = "/api/v1/integrations/deeptable-assistant/app-configs"
HEADERS = {"user": "enterprise-user", "authorization": "Bearer fixture-only"}
APP_A = ManagedApp("budget001", "budget", "tenant-1", "预算", "企业", "预算空间")
APP_B = ManagedApp("budget002", "budget", "tenant-1", "经营", "企业", "预算空间")
APP_C = ManagedApp("other001", "other", "tenant-2", "未授权", "另一企业")


class IntegrationAppScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory(prefix="TMP_to_delete_app_scopes_")
        self.addCleanup(temp.cleanup)
        root = self.root = Path(temp.name)
        self.stores, self.engine, self.factory = await make_sqlite_stores(f"sqlite+aiosqlite:///{root / 'test.db'}")
        self.agent = await seed_default_agent(self.stores, name="spreadsheet-test")
        self.other_agent = await seed_default_agent(self.stores, name="other-test")
        await self.stores.integration_settings.save(INTEGRATION, {"agent_id": self.agent})
        file_store = LocalFileStore(root / "files")
        await file_store.initialize()
        self.backend = RecordingBackend()
        context = AppContext.for_testing(
            stores=self.stores, db_engine=self.engine, db_session_factory=self.factory,
            console_admin_authorizer=ConsoleAdminAuthorizer(AsyncMock(return_value=True)),
            file_store=file_store, skill_runtime=SkillRuntimeLibrary(root / "runtime", file_store=file_store),
            sandbox_backend=self.backend,
        )
        init_context(context)
        self.addCleanup(reset_context)
        self.addAsyncCleanup(context.aclose)
        app = FastAPI()
        app.include_router(api_router)
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS)
        self.addAsyncCleanup(self.client.aclose)

        self.tenant_admin = {"tenant-1": True, "tenant-2": False}
        self.space_admin = set()
        self.app_roles = {}
        self.apps = {
            item.app_id: item for item in (
                APP_A, APP_B, APP_C, ManagedApp("other002", "other", "tenant-2", "普通应用", "另一企业"),
            )
        }

        async def platform(_service, path, *, space=None, app=None, params=None):
            if path == "api/enterprise/list":
                return [
                    {"enterpriseCode": "tenant-1", "id": "db-key-1", "enterpriseName": "企业"},
                    {"enterpriseCode": "tenant-2", "id": "db-key-2", "enterpriseName": "另一企业"},
                ]
            if path == "user-permission":
                self.assertIsNone(space)
                self.assertIsNone(app)
                tenant = {"db-key-1": "tenant-1", "db-key-2": "tenant-2"}[params["enterpriseId"]]
                return {
                    "permission": self.tenant_admin[tenant],
                    "spaceIds": sorted({item.space_id for item in self.apps.values()
                                        if item.tenant_id == tenant and item.space_id in self.space_admin}),
                    "appIds": [item.app_id for item in self.apps.values()
                               if item.tenant_id == tenant and self.app_roles.get(item.app_id) in {"admin", "developer"}],
                }
            if path == "user/get-user-detail":
                return {"userId": HEADERS["user"], "enterpriseDTOList": [
                    {"enterpriseCode": tenant, "roleList": [{"roleCode": "DEFAULT_ADMIN"}]}
                    for tenant, is_admin in self.tenant_admin.items() if is_admin
                ]}
            if path == "space/get-tenant-code":
                return next((item.tenant_id for item in self.apps.values() if item.space_id == space), None)
            if path == "api/space/app/query-app-info-list/by-space-id":
                return [{"id": item.app_id, "appType": "DEEPFOS", "appName": item.app_name}
                        for item in self.apps.values() if item.space_id == space]
            if path == "user/permission":
                item = self.apps[app]
                self.assertEqual(item.space_id, space)
                return {
                    "appAdmin": self.tenant_admin[item.tenant_id] or space in self.space_admin
                    or self.app_roles.get(app) == "admin",
                    "developerRole": self.app_roles.get(app) == "developer",
                }
            self.fail(f"unexpected platform request: {path}")

        self.platform = self.enterContext(patch(
            "deep_agent.services.integration_app_access.IntegrationAppDirectory._get", side_effect=platform,
        ))
        catalog = ScopeDirectory(
            apps={item.app_id: item.app_name for item in self.apps.values()},
            spaces={item.space_id: item.space_name for item in self.apps.values()},
            app_scopes={item.app_id: (item.space_id, item.tenant_id) for item in self.apps.values()},
        )
        self.directory = AsyncMock(return_value=catalog)
        self.enterContext(patch(
            "deep_agent.services.integration_app_access.PlatformScopeDirectoryFetcher", return_value=self.directory,
        ))
        self.enterContext(patch(
            "deep_agent.api.v1.integration_apps.resolve_scope_directory", return_value=catalog,
        ))

    async def _request(self, method, url, expected, **kwargs):
        response = await self.client.request(method, url, **kwargs)
        self.assertEqual(response.status_code, expected, response.text[:1400])
        return response

    async def _save(self, app=APP_A):
        return (await self._request("PUT", f"{PATH}/{app.app_id}", 200, json={
            "prompt_append": "预算专用口径：按部门汇总。fixture-app-rule",
        })).json()

    async def _install(self, path, name="scoped-report", *, scripts=False):
        raw = skill_zip(name=name, scripts=scripts)
        await self._request("POST", path, 201, files={"file": (name + ".zip", raw, "application/zip")})
        return raw

    @property
    def _database_url(self):
        return f"sqlite+aiosqlite:///{self.root / 'test.db'}"

    async def _mark_revision(self):
        async with self.engine.begin() as db:
            await db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
            await db.execute(text("INSERT INTO alembic_version VALUES ('016')"))

    async def _install_with_old_key(self, app, name, *, hashed):
        config = await self.stores.integration_app.get(app.app_id, app.space_id)
        key = f"app--{config.id}--{sha256(name.encode()).hexdigest()}" if hashed else f"app-skill--{config.id}--{name}"
        with patch("deep_agent.db.stores.skill_package.base.app_skill_storage_key", return_value=key):
            raw = await self._install(f"{PATH}/{app.app_id}/skills", name=name)
        return key, raw

    async def _mark_legacy(self, app):
        config = await self.stores.integration_app.get(app.app_id, app.space_id)
        async with self.engine.begin() as db:
            await db.execute(update(IntegrationScopedSettings).where(
                IntegrationScopedSettings.id == config.id,
            ).values(settings={"prompt_append": config.settings["prompt_append"]}))
            await db.execute(update(SkillPackage).where(
                SkillPackage.scope_ref == app_skill_scope(app.app_id),
            ).values(scope_ref=f"integration-scope:{config.id}"))
        return config

    async def test_single_app_developer_can_list_save_and_manage_only_that_app(self):
        self.app_roles[APP_C.app_id] = "developer"
        self.app_roles["other002"] = "reader"
        self.assertEqual((await self._request("GET", PATH, 200)).json(), [])
        self.platform.reset_mock()
        candidates = (await self._request("GET", f"{PATH}/available", 200)).json()
        self.assertEqual({row["app_id"] for row in candidates}, {APP_A.app_id, APP_B.app_id, APP_C.app_id})
        self.assertEqual([call.args[1] for call in self.platform.await_args_list],
                         ["api/enterprise/list", "user-permission", "user-permission"])
        self.directory.assert_awaited_once()
        self.assertEqual(self.directory.await_args.kwargs, {"timeout_seconds": 5})
        route = f"{PATH}/{APP_C.app_id}"
        self.assertIsNone((await self._request("GET", route, 200)).json()["config_id"])
        saved = await self._save(APP_C)
        saved_rows = (await self._request("GET", PATH, 200)).json()
        self.assertEqual(self.directory.await_args.kwargs, {"tenant_code": APP_C.tenant_id})
        self.assertEqual([(row["app_id"], row["tenant_id"]) for row in saved_rows], [(APP_C.app_id, "tenant-2")])
        await self._request("GET", f"{PATH}/other002", 404)
        await self._request("PUT", f"{PATH}/other002", 404, json={"prompt_append": "denied"})
        raw = await self._install(f"{route}/skills", name="developer-report")
        self.assertEqual((await self._request("GET", f"{route}/skills/developer-report/package", 200)).content, raw)
        await self._request("GET", "/api/v1/configuration/skills/export", 404)

        del self.app_roles[APP_C.app_id]
        self.assertEqual((await self._request("GET", PATH, 200)).json(), [])
        await self._request("GET", route, 404)
        await self._request("PUT", route, 404, json={"config_id": saved["config_id"], "prompt_append": "denied"})
        await self._request("GET", f"{route}/skills/developer-report/package", 404)
        await self._request("DELETE", f"{route}?config_id={saved['config_id']}", 404)
        self.assertIsNotNone(await self.stores.integration_app.get(APP_C.app_id, APP_C.space_id))

    async def test_space_admin_and_app_admin_have_distinct_access(self):
        self.app_roles[APP_C.app_id] = "admin"
        route = f"{PATH}/{APP_C.app_id}"
        await self._request("GET", route, 200)
        await self._request("GET", f"{PATH}/other002", 404)
        del self.app_roles[APP_C.app_id]
        self.space_admin.add(APP_C.space_id)
        candidates = (await self._request("GET", f"{PATH}/available", 200)).json()
        self.assertEqual({row["app_id"] for row in candidates if row["tenant_id"] == APP_C.tenant_id},
                         {APP_C.app_id, "other002"})
        await self._save(APP_C)
        await self._request("GET", f"{PATH}/other002", 200)

    async def test_batch_permission_failure_does_not_disguise_itself_as_empty_list(self):
        await self._save()
        with patch("deep_agent.services.integration_app_access.PlatformScopeDirectoryFetcher", return_value=AsyncMock(
            side_effect=TimeoutError,
        )):
            await self._request("GET", PATH, 503)
            await self._request("GET", f"{PATH}/available", 503)
        real_platform = self.platform.side_effect

        async def malformed(*args, **kwargs):
            if args[1] == "user-permission":
                return {"permission": "false", "spaceIds": [], "appIds": []}
            return await real_platform(*args, **kwargs)

        self.platform.side_effect = malformed
        await self._request("GET", PATH, 503)
        await self._request("GET", f"{PATH}/available", 503)

    async def test_same_zip_independent_lifecycle_and_backup_bundle(self):
        code = "scoped-report"
        base_a, base_b = f"{PATH}/{APP_A.app_id}", f"{PATH}/{APP_B.app_id}"
        original = await self._install("/api/v1/skills")
        general = await self.stores.skill_package.get_by_code(code)
        self.assertEqual(general.scope_ref, "general")
        async with self.engine.begin() as db:
            await db.execute(update(SkillPackage).where(SkillPackage.code == code).values(scope_ref=None))
        for base in (base_a, base_b):
            await self._request("POST", f"{base}/skills", 201, files={"file": ("same.zip", original)})
        await self._request("POST", f"{base_a}/skills", 409, files={"file": ("same.zip", original)})
        configs = (await self._request("GET", PATH, 200)).json()
        self.assertEqual([(row["app_id"], row["skill_count"]) for row in configs],
                         [(APP_A.app_id, 1), (APP_B.app_id, 1)])
        detail = (await self._request("GET", base_a, 200)).json()
        self.assertEqual(detail["skill_count"], 1)
        key = app_skill_storage_key(APP_A.app_id, code)
        self.assertTrue((self.root / "runtime" / key / "SKILL.md").is_file())
        app_row = await self.stores.skill_package.get_by_code(key, scope_ref=app_skill_scope(APP_A.app_id))
        self.assertEqual(app_row.scope_ref, "app:budget001")
        updated = (await self._request("PUT", base_a, 200, json={
            "config_id": detail["config_id"], "prompt_append": "按部门汇总",
        })).json()
        self.assertEqual(updated["skill_count"], 1)
        self.assertEqual((await self._request("GET", f"{base_a}/skills/{code}/package", 200)).content, original)
        await self._request("GET", f"{base_a}/skills/{code}/resources/references/guide.md", 200)
        await self._request("GET", f"{PATH}/{APP_C.app_id}/skills/{code}/package", 404)

        replacement = skill_zip(name=code, scripts=True)
        await self._request("PUT", f"{base_a}/skills/{code}/package", 200, files={"file": ("new.zip", replacement)})
        await self._request("PATCH", f"{base_a}/skills/{code}", 200, json={"label": "仅预算"})
        await self._request("POST", f"{base_a}/skills/{code}/reload", 200)
        self.assertEqual((await self._request("GET", f"{base_a}/skills/{code}/package", 200)).content, replacement)
        self.assertEqual((await self._request("GET", f"{base_b}/skills/{code}/package", 200)).content, original)

        for url, expected in (("/api/v1/skills/export", 1), ("/api/v1/configuration/skills/export", 3)):
            exported = await self._request("GET", url, 200)
            with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(len(manifest["skills"]), expected)
                if expected == 3:
                    scoped = [row for row in manifest["skills"] if row.get("scope_ref")]
                    self.assertEqual(len({row["scope_ref"] for row in scoped}), 2)
                    await self._request("GET", f"/api/v1/skills/{scoped[0]['code']}", 404)

        await self._request("DELETE", f"{base_a}?config_id={configs[0]['config_id']}", 200)
        await self._request("GET", f"{base_a}/skills/{code}", 404)
        await self._request("GET", f"{base_b}/skills/{code}", 200)
        await self._request("GET", f"/api/v1/skills/{code}", 200)

    async def test_legacy_scope_keeps_its_cache_and_cannot_be_installed_twice(self):
        code = "scoped-report"
        base = f"{PATH}/{APP_A.app_id}/skills"
        await self._save()
        key, original = await self._install_with_old_key(APP_A, code, hashed=True)
        readable_key, _ = await self._install_with_old_key(APP_A, "old-readable", hashed=False)
        await self._mark_legacy(APP_A)
        await self._mark_revision()
        with self.assertRaisesRegex(RuntimeError, "App Skill data needs conversion"):
            await self.stores.integration_app.require_app_skill_conversion()
        self.assertEqual(await convert_app_skill_ownership(self._database_url), (2, 1))
        with self.assertRaises(RuntimeError):
            await self.stores.integration_app.require_app_skill_conversion()
        self.assertEqual(await convert_app_skill_ownership(self._database_url, apply=True), (2, 1))
        self.assertEqual(await convert_app_skill_ownership(self._database_url, apply=True), (0, 0))
        await self.stores.integration_app.require_app_skill_conversion()
        self.assertTrue((self.root / "runtime" / readable_key / "SKILL.md").is_file())
        cached = self.root / "runtime" / key / "SKILL.md"
        self.assertTrue(cached.is_file())
        self.assertEqual((await self._request("GET", f"{base}/{code}/package", 200)).content, original)
        await self._request("GET", f"{base}/{code}/resources/references/guide.md", 200)
        await self._request("POST", base, 409, files={"file": ("duplicate.zip", original)})

        # A legal package code can equal the old key's hash suffix.
        hash_name = sha256(code.encode()).hexdigest()
        hash_named = await self._install(base, name=hash_name)
        self.assertEqual((await self._request("GET", f"{base}/{hash_name}/package", 200)).content, hash_named)
        unicode_named = await self._install(base, name="预算报告")
        self.assertEqual((await self._request("GET", f"{base}/预算报告/package", 200)).content, unicode_named)

        replacement = skill_zip(name=code, scripts=True)
        await self._request("PUT", f"{base}/{code}/package", 200, files={"file": ("updated.zip", replacement)})
        await self._request("PATCH", f"{base}/{code}", 200, json={"label": "旧包可编辑"})
        await self._request("POST", f"{base}/{code}/reload", 200)
        self.assertEqual((await self._request("GET", f"{base}/{code}/package", 200)).content, replacement)
        self.assertTrue(cached.is_file())
        await self._request("DELETE", f"{base}/{code}", 200)
        self.assertFalse(cached.exists())
        await self._request("GET", f"{base}/{code}", 404)
        await self._request("GET", f"{base}/{hash_name}", 200)

    async def test_run_reads_current_app_configuration_without_a_directory_lookup(self):
        await self._save()
        await self._install(f"{PATH}/{APP_A.app_id}/skills", scripts=True)
        await self._install(f"{PATH}/{APP_A.app_id}/skills", name="hidden-report")
        config = await self.stores.integration_app.get(APP_A.app_id, APP_A.space_id)
        async with self.engine.begin() as db:
            await db.execute(update(IntegrationScopedSettings).where(
                IntegrationScopedSettings.id == config.id,
            ).values(settings={**config.settings, "skills": ["scoped-report"]}))
        recorded = []

        def run_model(messages, info):
            tool_names = [tool.name for tool in info.function_tools]
            if "get_current_time" in tool_names:
                recorded.append((info.instructions or "", tool_names))
            return ModelResponse(parts=[TextPart("差额 20，差异率 20%。")])

        directory = self.enterContext(patch.object(PlatformScopeDirectoryFetcher, "__call__",
            side_effect=AssertionError("Chat must not read the platform app directory")))
        self.client.headers.pop("authorization")
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(run_model)), mock_request_disconnect():
            async def send(*, app=None, agent=self.agent, session=None, extra_headers=None):
                headers = {"x-deep-agent-app": app} if app else {}
                headers.update(extra_headers or {})
                body = {"agent_id": agent, "message": "实际120，预算100，给出差额。"}
                if session:
                    body["session_id"] = session
                return (await self._request("POST", "/api/v1/chat", 200, headers=headers, json=body)).json()

            legacy = await send()
            scoped = await send(app=APP_A.app_id)
            await send(app=APP_B.app_id)
            await send(app=APP_A.app_id, agent=self.other_agent)
            await send(app=APP_A.app_id, extra_headers={
                "x-deepfos-key": "fixture-key", "app": APP_B.app_id, "space": APP_B.space_id,
            })
            await send(app=APP_A.app_id, session=legacy["session_id"])
            await send(session=scoped["session_id"])
            await self._request("POST", "/api/v1/chat", 409, headers={"x-deep-agent-app": APP_B.app_id}, json={
                "agent_id": self.agent, "session_id": scoped["session_id"], "message": "切换应用",
            })
            async with self.engine.begin() as db:
                await db.execute(update(IntegrationScopedSettings).where(
                    IntegrationScopedSettings.id == config.id,
                ).values(settings={**config.settings, "skills": []}))
            await send(session=scoped["session_id"])
        directory.assert_not_called()
        self.assertNotIn("app:budget001:scoped-report", recorded[-1][0])
        self.assertTrue(all("hidden-report" not in instructions for instructions, _ in recorded))
        self.assertEqual([("fixture-app-rule" in value, "app_id=" in value) for value, _ in recorded],
                         [(False, False), (True, True), (False, True), (False, True), (False, False), (False, False), (True, True), (True, True)])
        for index in (1, 6):
            instructions, _ = recorded[index]
            self.assertIn("fixture-app-rule", instructions)
            self.assertIn("加载使用 id=app--budget001--scoped-report", instructions)
            self.assertIn("资源和脚本使用 skill_code=app:budget001:scoped-report", instructions)
            self.assertIn("app_id=budget001，space_id=budget", instructions)
            self.assertNotIn("fixture-only", instructions)
        for index in (0, 2, 3, 4, 5):
            self.assertNotIn("fixture-app-rule", recorded[index][0])
            self.assertNotIn("app:budget001:scoped-report", recorded[index][0])

    async def test_scoped_stream_resumes_ui_tool_with_both_same_name_skills(self):
        await self._install("/api/v1/skills")
        await self.stores.agent.update_unique(self.agent, AgentUpdate(
            skills=["scoped-report"], capabilities=["ui:builtin"],
        ))
        await self._save()
        await self._install_with_old_key(APP_A, "scoped-report", hashed=True)
        self.client.headers.pop("authorization")
        seen = []

        async def respond(messages, info):
            instructions = info.instructions or ""
            seen.append(instructions)
            self.assertIn("fixture-app-rule", instructions)
            self.assertIn("app:budget001:scoped-report", instructions)
            self.assertIn("scoped-report:", instructions)
            if any(isinstance(part, ToolReturnPart) and part.tool_call_id == "app-confirm"
                   for message in messages for part in message.parts):
                yield "确认后继续使用预算口径"
            else:
                yield {0: DeltaToolCall("builtin__confirm", '{"message":"确认预算口径？"}', tool_call_id="app-confirm")}

        endpoint = "/api/v1/chat/stream"
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(stream_function=respond)), mock_request_disconnect():
            started = await self._request("POST", endpoint, 200, headers={"x-deep-agent-app": APP_A.app_id},
                                          json={"agent_id": self.agent, "message": "继续前先确认", "session_title": "验收"})
            events = parse_sse_events(started.text)
            pending = next(event for event in events if event["type"] == "final")
            self.assertEqual(pending["status"], "pending")
            resume = {
                "agent_id": self.agent,
                "session_id": events[0]["session_id"],
                "deferred_tool_results": [{"tool_call_id": "app-confirm", "result": {"confirmed": True}}],
            }
            await self._request("POST", endpoint, 409, headers={"x-deep-agent-app": APP_B.app_id}, json=resume)
            self.assertEqual(len(seen), 1)
            resumed = await self._request("POST", endpoint, 200, json=resume)
        final = next(event for event in parse_sse_events(resumed.text) if event["type"] == "final")
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["output"], "确认后继续使用预算口径")
        self.assertEqual(len(seen), 2)

    async def test_scoped_skill_scripts_share_native_and_ptc_authorization(self):
        await self._save()
        await self._install(f"{PATH}/{APP_A.app_id}/skills", scripts=True)
        alias = "app:budget001:scoped-report"

        def respond(messages, info):
            names = {tool.name for tool in info.function_tools}
            if "get_current_time" not in names:
                return ModelResponse(parts=[TextPart("验收")])
            self.assertIn("execute_skill_script", names)
            by_id = {part.tool_call_id: part for message in messages for part in message.parts
                     if isinstance(part, ToolReturnPart)}
            if "native" not in by_id:
                return ModelResponse(parts=[ToolCallPart("execute_skill_script", {
                    "skill_code": alias, "script_path": "scripts/run.py",
                }, "native")])
            self.assertEqual(by_id["native"].outcome, "success")
            if "monty" not in by_id:
                code = (
                    f"value = await execute_skill_script(skill_code={alias!r}, script_path='scripts/run.py')\n"
                    "assert value['status'] == 'ok'\nvalue['result']['valid']"
                )
                return ModelResponse(parts=[ToolCallPart("run_code", {"code": code}, "monty")])
            self.assertEqual(by_id["monty"].outcome, "success")
            self.assertIn("true", str(by_id["monty"].content).lower())
            return ModelResponse(parts=[TextPart("both paths completed")])

        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(respond)), mock_request_disconnect():
            response = await self._request(
                "POST", "/api/v1/chat", 200, headers={"x-deep-agent-app": APP_A.app_id},
                json={"agent_id": self.agent, "message": "执行验收脚本", "session_title": "验收",
                      "capabilities": ["builtin:sandbox", "builtin:ptc"]},
            )
            self.assertEqual(response.json()["output"], "both paths completed")
        self.assertEqual([item.script_entry for item in self.backend.requests], ["scripts/run.py"] * 2)
        self.assertTrue(all(not item.context_env for item in self.backend.requests))

    async def test_scoped_configuration_backup_restore_checks_app_authorization(self):
        await self._save()
        _, legacy = await self._install_with_old_key(APP_A, "old-report", hashed=True)
        await self._install_with_old_key(APP_A, "scoped-report", hashed=False)
        original = await self._mark_legacy(APP_A)
        await self._mark_revision()
        for attempt in ("legacy", "converted"):
            if attempt == "converted":
                await self._install(f"{PATH}/{APP_A.app_id}/skills", name="app-owned")
                original = await self.stores.integration_app.get(APP_A.app_id, APP_A.space_id)
            backup = await export_backup(
                self._database_url, environment=attempt, output_root=self.root / "backup", git_commit="test",
            )
            bundle = await self._request("GET", "/api/v1/configuration/skills/export", 200)
            uploads = {"database_file": ("backup.db", backup.path.read_bytes()),
                       "skills_bundle": ("skills.zip", bundle.content)}
            await self._request("POST", "/api/v1/configuration/import", 200, files=uploads)
            with patch("deep_agent.services.integration_app_access.IntegrationAppDirectory.verify",
                       side_effect=IntegrationAppAccessError(404, "应用不可用")):
                await self._request("POST", "/api/v1/configuration/import", 404, files=uploads,
                                    data={"apply": "true", "confirm": "true"})
            await self._request("POST", "/api/v1/configuration/import", 200, files=uploads,
                                data={"apply": "true", "confirm": "true"})
            imported = await self.stores.integration_app.get(APP_A.app_id, APP_A.space_id)
            self.assertEqual(imported.id, original.id)
            self.assertEqual(imported.settings, original.settings)
            await self._request("GET", f"{PATH}/{APP_A.app_id}/skills/scoped-report/package", 200)
            self.assertEqual((await self._request(
                "GET", f"{PATH}/{APP_A.app_id}/skills/old-report/package", 200,
            )).content, legacy)

    async def test_delete_configuration_keeps_shared_and_unreferenced_app_packages(self):
        base = f"{PATH}/{APP_A.app_id}"
        await self._install(f"{base}/skills", name="shared")
        await self._install(f"{base}/skills", name="exclusive")
        await self._install(f"{base}/skills", name="unreferenced")
        config = await self.stores.integration_app.get(APP_A.app_id, APP_A.space_id)
        async with self.factory.begin() as db:
            db.add(IntegrationScopedSettings(
                id=str(uuid4()), integration_code="deepcube-assistant", scope_type="app",
                scope_key=config.scope_key, tenant_id=config.tenant_id,
                settings={"prompt_append": "", "skills": ["shared"]},
            ))
            await db.execute(update(IntegrationScopedSettings).where(
                IntegrationScopedSettings.id == config.id,
            ).values(settings={**config.settings, "skills": ["shared", "exclusive"]}))
        self.assertEqual({row["code"] for row in (await self._request("GET", f"{base}/skills", 200)).json()},
                         {"shared", "exclusive"})
        await self._request("GET", f"{base}/skills/unreferenced/package", 404)
        await self._request("DELETE", f"{base}/skills/shared", 409)
        await self._request("DELETE", f"{base}?config_id={config.id}", 200)
        async with self.factory() as db:
            owners = (await db.scalars(select(SkillPackage).where(
                SkillPackage.scope_ref == app_skill_scope(APP_A.app_id),
            ))).all()
        self.assertEqual({row.manifest["code"] for row in owners}, {"shared", "unreferenced"})
        self.assertTrue(all((self.root / "runtime" / row.code / "SKILL.md").is_file() for row in owners))
        # Once there are no integration rows, package ownership must still
        # trigger the same platform authorization for configuration exports.
        async with self.engine.begin() as db:
            await db.execute(delete(IntegrationScopedSettings))
        with patch("deep_agent.services.integration_app_access.IntegrationAppDirectory.verify",
                   side_effect=IntegrationAppAccessError(404, "应用不可用")):
            await self._request("GET", "/api/v1/configuration/skills/export", 404)
        await self._request("GET", "/api/v1/configuration/skills/export", 200)

    async def test_conflicting_conversion_preserves_legacy_rows(self):
        await self._save()
        await self._install_with_old_key(APP_A, "scoped-report", hashed=True)
        config = await self._mark_legacy(APP_A)
        await self._mark_revision()
        # Simulate a separately written App-owned row with the same original name.
        table = SkillPackage.__table__
        async with self.engine.begin() as db:
            row = dict((await db.execute(select(table))).mappings().one())
            await db.execute(table.insert().values({
                **row, "code": app_skill_storage_key(APP_A.app_id, "scoped-report"),
                "scope_ref": app_skill_scope(APP_A.app_id),
            }))
        with self.assertRaisesRegex(AppSkillDataError, "Duplicate Skill codes"):
            await convert_app_skill_ownership(self._database_url, apply=True)
        current = await self.stores.integration_app.get(APP_A.app_id, APP_A.space_id)
        self.assertEqual(current.settings, {"prompt_append": config.settings["prompt_append"]})
        async with self.factory() as db:
            scopes = set(await db.scalars(select(SkillPackage.scope_ref)))
        self.assertIn(f"integration-scope:{config.id}", scopes)

    async def test_concurrent_upload_and_config_removal_leave_no_dangling_reference(self):
        base = f"{PATH}/{APP_A.app_id}"
        config = await self._save()
        original = skill_zip(name="racing")
        ready, deleted = asyncio.Event(), asyncio.Event()

        async def delayed_upload(*args, **kwargs):
            result = await _upload_file(*args, **kwargs)
            ready.set()
            await asyncio.wait_for(deleted.wait(), timeout=5)
            return result

        with patch("deep_agent.api.v1.skills._upload_file", side_effect=delayed_upload):
            async with asyncio.TaskGroup() as group:
                task = group.create_task(self.client.post(
                    f"{base}/skills", files={"file": ("racing.zip", original)},
                ))
                await asyncio.wait_for(ready.wait(), timeout=5)
                await self._request("DELETE", f"{base}?config_id={config['config_id']}", 200)
                deleted.set()
        self.assertEqual(task.result().status_code, 409)
        await self._request("GET", f"{base}/skills/racing", 404)
        self.assertIsNone(await self.stores.skill_package.get_by_code(
            app_skill_storage_key(APP_A.app_id, "racing"), scope_ref=app_skill_scope(APP_A.app_id),
        ))
