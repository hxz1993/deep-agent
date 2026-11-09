"""Application configuration through ASGI, SQLite, local files and the real Harness."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import text, update

from maintenance.config_backup import export_backup

from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.db.models import SkillPackage
from deep_agent.files.storage import LocalFileStore
from deep_agent.integrations.app_scope import INTEGRATION
from deep_agent.schemas.agent import AgentUpdate
from deep_agent.security import ConsoleAdminAuthorizer
from deep_agent.services.integration_app_access import IntegrationAppAccessError, ManagedApp
from deep_agent.services.scope_names import ScopeDirectory
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

        async def verify(app_id):
            if app_id in (APP_A.app_id, APP_B.app_id):
                return APP_A if app_id == APP_A.app_id else APP_B
            raise IntegrationAppAccessError(404, "应用不存在或当前账号无权管理。")

        self.enterContext(patch(
            "deep_agent.services.integration_app_access.IntegrationAppDirectory.verify", side_effect=verify,
        ))
        self.enterContext(patch(
            "deep_agent.services.integration_app_access.IntegrationAppDirectory.tenants", return_value={"tenant-1": "企业"},
        ))
        self.enterContext(patch(
            "deep_agent.api.v1.integration_apps.resolve_scope_directory",
            return_value=ScopeDirectory(spaces={APP_A.space_id: APP_A.space_name}),
        ))
        self.fetch_scope = AsyncMock()
        self.enterContext(patch(
            "deep_agent.services.integration_app_runtime.PlatformScopeDirectoryFetcher", return_value=self.fetch_scope,
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

    async def test_same_zip_independent_lifecycle_and_backup_bundle(self):
        code = "scoped-report"
        base_a, base_b = f"{PATH}/{APP_A.app_id}", f"{PATH}/{APP_B.app_id}"
        await self._install("/api/v1/skills")
        general = await self.stores.skill_package.get_by_code(code)
        self.assertEqual(general.scope_ref, "general")
        async with self.engine.begin() as db:
            await db.execute(update(SkillPackage).where(SkillPackage.code == code).values(scope_ref=None))
        original = await self._install(f"{base_a}/skills")
        await self._install(f"{base_b}/skills")
        await self._request("POST", f"{base_a}/skills", 409, files={"file": ("same.zip", original)})
        configs = (await self._request("GET", PATH, 200)).json()
        self.assertEqual([(row["app_id"], row["skill_count"]) for row in configs],
                         [(APP_A.app_id, 1), (APP_B.app_id, 1)])
        detail = (await self._request("GET", base_a, 200)).json()
        self.assertEqual(detail["skill_count"], 1)
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

    async def test_run_reads_fresh_access_and_keeps_original_session_scope(self):
        await self._save()
        await self._install(f"{PATH}/{APP_A.app_id}/skills", scripts=True)
        recorded = []

        def run_model(messages, info):
            tool_names = [tool.name for tool in info.function_tools]
            if "get_current_time" in tool_names:
                recorded.append((info.instructions or "", tool_names))
            return ModelResponse(parts=[TextPart("差额 20，差异率 20%。")])

        async def directory(headers):
            scopes = {APP_A.app_id: (APP_A.space_id, APP_A.tenant_id), APP_B.app_id: (APP_B.space_id, APP_B.tenant_id)}
            return ScopeDirectory(app_scopes=scopes if headers.get("authorization") == "Bearer fixture-only" else {})

        self.fetch_scope.side_effect = directory
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(run_model)), mock_request_disconnect():
            async def send(*, app=None, agent=self.agent, session=None, auth=None):
                headers = {"x-deep-agent-app": app} if app else {}
                if auth is not None:
                    headers["authorization"] = auth
                body = {"agent_id": agent, "message": "实际120，预算100，给出差额。"}
                if session:
                    body["session_id"] = session
                return (await self._request("POST", "/api/v1/chat", 200, headers=headers, json=body)).json()

            legacy = await send()
            scoped = await send(app=APP_A.app_id)
            await send(app=APP_B.app_id)
            await send(app=APP_A.app_id, agent=self.other_agent)
            await send(app=APP_A.app_id, auth="Bearer unauthorized")
            await send(app=APP_A.app_id, session=legacy["session_id"])
            await send(session=scoped["session_id"])
            await self._request("POST", "/api/v1/chat", 409, headers={"x-deep-agent-app": APP_B.app_id}, json={
                "agent_id": self.agent, "session_id": scoped["session_id"], "message": "切换应用",
            })
        self.assertEqual([("fixture-app-rule" in value, "app_id=" in value) for value, _ in recorded],
                         [(False, False), (True, True), (False, True), (False, True), (False, False), (False, False), (True, True)])
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
        await self._install(f"{PATH}/{APP_A.app_id}/skills")
        self.fetch_scope.return_value = ScopeDirectory(app_scopes={
            APP_A.app_id: (APP_A.space_id, APP_A.tenant_id),
            APP_B.app_id: (APP_B.space_id, APP_B.tenant_id),
        })
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
        self.fetch_scope.return_value = ScopeDirectory(app_scopes={
            APP_A.app_id: (APP_A.space_id, APP_A.tenant_id),
            APP_B.app_id: (APP_B.space_id, APP_B.tenant_id),
        })
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
        await self._install(f"{PATH}/{APP_A.app_id}/skills")
        async with self.engine.begin() as db:
            await db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
            await db.execute(text("INSERT INTO alembic_version VALUES ('015')"))
        backup = await export_backup(
            f"sqlite+aiosqlite:///{self.root / 'test.db'}",
            environment="test", output_root=self.root / "backup", git_commit="test",
        )
        bundle = await self._request("GET", "/api/v1/configuration/skills/export", 200)
        uploads = {"database_file": ("backup.db", backup.path.read_bytes()),
                   "skills_bundle": ("skills.zip", bundle.content)}
        await self._request("POST", "/api/v1/configuration/import", 200, files=uploads)
        original = await self.stores.integration_app.get(APP_A.app_id, APP_A.space_id)
        with patch("deep_agent.services.integration_app_access.IntegrationAppDirectory.verify",
                   side_effect=IntegrationAppAccessError(404, "应用不可用")):
            await self._request("POST", "/api/v1/configuration/import", 404, files=uploads,
                                data={"apply": "true", "confirm": "true"})
        await self._request("POST", "/api/v1/configuration/import", 200, files=uploads,
                            data={"apply": "true", "confirm": "true"})
        imported = await self.stores.integration_app.get(APP_A.app_id, APP_A.space_id)
        self.assertEqual((imported.id, imported.settings), (original.id, original.settings))
        await self._request("GET", f"{PATH}/{APP_A.app_id}/skills/scoped-report/package", 200)
