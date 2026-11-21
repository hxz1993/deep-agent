"""Exercise the standard A2A wire contract with the real Runner and SQLite."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import tempfile
import unittest
from datetime import timedelta
from inspect import isawaitable
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from a2a import types as wire
from a2a.client.card_resolver import parse_agent_card
from fastapi import FastAPI
from google.protobuf.timestamp_pb2 import Timestamp
from httpx import ASGITransport, AsyncClient
from PIL import Image
from pydantic_ai.messages import BinaryContent, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from deep_agent.agent_service.download import ArtifactDownloadResponse
from deep_agent.agent_service.publications import PublicationCreate, PublicationPatch, PublicationStore
from deep_agent.agent_service.tasks import RETENTION, ArtifactFile, Result
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.sandbox import SubprocessBackend
from deep_agent.schemas.agent import AgentCreate, AgentUpdate
from deep_agent.security.console_admin import ConsoleAdminAuthenticationError
from tests.helpers.sandbox import sandbox_test_python

from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.files.storage import LocalFileStore
from deep_agent.security import ConsoleAdminAccess, ConsoleAdminAuthorizer
from deep_agent.services.integration_app_access import IntegrationAppAccessError
from deep_agent.services.scope_names import ScopeDirectory
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent

ROOT = "/api/v1/agent-service"
DIRECT_BASE = "http://127.0.0.1:8002"
GATEWAY_PREFIX = "/deepfos-server/deep-agent-server1-0"
BACKEND_AUTHORITY = "10.77.0.12:8080"
HEADERS = {"authorization": "Bearer fixture-owner", "app": "budget001", "space": "budget", "A2A-Version": "1.0"}


PUBLICATION_OUTPUT = "output__publication_result"


def publication_model(respond):
    async def stream(messages, info):
        response = respond(messages, info)
        if isawaitable(response):
            response = await response
        for index, part in enumerate(response.parts):
            if isinstance(part, TextPart):
                yield part.content
            else:
                assert isinstance(part, ToolCallPart), part
                yield {index: DeltaToolCall(
                    name=part.tool_name, json_args=part.args_as_json_str(), tool_call_id=part.tool_call_id,
                )}
    return FunctionModel(respond, stream_function=stream)


def publication_result(status="completed", *, message=None, **reply):
    return ModelResponse(parts=[ToolCallPart(PUBLICATION_OUTPUT, {
        "status": status, **({"message": message} if message else {}), "reply": reply,
    })])


def text_result(info, text):
    if PUBLICATION_OUTPUT in {tool.name for tool in info.output_tools}:
        return publication_result(text=text)
    return ModelResponse(parts=[TextPart(text)])


def latest_tool_result(messages, name):
    return next((part for message in reversed(messages) for part in reversed(message.parts)
                 if isinstance(part, ToolReturnPart) and part.tool_name == name), None)


def mcp_message(key, text, *, files=(), **bindings):
    parts = [{"text": text}, *[
        {"filename": name, "mediaType": mime, "raw": base64.b64encode(content).decode()}
        for name, mime, content in files
    ]]
    return {"messageId": key, "role": "ROLE_USER", "parts": parts, **bindings}


class AgentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="TMP_to_delete_a2a_")
        self.addCleanup(tmp.cleanup)
        directory = Path(tmp.name)
        stores, engine, factory = await make_sqlite_stores(f"sqlite+aiosqlite:///{directory / 'test.db'}")
        self.agent = await seed_default_agent(stores)
        files = LocalFileStore(directory / "files")
        await files.initialize()
        self.ctx = AppContext.for_testing(
            stores=stores, db_engine=engine, db_session_factory=factory, file_store=files,
            console_admin_authorizer=ConsoleAdminAuthorizer(AsyncMock(return_value=True)),
        )
        init_context(self.ctx)
        await self.ctx.event_persister.start()
        self.addCleanup(reset_context)
        self.addAsyncCleanup(self.ctx.aclose)
        app = FastAPI()
        app.include_router(api_router)
        self.app = app
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url=DIRECT_BASE)
        self.addAsyncCleanup(self.client.aclose)
        self.revoked = False
        self.platform_realm = "https://gateway.example.test/"
        self.realm_queries = 0

        async def platform(client, _service, path, **_kwargs):
            auth = client._headers.get("token")
            if path == "api/user/full-detail":
                self.assertNotIn("user", client._headers)
                if auth not in {"fixture-owner", "fixture-other"}:
                    raise IntegrationAppAccessError(401, "Fixture token rejected")
                return {"id": auth.split("-")[-1]}
            if path == "check-permission-by-space-app":
                return {"app": not self.revoked, "space": not self.revoked}
            if path == "server-config-params":
                self.realm_queries += 1
                self.assertEqual(_service, "system")
                return {"EXPORT_ENV_REALM_NAME": self.platform_realm}
            self.fail(f"Unexpected platform request {path}")

        self.enterContext(patch(
            "deep_agent.services.integration_app_access.PlatformAppAccessClient._get",
            autospec=True, side_effect=platform,
        ))
        self.enterContext(patch("deep_agent.api.v1.agent_service.resolve_diagnostic_users", return_value={}))
        self.admin = {"user": "fixture-admin", "authorization": "Bearer fixture-admin"}
        await PublicationStore(factory).create(PublicationCreate.model_validate({
            "agent_code": "budget-report", "name": "预算报告", "agent_id": self.agent,
            "scope": {"apps": {"mode": "selected", "items": [
                {"app_id": app, "space_id": "budget"} for app in ("budget001", "budget002")
            ]}},
        }))
        self.url = f"{ROOT}/a2a/budget-report"

    async def rpc(self, method, params, *, headers=HEADERS, url=None):
        return await self.client.post(url or self.url, headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
        })

    async def test_publication_updater_tracks_authenticated_admin_and_current_revision(self):
        collection = f"{ROOT}/publications"
        url = f"{collection}/operator-audit"
        scope = {"apps": {"mode": "all", "items": []}}
        editor = {"user": "second-admin", "authorization": "Bearer fixture-second-admin"}
        body = {"agent_code": "operator-audit", "name": "更新人测试", "agent_id": self.agent, "scope": scope}

        create = await self.client.post(collection, headers=self.admin, json=body)
        self.assertEqual(create.status_code, 201, create.text)
        created = create.json()
        self.assertEqual(created["updated_by"], "fixture-admin")
        self.assertEqual(created["scope"], scope)

        edit = await self.client.patch(url, headers=editor, json={"revision": 1, "enabled": False})
        self.assertEqual(edit.status_code, 200, edit.text)
        self.assertEqual(edit.json()["updated_by"], "second-admin")
        stale = await self.client.patch(url, headers=self.admin, json={"revision": 1, "name": "过期修改"})
        self.assertEqual(stale.status_code, 409, stale.text)

        profile = {"username": "operator", "display_name": "更新人"}
        with patch("deep_agent.api.v1.agent_service.resolve_diagnostic_users",
                   return_value={"second-admin": profile}) as users:
            response = await self.client.get(collection, headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        users.assert_awaited_once()
        self.assertEqual(users.call_args.args[1:], ("fixture-admin", {"second-admin"}))
        rows = {row["agent_code"]: row for row in response.json()}
        self.assertIsNone(rows["budget-report"]["updated_by"])
        self.assertEqual(rows["operator-audit"]["updated_by_user"], {"id": "second-admin", **profile})

        detail = await self.client.get(url, headers=self.admin)
        self.assertEqual(detail.json()["updated_by"], "second-admin")
        self.assertIsNone(detail.json()["updated_by_user"]["display_name"])
        self.assertEqual(detail.json()["scope"], scope)

        store = PublicationStore(self.ctx.db_session_factory)
        await store.update("operator-audit", PublicationPatch(revision=2, name="旧版更新"))
        detail = await self.client.get(url, headers=self.admin)
        self.assertEqual(detail.json()["revision"], 3)
        self.assertIsNone(detail.json()["updated_by"])
        updater_key = f"agent_publication_updater:{created['id']}"
        await self.ctx.stores.global_setting.upsert(updater_key, value=["invalid updater"], version=1)
        for endpoint in (collection, url):
            with self.subTest(endpoint=endpoint):
                response = await self.client.get(endpoint, headers=self.admin)
                self.assertEqual(response.status_code, 200, response.text)
                row = response.json() if endpoint == url else next(
                    item for item in response.json() if item["agent_code"] == "operator-audit"
                )
                self.assertIsNone(row["updated_by"])

        deleted = await self.client.delete(url, headers=self.admin, params={"revision": 3})
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertIsNone(await self.ctx.stores.global_setting.get(updater_key))

    async def test_documented_a2a_and_mcp_auth_headers(self):
        app = {"app": "budget001"}
        rpc_version = {"A2A-Version": "1.0"}
        app_url = f"{self.url}/apps/budget001"
        mcp_url = f"{ROOT}/mcp"
        mcp_request = {"jsonrpc": "2.0", "id": 1, "method": "resources/list"}
        rpc_call = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
        with patch("deep_agent.agent_service.security.PlatformEnterpriseAdminChecker.resolve_api_key_user",
                   return_value="owner") as resolve_key:
            for credential in (
                {"token": "fixture-owner"},
                {"Authorization": "Bearer fixture-owner"},
                {"X-Deepfos-Key": "fixture-key"},
            ):
                with self.subTest(credential=next(iter(credential))):
                    for url, context in ((self.url, app), (app_url, {})):
                        card = await self.client.get(f"{url}/agent-card.json", headers={**credential, **context})
                        self.assertEqual(card.status_code, 200)
                        task_list = await self.rpc("ListTasks", {}, url=url, headers={**credential, **context, **rpc_version})
                        self.result(task_list)
                    resources = await self.client.post(mcp_url, json=mcp_request, headers={**rpc_call, **credential, **app})
                    self.assertEqual(resources.status_code, 200)
                    self.assertIn("resources", resources.json()["result"])
            resolve_key.assert_awaited_with("fixture-key", "budget001", "budget")
        wrong_version = await self.rpc("ListTasks", {}, headers={**app, "token": "fixture-owner"})
        self.assertEqual(wrong_version.json()["error"]["code"], -32009)
        conflicting = {**app, "token": "fixture-owner", "Authorization": "Bearer fixture-owner"}
        with patch("deep_agent.agent_service.security.emit") as logged:
            self.assertEqual((await self.client.get(f"{self.url}/agent-card.json", headers=conflicting)).status_code, 401)
            self.assertEqual((await self.client.post(mcp_url, headers={**rpc_call, **conflicting}, json=mcp_request)).status_code, 401)
        self.assertEqual([call.kwargs["protocol"] for call in logged.call_args_list], ["a2a", "mcp"])
        for call in logged.call_args_list:
            self.assertEqual(call.args, ("published_agent_access_failed",))
            self.assertEqual(call.kwargs["http_status"], 401)
            self.assertNotIn("fixture-owner", str(call))

    @staticmethod
    def message(id, content, **bindings):
        return {"messageId": id, "role": "ROLE_USER", "parts": [{"text": content}], **bindings}

    def result(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("result", response.json(), response.text)
        return response.json()["result"]

    async def test_invalid_request_address_records_one_diagnostic_per_a2a_request(self):
        publication = (await self.client.get(f"{ROOT}/publications/budget-report", headers=self.admin)).json()
        headers = {**HEADERS, "host": "fixture-user:fixture-secret@example.test"}
        with patch("deep_agent.agent_service.security.emit") as logged:
            card = await self.client.get(f"{self.url}/agent-card.json", headers=headers)
            tasks = await self.rpc("ListTasks", {}, headers=headers)
        self.assertEqual(card.status_code, 503)
        self.assertEqual(tasks.status_code, 503)
        self.assertEqual(logged.call_count, 2)
        for call in logged.call_args_list:
            self.assertEqual(call.args, ("published_agent_access_failed",))
            self.assertEqual(call.kwargs["protocol"], "a2a")
            self.assertEqual(call.kwargs["publication_id"], publication["id"])
            self.assertEqual(call.kwargs["phase"], "public_address")
            self.assertEqual(call.kwargs["http_status"], 503)
            self.assertIsNotNone(call.kwargs["exception"])
            self.assertNotIn("fixture-secret", str(call))

    async def test_card_runner_history_scope_and_idempotency(self):
        card = await self.client.get(f"{self.url}/agent-card.json", headers=HEADERS)
        self.assertEqual(card.status_code, 200, card.text)
        self.assertEqual(card.json()["supportedInterfaces"][0]["protocolVersion"], "1.0")
        parsed_card = parse_agent_card(card.json())
        self.assertEqual(parsed_card.security_schemes["platformKey"].api_key_security_scheme.name, "X-Deepfos-Key")
        self.assertEqual(parsed_card.security_schemes["platformBearer"].http_auth_security_scheme.scheme, "bearer")
        self.assertEqual(len(parsed_card.security_requirements), 2)
        model = publication_model(lambda _messages, info: text_result(info, "预算差异 20"))
        with patch("deep_agent.agent.factory._resolve_model", return_value=model):
            params = {"message": self.message("m1", "计算预算差异")}
            task = self.result(await self.rpc("SendMessage", params))["task"]
            self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED", task)
            self.assertEqual(task["artifacts"][0]["parts"][0]["text"], "预算差异 20")
            self.assertEqual(self.result(await self.rpc("SendMessage", params))["task"]["id"], task["id"])
            self.assertIn("error", (await self.rpc("SendMessage", {"message": self.message("m1", "不同内容")})).json())
            next_task = self.result(await self.rpc("SendMessage", {
                "message": self.message("m2", "再算一次", contextId=task["contextId"]),
            }))["task"]
            self.assertNotEqual(next_task["id"], task["id"])
            self.assertEqual(next_task["contextId"], task["contextId"])
        page = self.result(await self.rpc("ListTasks", {"pageSize": 1, "includeArtifacts": True}))
        self.assertEqual(page["totalSize"], 2)
        self.assertEqual(page["tasks"][0]["id"], next_task["id"])
        older = self.result(await self.rpc("ListTasks", {
            "pageSize": 1, "pageToken": page["nextPageToken"], "includeArtifacts": True,
        }))
        self.assertEqual(older["tasks"][0]["id"], task["id"])
        item = self.result(await self.rpc("GetTask", {"id": task["id"], "historyLength": 0}))
        self.assertNotIn("history", item)
        other = await self.rpc("GetTask", {"id": task["id"]}, headers={
            **HEADERS, "authorization": "Bearer fixture-other", "user": "owner",
        })
        self.assertIn("error", other.json())
        self.assertEqual((await self.rpc("GetTask", {"id": task["id"]}, headers={
            **HEADERS, "app": "budget/nope",
        })).status_code, 400)
        self.revoked = True
        self.assertEqual((await self.rpc("GetTask", {"id": task["id"]})).status_code, 403)

    async def test_list_tasks_time_cutoff_is_inclusive_and_metadata_is_stable(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        with patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(
            lambda _, info: text_result(info, "核对完毕"),
        )):
            tasks = [
                self.result(await self.rpc("SendMessage", {
                    "message": self.message(f"cutoff-{name}", f"{name}的预算核对"),
                }))["task"]
                for name in ("较早", "等于", "较晚")
            ]
        entries = [self.ctx.agent_tasks.entries[task["id"]] for task in tasks]
        cutoff = entries[1].changed
        for offset, entry in zip((-1, 0, 1), entries):
            entry.changed = cutoff + timedelta(seconds=offset)
            entry.task.status.timestamp.FromDatetime(entry.changed)
        filters = {"statusTimestampAfter": cutoff.isoformat(), "historyLength": 1, "includeArtifacts": True}
        expected = [tasks[2]["id"], tasks[1]["id"]]
        a2a = self.result(await self.rpc("ListTasks", filters))
        self.assertEqual([task["id"] for task in a2a["tasks"]], expected)
        next_nanosecond = Timestamp()
        next_nanosecond.FromNanoseconds(entries[1].task.status.timestamp.ToNanoseconds() + 1)
        later_filters = {**filters, "statusTimestampAfter": next_nanosecond.ToJsonString()}
        later = self.result(await self.rpc("ListTasks", later_filters))
        self.assertEqual([task["id"] for task in later["tasks"]], [tasks[2]["id"]])
        self.client.headers.update(HEADERS)
        async with streamable_http_client(f"{DIRECT_BASE}{ROOT}/mcp", http_client=self.client) as (rx, tx, _):
            async with ClientSession(rx, tx) as client:
                await client.initialize()
                listed = await client.call_tool("ListTasks", {"agentCode": "budget-report", **filters})
                self.assertFalse(listed.isError, listed)
                self.assertEqual(listed.structuredContent, a2a)
                after = await client.call_tool("ListTasks", {"agentCode": "budget-report", **later_filters})
                self.assertFalse(after.isError, after)
                self.assertEqual(after.structuredContent, later)
                self.assertEqual(a2a["tasks"][0]["metadata"]["description"], "较晚的预算核对")
                self.assertTrue(all(len(task["history"]) == 1 for task in a2a["tasks"]))

    async def test_rpc_envelope_and_error_compatibility(self):
        for body, expected in (
            ({"jsonrpc": "2.0", "id": True, "method": "ListTasks"}, -32600),
            ({"jsonrpc": "2.0", "id": 1, "method": "unknown"}, -32601),
            ({"jsonrpc": "2.0", "id": 1, "method": "ListTasks", "params": None}, -32602),
        ):
            reply = await self.client.post(self.url, headers=HEADERS, json=body)
            self.assertEqual(reply.json()["error"]["code"], expected)
        malformed = await self.client.post(self.url, headers=HEADERS, content=b"{")
        self.assertEqual(malformed.json()["error"]["code"], -32700)
        self.assertEqual((await self.rpc("ListTasks", {}, headers={**HEADERS, "A2A-Version": "2.0"})).json()["error"]["code"], -32009)
        self.assertEqual((await self.rpc("SendMessage", {
            "message": self.message("push", "开始"),
            "configuration": {"taskPushNotificationConfig": {}},
        })).json()["error"]["code"], -32003)
        page = self.result(await self.rpc("ListTasks", {"historyLength": None}))
        self.assertEqual(page["tasks"], [])
        self.assertEqual(page["totalSize"], 0)

    async def test_four_running_segments_and_cancel_queued_segment(self):
        release = asyncio.Event()
        four_running = asyncio.Event()
        executing = []

        async def execute(entry, _message, _continuing):
            executing.append(entry.task.id)
            if len(executing) == 4:
                four_running.set()
            await release.wait()
            return Result()

        with patch("deep_agent.agent_service.protocol.RunnerExecution", return_value=execute):
            ids = []
            try:
                for index in range(5):
                    reply = await self.rpc("SendMessage", {
                        "message": self.message(f"queued-{index}", "并发"),
                        "configuration": {"returnImmediately": True},
                    })
                    ids.append(self.result(reply)["task"]["id"])
                await asyncio.wait_for(four_running.wait(), 2)
                self.assertCountEqual(executing, ids[:4])
                detail_url = f"{ROOT}/publications/budget-report/tasks/{ids[4]}"
                detail = await self.client.get(detail_url, headers=self.admin)
                self.assertEqual(detail.status_code, 200, detail.text)
                self.assertEqual(detail.json()["status"], "TASK_STATE_SUBMITTED")
                self.assertIsNone(detail.json()["session_id"])
                self.assertEqual(detail.json()["turns"], [])
                cancelled = self.result(await self.rpc("CancelTask", {"id": ids[4]}))
                self.assertEqual(cancelled["status"]["state"], "TASK_STATE_CANCELED")
            finally:
                release.set()
            for task_id in ids[:4]:
                task = self.ctx.agent_tasks.entries[task_id]
                self.assertEqual((await self.ctx.agent_tasks.wait(task)).status.state, wire.TaskState.TASK_STATE_COMPLETED)
            self.assertCountEqual(executing, ids[:4])

    async def test_pending_question_expires_with_its_task(self):
        from deep_agent.agent_service.public import snapshot

        async def ask(*_args):
            return Result(state=wire.TaskState.TASK_STATE_INPUT_REQUIRED, question="月份？")

        with patch("deep_agent.agent_service.protocol.RunnerExecution", return_value=ask):
            for terminal in ("cancel", "expire"):
                task = self.result(await self.rpc("SendMessage", {"message": self.message(terminal, "开始")}))["task"]
                entry = self.ctx.agent_tasks.entries[task["id"]]
                question = task["status"]["message"]["messageId"]
                self.assertEqual(snapshot(entry)["question_id"], question)
                if terminal == "cancel":
                    self.result(await self.rpc("CancelTask", {"id": task["id"]}))
                else:
                    entry.changed -= RETENTION
                    self.ctx.agent_tasks.prune()
                    self.assertNotEqual(entry.task.status.message.message_id, question)
                self.assertIsNone(snapshot(entry)["question_id"])

    async def test_published_runs_hide_ui_tools_and_ask_for_input(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        from deep_agent.api.v1.deps import build_runner_for_agent
        from deep_agent.schemas.ui_tools import UiToolCreate, UiToolPackageCreate

        await self.ctx.stores.ui_tool_package.create_manual(UiToolPackageCreate(code="ui:budget", label="预算"))
        await self.ctx.stores.ui_tool.create_manual(UiToolCreate(
            name="budget__clarify", package_code="ui:budget", description="Ask for the budget month.",
            parameters_json_schema={"type": "object"},
        ))
        prompt = "Always use a UI tool when the month is missing."
        capabilities = ["ui:builtin", "ui:budget"]
        await self.ctx.stores.agent.update_unique(self.agent, AgentUpdate(prompt=prompt, capabilities=capabilities))

        published_requests = []

        def respond(_messages, info):
            if not info.output_tools:
                return ModelResponse(parts=[TextPart("预算核对")])
            published_requests.append(info)
            return publication_result("input_required", message="请提供预算月份")

        with patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(respond)):
            runner = await build_runner_for_agent(self.ctx, self.agent)
            self.assertEqual(set(runner.ui_tool_hints), {"builtin__choice", "builtin__confirm", "budget__clarify"})

            a2a_task = self.result(await self.rpc("SendMessage", {
                "message": self.message("a2a-no-ui", "核对预算"),
            }))["task"]
            self.client.headers.update(HEADERS)
            async with streamable_http_client(f"{DIRECT_BASE}{ROOT}/mcp", http_client=self.client) as (rx, tx, _):
                async with ClientSession(rx, tx) as session:
                    await session.initialize()
                    result = await session.call_tool("SendMessage", {
                        "agentCode": "budget-report", "message": mcp_message("mcp-no-ui", "核对预算"),
                    })
                    self.assertFalse(result.isError, result)
                    mcp_task = result.structuredContent

        for task in (a2a_task, mcp_task):
            self.assertEqual(task["status"]["state"], "TASK_STATE_INPUT_REQUIRED", task)
            self.assertEqual(task["status"]["message"]["parts"][0]["text"], "请提供预算月份")
        self.assertEqual(len(published_requests), 2)
        for info in published_requests:
            names = {tool.name for tool in info.function_tools}
            self.assertTrue(names.isdisjoint({"builtin__choice", "builtin__confirm", "budget__clarify"}))
            self.assertFalse(any(name.startswith("ui__") for name in names), names)
            self.assertIn(prompt, info.instructions)
            self.assertIn("status=input_required", info.instructions)
        row = await self.ctx.stores.agent.get_by_id(self.agent)
        self.assertEqual(row.capabilities, capabilities)

    async def test_published_run_is_observable_before_completion(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        self.client.headers.update(HEADERS)
        async with streamable_http_client(f"{DIRECT_BASE}{ROOT}/mcp", http_client=self.client) as (rx, tx, _):
            async with ClientSession(rx, tx) as mcp:
                await mcp.initialize()
                for protocol in ("a2a", "mcp"):
                    with self.subTest(protocol=protocol):
                        entered = asyncio.Event()
                        complete = asyncio.Event()

                        async def respond(_messages, info):
                            if not info.output_tools:
                                return ModelResponse(parts=[TextPart("预算验收")])
                            entered.set()
                            await complete.wait()
                            return publication_result(text=f"完成 {protocol}")

                        with patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(respond)):
                            if protocol == "a2a":
                                task = self.result(await self.rpc("SendMessage", {
                                    "message": self.message("live-a2a", "核对预算"),
                                    "configuration": {"returnImmediately": True},
                                }))["task"]
                            else:
                                result = await mcp.call_tool("SendMessage", {
                                    "agentCode": "budget-report",
                                    "message": mcp_message("live-mcp", "核对预算"),
                                    "configuration": {"waitSeconds": 0},
                                })
                                self.assertFalse(result.isError, result)
                                task = result.structuredContent
                            entry = self.ctx.agent_tasks.entries[task["id"]]
                            observer = None
                            try:
                                await asyncio.wait_for(entered.wait(), 4)
                                key = (self.agent, entry.context.session_id, "owner")
                                observer = self.ctx.session_live.subscribe(key, entry.segment_run_id)
                                self.assertEqual(observer.initial[0].event, "observation_sync")
                                self.assertTrue(any(
                                    json.loads(frame.data).get("data", {}).get("event", {}).get("event_type")
                                    == "user/message" for frame in observer.initial[1:]
                                ))
                                complete.set()
                                observed = []
                                while (frame := await asyncio.wait_for(observer.get(), 4)).event != "observation_end":
                                    observed.append(json.loads(frame.data))
                                output = next(frame["data"] for frame in observed if frame["event"] == "final")
                                self.assertEqual(output["output"]["reply"]["text"], f"完成 {protocol}")
                            finally:
                                complete.set()
                                if observer is not None:
                                    self.ctx.session_live.unsubscribe(observer)
                            await self.ctx.agent_tasks.wait(entry)
                            self.assertEqual(entry.state, wire.TaskState.TASK_STATE_COMPLETED)

    async def test_mcp_wait_expiry_leaves_original_task_running(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        from deep_agent.agent_service.execution import RunnerExecution

        release = asyncio.Event()

        async def execute(_runner, _entry, _message, _continuing):
            await release.wait()
            return Result(artifacts=[wire.Artifact(artifact_id="answer", parts=[wire.Part(text="完成")])])

        self.client.headers.update(HEADERS)
        with patch.object(RunnerExecution, "__call__", autospec=True, side_effect=execute):
            async with streamable_http_client(f"{DIRECT_BASE}{ROOT}/mcp", http_client=self.client) as (rx, tx, _):
                async with ClientSession(rx, tx, read_timeout_seconds=timedelta(seconds=4)) as session:
                    await session.initialize()

                    async def call(tool, arguments):
                        response = await session.call_tool(tool, arguments)
                        self.assertFalse(response.isError, response)
                        return response.structuredContent

                    message = {"agentCode": "budget-report", "message": mcp_message("wait-budget", "核对预算")}
                    active = await call("SendMessage", {**message, "configuration": {"waitSeconds": 0.05}})
                    self.assertEqual(active["status"]["state"], "TASK_STATE_WORKING")
                    ids = {"id": active["id"], "contextId": active["contextId"]}
                    repeated = await call("SendMessage", {**message, "configuration": {"waitSeconds": 0}})
                    immediate = await call("GetTask", {"id": active["id"], "waitSeconds": 0})
                    timed = await call("GetTask", {"id": active["id"], "waitSeconds": 0.05})
                    for snapshot in (repeated, immediate, timed):
                        self.assertEqual({key: snapshot[key] for key in ids}, ids)
                        self.assertEqual(snapshot["status"]["state"], "TASK_STATE_WORKING")
                    for tool, params in (
                        ("SendMessage", {**message, "configuration": {"waitSeconds": -1}}),
                        ("GetTask", {"id": active["id"], "waitSeconds": -1}),
                    ):
                        invalid = await session.call_tool(tool, params)
                        self.assertTrue(invalid.isError)
                        self.assertEqual(invalid.structuredContent["status"], 422)

                    async with asyncio.TaskGroup() as group:
                        get = group.create_task(call("GetTask", {"id": active["id"], "historyLength": 0}))
                        try:
                            done, _ = await asyncio.wait([get], timeout=0.05)
                            self.assertFalse(done)
                        finally:
                            release.set()
                    final = get.result()
                    self.assertEqual({key: final[key] for key in ids}, ids)
                    self.assertEqual(final["status"]["state"], "TASK_STATE_COMPLETED")
                    self.assertEqual(final["artifacts"][0]["parts"][0]["text"], "完成")
                    self.assertFalse(final.get("history"))

    async def test_question_and_continuation(self):

        await self.ctx.stores.agent.update_unique(self.agent, AgentUpdate(
            structured_output_enabled=True,
            output_config={
                "name": "budget_result", "description": "预算分析结果",
                "json_schema": {"type": "object", "properties": {"month": {"type": "string"}}, "required": ["month"]},
            },
        ), user_id="fixture-admin")

        def respond(_messages, info):
            if not info.output_tools:
                return ModelResponse(parts=[TextPart("预算分析")])
            names = {tool.name for tool in info.output_tools}
            self.assertEqual(names, {PUBLICATION_OUTPUT})
            return publication_result("input_required", message="请提供预算月份", text="已准备预算数据")

        app_url = f"{self.url}/apps/budget001"
        token = {key: HEADERS[key] for key in ("authorization", "A2A-Version")}
        with patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(respond)):
            task = self.result(await self.rpc("SendMessage", {"message": self.message("q", "预算分析")}))["task"]
        self.assertEqual(task["status"]["state"], "TASK_STATE_INPUT_REQUIRED", task)
        self.assertEqual(task["status"]["message"]["parts"][0]["text"], "请提供预算月份")
        detail_url = f"{ROOT}/publications/budget-report/tasks/{task['id']}"
        paused = await self.client.get(detail_url, headers=self.admin)
        self.assertEqual(paused.status_code, 200, paused.text)
        session_id = paused.json()["session_id"]
        self.assertNotEqual(session_id, task["contextId"])
        self.assertEqual(paused.json()["caller"], "owner")
        self.assertEqual(len(paused.json()["turns"]), 1)
        ordinary_user = {**self.admin, "user": "owner"}
        with patch.object(self.ctx.console_admin_authorizer, "access_for", return_value=ConsoleAdminAccess(allowed=False)):
            self.assertEqual((await self.client.get(detail_url, headers=ordinary_user)).status_code, 403)
        bindings = {"taskId": task["id"], "contextId": task["contextId"]}
        with patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(
            lambda *_: publication_result(data={"month": "十月"}),
        )):
            answer = self.message("answer", "十月", **bindings, metadata={"replyTo": "unused"})
            task2 = self.result(await self.rpc("SendMessage", {"message": answer}))["task"]
        self.assertEqual(task2["id"], task["id"])
        self.assertEqual(task2["status"]["state"], "TASK_STATE_COMPLETED", task2)
        self.assertEqual(task2["artifacts"][0]["parts"][0]["data"], {"month": "十月"})
        with patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(
            lambda *_: publication_result(data={"month": "十一月"}),
        )):
            next_task = self.result(await self.rpc("SendMessage", {
                "message": self.message("next", "十一月", contextId=task["contextId"]),
            }))["task"]
        self.assertNotEqual(next_task["id"], task["id"])
        persisted_turns = await self.ctx.store.list_events(session_id, event_type="user/message")
        self.assertEqual(len(persisted_turns), 3)
        with (
            patch("deep_agent.api.v1.agent_service.resolve_diagnostic_users", return_value={"owner": {"username": "budget-user"}}),
            patch("deep_agent.api.v1.agent_service.resolve_scope_directory", return_value=ScopeDirectory(
                apps={"budget001": "预算应用"}, spaces={"budget": "预算空间"},
            )),
        ):
            finished = await self.client.get(detail_url, headers=self.admin)
            task_list = await self.client.get(f"{ROOT}/publications/budget-report/tasks", headers=self.admin)
        self.assertEqual(finished.status_code, 200, finished.text)
        detail = finished.json()
        self.assertEqual(detail["session_id"], session_id)
        self.assertEqual([(row["id"], row["event_id"]) for row in detail["turns"]], [
            (event.turn_id, event.event_id) for event in persisted_turns[:2]
        ])
        self.assertEqual([row["summary"] for row in detail["turns"]], ["预算分析", "十月"])
        self.assertTrue(all(row["created_at"] for row in detail["turns"]))
        self.assertEqual((detail["caller_username"], detail["app_name"], detail["space_name"]),
                         ("budget-user", "预算应用", "预算空间"))
        self.assertEqual(task_list.json()["items"][0]["caller_username"], "budget-user")
        diagnostic = await self.client.get(f"/api/v1/diagnostics/sessions/{session_id}", headers=self.admin)
        self.assertEqual(diagnostic.status_code, 200, diagnostic.text)
        self.assertEqual(diagnostic.json()["source_type"], "a2a")
        self.assertEqual(detail["session_title"], diagnostic.json()["title"])

    async def test_publication_retries_text_outside_result(self):
        calls = 0

        def respond(_messages, info):
            nonlocal calls
            if not info.output_tools:
                return ModelResponse(parts=[TextPart("预算说明")])
            calls += 1
            if calls == 1:
                result = publication_result(text="见正文")
                result.parts.insert(0, TextPart("本月实际 92 万元，低于预算 8 万元。"))
                return result
            return publication_result(text="本月实际 92 万元，低于预算 8 万元。")

        with patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(respond)):
            task = self.result(await self.rpc("SendMessage", {
                "message": self.message("reply-only", "请解释差额"),
            }))["task"]
        self.assertEqual(calls, 2)
        self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(task["artifacts"][0]["parts"][0]["text"], "本月实际 92 万元，低于预算 8 万元。")
        entry = self.ctx.agent_tasks.entries[task["id"]]
        events = await self.ctx.store.list_events(entry.context.session_id, event_type="output/attempt")
        self.assertEqual([event.payload["accepted"] for event in events], [False, True])

    async def test_publication_retries_a_business_number_that_would_lose_precision(self):
        await self.ctx.stores.agent.update_unique(self.agent, AgentUpdate(
            structured_output_enabled=True,
            output_config={"name": "budget", "json_schema": {
                "type": "object", "required": ["record"],
                "properties": {"record": {"anyOf": [{"type": "integer"}, {"type": "string"}]}},
            }},
        ))
        record = 2 ** 53 + 1
        calls = 0

        def respond(_messages, info):
            nonlocal calls
            if not info.output_tools:
                return ModelResponse(parts=[TextPart("预算记录编号")])
            calls += 1
            return publication_result(data={"record": record if calls == 1 else str(record)})

        with patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(respond)):
            task = self.result(await self.rpc("SendMessage", {
                "message": self.message("exact-record", "返回预算记录编号"),
            }))["task"]
        self.assertEqual(calls, 2)
        self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(task["artifacts"][0]["parts"][0]["data"], {"record": str(record)})

    async def test_publication_pending_file_and_mixed_result_across_transports(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        await self.ctx.stores.agent.update_unique(self.agent, AgentUpdate(
            structured_output_enabled=True, allow_text_output=True,
            output_config={"name": "budget", "json_schema": {
                "type": "object", "required": ["total"],
                "properties": {"total": {"type": "number"}}, "additionalProperties": False,
            }},
        ))
        data = b"month,total\nNovember,20\n"
        selected_id = None

        def respond(messages, info):
            nonlocal selected_id
            if not info.output_tools:
                return ModelResponse(parts=[TextPart("两轮预算验收")])
            self.assertEqual([tool.name for tool in info.output_tools], [PUBLICATION_OUTPUT])
            if latest_tool_result(messages, "present"):
                if any(part.content == "十一月" for message in messages for part in message.parts
                       if isinstance(part, UserPromptPart)):
                    return publication_result(text="十一月差额 20", data={"total": 20})
                return publication_result("input_required", message="核对哪个月？", text="已附上原始数据。")
            if selected_id is None:
                selected_id = re.search(r"local_[0-9a-f]{32}", str(messages))[0]
            return ModelResponse(parts=[ToolCallPart("present", {"file_ids": [selected_id]})])

        initial = mcp_message("start-mixed", "请先确认月份", files=[("budget.csv", "text/csv", data)])
        with patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(respond)):
            first = self.result(await self.rpc("SendMessage", {"message": initial}))["task"]
            self.assertEqual(first["status"]["state"], "TASK_STATE_INPUT_REQUIRED", first)
            self.assertEqual([part["text"] for part in first["status"]["message"]["parts"]],
                             ["核对哪个月？", "已附上原始数据。"])
            file, = first["artifacts"]
            self.assertEqual((await self.client.get(file["parts"][0]["url"], headers=HEADERS)).content, data)
            self.client.headers.update(HEADERS)
            async with streamable_http_client(f"{DIRECT_BASE}{ROOT}/mcp", http_client=self.client) as (rx, tx, _):
                async with ClientSession(rx, tx) as client:
                    await client.initialize()
                    ack = await client.call_tool("SendMessage", {
                        "message": mcp_message("answer-mixed", "十一月", taskId=first["id"]),
                    })
                    self.assertFalse(ack.isError, ack)
                    await self.ctx.agent_tasks.wait(self.ctx.agent_tasks.entries[first["id"]])
                    checked = await client.call_tool("GetTask", {"id": first["id"], "historyLength": 0})
                    task = checked.structuredContent
                    self.assertEqual(json.loads(checked.content[0].text), task)
                    self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED", task)
                    self.assertEqual(task["contextId"], first["contextId"])
                    self.assertFalse(task.get("history"))
                    self.assertEqual(task["metadata"], first["metadata"])
                    parts = [part for artifact in task["artifacts"] for part in artifact["parts"]]
                    delivered, = [part for part in parts if "url" in part]
                    self.assertEqual((await self.client.get(delivered["url"])).content, data)
                    self.assertIn({"text": "十一月差额 20", "mediaType": "text/plain"}, parts)
                    self.assertIn({"data": {"total": 20}, "mediaType": "application/json"}, parts)
                    page = (await client.call_tool("ListTasks", {"agentCode": "budget-report",
                        "statusTimestampAfter": task["status"]["timestamp"]})).structuredContent
                    self.assertEqual(page["tasks"][0]["id"], first["id"])
                    self.assertEqual(page["nextPageToken"], "")
                    self.assertNotIn("artifacts", page["tasks"][0])

        detail = (await self.client.get(f"{ROOT}/publications/budget-report/tasks/{first['id']}", headers=self.admin)).json()
        history_url = f"/api/v1/diagnostics/sessions/{detail['session_id']}/history"
        history = await self.client.get(history_url, headers=self.admin)
        self.assertEqual(history.status_code, 200, history.text)
        outputs = [row for row in history.json()["items"] if row["kind"] == "structured_output"]
        self.assertEqual([row["publication_protocol"] for row in outputs], ["a2a", "mcp"])
        self.assertEqual([row["output"]["status"] for row in outputs], ["input_required", "completed"])
        self.assertEqual([len(row["output_files"]) for row in outputs], [1, 1])

    async def test_outbound_clients_use_same_task_for_two_replies_and_retrieve_artifacts(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        from deep_agent.remote_agents import network
        from deep_agent.remote_agents.contracts import A2AConfig, InputFile, RemoteState

        backend = SubprocessBackend(python_executable=sandbox_test_python(), max_concurrency=1)
        self.ctx.sandbox_backend = backend
        self.ctx.registry = build_registry(sandbox_backend=backend)
        await self.ctx.stores.agent.update_unique(self.agent, AgentUpdate(capabilities=["builtin:sandbox"]))
        csv = b"month,region,budget,actual\nNovember,East,100,120\n"
        extra = b"confirmed\n"
        result_csv = b"region,difference\nEast,20\n"
        evidence = b"e" * (1024 * 1024 + 1)
        script = (
            "from deepagent_files import publish_path\n"
            f"open(publish_path('result.csv'), 'wb').write({result_csv!r})\n"
            f"open(publish_path('evidence.txt'), 'wb').write(b'e' * {len(evidence)})\n"
            "for n in range(3): open(publish_path(f'draft-{n}.txt'), 'w').write('draft')"
        )
        http_client = network._http_client
        def client_factory(headers):
            client = http_client(headers)
            client._transport = ASGITransport(app=self.app)
            return client

        def respond(messages, info):
            if PUBLICATION_OUTPUT not in {tool.name for tool in info.output_tools}:
                return ModelResponse(parts=[TextPart("预算核对")])
            latest = next(part.content for message in reversed(messages) for part in reversed(message.parts)
                          if isinstance(part, UserPromptPart))
            text = latest if isinstance(latest, str) else "\n".join(part for part in latest if isinstance(part, str))
            if "开始核对" in text:
                return publication_result("input_required", message="哪个月份？")
            if "用十一月" in text:
                return publication_result("input_required", message="哪个地区？")
            if "继续解释" in text:
                return publication_result(text="仍然使用十一月和华东地区")
            if latest_tool_result(messages, "present"):
                return publication_result(text="预算差额 20")
            if generated := latest_tool_result(messages, "execute_python"):
                return ModelResponse(parts=[ToolCallPart("present", {"file_ids": [
                    item["file_id"] for item in generated.metadata["generated_files"] if item["file_name"] == "result.csv"
                ]})])
            self.assertIn("用华东", text)
            return ModelResponse(parts=[ToolCallPart("execute_python", {"code": script})])

        with (
            patch.object(network, "_http_client", side_effect=client_factory),
            patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(respond)),
        ):
            a2a_config = A2AConfig(card_url=f"http://127.0.0.1:8002{self.url}/agent-card.json", headers=HEADERS)
            inspection = await network.A2AAdapter(a2a_config).inspect()
            self.assertTrue(inspection["streaming"])
            adapter = network.A2AAdapter(a2a_config.model_copy(update={
                "approved_interface": inspection["approved_interface"],
            }))
            checkpoints = []

            async def checkpoint(state):
                checkpoints.append(state)

            first = await adapter.run(
                RemoteState(), "开始核对", [InputFile("budget.csv", "text/csv", csv)],
                "start", checkpoint, message_id="a2a-first",
            )
            self.assertEqual((first.state.status, first.text), ("input_required", "哪个月份？"))
            self.assertTrue(first.state.task_id)
            self.assertTrue(first.state.question_id)
            second = await adapter.run(
                first.state, "用十一月", [], "answer", checkpoint, message_id="a2a-second",
            )
            if second.state.status in {"submitted", "working"}:
                async with asyncio.timeout(5):
                    while second.state.status in {"submitted", "working"}:
                        await asyncio.sleep(0.05)
                        second = await adapter.refresh(second.state, checkpoint)
            self.assertEqual((second.state.status, second.text), ("input_required", "哪个地区？"))
            self.assertEqual(second.state.task_id, first.state.task_id)
            self.assertNotEqual(second.state.question_id, first.state.question_id)
            resumed = await adapter.refresh(second.state, checkpoint)
            self.assertEqual(resumed.state, second.state)
            completed = await adapter.run(
                resumed.state, "用华东", [InputFile("extra.txt", "text/plain", extra)],
                "answer", checkpoint, message_id="a2a-third",
            )
            self.assertEqual((completed.state.status, completed.text), ("completed", "预算差额 20"))
            self.assertEqual(completed.state.task_id, first.state.task_id)
            self.assertEqual(completed.state.context_id, first.state.context_id)
            self.assertEqual(completed.warnings, ())
            self.assertEqual({file.name: file.data for file in completed.files},
                             {"result.csv": result_csv})
            retrieved = await adapter.refresh(completed.state, checkpoint)
            self.assertEqual(retrieved.files, completed.files)
            continued = await adapter.run(
                completed.state, "继续解释", [], "resume", checkpoint, message_id="a2a-continued",
            )
            self.assertEqual(continued.text, "仍然使用十一月和华东地区")
            self.assertEqual(continued.state.context_id, first.state.context_id)
            self.assertNotEqual(continued.state.task_id, first.state.task_id)
            self.assertEqual(checkpoints[0].task_id, first.state.task_id)
            self.assertIn(checkpoints[0].status, {"submitted", "working"})
            entry = self.ctx.agent_tasks.entries[first.state.task_id]
            events = await self.ctx.store.list_events(entry.context.session_id, event_type="user/message")
            self.assertEqual(len(events), 4)
            self.assertIn("budget.csv", str(events[0].payload))
            self.assertIn("extra.txt", str(events[2].payload))

            self.client.headers.update(HEADERS)
            async with streamable_http_client(f"{DIRECT_BASE}{ROOT}/mcp", http_client=self.client) as (rx, tx, _):
                async with ClientSession(rx, tx) as session:
                    await session.initialize()

                    async def call(tool, args=None):
                        response = await session.call_tool(tool, args or {})
                        self.assertFalse(response.isError, response)
                        return response.structuredContent

                    async def advance(message, **extra):
                        submitted = await call("SendMessage", {"message": message, **extra})
                        task_id = submitted["id"]
                        await self.ctx.agent_tasks.wait(self.ctx.agent_tasks.entries[task_id])
                        return await call("GetTask", {"id": task_id})

                    publication = (await call("list_agents"))["agents"][0]["agent_code"]
                    first = await advance(mcp_message("mcp-first", "开始核对",
                        files=[("budget.csv", "text/csv", csv)]), agentCode=publication)
                    self.assertEqual(first["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
                    self.assertEqual(first["status"]["message"]["parts"][0]["text"], "哪个月份？")

                    second = await advance(mcp_message("mcp-second", "用十一月", taskId=first["id"]))
                    self.assertEqual(second["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
                    self.assertEqual(second["status"]["message"]["parts"][0]["text"], "哪个地区？")
                    self.assertEqual(second["id"], first["id"])

                    completed = await advance(mcp_message("mcp-third", "用华东", taskId=second["id"],
                        files=[("extra.txt", "text/plain", extra)]))
                    self.assertEqual(completed["status"]["state"], "TASK_STATE_COMPLETED")
                    self.assertEqual(completed["id"], first["id"])
                    self.assertEqual(completed["contextId"], first["contextId"])
                    self.assertTrue(any(part.get("text") == "预算差额 20"
                                        for artifact in completed["artifacts"] for part in artifact["parts"]))

                    file, = [part for artifact in completed["artifacts"] for part in artifact["parts"] if part.get("url")]
                    self.assertEqual(file["filename"], "result.csv")
                    download = await self.client.get(file["url"])
                    self.assertEqual(download.status_code, 200)
                    self.assertEqual(download.content, result_csv)

                    continued = await advance(mcp_message("mcp-continued", "继续解释", contextId=completed["contextId"]),
                                              agentCode=publication)
                    self.assertEqual(continued["contextId"], completed["contextId"])
                    self.assertNotEqual(continued["id"], completed["id"])
                    self.assertTrue(any(part.get("text") == "仍然使用十一月和华东地区"
                                        for artifact in continued["artifacts"] for part in artifact["parts"]))

                    entry = self.ctx.agent_tasks.entries[first["id"]]
                    events = await self.ctx.store.list_events(entry.context.session_id, event_type="user/message")
                    self.assertEqual(len(events), 4)
                    self.assertIn("budget.csv", str(events[0].payload))
                    self.assertIn("extra.txt", str(events[2].payload))

        recent = await self.client.get("/api/v1/remote-agent-connections/recent", headers=self.admin)
        self.assertEqual((recent.status_code, recent.json()), (200, []))

    async def test_inline_image_reaches_model_in_original_task(self):

        card = await self.client.get(f"{self.url}/agent-card.json", headers=HEADERS)
        self.assertIn("image/png", card.json()["defaultInputModes"])
        png = io.BytesIO()
        Image.new("RGBA", (32, 32), (255, 0, 0, 180)).save(png, "PNG")

        def respond(messages, _info):
            latest = next(part for part in reversed(messages[-1].parts) if isinstance(part, UserPromptPart))
            if isinstance(latest.content, str):
                return publication_result("input_required", message="请上传图片")
            images = [part for part in latest.content if isinstance(part, BinaryContent)]
            self.assertEqual([image.media_type for image in images], ["image/png"])
            with Image.open(io.BytesIO(images[0].data)) as image:
                self.assertEqual(image.convert("RGB").getpixel((0, 0)), (255, 0, 0))
            return publication_result(text="红色")

        with patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(respond)):
            paused = self.result(await self.rpc("SendMessage", {
                "message": self.message("need-image", "图片是什么颜色？"),
            }))["task"]
            self.assertEqual(paused["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
            message = {
                "messageId": "image-answer", "role": "ROLE_USER", "taskId": paused["id"],
                "contextId": paused["contextId"], "metadata": {"replyTo": paused["status"]["message"]["messageId"]},
                "parts": [{"raw": base64.b64encode(png.getvalue()).decode(),
                           "mediaType": "image/png", "filename": "color.png"}],
            }
            completed = self.result(await self.rpc("SendMessage", {"message": message}))["task"]
        self.assertEqual(completed["id"], paused["id"])
        self.assertEqual(completed["contextId"], paused["contextId"])
        self.assertEqual(completed["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(completed["artifacts"][0]["parts"][0]["text"], "红色")
        self.assertTrue(all("raw" not in part for item in completed["history"] for part in item["parts"]))

    async def test_publication_routing_api_key_and_disabled_task(self):
        original = await self.ctx.stores.agent.get_by_id(self.agent)
        agent = await self.ctx.stores.agent.create_unique(AgentCreate(
            name="Reconciliation fixture", prompt="Always use the marker RECON",
            model_id=original.model_id,
        ), user_id="fixture-admin")
        response = await self.client.post(f"{ROOT}/publications", headers=self.admin, json={
            "agent_code": "recon", "name": "对账", "agent_id": agent.id,
            "scope": {"apps": {"mode": "selected", "items": [{"app_id": "budget001", "space_id": "budget"}]}},
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["agent_code"], "recon")
        self.assertEqual(response.json()["agent_id"], agent.id)
        self.assertNotIn("code", response.json())
        self.url = f"{ROOT}/a2a/recon"
        self.assertEqual(response.json()["card_url"], f"http://127.0.0.1:8002{self.url}/agent-card.json")
        key_headers = {name: value for name, value in HEADERS.items() if name != "authorization"}
        key_headers["x-deepfos-key"] = "test-key"
        key_headers["user"] = "untrusted"
        with patch("deep_agent.agent_service.security.PlatformEnterpriseAdminChecker.resolve_api_key_user",
                   return_value="key-owner") as resolve_key:
            self.assertEqual((await self.client.get(f"{self.url}/agent-card.json", headers={
                **key_headers, "app": "budget002",
            })).status_code, 403)
            self.assertEqual((await self.client.get(f"{self.url}/agent-card.json", headers={
                **key_headers, "authorization": HEADERS["authorization"],
            })).status_code, 401)
            model = publication_model(lambda _, info: text_result(info, "对账" if "RECON" in (info.instructions or "") else "预算"))
            with patch("deep_agent.agent.factory._resolve_model", return_value=model):
                task = self.result(await self.rpc("SendMessage", {
                    "message": self.message("route", "开始"),
                }, headers=key_headers))["task"]
            self.assertEqual(task["artifacts"][0]["parts"][0]["text"], "对账")
            resolve_key.assert_awaited_with("test-key", "budget001", "budget")
            response = await self.client.patch(f"{ROOT}/publications/recon", headers=self.admin, json={
                "revision": 1, "enabled": False,
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["agent_code"], "recon")
            self.assertEqual((await self.client.get(f"{self.url}/agent-card.json", headers=key_headers)).status_code, 404)
            self.assertEqual(self.result(await self.rpc("GetTask", {"id": task["id"]}, headers=key_headers))["id"], task["id"])
            self.assertIn("error", (await self.rpc("SendMessage", {
                "message": self.message("disabled", "再算一次"),
            }, headers=key_headers)).json())
        response = await self.client.get(f"{ROOT}/publications", headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        cards = {row["agent_code"]: row["card_url"] for row in response.json()}
        self.assertEqual(cards, {"budget-report": f"{DIRECT_BASE}{ROOT}/a2a/budget-report/agent-card.json", "recon": None})
        response = await self.client.patch(f"{ROOT}/publications/recon", headers=self.admin, json={
            "revision": 2, "enabled": True,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["enabled"])
        self.assertEqual(response.json()["card_url"], f"{DIRECT_BASE}{ROOT}/a2a/recon/agent-card.json")

    async def test_app_routes_share_card_scope_and_task_ownership(self):
        app_url = f"{self.url}/apps/budget001"
        card_path = "/agent-card.json"
        token = {key: HEADERS[key] for key in ("authorization", "A2A-Version")}
        missing_app = await self.client.get(f"{self.url}{card_path}", headers=token, params={"app_id": "budget001"})
        self.assertEqual(missing_app.status_code, 400)
        self.assertIn("app Header", missing_app.json()["detail"])

        mounted = FastAPI()
        mounted.mount("/gateway", self.app)
        async with AsyncClient(transport=ASGITransport(app=mounted), base_url="https://example.test/gateway") as gateway:
            publication = (await gateway.get(f"{ROOT}/publications/budget-report", headers=self.admin)).json()
            self.assertEqual(publication["app_card_url_template"], f"https://example.test/gateway{self.url}/apps/{{app_id}}{card_path}")
            generic = await gateway.get(f"{self.url}{card_path}", headers=HEADERS)
            scoped = await gateway.get(f"{app_url}{card_path}", headers=[
                *token.items(), ("app", "budget002"), ("app", "budget003"), ("space", "unrelated"),
            ])
            self.assertEqual(generic.status_code, 200, generic.text)
            self.assertEqual(scoped.status_code, 200, scoped.text)
            generic_card, scoped_card = generic.json(), scoped.json()
            parse_agent_card(scoped_card)
            self.assertEqual(scoped_card["supportedInterfaces"][0]["url"], f"https://example.test/gateway{app_url}")
            generic_card["supportedInterfaces"][0]["url"] = scoped_card["supportedInterfaces"][0]["url"]
            self.assertEqual(generic_card, scoped_card)
            for url, headers, expected in ((self.url, HEADERS, generic.json()), (app_url, token, scoped_card)):
                discovery = await gateway.get(f"{url}/.well-known/agent-card.json", headers=headers)
                self.assertEqual(discovery.json(), expected)

        no_credentials = await self.client.get(f"{app_url}{card_path}")
        self.assertEqual(no_credentials.status_code, 401)
        self.assertEqual((await self.client.get(f"{self.url}/apps/bad-id{card_path}", headers=token)).status_code, 400)
        denied = await self.client.get(f"{self.url}/apps/budget003{card_path}", headers=HEADERS)
        self.assertEqual(denied.status_code, 403)
        rejected_scope = await self.client.patch(f"{ROOT}/publications/budget-report", headers=self.admin, json={
            "revision": 1, "scope": {"apps": {"mode": "fixed", "items": [{"app_id": "budget001", "space_id": "budget"}]}},
        })
        self.assertEqual(rejected_scope.status_code, 422)

        with patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(
            lambda _, info: text_result(info, "预算差异 20"),
        )):
            task = self.result(await self.rpc("SendMessage", {
                "message": self.message("app-route", "开始"),
            }, headers=token, url=app_url))["task"]
        self.assertEqual(self.result(await self.rpc("GetTask", {"id": task["id"]}))["id"], task["id"])
        scoped_task = self.result(await self.rpc("GetTask", {"id": task["id"]}, headers=token, url=app_url))
        self.assertEqual(scoped_task["contextId"], task["contextId"])
        denied_task = await self.rpc("GetTask", {"id": task["id"]}, headers=token, url=f"{self.url}/apps/budget002")
        self.assertIn("error", denied_task.json())
        other_user = await self.rpc("GetTask", {"id": task["id"]}, headers={
            **token, "authorization": "Bearer fixture-other",
        }, url=app_url)
        self.assertIn("error", other_user.json())
        self.revoked = True
        self.assertEqual((await self.rpc("GetTask", {"id": task["id"]}, headers=token, url=app_url)).status_code, 403)

    async def test_app_route_accepts_app_bound_api_key(self):
        app_url = f"{self.url}/apps/budget001"
        with patch("deep_agent.agent_service.security.PlatformEnterpriseAdminChecker.resolve_api_key_user",
                   return_value="key-owner") as resolve_key:
            response = await self.client.get(f"{app_url}/agent-card.json", headers={"x-deepfos-key": "test-key"})
        self.assertEqual(response.status_code, 200, response.text)
        resolve_key.assert_awaited_once_with("test-key", "budget001", "budget")

    async def test_all_apps_scope_still_checks_platform_access_and_filters_recent_tasks(self):
        publication = f"{ROOT}/publications/budget-report"
        card_url = f"{self.url}/agent-card.json"
        app = {"app_id": "budget001", "space_id": "budget"}
        outside = {**HEADERS, "app": "budget003"}
        scope = {"apps": {"mode": "all", "items": []}}
        self.assertEqual((await self.client.get(card_url, headers=outside)).status_code, 403)
        response = await self.client.patch(publication, headers=self.admin, json={
            "revision": 1, "scope": {"apps": {"mode": "all", "items": [app]}},
        })
        self.assertEqual(response.status_code, 422)
        response = await self.client.post(f"{ROOT}/publications", headers=self.admin, json={
            "agent_code": "another", "name": "另一个发布", "agent_id": self.agent, "scope": scope,
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["scope"], scope)
        response = await self.client.patch(publication, headers=self.admin, json={
            "revision": 1, "scope": scope,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["scope"], scope)
        self.assertEqual((await self.client.get(card_url, headers=outside)).status_code, 200)
        token_only = {"authorization": HEADERS["authorization"]}
        self.assertEqual((await self.client.get(card_url, headers=token_only)).status_code, 400)
        self.revoked = True
        self.assertEqual((await self.client.get(card_url, headers=outside)).status_code, 403)
        self.revoked = False

        model = publication_model(lambda _, info: text_result(info, "预算差异 20"))
        with patch("deep_agent.agent.factory._resolve_model", return_value=model):
            for app_id in ("budget001", "budget002"):
                self.result(await self.rpc("SendMessage", {
                    "message": self.message(app_id, f"查看 {app_id}"),
                }, headers={**HEADERS, "app": app_id}))

        for params, count in (({}, 2), ({"app_id": "budget001"}, 1), ({"app_id": "budget001", "q": "budget002"}, 0)):
            with self.subTest(params=params):
                response = await self.client.get(f"{publication}/tasks", headers=self.admin, params=params)
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["total"], count)
                self.assertEqual(result["app_ids"], ["budget001", "budget002"])
                if params.get("app_id") and count:
                    self.assertEqual(result["items"][0]["app_id"], params["app_id"])

        response = await self.client.get(f"{publication}/tasks", headers={
            **self.admin, "X-Deep-Agent-App": "budget001",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertCountEqual([entry["app_id"] for entry in response.json()["items"]], ["budget001", "budget002"])

        response = await self.client.patch(publication, headers=self.admin, json={
            "revision": 2, "scope": {"apps": {"mode": "selected", "items": [app]}},
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self.client.get(card_url, headers=HEADERS)).status_code, 200)
        self.assertEqual((await self.client.get(card_url, headers=outside)).status_code, 403)

    async def test_publication_save_does_not_require_app_directory_access(self):
        headers = {"user": "fixture-admin"}
        scope = {"apps": {"mode": "selected", "items": [{"app_id": "budget001", "space_id": "budget"}]}}
        agent = await self.ctx.stores.agent.create_unique(AgentCreate(
            name="Unconfigured model fixture", prompt="Test publication save and runtime validation.",
        ), user_id="fixture-admin")
        listing = await self.client.get("/api/v1/agents", headers=headers, params={"q": agent.name})
        self.assertEqual(listing.status_code, 200, listing.text)
        summary, = listing.json()
        self.assertEqual((summary["id"], summary["name"]), (agent.id, agent.name))
        self.assertNotIn("prompt", summary)
        self.assertNotIn("updated_by_user", summary)
        with patch(
            "deep_agent.api.v1.agent_service.IntegrationAppDirectory.available_apps",
            side_effect=IntegrationAppAccessError(401, "请使用平台登录身份验证应用权限。"),
        ) as directory:
            created = await self.client.post(f"{ROOT}/publications", headers=headers, json={
                "agent_code": "selected-apps", "name": "指定应用", "agent_id": summary["id"], "scope": scope,
            })
            self.assertEqual(created.status_code, 201, created.text)
            response = await self.client.patch(f"{ROOT}/publications/selected-apps", headers=headers, json={
                "revision": created.json()["revision"], "name": "更新指定应用", "scope": scope,
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["scope"], scope)
            directory.assert_not_awaited()

            response = await self.client.get(f"{ROOT}/applications", headers=self.admin)
            self.assertEqual(response.status_code, 401, response.text)
            directory.assert_awaited_once()

        with patch("deep_agent.diagnostics.emit") as emit:
            result = self.result(await self.rpc("SendMessage", {
                "message": self.message("unconfigured-model", "测试执行"),
            }, url=f"{ROOT}/a2a/selected-apps"))["task"]
        self.assertEqual(result["status"]["state"], "TASK_STATE_FAILED")
        self.assertEqual(result["status"]["message"]["parts"][0]["text"], "Agent 尚未配置模型")
        events = [call.kwargs for call in emit.call_args_list if call.args == ("published_agent_task",)]
        self.assertEqual([event["status"] for event in events], ["submitted", "working", "failed"])
        entry = self.ctx.agent_tasks.entries[result["id"]]
        self.assertTrue(all(event["task_id"] == result["id"] and event["protocol"] == "a2a" and
                            event["session_id"] == entry.context.session_id and event["agent_id"] == agent.id and
                            event["publication_id"] == entry.context.owner.publication_id for event in events))
        self.assertEqual(events[-1]["reason"], "model_not_configured")
        self.assertEqual(events[-1]["error_type"], "AgentModelNotConfiguredError")
        self.assertGreaterEqual(events[-1]["duration_ms"], 0)
        self.assertNotIn("测试执行", str(events))

    async def test_download_document_evidence_from_published_agent(self):
        from tests.unit.test_document_layout_tool import original
        from tests.unit.test_document_tools import pdf

        await self.ctx.stores.agent.update_unique(self.agent, AgentUpdate(capabilities=["builtin:document"]))
        await self.ctx.stores.global_setting.upsert(
            "document_parsing", value={"mode": "provider", "provider": "glm", "api_key": "fixture"}, version=1,
        )
        self.ctx.registry = build_registry()
        exported_files = []

        def respond(messages, info):
            if any(tool.name == "parse_document" for tool in info.function_tools):
                if latest_tool_result(messages, "present"):
                    return publication_result(text="证据已导出")
                if parsed := latest_tool_result(messages, "parse_document"):
                    file = parsed.content["export"]["file"]
                    exported_files.append(file)
                    return ModelResponse(parts=[ToolCallPart("present", {"file_ids": [file["file_id"]]})])
                file_id = re.search(r"local_[0-9a-f]{32}", str(messages))[0]
                return ModelResponse(parts=[ToolCallPart("parse_document", {
                    "resource": {"type": "attachment", "file_id": file_id}, "export_file": True,
                })])
            return ModelResponse(parts=[TextPart("导出文档证据")])

        message = self.message("parse", "解析附件并导出文档证据")
        message["parts"].append({
            "raw": base64.b64encode(pdf(4)).decode(), "mediaType": "application/pdf", "filename": "sample.pdf",
        })
        with (
            patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(respond)),
            patch("deep_agent.documents.toolset.parse_with_provider", AsyncMock(return_value=original())),
        ):
            task = self.result(await self.rpc("SendMessage", {"message": message}))["task"]
            with patch("deep_agent.agent_service.tasks.MAX_ARTIFACTS", 1):
                over_limit = self.result(await self.rpc("SendMessage", {
                    "message": {**message, "messageId": "parse-limit"},
                }))["task"]
        self.assertEqual(over_limit["status"]["state"], "TASK_STATE_FAILED", over_limit)
        self.assertIn("产物超过", over_limit["status"]["message"]["parts"][0]["text"])
        self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED", task)
        file = exported_files[0]
        artifact, = [part for item in task["artifacts"] for part in item["parts"] if "url" in part]
        self.assertEqual((artifact["filename"], artifact["mediaType"]), (file["file_name"], "application/zip"))
        response = await self.client.get(artifact["url"], headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), file["sha256"])

    async def test_download_xlsx_published_by_python_sandbox(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from openpyxl import load_workbook

        backend = SubprocessBackend(python_executable=sandbox_test_python(), max_concurrency=1)
        self.ctx.sandbox_backend = backend
        self.ctx.registry = build_registry(sandbox_backend=backend)
        await self.ctx.stores.agent.update_unique(self.agent, AgentUpdate(capabilities=["builtin:sandbox"]))
        script = """
