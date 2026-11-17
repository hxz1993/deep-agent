"""Exercise the standard A2A wire contract with the real Runner and SQLite."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from a2a import types as wire
from a2a.client.card_resolver import parse_agent_card
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image
from pydantic_ai.messages import BinaryContent, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from deep_agent.agent_service.publications import PublicationCreate, PublicationStore
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
HEADERS = {"authorization": "Bearer fixture-owner", "app": "budget001", "space": "budget", "A2A-Version": "1.0"}


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
        self.addCleanup(reset_context)
        self.addAsyncCleanup(self.ctx.aclose)
        app = FastAPI()
        app.include_router(api_router)
        self.app = app
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.enterContext(patch.dict("os.environ", {
            "DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL": "http://127.0.0.1:8002",
        }))
        self.revoked = False

        async def platform(client, _service, path, **_kwargs):
            auth = client._headers.get("token")
            if path == "api/user/full-detail":
                self.assertNotIn("user", client._headers)
                if auth not in {"fixture-owner", "fixture-other"}:
                    raise IntegrationAppAccessError(401, "Fixture token rejected")
                return {"id": auth.split("-")[-1]}
            if path == "check-permission-by-space-app":
                return {"app": not self.revoked, "space": not self.revoked}
            self.fail(f"Unexpected platform request {path}")

        self.enterContext(patch(
            "deep_agent.services.integration_app_access.PlatformAppAccessClient._get",
            autospec=True, side_effect=platform,
        ))
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
        self.assertEqual((await self.client.get(f"{self.url}/agent-card.json", headers=conflicting)).status_code, 401)
        self.assertEqual((await self.client.post(mcp_url, headers={**rpc_call, **conflicting}, json=mcp_request)).status_code, 401)

    @staticmethod
    def message(id, content, **bindings):
        return {"messageId": id, "role": "ROLE_USER", "parts": [{"text": content}], **bindings}

    def result(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("result", response.json(), response.text)
        return response.json()["result"]

    async def test_card_runner_history_scope_and_idempotency(self):
        card = await self.client.get(f"{self.url}/agent-card.json", headers=HEADERS)
        self.assertEqual(card.status_code, 200, card.text)
        self.assertEqual(card.json()["supportedInterfaces"][0]["protocolVersion"], "1.0")
        parsed_card = parse_agent_card(card.json())
        self.assertEqual(parsed_card.security_schemes["platformKey"].api_key_security_scheme.name, "X-Deepfos-Key")
        self.assertEqual(parsed_card.security_schemes["platformBearer"].http_auth_security_scheme.scheme, "bearer")
        self.assertEqual(len(parsed_card.security_requirements), 2)
        model = FunctionModel(lambda _messages, _info: ModelResponse(parts=[TextPart("预算差异 20")]))
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

    async def test_question_and_continuation(self):
        from deep_agent.agent_service.output import QUESTION_NAME

        output_name = "output__budget_result"
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
            self.assertTrue({QUESTION_NAME, output_name} <= names)
            return ModelResponse(parts=[ToolCallPart(QUESTION_NAME, {"question": "请提供预算月份"})])

        app_url = f"{self.url}/apps/budget001"
        token = {key: HEADERS[key] for key in ("authorization", "A2A-Version")}
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(respond)):
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
        wrong = self.message("wrong", "十月", **bindings, metadata={"replyTo": "stale"})
        self.assertIn("error", (await self.rpc("SendMessage", {"message": wrong})).json())
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(
            lambda *_: ModelResponse(parts=[ToolCallPart(output_name, {"month": "十月"})]),
        )):
            answer = self.message("answer", "十月", **bindings)
            task2 = self.result(await self.rpc("SendMessage", {"message": answer}))["task"]
        self.assertEqual(task2["id"], task["id"])
        self.assertEqual(task2["status"]["state"], "TASK_STATE_COMPLETED", task2)
        self.assertEqual(task2["artifacts"][0]["parts"][0]["data"], {"month": "十月"})
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(
            lambda *_: ModelResponse(parts=[ToolCallPart(output_name, {"month": "十一月"})]),
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
        self.assertEqual(detail["turns"], [
            {"id": event.turn_id, "event_id": event.event_id} for event in persisted_turns[:2]
        ])
        self.assertEqual((detail["caller_username"], detail["app_name"], detail["space_name"]),
                         ("budget-user", "预算应用", "预算空间"))
        self.assertEqual(task_list.json()["items"][0]["caller_username"], "budget-user")
        diagnostic = await self.client.get(f"/api/v1/diagnostics/sessions/{session_id}", headers=self.admin)
        self.assertEqual(diagnostic.status_code, 200, diagnostic.text)
        self.assertEqual(diagnostic.json()["source_type"], "a2a")
        self.assertEqual(detail["session_title"], diagnostic.json()["title"])

    async def test_outbound_clients_use_same_task_for_two_replies_and_retrieve_artifacts(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        from deep_agent.agent_service.output import QUESTION_NAME
        from deep_agent.remote_agents import network
        from deep_agent.remote_agents.contracts import A2AConfig, InputFile, RemoteAgentError, RemoteState

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
            f"open(publish_path('evidence.txt'), 'wb').write(b'e' * {len(evidence)})"
        )
        http_client = network._http_client
        def client_factory(headers):
            client = http_client(headers)
            client._transport = ASGITransport(app=self.app)
            return client

        def respond(messages, info):
            if QUESTION_NAME not in {tool.name for tool in info.output_tools}:
                return ModelResponse(parts=[TextPart("预算核对")])
            latest = next(part.content for message in reversed(messages) for part in reversed(message.parts)
                          if isinstance(part, UserPromptPart))
            text = latest if isinstance(latest, str) else "\n".join(part for part in latest if isinstance(part, str))
            if "开始核对" in text:
                return ModelResponse(parts=[ToolCallPart(QUESTION_NAME, {"question": "哪个月份？"})])
            if "用十一月" in text:
                return ModelResponse(parts=[ToolCallPart(QUESTION_NAME, {"question": "哪个地区？"})])
            if "继续解释" in text:
                return ModelResponse(parts=[TextPart("仍然使用十一月和华东地区")])
            if getattr(messages[-1].parts[-1], "tool_name", None) == "execute_python":
                return ModelResponse(parts=[TextPart("预算差额 20")])
            self.assertIn("用华东", text)
            return ModelResponse(parts=[ToolCallPart("execute_python", {"code": script})])

        with (
            patch.object(network, "_http_client", side_effect=client_factory),
            patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(respond)),
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
            with self.assertRaises(RemoteAgentError):
                await adapter.run(
                    second.state.model_copy(update={"question_id": first.state.question_id}),
                    "旧问题", [], "answer", checkpoint, message_id="a2a-stale",
                )
            completed = await adapter.run(
                resumed.state, "用华东", [InputFile("extra.txt", "text/plain", extra)],
                "answer", checkpoint, message_id="a2a-third",
            )
            self.assertEqual((completed.state.status, completed.text), ("completed", "预算差额 20"))
            self.assertEqual(completed.state.task_id, first.state.task_id)
            self.assertEqual(completed.state.context_id, first.state.context_id)
            self.assertEqual(completed.warnings, ())
            self.assertEqual({file.name: file.data for file in completed.files},
                             {"result.csv": result_csv, "evidence.txt": evidence})
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
            async with streamable_http_client(f"http://test{ROOT}/mcp", http_client=self.client) as (rx, tx, _):
                async with ClientSession(rx, tx) as session:
                    await session.initialize()

                    async def call(tool, args=None):
                        response = await session.call_tool(tool, args or {})
                        self.assertFalse(response.isError, response)
                        return response.structuredContent

                    async def advance(tool, args):
                        submitted = await call(tool, args)
                        task_id = submitted["id"]
                        await self.ctx.agent_tasks.wait(self.ctx.agent_tasks.entries[task_id])
                        return await call("get_task", {"task_id": task_id})

                    publication = (await call("list_agents"))["agents"][0]["agent_code"]
                    first = await advance("submit_task", {
                        "agent_code": publication, "message_id": "mcp-first", "text": "开始核对",
                        "attachments": [{"name": "budget.csv", "media_type": "text/csv",
                                         "data_base64": base64.b64encode(csv).decode()}],
                    })
                    self.assertEqual((first["state"], first["message"]), ("input_required", "哪个月份？"))
                    self.assertEqual(first["source"], "mcp")

                    second = await advance("reply_task", {
                        "task_id": first["id"], "reply_to": first["question_id"],
                        "message_id": "mcp-second", "text": "用十一月",
                    })
                    self.assertEqual((second["state"], second["message"]), ("input_required", "哪个地区？"))
                    self.assertEqual(second["id"], first["id"])
                    self.assertNotEqual(second["question_id"], first["question_id"])

                    completed = await advance("reply_task", {
                        "task_id": second["id"], "reply_to": second["question_id"],
                        "message_id": "mcp-third", "text": "用华东",
                        "attachments": [{"name": "extra.txt", "media_type": "text/plain",
                                         "data_base64": base64.b64encode(extra).decode()}],
                    })
                    self.assertEqual(completed["state"], "completed")
                    self.assertEqual(completed["id"], first["id"])
                    self.assertEqual(completed["context_id"], first["context_id"])
                    self.assertTrue(any(part.get("text") == "预算差额 20"
                                        for artifact in completed["artifacts"] for part in artifact.get("parts", ())))

                    artifacts = {item["name"]: item for item in completed["artifacts"] if "download_url" in item}
                    for file_name, data in (("result.csv", result_csv), ("evidence.txt", evidence)):
                        artifact = artifacts[file_name]
                        self.assertEqual(artifact["size"], len(data))
                        download = await self.client.get(artifact["download_url"])
                        self.assertEqual(download.status_code, 200)
                        self.assertEqual(download.content, data)

                    continued = await advance("submit_task", {
                        "agent_code": publication, "context_id": completed["context_id"],
                        "message_id": "mcp-continued", "text": "继续解释",
                    })
                    self.assertEqual(continued["context_id"], completed["context_id"])
                    self.assertNotEqual(continued["id"], completed["id"])
                    self.assertTrue(any(part.get("text") == "仍然使用十一月和华东地区"
                                        for artifact in continued["artifacts"] for part in artifact.get("parts", ())))

                    entry = self.ctx.agent_tasks.entries[first["id"]]
                    events = await self.ctx.store.list_events(entry.context.session_id, event_type="user/message")
                    self.assertEqual(len(events), 4)
                    self.assertIn("budget.csv", str(events[0].payload))
                    self.assertIn("extra.txt", str(events[2].payload))

        recent = await self.client.get("/api/v1/remote-agent-connections/recent", headers=self.admin)
        self.assertEqual((recent.status_code, recent.json()), (200, []))

    async def test_inline_image_reaches_model_in_original_task(self):
        from deep_agent.agent_service.output import QUESTION_NAME

        card = await self.client.get(f"{self.url}/agent-card.json", headers=HEADERS)
        self.assertIn("image/png", card.json()["defaultInputModes"])
        png = io.BytesIO()
        Image.new("RGBA", (32, 32), (255, 0, 0, 180)).save(png, "PNG")

        def respond(messages, _info):
            latest = next(part for part in reversed(messages[-1].parts) if isinstance(part, UserPromptPart))
            if isinstance(latest.content, str):
                return ModelResponse(parts=[ToolCallPart(QUESTION_NAME, {"question": "请上传图片"})])
            images = [part for part in latest.content if isinstance(part, BinaryContent)]
            self.assertEqual([image.media_type for image in images], ["image/png"])
            with Image.open(io.BytesIO(images[0].data)) as image:
                self.assertEqual(image.convert("RGB").getpixel((0, 0)), (255, 0, 0))
            return ModelResponse(parts=[TextPart("红色")])

        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(respond)):
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
            model = FunctionModel(lambda _, info: ModelResponse(parts=[
                TextPart("对账" if "RECON" in (info.instructions or "") else "预算"),
            ]))
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
        with patch.dict("os.environ", {"DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL": ""}):
            response = await self.client.get(f"{ROOT}/publications", headers=self.admin)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual({row["agent_code"] for row in response.json()}, {"budget-report", "recon"})
            self.assertIsNone(response.json()[0]["card_url"])
            response = await self.client.patch(f"{ROOT}/publications/recon", headers=self.admin, json={
                "revision": 2, "enabled": True,
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["enabled"])
            self.assertIsNone(response.json()["card_url"])

    async def test_app_routes_share_card_scope_and_task_ownership(self):
        app_url = f"{self.url}/apps/budget001"
        card_path = "/agent-card.json"
        token = {key: HEADERS[key] for key in ("authorization", "A2A-Version")}
        missing_app = await self.client.get(f"{self.url}{card_path}", headers=token, params={"app_id": "budget001"})
        self.assertEqual(missing_app.status_code, 400)
        self.assertIn("app Header", missing_app.json()["detail"])

        with patch.dict("os.environ", {"DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL": "https://example.test/gateway"}):
            publication = (await self.client.get(f"{ROOT}/publications/budget-report", headers=self.admin)).json()
            self.assertEqual(publication["app_card_url_template"], f"https://example.test/gateway{self.url}/apps/{{app_id}}{card_path}")
            generic = await self.client.get(f"{self.url}{card_path}", headers=HEADERS)
            scoped = await self.client.get(f"{app_url}{card_path}", headers=[
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
                discovery = await self.client.get(f"{url}/.well-known/agent-card.json", headers=headers)
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

        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(
            lambda *_: ModelResponse(parts=[TextPart("预算差异 20")]),
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

        model = FunctionModel(lambda *_: ModelResponse(parts=[TextPart("预算差异 20")]))
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
        with patch(
            "deep_agent.api.v1.agent_service.IntegrationAppDirectory.available_apps",
            side_effect=IntegrationAppAccessError(401, "请使用平台登录身份验证应用权限。"),
        ) as directory:
            created = await self.client.post(f"{ROOT}/publications", headers=headers, json={
                "agent_code": "selected-apps", "name": "指定应用", "agent_id": agent.id, "scope": scope,
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

        result = self.result(await self.rpc("SendMessage", {
            "message": self.message("unconfigured-model", "测试执行"),
        }, url=f"{ROOT}/a2a/selected-apps"))["task"]
        self.assertEqual(result["status"]["state"], "TASK_STATE_FAILED")
        self.assertEqual(result["status"]["message"]["parts"][0]["text"], "Agent 尚未配置模型")

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
            if any(getattr(part, "tool_name", None) == "execute_python"
                   and part.part_kind == "tool-return" for message in messages for part in message.parts):
                return ModelResponse(parts=[TextPart("差异 20")])
            return ModelResponse(parts=[ToolCallPart("execute_python", {"code": script})])

        app_url = f"{self.url}/apps/budget001"
        token = {key: HEADERS[key] for key in ("authorization", "A2A-Version")}
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(respond)):
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
        async with streamable_http_client(f"http://test{ROOT}/mcp", http_client=self.client) as (rx, tx, _):
            async with ClientSession(rx, tx) as session:
                await session.initialize()
                tool = await session.call_tool("get_task", {"task_id": task["id"]})
                self.assertFalse(tool.isError)
                file = next(item for item in tool.structuredContent["artifacts"] if item["artifact_id"] == artifact_id)
                self.assertEqual(file["media_type"], artifact["mediaType"])
        download_url = file["download_url"]
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
        async with streamable_http_client(f"http://test{ROOT}/mcp", http_client=self.client) as (rx, tx, _):
            async with ClientSession(rx, tx) as session:
                await session.initialize()

                async def fetch():
                    return await session.call_tool("get_task", {"task_id": task_id})

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
                    key = file["artifact_id"]
                    url = f"http://127.0.0.1:8002{ROOT}/tasks/{task_id}/artifacts/{key}/download"
                    download_urls[key] = url
                    self.assertEqual(file, {
                        "artifact_id": key, "name": f"{key}.txt", "media_type": "text/plain",
                        "size": len(files[key]), "download_url": url,
                    })
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

                with patch.dict("os.environ", {"DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL": "https://example.test/gateway"}):
                    result = await fetch()
                    over_limit = await fetch_artifact("large", format="base64")
                self.assertEqual({file["download_url"] for file in result.structuredContent["artifacts"]}, {
                    f"https://example.test/gateway{ROOT}/tasks/{task_id}/artifacts/{key}/download" for key in files
                })
                self.assertEqual(over_limit.structuredContent["download_url"],
                                 f"https://example.test/gateway{ROOT}/tasks/{task_id}/artifacts/large/download")
                with patch.dict("os.environ", {"DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL": ""}):
                    result = await fetch()
                    self.assertEqual((await fetch_artifact("empty")).structuredContent["status"], 503)
                    self.assertEqual((await fetch_artifact("large", format="base64")).structuredContent["status"], 503)
                    self.assertFalse((await fetch_artifact("empty", format="base64")).isError)
                self.assertEqual(result.structuredContent["state"], "completed")
                self.assertTrue(all("download_url" not in file for file in result.structuredContent["artifacts"]))

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
                self.assertEqual((await self.client.get(download_urls["empty"])).status_code, 404)
                self.assertEqual((await fetch_artifact("empty", format="base64")).structuredContent["status"], 404)
                self.ctx.agent_tasks.entries[task_id].changed -= RETENTION + timedelta(seconds=1)
                expired = await fetch()
                self.assertTrue(expired.isError)
                self.assertEqual(expired.structuredContent["status"], 404)
                self.assertEqual((await fetch_artifact("boundary")).structuredContent["status"], 404)
                self.assertEqual((await self.client.get(download_urls["large"])).status_code, 404)

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
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(
            lambda *_: ModelResponse(parts=[TextPart("流式报告")]),
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
        with patch.dict("os.environ", {"DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL": "http://["}):
            self.assertEqual((await self.client.get(f"{ROOT}/client-config", headers=self.admin)).json(),
                             {"mcp_url": None})
            self.assertEqual((await self.client.post(f"{ROOT}/mcp", headers={"Origin": "http://test"})).status_code, 403)
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

        async with streamable_http_client(f"http://test{ROOT}/mcp", http_client=self.client) as (rx, tx, _):
            async with ClientSession(rx, tx) as session:
                hello = await session.initialize()
                self.assertIsNotNone(hello.capabilities.resources)
                descriptions = {tool.name: tool.description for tool in (await session.list_tools()).tools}
                names = set(descriptions)
                self.assertEqual(names, {"list_agents", "submit_task", "get_task", "list_tasks",
                                         "reply_task", "cancel_task", "get_artifact"})
                self.assertIn("download_url", descriptions["get_task"])
                self.assertIn("HTTP GET", descriptions["get_task"])
                for field in ("data_base64", "format=url by default", "format=base64"):
                    self.assertIn(field, descriptions["get_artifact"])
                guides = {str(resource.uri): resource.mimeType for resource in (await session.list_resources()).resources}
                guide_uri = "deepagent://guides/mcp"
                self.assertEqual(guides, {guide_uri: "text/markdown"})
                resource = (await session.read_resource(guide_uri)).contents[0]
                self.assertEqual(resource.mimeType, "text/markdown")
                for example in names | {"context_id", "data_base64", "download_url", "HTTP GET"}:
                    self.assertIn(example, resource.text)
                for example in ("reply_to", "download_url", "HTTP GET", "authentication headers", "format=base64"):
                    self.assertIn(example, hello.instructions)
                listed = await session.call_tool("list_agents")
                self.assertEqual(listed.structuredContent, response.json())
                del self.client.headers["app"]
                del self.client.headers["space"]
                self.assertEqual((await session.call_tool("list_agents")).structuredContent, response.json())
                missing = await session.call_tool("get_task", {"task_id": "unknown"})
                self.assertTrue(missing.isError)
                self.assertEqual(missing.structuredContent["status"], 400)
                self.client.headers.update(HEADERS)
                with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(
                    lambda *_: ModelResponse(parts=[TextPart("20")]),
                )):
                    result = await session.call_tool("submit_task", {"agent_code": "budget-report", **request})
                    self.assertFalse(result.isError, result)
                    initial = result.structuredContent
                    self.assertEqual(initial["source"], "mcp")
                    task_id = initial["id"]
                    await self.ctx.agent_tasks.wait(self.ctx.agent_tasks.entries[task_id])
                self.assertEqual(self.result(await self.rpc("GetTask", {"id": task_id}))["id"], task_id)
                self.assertEqual((await session.call_tool("list_tasks", {"agent_code": "budget-report"}))
                                 .structuredContent["items"][0]["id"], task_id)
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
                self.assertEqual((await session.call_tool("get_task", {"task_id": task_id})).structuredContent["status"], 404)


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