from openpyxl import Workbook
from deepagent_files import publish_path
w = Workbook()
w.active.append(["预算", "实际", "差额"])
w.active.append([100, 120, 20])
w.save(publish_path("预算.xlsx"))
"""

        def respond(messages, info):
            if not any(tool.name == "execute_python" for tool in info.function_tools):
                return ModelResponse(parts=[TextPart("预算差异报告")])
            if latest_tool_result(messages, "present"):
                return publication_result(text="差异 20")
            if generated := latest_tool_result(messages, "execute_python"):
                return ModelResponse(parts=[ToolCallPart("present", {"file_ids": [
                    item["file_id"] for item in generated.metadata["generated_files"]
                ]})])
            return ModelResponse(parts=[ToolCallPart("execute_python", {"code": script})])

        app_url = f"{self.url}/apps/budget001"
        token = {key: HEADERS[key] for key in ("authorization", "A2A-Version")}
        with patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(respond)):
            task = self.result(await self.rpc("SendMessage", {
                "message": self.message("xlsx", "输出预算报告 Excel"),
            }, headers=token, url=app_url))["task"]
        self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED", task)
        artifacts = [part for artifact in task["artifacts"] for part in artifact["parts"] if "url" in part]
        self.assertEqual(len(artifacts), 1, task)
        artifact = artifacts[0]
        self.assertIn(f"{app_url}/tasks/", artifact["url"])
        response = await self.client.get(artifact["url"], headers=token)
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertEqual(response.headers["content-type"], artifact["mediaType"])
        self.assertIn("%E9%A2%84%E7%AE%97.xlsx", response.headers["content-disposition"])
        book = load_workbook(io.BytesIO(response.content))
        self.assertEqual(book.active["C2"].value, 20)
        artifact_id = next(item["artifactId"] for item in task["artifacts"] if artifact in item["parts"])
        self.client.headers.update(HEADERS)
        async with streamable_http_client(f"{DIRECT_BASE}{ROOT}/mcp", http_client=self.client) as (rx, tx, _):
            async with ClientSession(rx, tx) as session:
                await session.initialize()
                tool = await session.call_tool("GetTask", {"id": task["id"]})
                self.assertFalse(tool.isError)
                file = next(item for item in tool.structuredContent["artifacts"] if item["artifactId"] == artifact_id)["parts"][0]
                self.assertEqual(file["mediaType"], artifact["mediaType"])
        download_url = file["url"]
        self.assertEqual((await self.client.get(download_url)).content, response.content)
        self.assertEqual((await self.client.get(download_url, headers={
            **HEADERS, "authorization": "Bearer fixture-other",
        })).status_code, 404)
        generic_task = self.result(await self.rpc("GetTask", {"id": task["id"]}))
        generic_url = next(part["url"] for item in generic_task["artifacts"] for part in item["parts"] if "url" in part)
        self.assertIn(f"{self.url}/tasks/", generic_url)
        self.assertEqual((await self.client.get(generic_url, headers=HEADERS)).content, response.content)
        for url, headers, expected in ((self.url, HEADERS, generic_url), (app_url, token, artifact["url"])):
            page = self.result(await self.rpc("ListTasks", {"includeArtifacts": True}, url=url, headers=headers))
            self.assertEqual(page["tasks"][0]["id"], task["id"])
            self.assertIn(expected, [part.get("url") for item in page["tasks"][0]["artifacts"] for part in item["parts"]])
        denied = await self.client.get(generic_url, headers={**HEADERS, "app": "budget002"})
        self.assertEqual(denied.status_code, 404)
        self.revoked = True
        self.assertEqual((await self.client.get(artifact["url"], headers=HEADERS)).status_code, 403)
        self.assertEqual((await self.client.get(download_url, headers=HEADERS)).status_code, 403)

    async def test_mcp_download_empty_large_and_authorization(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        files = {"empty": b"", "boundary": b"a" * (20 * 1024 * 1024), "large": b"b" * (20 * 1024 * 1024 + 1)}
        headers = {"user": "owner", "app": "budget001", "space": "budget"}
        artifacts = {}
        for key, data in files.items():
            filename = f"{key}.txt"
            stored = await self.ctx.file_store.upload(
                file_type="attachment", file_name=filename, mime="text/plain", data=data, request_headers=headers,
            )
            artifacts[key] = ArtifactFile(stored.id, filename, "text/plain", len(data))

        async def execute(_entry, _message, _continuing):
            return Result(
                artifacts=[wire.Artifact(artifact_id=key, parts=[wire.Part(filename=file.name)])
                           for key, file in artifacts.items()],
                files=artifacts,
            )

        with patch("deep_agent.agent_service.protocol.RunnerExecution", return_value=execute):
            task_id = self.result(await self.rpc("SendMessage", {
                "message": self.message("artifact-boundaries", "生成文件"),
            }))["task"]["id"]
        self.client.headers.update(HEADERS)
        async with streamable_http_client(f"{DIRECT_BASE}{ROOT}/mcp", http_client=self.client) as (rx, tx, _):
            async with ClientSession(rx, tx) as session:
                await session.initialize()

                async def fetch():
                    return await session.call_tool("GetTask", {"id": task_id})

                async def fetch_artifact(key, **options):
                    return await session.call_tool("get_artifact", {
                        "task_id": task_id, "artifact_id": key, **options,
                    })

                result = await fetch()
                self.assertFalse(result.isError, result.structuredContent)
                self.assertEqual([item.type for item in result.content], ["text"])
                self.assertEqual(json.loads(result.content[0].text), result.structuredContent)
                download_urls = {}
                for file in result.structuredContent["artifacts"]:
                    key = file["artifactId"]
                    url = f"http://127.0.0.1:8002{ROOT}/tasks/{task_id}/artifacts/{key}/download"
                    download_urls[key] = url
                    self.assertEqual(file["parts"], [{"filename": f"{key}.txt", "url": url}])
                    download = await self.client.get(url)
                    self.assertEqual(download.status_code, 200)
                    self.assertEqual(download.content, files[key])
                self.assertEqual(download_urls.keys(), files.keys())
                for key, url in download_urls.items():
                    default = await fetch_artifact(key)
                    self.assertFalse(default.isError)
                    self.assertEqual(json.loads(default.content[0].text), default.structuredContent)
                    self.assertEqual(default.structuredContent["delivery"], "http_download")
                    self.assertEqual(default.structuredContent["download_url"], url)
                    self.assertNotIn("data_base64", default.structuredContent)

                    tool = await fetch_artifact(key, format="base64")
                    self.assertFalse(tool.isError)
                    self.assertEqual([item.type for item in tool.content], ["text"])
                    self.assertNotIn("data_base64", tool.structuredContent)
                    payload = json.loads(tool.content[0].text)
                    self.assertEqual(payload["size"], len(files[key]))
                    if key == "large":
                        self.assertEqual(payload, default.structuredContent)
                    else:
                        self.assertEqual(payload["delivery"], "inline")
                        decoded = base64.b64decode(payload["data_base64"], validate=True)
                        self.assertEqual(hashlib.sha256(decoded).digest(), hashlib.sha256(files[key]).digest())
                explicit_url = await fetch_artifact("empty", format="url")
                self.assertEqual(explicit_url.structuredContent["download_url"], download_urls["empty"])
                self.assertEqual((await fetch_artifact("empty", format="invalid")).structuredContent["status"], 422)

                gateway_headers = {"host": BACKEND_AUTHORITY}
                self.client.headers.update(gateway_headers)
                try:
                    result = await fetch()
                    over_limit = await fetch_artifact("large", format="base64")
                finally:
                    for name in gateway_headers:
                        del self.client.headers[name]
                self.assertEqual({file["parts"][0]["url"] for file in result.structuredContent["artifacts"]}, {
                    f"https://gateway.example.test{GATEWAY_PREFIX}{ROOT}/tasks/{task_id}/artifacts/{key}/download"
                    for key in files
                })
                self.assertEqual(over_limit.structuredContent["download_url"],
                                 f"https://gateway.example.test{GATEWAY_PREFIX}{ROOT}/tasks/{task_id}/artifacts/large/download")
                self.assertEqual(self.realm_queries, 1)
                self.client.headers["host"] = "invalid-host.test"
                try:
                    result = await fetch()
                    self.assertEqual((await fetch_artifact("empty")).structuredContent["status"], 503)
                    self.assertEqual((await fetch_artifact("large", format="base64")).structuredContent["status"], 503)
                    self.assertFalse((await fetch_artifact("empty", format="base64")).isError)
                finally:
                    del self.client.headers["host"]
                self.assertEqual(result.structuredContent["status"]["state"], "TASK_STATE_COMPLETED")
                self.assertTrue(all("url" not in file["parts"][0] for file in result.structuredContent["artifacts"]))

                for wrong in ({"app": "budget002"}, {"authorization": "Bearer fixture-other"}):
                    try:
                        self.client.headers.update(wrong)
                        denied = await fetch()
                        self.assertTrue(denied.isError)
                        self.assertEqual(denied.structuredContent["status"], 404)
                        self.assertEqual((await fetch_artifact("empty")).structuredContent["status"], 404)
                        self.assertEqual((await self.client.get(download_urls["large"])).status_code, 404)
                    finally:
                        self.client.headers.update(HEADERS)
                await self.ctx.file_store.delete(artifacts["empty"].id, headers)
                with patch("deep_agent.diagnostics.emit") as emit:
                    self.assertEqual((await self.client.get(download_urls["empty"])).status_code, 404)
                    self.assertEqual((await fetch_artifact("empty", format="base64")).structuredContent["status"], 404)
                events = [call.kwargs for call in emit.call_args_list if call.args == ("published_agent_task",)]
                self.assertEqual(len(events), 2)
                for event in events:
                    self.assertEqual((event["phase"], event["protocol"], event["task_id"], event["reason"]),
                                     ("artifact_read", "mcp", task_id, "artifact_unavailable"))
                    self.assertNotIn("empty.txt", str(event))
                short_file = io.BytesIO(b"one chunk")
                with patch.object(self.ctx.file_store, "open_download", return_value=short_file), \
                     patch("deep_agent.diagnostics.emit") as emit:
                    failed = await fetch_artifact("boundary", format="base64")
                self.assertTrue(short_file.closed)
                self.assertTrue(failed.isError)
                self.assertEqual(failed.structuredContent["status"], 502)
                reads = [call.kwargs for call in emit.call_args_list if call.args == ("published_agent_task",)]
                self.assertEqual([(row["protocol"], row["reason"], row["error_type"]) for row in reads],
                                 [("mcp", "artifact_read_failed", "FileStoreIntegrityError")])
                self.ctx.agent_tasks.entries[task_id].changed -= RETENTION + timedelta(seconds=1)
                expired = await fetch()
                self.assertTrue(expired.isError)
                self.assertEqual(expired.structuredContent["status"], 404)
                self.assertEqual((await fetch_artifact("boundary")).structuredContent["status"], 404)
                self.assertEqual((await self.client.get(download_urls["large"])).status_code, 404)

    async def test_download_read_failure_and_disconnect_after_headers(self):
        class BrokenFile(io.BytesIO):
            def read(self, size):
                if self.tell() > 0:
                    raise OSError("private storage path")
                return super().read(size)

        artifact = ArtifactFile("test-id", "test.bin", "application/octet-stream", 10)
        scope = {"type": "http", "asgi": {"spec_version": "2.3"}}
        for handle, expected_error in ((BrokenFile(b"x"), "OSError"),
                                       (io.BytesIO(b"x"), "FileStoreIntegrityError"),
                                       (io.BytesIO(b"x" * 10), None)):
            with self.subTest(expected_error=expected_error):
                entry = Mock()
                response = ArtifactDownloadResponse(entry, artifact, handle, protocol="a2a")
                messages = []
                disconnect = asyncio.Event()

                async def receive():
                    await disconnect.wait()
                    return {"type": "http.disconnect"}

                async def send(message):
                    messages.append(message)
                    if expected_error is None and message["type"] == "http.response.body":
                        disconnect.set()
                        await asyncio.Event().wait()

                if expected_error:
                    with self.assertRaises(Exception):
                        await response(scope, receive, send)
                    entry.diagnose.assert_called_once()
                    event = entry.diagnose.call_args.kwargs
                    self.assertEqual((event["phase"], event["protocol"], event["error_type"], event["reason"]),
                                     ("artifact_read", "a2a", expected_error, "artifact_read_failed"))
                    self.assertNotIn("private storage path", str(event))
                else:
                    await response(scope, receive, send)
                    entry.diagnose.assert_not_called()
                self.assertTrue(handle.closed)
                self.assertEqual(messages[0]["status"], 200)
                self.assertTrue(messages[1]["body"])
                self.assertTrue(all(m.get("more_body", True) for m in messages))

    async def test_file_events_use_each_subscriber_address(self):
        done = asyncio.Event()
        app_url = f"{self.url}/apps/budget001"
        token = {key: HEADERS[key] for key in ("authorization", "A2A-Version")}
        data = b"A2A streaming file\n"
        stored = await self.ctx.file_store.upload(
            file_type="attachment", file_name="test.txt", mime="text/plain", data=data,
            request_headers={"user": "owner", "app": "budget001", "space": "budget"},
        )

        async def execute(_entry, _message, _continuing):
            await done.wait()
            return Result(
                artifacts=[wire.Artifact(artifact_id="file", parts=[wire.Part(filename="test.txt")])],
                files={"file": ArtifactFile(stored.id, "test.txt", "text/plain", len(data))},
            )

        with patch("deep_agent.agent_service.protocol.RunnerExecution", return_value=execute):
            send = asyncio.create_task(self.rpc("SendStreamingMessage", {
                "message": self.message("stream-file", "生成文件"),
            }))
            subscribe = None
            try:
                async with asyncio.timeout(3):
                    while not self.ctx.agent_tasks or not self.ctx.agent_tasks.entries:
                        await asyncio.sleep(0.01)
                    entry = next(iter(self.ctx.agent_tasks.entries.values()))
                    subscribe = asyncio.create_task(self.rpc("SubscribeToTask", {"id": entry.task.id}, url=app_url, headers=token))
                    while len(entry.subscribers) < 2:
                        await asyncio.sleep(0.01)
            finally:
                done.set()
                responses = await asyncio.gather(*(task for task in (send, subscribe) if task is not None))
            for response, url, headers in zip(responses, (self.url, app_url), (HEADERS, token), strict=True):
                self.assertEqual(response.status_code, 200, response.text)
                frames = [json.loads(line[6:])["result"] for line in response.text.splitlines() if line.startswith("data: ")]
                artifact = next(frame["artifactUpdate"]["artifact"] for frame in frames if "artifactUpdate" in frame)
                download_url = artifact["parts"][0]["url"]
                self.assertEqual(download_url, f"http://127.0.0.1:8002{url}/tasks/{entry.task.id}/artifacts/file/download")
                downloaded = await self.client.get(download_url, headers=headers)
                self.assertEqual(downloaded.status_code, 200)
                self.assertEqual(hashlib.sha256(downloaded.content).digest(), hashlib.sha256(data).digest())
                denied = await self.client.get(download_url, headers={**headers, "authorization": "Bearer fixture-other"})
                self.assertEqual(denied.status_code, 404)
            snapshot = self.result(await self.rpc("GetTask", {"id": entry.task.id}, url=app_url, headers=token))
            self.assertTrue(snapshot["artifacts"][0]["parts"][0]["url"].endswith(
                f"{app_url}/tasks/{entry.task.id}/artifacts/file/download",
            ))
            self.assertFalse(entry.task.artifacts[0].parts[0].HasField("url"))

    async def test_cancel_while_runner_is_still_preparing(self):
        preparing = asyncio.Event()
        stopped = asyncio.Event()

        async def execute(_entry, _message, _continuing):
            preparing.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        with patch("deep_agent.agent_service.protocol.RunnerExecution", return_value=execute):
            task = self.result(await self.rpc("SendMessage", {
                "message": self.message("preparing", "准备运行"),
                "configuration": {"returnImmediately": True},
            }))["task"]
            await asyncio.wait_for(preparing.wait(), 2)
            response = await asyncio.wait_for(self.rpc("CancelTask", {"id": task["id"]}), 2)
            self.assertTrue(stopped.is_set())
            self.assertEqual(self.result(response)["status"]["state"], "TASK_STATE_CANCELED")

    async def test_stream_and_subscribe_cancel(self):
        ready = asyncio.Event()
        done = asyncio.Event()

        async def execute(entry, _message, _continuing):
            entry.stop = done.set
            ready.set()
            await done.wait()
            return Result(state=wire.TaskState.TASK_STATE_CANCELED if entry.cancellation_requested
                          else wire.TaskState.TASK_STATE_COMPLETED)

        def events(response):
            return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

        with patch("deep_agent.agent_service.protocol.RunnerExecution", return_value=execute):
            task = self.result(await self.rpc("SendMessage", {
                "message": self.message("pending", "慢任务"), "configuration": {"returnImmediately": True},
            }))["task"]
            await asyncio.wait_for(ready.wait(), 2)
            subscribers = [asyncio.create_task(self.rpc("SubscribeToTask", {"id": task["id"]})) for _ in range(2)]
            for _ in range(100):
                entry = self.ctx.agent_tasks.entries[task["id"]]
                if len(entry.subscribers) == 2:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(len(entry.subscribers), 2)
            cancelled = self.result(await self.rpc("CancelTask", {"id": task["id"]}))
            self.assertEqual(cancelled["status"]["state"], "TASK_STATE_CANCELED")
            for response in await asyncio.gather(*subscribers):
                self.assertEqual(events(response)[0]["result"]["task"]["id"], task["id"])
                self.assertEqual(events(response)[-1]["result"]["statusUpdate"]["status"]["state"], "TASK_STATE_CANCELED")
            self.assertEqual(self.result(await self.rpc("CancelTask", {"id": task["id"]}))["id"], task["id"])
        with patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(
            lambda _, info: text_result(info, "流式报告"),
        )):
            response = await self.rpc("SendStreamingMessage", {"message": self.message("stream", "生成报告")})
        self.assertIn("text/event-stream", response.headers.get("content-type", ""), response.text)
        frames = events(response)
        self.assertIn("task", frames[0]["result"])
        self.assertTrue(any("artifactUpdate" in frame["result"] for frame in frames))
        self.assertEqual(frames[-1]["result"]["statusUpdate"]["status"]["state"], "TASK_STATE_COMPLETED")

    async def test_only_a2a_mcp_and_mcp_download_are_exposed(self):
        config = await self.client.get(f"{ROOT}/client-config", headers=self.admin)
        self.assertEqual(config.json(), {"mcp_url": f"http://127.0.0.1:8002{ROOT}/mcp"})
        self.assertIn((await self.client.get(f"{ROOT}/client-config")).status_code, (401, 403))
        self.assertEqual((await self.client.get(f"{ROOT}/client-config", headers={
            **self.admin, "Host": "invalid-host.test",
        })).json(), {"mcp_url": None})
        for method, path in (
            ("GET", "/agents"), ("POST", "/agents/budget-report/tasks"),
            ("GET", "/agents/budget-report/tasks"), ("GET", "/tasks/" + "a" * 32),
            ("POST", "/tasks/" + "a" * 32 + "/messages"), ("POST", "/tasks/" + "a" * 32 + "/cancel"),
            ("GET", "/client-packages/deepfos_agent_client-0.17.0a3-py3-none-any.whl"),
        ):
            response = await self.client.request(method, ROOT + path, headers=HEADERS)
            self.assertEqual((response.status_code, response.json()), (404, {"detail": "Not Found"}), path)
        artifact = f"{ROOT}/tasks/{'a' * 32}/artifacts/{'b' * 32}/download"
        self.assertEqual((await self.client.get(artifact)).status_code, 400)

    async def test_gateway_returns_public_card_and_mcp_urls_and_checks_origin(self):
        gateway = {
            "X-Forwarded-Prefix": f"/elsewhere, {GATEWAY_PREFIX}",
            "X-Forwarded-Host": "ignored.invalid, gateway.example.test:443",
            "X-Forwarded-Proto": "http, https",
        }
        base = f"https://gateway.example.test{GATEWAY_PREFIX}"
        config = await self.client.get(f"{ROOT}/client-config", headers={**self.admin, **gateway})
        self.assertEqual(config.json(), {"mcp_url": f"{base}{ROOT}/mcp"})
        card = await self.client.get(f"{self.url}/agent-card.json", headers={**HEADERS, **gateway})
        self.assertEqual(card.json()["supportedInterfaces"][0]["url"], f"{base}{self.url}")
        for prefix in ("/deepfos-server", "/deepfos-server, /deep-agent-server1-0"):
            config = await self.client.get(f"{ROOT}/client-config", headers={
                **self.admin, **gateway, "X-Forwarded-Prefix": prefix,
            })
            self.assertEqual(config.json(), {"mcp_url": f"{base}{ROOT}/mcp"})
        for origin, status in (("https://gateway.example.test", 200), ("https://ignored.invalid", 403)):
            response = await self.client.post(f"{ROOT}/mcp", headers={
                **HEADERS, **gateway, "Origin": origin, "Accept": "application/json, text/event-stream",
            }, json={"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
            self.assertEqual(response.status_code, status, response.text)
        unrecognized = {**gateway, "X-Forwarded-Prefix": "/elsewhere"}
        config = await self.client.get(f"{ROOT}/client-config", headers={**self.admin, **unrecognized})
        self.assertEqual(config.json(), {"mcp_url": f"{DIRECT_BASE}{ROOT}/mcp"})
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="https://[fd00::42]:8010") as direct:
            config = await direct.get(f"{ROOT}/client-config", headers=self.admin)
            self.assertEqual(config.json(), {"mcp_url": f"https://[fd00::42]:8010{ROOT}/mcp"})
        self.assertEqual(self.realm_queries, 0)

    async def test_rewritten_gateway_host_uses_system_realm_and_rejects_different_origin(self):
        internal = {"Host": BACKEND_AUTHORITY}
        public = "https://gateway.example.test"
        base = f"{public}{GATEWAY_PREFIX}"
        config, views = await asyncio.gather(
            self.client.get(f"{ROOT}/client-config", headers={**self.admin, **internal}),
            self.client.get(f"{ROOT}/publications", headers={**self.admin, **internal}),
        )
        self.assertEqual(config.json(), {"mcp_url": f"{base}{ROOT}/mcp"})
        self.assertEqual(views.json()[0]["card_url"], f"{base}{self.url}/agent-card.json")
        for path in (self.url, f"{self.url}/apps/budget001"):
            card = await self.client.get(f"{path}/agent-card.json", headers={**HEADERS, **internal})
            self.assertEqual(card.json()["supportedInterfaces"][0]["url"], f"{base}{path}")
        for origin, expected in ((public, 200), (f"http://{BACKEND_AUTHORITY}", 403)):
            response = await self.client.post(f"{ROOT}/mcp", headers={
                **HEADERS, **internal, "Origin": origin, "Accept": "application/json, text/event-stream",
            }, json={"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
            self.assertEqual(response.status_code, expected)
        self.assertEqual(self.realm_queries, 1)

    async def test_invalid_system_realm_does_not_advertise_internal_service(self):
        self.platform_realm = "https://user:secret@gateway.example.test"
        internal = {"Host": BACKEND_AUTHORITY}
        config = await self.client.get(f"{ROOT}/client-config", headers={**self.admin, **internal})
        self.assertEqual(config.json(), {"mcp_url": None})
        card = await self.client.get(f"{self.url}/agent-card.json", headers={**HEADERS, **internal})
        self.assertEqual(card.status_code, 503)
        self.assertNotIn("secret", card.text)
        self.platform_realm = "https://gateway.example.test"
        config = await self.client.get(f"{ROOT}/client-config", headers={**self.admin, **internal})
        self.assertEqual(config.json(), {"mcp_url": f"https://gateway.example.test{GATEWAY_PREFIX}{ROOT}/mcp"})

    async def test_public_task_visibility_and_cross_transport_mcp(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from deep_agent.api.v1.agent_service_public import draft_router

        self.app.include_router(draft_router, prefix="/api/v1")

        directory = f"{ROOT}/agents"
        alternate = {"authorization": "Bearer fixture-other", "app": "budget003"}
        without_app = {"authorization": HEADERS["authorization"]}
        self.assertEqual((await self.client.get(directory)).status_code, 401)
        response = await self.client.get(directory, headers=without_app)
        self.assertEqual(response.json(), {"agents": [{
            "agent_code": "budget-report", "name": "预算报告", "description": "",
        }]})
        self.assertEqual((await self.client.get(directory, headers=alternate)).json(), response.json())
        self.assertEqual((await self.client.get(directory, headers={"X-Deepfos-Key": "fixture"})).status_code, 400)
        async def key_user(key, app, space):
            self.assertEqual((app, space), ("budget001", "budget"))
            if key != "fixture-key":
                raise ConsoleAdminAuthenticationError("Fixture key rejected")
            return "owner"

        with patch("deep_agent.agent_service.security.PlatformEnterpriseAdminChecker.resolve_api_key_user",
                   side_effect=key_user):
            key_headers = {"X-Deepfos-Key": "fixture-key", "app": "budget001"}
            self.assertEqual((await self.client.get(directory, headers=key_headers)).json(), response.json())
            denied = await self.client.get(directory, headers={**key_headers, "X-Deepfos-Key": "invalid"})
            self.assertEqual(denied.status_code, 401)

        request = {"message_id": "cross-transport", "text": "使用模拟数字回答差额。"}
        target = f"{ROOT}/agents/budget-report/tasks"
        self.assertEqual((await self.client.post(target, headers=alternate, json=request)).status_code, 403)
        self.assertEqual((await self.client.post(target, headers=without_app, json=request)).status_code, 400)
        oversized_number = {**request, "data": {"value": 10 ** 400}}
        self.assertEqual((await self.client.post(target, headers=HEADERS, json=oversized_number)).status_code, 422)
        mcp_url = f"{ROOT}/mcp"
        denied_origin = await self.client.post(mcp_url, headers={**HEADERS, "origin": "https://example.net"})
        self.assertEqual(denied_origin.status_code, 403)
        unauthenticated = await self.client.post(mcp_url, json={
            "jsonrpc": "2.0", "id": 1, "method": "resources/list",
        })
        self.assertEqual(unauthenticated.status_code, 401)
        self.client.headers.update(HEADERS)

        async with streamable_http_client(f"{DIRECT_BASE}{ROOT}/mcp", http_client=self.client) as (rx, tx, _):
            async with ClientSession(rx, tx) as session:
                hello = await session.initialize()
                self.assertIsNotNone(hello.capabilities.resources)
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                descriptions = {name: tool.description for name, tool in tools.items()}
                get_wait = tools["GetTask"].inputSchema["properties"]["waitSeconds"]
                send_wait = tools["SendMessage"].inputSchema["$defs"]["SendConfiguration"]["properties"]["waitSeconds"]
                self.assertEqual((get_wait["default"], send_wait["default"]), (120, 120))
                names = set(descriptions)
                self.assertEqual(names, {"list_agents", "SendMessage", "GetTask", "ListTasks", "CancelTask", "get_artifact"})
                self.assertIn("HTTP GET", descriptions["GetTask"])
                for field in ("data_base64", "format=url by default", "format=base64"):
                    self.assertIn(field, descriptions["get_artifact"])
                guides = {str(resource.uri): resource.mimeType for resource in (await session.list_resources()).resources}
                guide_uri = "deepagent://guides/mcp"
                self.assertEqual(guides, {guide_uri: "text/markdown"})
                resource = (await session.read_resource(guide_uri)).contents[0]
                self.assertEqual(resource.mimeType, "text/markdown")
                for example in names | {"contextId", "data_base64", "download_url", "HTTP GET"}:
                    self.assertIn(example, resource.text)
                for example in ("taskId", "download_url", "HTTP GET", "authentication headers", "format=base64"):
                    self.assertIn(example, hello.instructions)
                listed = await session.call_tool("list_agents")
                self.assertEqual(listed.structuredContent, response.json())
                del self.client.headers["app"]
                del self.client.headers["space"]
                self.assertEqual((await session.call_tool("list_agents")).structuredContent, response.json())
                missing = await session.call_tool("GetTask", {"id": "unknown"})
                self.assertTrue(missing.isError)
                self.assertEqual(missing.structuredContent["status"], 400)
                self.client.headers.update(HEADERS)
                with patch("deep_agent.agent.factory._resolve_model", return_value=publication_model(
                    lambda _, info: text_result(info, "20"),
                )):
                    result = await session.call_tool("SendMessage", {
                        "agentCode": "budget-report", "message": mcp_message(request["message_id"], request["text"]),
                    })
                    self.assertFalse(result.isError, result)
                    initial = result.structuredContent
                    self.assertEqual(initial["status"]["state"], "TASK_STATE_COMPLETED")
                    task_id = initial["id"]
                self.assertEqual(self.result(await self.rpc("GetTask", {"id": task_id}))["id"], task_id)
                self.assertEqual((await session.call_tool("ListTasks", {"agentCode": "budget-report"}))
                                 .structuredContent["tasks"][0]["id"], task_id)
                repeated = await self.client.post(target, json=request)
                self.assertEqual(repeated.status_code, 202, repeated.text)
                self.assertEqual(repeated.json()["id"], task_id)
                self.assertEqual((await self.client.post(target, json={**request, "text": "different"})).status_code, 400)
                task = (await self.client.get(f"{ROOT}/tasks/{task_id}")).json()
                self.assertEqual(task["state"], "completed")
                self.assertEqual(task["artifacts"][0]["parts"][0]["text"], "20")
                page = await self.client.get(target, params={"state": "completed"})
                self.assertEqual(page.json()["items"][0]["id"], task_id)
                detail = await self.client.get(f"{ROOT}/publications/budget-report/tasks/{task_id}", headers=self.admin)
                self.assertEqual(detail.json()["source"], "mcp")
                self.assertEqual((await self.ctx.stores.chat_session.get_source(self.agent, detail.json()["session_id"]))[0], "mcp")
                for headers in ({"authorization": "Bearer fixture-other"}, {"app": "budget002"}):
                    self.assertEqual((await self.client.get(f"{ROOT}/tasks/{task_id}", headers=headers)).status_code, 404)
                self.revoked = True
                self.assertEqual((await self.client.get(f"{ROOT}/tasks/{task_id}")).status_code, 403)
                self.revoked = False
                self.ctx.agent_tasks.entries[task_id].changed -= RETENTION + timedelta(seconds=1)
                self.assertEqual((await self.client.get(f"{ROOT}/tasks/{task_id}")).status_code, 404)
                self.assertEqual((await session.call_tool("GetTask", {"id": task_id})).structuredContent["status"], 404)


class IndependentA2ASdkTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_server_contract_supports_streaming_and_polling_with_tenant(self):
        from deep_agent.remote_agents import network
        from deep_agent.remote_agents.contracts import A2AConfig, InputFile, RemoteState
        from tests.integration.fixtures.remote_a2a_server import create_app

        base = "http://external.example"
        http_client = network._http_client
        attachment = InputFile(
            "budget.csv", "text/csv",
            b"month,region,budget,actual,nonce\nNovember,East,100,120,from-outbound-fixture\n",
        )
        extra = InputFile("review.txt", "text/plain", b"confirmed")

        for streaming in (True, False):
            with self.subTest(streaming=streaming):
                app = create_app(base, streaming=streaming)

                def client_factory(headers):
                    client = http_client(headers)
                    client._transport = ASGITransport(app=app)
                    return client

                checkpoint = AsyncMock()
                config = A2AConfig(card_url=f"{base}/.well-known/agent-card.json")
                with patch.object(network, "_http_client", side_effect=client_factory):
                    inspected = await network.A2AAdapter(config).inspect()
                    self.assertEqual(inspected["streaming"], streaming)
                    self.assertEqual(inspected["approved_interface"]["tenant"], "budget-fixture")
                    adapter = network.A2AAdapter(config.model_copy(update={
                        "approved_interface": inspected["approved_interface"],
                    }))
                    first = await adapter.run(RemoteState(), "start", [attachment], "start", checkpoint, message_id="start")
                    self.assertEqual((first.state.status, first.text), ("input_required", "请确认月份。"))
                    second = await adapter.run(first.state, "November", [], "answer", checkpoint, message_id="month")
                    self.assertEqual(second.state.task_id, first.state.task_id)
                    self.assertNotEqual(second.state.question_id, first.state.question_id)
                    third = await adapter.run(second.state, "East", [extra], "answer", checkpoint, message_id="region")
                    self.assertEqual((third.state.status, third.text), ("completed", "预算差额 20。"))
                    self.assertEqual(third.state.task_id, first.state.task_id)
                    self.assertEqual(third.files[0].data,
                                     b"month,region,difference,nonce\nNovember,East,20,from-outbound-fixture\n")
                    self.assertEqual((await adapter.refresh(third.state, checkpoint)).files, third.files)
                    follow_up = await adapter.run(third.state, "repeat", [], "resume", checkpoint, message_id="next")
                    self.assertEqual(follow_up.state.context_id, first.state.context_id)
                    self.assertNotEqual(follow_up.state.task_id, first.state.task_id)
                    self.assertEqual(follow_up.text, "沿用 November / East，差额 20。")
                    paused = await adapter.run(
                        RemoteState(), "another", [attachment], "start", checkpoint, message_id="cancel-start",
                    )
                    self.assertEqual((await adapter.cancel(paused.state)).state.status, "canceled")

    async def test_interrupted_send_recovers_known_task_without_repeating_mutation(self):
        from httpx import MockTransport, ReadError, Response
        from deep_agent.remote_agents import network
        from deep_agent.remote_agents.contracts import A2AConfig, RemoteAgentError, RemoteState

        base = "http://external.example"
        advertised = {"url": f"{base}/rpc", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        methods = []
        task = {"id": "task", "contextId": "context", "status": {"state": "TASK_STATE_WORKING"}}

        for task_known in (True, False):
            with self.subTest(task_known=task_known):
                methods.clear()

                async def respond(request):
                    if request.method == "GET":
                        return Response(200, json={
                            "name": "independent", "supportedInterfaces": [advertised],
                            "capabilities": {"streaming": True},
                        })
                    rpc = json.loads(await request.aread())
                    methods.append(rpc["method"])
                    if rpc["method"] == "SendStreamingMessage":
                        if not task_known:
                            raise ReadError("Lost the first response", request=request)
                        first = {"jsonrpc": "2.0", "id": rpc["id"], "result": {"task": task}}
                        return Response(200, text=f"data: {json.dumps(first)}\n\n",
                                        headers={"Content-Type": "text/event-stream"})
                    completed = {
                        **task, "status": {"state": "TASK_STATE_COMPLETED"},
                        "artifacts": [{"artifactId": "result", "parts": [{"text": "finished"}]}],
                    }
                    return Response(200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": completed})

                config = A2AConfig(card_url=f"{base}/card", approved_interface=advertised)
                checkpoint = AsyncMock()
                adapter = network.A2AAdapter(config)
                with patch.object(network, "_http_client", side_effect=lambda headers: AsyncClient(
                    headers=headers, transport=MockTransport(respond),
                )):
                    if task_known:
                        finished = await adapter.run(RemoteState(), "start", [], "start", checkpoint, message_id="unique")
                        self.assertEqual((finished.state.status, finished.text), ("completed", "finished"))
                        self.assertEqual(methods, ["SendStreamingMessage", "GetTask"])
                        self.assertEqual(checkpoint.await_args_list[0].args[0].task_id, "task")
                    else:
                        with self.assertRaises(RemoteAgentError) as failed:
                            await adapter.run(RemoteState(), "start", [], "start", checkpoint, message_id="unique")
                        self.assertEqual(failed.exception.status, "unknown")
                        self.assertEqual(methods, ["SendStreamingMessage"])
                        checkpoint.assert_not_awaited()

    async def test_interrupted_answer_queries_existing_task_and_preserves_original_question(self):
        from httpx import MockTransport, ReadError, Response
        from deep_agent.remote_agents import network
        from deep_agent.remote_agents.contracts import A2AConfig, RemoteAgentError, RemoteState

        base = "http://external.example"
        advertised = {"url": f"{base}/rpc", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        previous = RemoteState(task_id="task", context_id="context", status="input_required", question_id="first")
        for recovered_question in ("first", "second"):
            with self.subTest(recovered_question=recovered_question):
                methods = []

                async def respond(request):
                    if request.method == "GET":
                        return Response(200, json={
                            "name": "independent", "supportedInterfaces": [advertised],
                            "capabilities": {"streaming": True},
                        })
                    rpc = json.loads(await request.aread())
                    methods.append(rpc["method"])
                    if rpc["method"] == "SendStreamingMessage":
                        raise ReadError("The reply response was interrupted", request=request)
                    self.assertEqual(rpc["params"]["id"], "task")
                    return Response(200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": {
                        "id": "task", "contextId": "context",
                        "status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {
                            "messageId": recovered_question, "role": "ROLE_AGENT",
                            "parts": [{"text": f"question {recovered_question}"}],
                        }},
                    }})

                config = A2AConfig(card_url=f"{base}/card", approved_interface=advertised)
                adapter = network.A2AAdapter(config)
                checkpoint = AsyncMock()
                with patch.object(network, "_http_client", side_effect=lambda headers: AsyncClient(
                    headers=headers, transport=MockTransport(respond),
                )):
                    result = await adapter.run(previous, "answer", [], "answer", checkpoint, message_id="answer")
                    self.assertEqual(result.state.question_id, recovered_question)
                    self.assertEqual(result.text, f"question {recovered_question}")
                    self.assertEqual(result.warnings, (network.UNCONFIRMED_ANSWER,) if recovered_question == "first" else ())
                    self.assertEqual(methods, ["SendStreamingMessage", "GetTask"])
                    self.assertEqual(checkpoint.await_args.args[0], result.state)

                    methods.clear()
                    with self.assertRaises(RemoteAgentError) as error:
                        await adapter.run(previous.model_copy(update={"status": "completed"}), "follow-up", [],
                                          "resume", checkpoint, message_id="next")
                    self.assertEqual(error.exception.status, "unknown")
                    self.assertEqual(methods, ["SendStreamingMessage"])

    async def test_artifact_download_keeps_credentials_on_approved_origin(self):
        from httpx import MockTransport, Response
        from deep_agent.remote_agents.network import A2AAdapter

        base = "https://a2a.example/rpc"
        targets = []

        async def respond(request):
            targets.append(str(request.url))
            self.assertEqual(request.headers["authorization"], "Bearer fixture")
            return Response(200, content=b"safe", headers={"content-type": "text/plain"})

        artifact = wire.Artifact(artifact_id="files", parts=[
            wire.Part(url="https://other.example/file", filename="unsafe.txt"),
            wire.Part(url="/download", filename="safe.txt"),
        ])
        async with AsyncClient(transport=MockTransport(respond), headers={"authorization": "Bearer fixture"}) as http:
            files, warnings = await A2AAdapter._files([artifact], "task", http, base)
        self.assertEqual(targets, ["https://a2a.example/download"])
        self.assertEqual([(file.name, file.data) for file in files], [("safe.txt", b"safe")])
        self.assertEqual(len(warnings), 1)
        self.assertIn("unsafe.txt", warnings[0])

    async def test_stream_joins_chunks_and_does_not_publish_partial_file(self):
        from httpx import MockTransport, Response
        from deep_agent.remote_agents import network
        from deep_agent.remote_agents.contracts import A2AConfig, RemoteState

        base = "http://external.example"
        advertised = {"url": f"{base}/rpc", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        working = {"id": "task", "contextId": "context", "status": {"state": "TASK_STATE_WORKING"}}
        ids = {"taskId": "task", "contextId": "context"}
        first_file_part = {"filename": "result.csv", "mediaType": "text/csv", "text": "row,value\n"}

        async def respond(request):
            if request.method == "GET":
                return Response(200, json={
                    "name": "independent", "supportedInterfaces": [advertised],
                    "capabilities": {"streaming": True},
                })
            rpc = json.loads(await request.aread())
            if rpc["method"] == "GetTask":
                return Response(200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": {
                    **working, "artifacts": [{"artifactId": "file", "parts": [first_file_part]}],
                }})
            events = [
                {"task": working},
                {"artifactUpdate": {**ids, "artifact": {"artifactId": "file", "parts": [first_file_part]}}},
                {"artifactUpdate": {**ids, "append": True, "lastChunk": True, "artifact": {
                    "artifactId": "file", "parts": [{**first_file_part, "text": "East,20\n"}],
                }}},
                {"statusUpdate": {**ids, "status": {"state": "TASK_STATE_COMPLETED"}}},
            ]
            stream = "".join(f"data: {json.dumps({'jsonrpc': '2.0', 'id': rpc['id'], 'result': event})}\n\n"
                             for event in events)
            return Response(200, text=stream, headers={"Content-Type": "text/event-stream"})

        adapter = network.A2AAdapter(A2AConfig(card_url=f"{base}/card", approved_interface=advertised))
        checkpoint = AsyncMock()
        with patch.object(network, "_http_client", side_effect=lambda headers: AsyncClient(
            headers=headers, transport=MockTransport(respond),
        )):
            active = await adapter.refresh(RemoteState(task_id="task", context_id="context"), checkpoint)
            self.assertEqual(active.state.status, "working")
            self.assertEqual(active.files, ())
            finished = await adapter.run(RemoteState(), "start", [], "start", checkpoint, message_id="start")
        self.assertEqual(finished.state.status, "completed")
        self.assertEqual([(file.name, file.data) for file in finished.files], [
            ("result.csv", b"row,value\nEast,20\n"),
        ])
