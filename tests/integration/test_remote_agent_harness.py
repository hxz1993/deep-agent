from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from deep_agent.api.v1 import api_router
from deep_agent.api.v1.chat import _agent_deps, _prepare_remote_reply
from deep_agent.api.v1.deps import build_runner_for_agent
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.files.storage import FileStoreError, LocalFileStore
from deep_agent.remote_agents.contracts import ConnectionWrite, OutputFile, RemoteAgentError, RemoteResult, RemoteState
from deep_agent.schemas.agent import AgentUpdate
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.schemas.chat import ChatRequest
from deep_agent.security import ConsoleAdminAuthorizer
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class HarnessRemoteAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_api_masks_headers_and_reports_delayed_debug_failure(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="TMP_to_delete_remote_management_"))
        files = LocalFileStore(Path(temporary) / "files")
        await files.initialize()
        stores, engine, factory = await make_sqlite_stores(f"sqlite+aiosqlite:///{temporary}/runtime.db")
        ctx = AppContext.for_testing(stores=stores, db_engine=engine, db_session_factory=factory, file_store=files,
                                     console_admin_authorizer=ConsoleAdminAuthorizer(AsyncMock(return_value=True)))
        init_context(ctx)
        self.addCleanup(reset_context)
        self.addAsyncCleanup(ctx.aclose)
        app = FastAPI()
        app.include_router(api_router)
        api = "/api/v1/remote-agent-connections"
        headers = {"user": "owner", "authorization": "Bearer test-token"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
            config = {"protocol": "a2a", "card_url": "https://agent.example/card.json",
                      "headers": {"Authorization": "Bearer private-value"}}
            create = await client.post(api, json={"name": "Remote review", "config": config})
            self.assertEqual(create.status_code, 201, create.text)
            row = create.json()
            url = f"{api}/{row['id']}"
            self.assertEqual(row["config"]["headers"], {"authorization": None})
            self.assertNotIn("private-value", (await client.get(url)).text)
            renamed = await client.put(url, json={"name": "Review", "revision": row["revision"], "config": row["config"]})
            self.assertEqual(renamed.status_code, 200, renamed.text)
            self.assertEqual((await client.put(url, json={"name": "Stale", "revision": row["revision"],
                                                        "config": config})).status_code, 409)
            stored = await stores.remote_agent.get(row["id"])
            self.assertEqual(stored.config["headers"], {"authorization": "Bearer private-value"})

            release = asyncio.Event()

            async def input_error(_request):
                await release.wait()
                raise RemoteAgentError("无法读取附件")

            runtime = ctx.get_remote_agents()
            with patch.object(runtime, "_inputs", side_effect=input_error):
                first = await client.post(url + "/debug", json={"file_ids": ["inaccessible"]})
                self.assertEqual(first.status_code, 200, first.text)
                self.assertEqual(first.json()["status"], "submitted")
                invocation_id = first.json()["id"]
                pending = runtime.operations[invocation_id]
                try:
                    self.assertEqual((await client.post(url + "/debug", json={
                        "action": "clear", "invocation_id": invocation_id,
                    })).status_code, 409)
                    self.assertEqual((await client.post(url + "/debug", headers={"user": "other"}, json={
                        "action": "refresh", "invocation_id": invocation_id,
                    })).status_code, 409)
                finally:
                    release.set()
                await asyncio.wait_for(pending, 2)
                result = await client.post(url + "/debug", json={"action": "refresh", "invocation_id": invocation_id})
                self.assertEqual((result.json()["status"], result.json()["text"]), ("failed", "无法读取附件"))
                self.assertEqual((await client.get(api + "/recent")).json(), [])

    async def test_harness_keeps_two_questions_and_exports_files_in_normal_chat(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="TMP_to_delete_remote_harness_"))
        files = LocalFileStore(Path(temporary) / "files")
        await files.initialize()
        stores, engine, factory = await make_sqlite_stores(f"sqlite+aiosqlite:///{temporary}/runtime.db")
        ctx = AppContext.for_testing(stores=stores, db_engine=engine, db_session_factory=factory, file_store=files)
        self.addAsyncCleanup(ctx.aclose)
        connection = await stores.remote_agent.save(ConnectionWrite(name="budget", config={"protocol": "codex"}))
        other = await stores.remote_agent.save(ConnectionWrite(name="other", config={"protocol": "codex"}))
        owner = "user-1"
        agent_id = await seed_default_agent(stores)
        await stores.agent.update_unique(agent_id, AgentUpdate(capabilities=[
            f"agent_connection:{connection.id}", f"agent_connection:{other.id}",
        ]))
        headers = {"user": owner, "app": "budget001", "space": "budget"}
        received = []

        class Adapter:
            async def refresh(self, state, checkpoint):
                return RemoteResult(state, "Region?")

            async def run(self, state, message, attachments, mode, checkpoint, *, message_id):
                received.append((mode, message, [(file.name, file.data) for file in attachments]))
                await checkpoint(RemoteState(status="working", session_id="same-session"))
                step = len(received)
                if step < 3:
                    return RemoteResult(RemoteState(status="input_required", session_id="same-session", question_id=str(step)),
                                        "Month?" if step == 1 else "Region?")
                return RemoteResult(RemoteState(status="completed", session_id="same-session"), "20", (
                    OutputFile("result", "result.csv", "text/csv", b"variance\n20\n"),
                ))

        async def model(messages, _info):
            if "delegate_task" not in {tool.name for tool in _info.function_tools}:
                return ModelResponse(parts=[TextPart("Budget test")])
            if not received:
                return ModelResponse(parts=[ToolCallPart("delegate_task", {"agent_name": "budget", "task": "Read budget"})])
            text = str(messages[-1].parts)
            if "remote_task_result" in text and any(isinstance(part, ToolReturnPart) and part.tool_name == "remote_task_result"
                                                    for part in messages[-1].parts):
                return ModelResponse(parts=[TextPart("20")])
            deadline = asyncio.get_running_loop().time() + 2
            while received and received[-1][1] not in ("East",) and "East" in text and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)
            if "East" in text or len(received) >= 3:
                entries = await ctx.get_remote_agents().journal.list("session", user=owner, agent_id=agent_id,
                                                                      app_id="budget001", space_id="budget")
                return ModelResponse(parts=[ToolCallPart("remote_task_result", {"task_id": entries[0].id})])
            return ModelResponse(parts=[TextPart("Please answer the question")])

        async def stream(messages, info):
            parts = (await model(messages, info)).parts
            for index, part in enumerate(parts):
                if isinstance(part, ToolCallPart):
                    yield {index: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args))}
                else:
                    yield part.content

        async def run(text, attachments=(), *, selected=None, question=None, capabilities=None):
            deps = _agent_deps(ctx, session_id="session", user=owner, agent_id=agent_id,
                               request_headers=headers, execution_request_headers=headers)
            await _prepare_remote_reply(ctx, deps, ChatRequest(
                session_id="session", message=text, remote_task_id=selected, remote_question_id=question,
                capabilities=capabilities,
            ))
            runner = await build_runner_for_agent(ctx, agent_id, capabilities=capabilities)
            return await asyncio.wait_for(runner.run("session", text, deps, attachments=list(attachments), run_id=deps.run_id), 15)

        async def upload(name, data):
            item = await files.upload(file_type=FILE_TYPE_ATTACHMENT, file_name=name, mime="text/csv", data=data,
                                      request_headers=headers)
            return AttachmentRef(id=item.id, name=name, mime="text/csv")

        async def state(expected):
            for _ in range(300):
                entries = await ctx.get_remote_agents().journal.list("session", user=owner, agent_id=agent_id,
                                                                      app_id="budget001", space_id="budget")
                if entries and entries[0].state.status == expected:
                    return entries[0]
                await asyncio.sleep(0.01)
            self.fail(f"No invocation with expected state {expected}")

        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(model, stream_function=stream)), \
                patch("deep_agent.remote_agents.runtime.RemoteAgentRuntime.adapter", return_value=Adapter()):
            await run("Check budget", [await upload("budget.csv", b"budget,actual\n100,120\n")])
            first = await state("input_required")
            self.assertEqual(first.state.question_id, "1")
            history = await ctx.store.get_raw_history("session", agent_id=agent_id)
            self.assertTrue(any(isinstance(part, ToolReturnPart) and part.tool_name == "delegate_task"
                                and (part.metadata or {}).get("remote_agent", {}).get("id") == first.id
                                for message in history if isinstance(message, ModelRequest) for part in message.parts))
            await run("Unrelated request", capabilities=[f"agent_connection:{other.id}"])
            self.assertEqual(len(received), 1)
            await run("October", selected=first.id, question="1")
            second = await state("input_required")
            self.assertEqual((second.id, second.state.question_id), (first.id, "2"))
            with self.assertRaises(HTTPException) as response:
                await run("Stale reply", selected=first.id, question="1")
            self.assertEqual(response.exception.status_code, 409)
            self.assertEqual(len(received), 2)
            with self.assertRaisesRegex(RemoteAgentError, "远程调用已更新"):
                await ctx.get_remote_agents()._save(first, text="Uncommitted update")
            self.assertEqual(first.text, "Month?")
            await ctx.get_remote_agents().close()
            ctx.remote_agents = None
            await run("East", [await upload("extra.csv", b"East\n")], selected=second.id, question="2")
            final = await state("completed")
        self.assertEqual([step[0] for step in received], ["start", "answer", "answer"])
        self.assertEqual(received[-1][2], [("extra.csv", b"East\n")])
        self.assertEqual((final.id, final.state.session_id, final.text), (first.id, "same-session", "20"))
        result = final.files["result"]
        self.assertEqual(await files.download(result["file_id"], headers), b"variance\n20\n")
        with self.assertRaises(FileStoreError):
            await files.download(result["file_id"], {**headers, "user": "someone-else"})
        self.assertEqual(await ctx.get_remote_agents().journal.list(
            "session", user="someone-else", agent_id=agent_id, app_id="budget001", space_id="budget",
        ), [])
        history = await ctx.store.get_raw_history("session", agent_id=agent_id)
        self.assertTrue(any(isinstance(part, ToolReturnPart) and result["file_id"] in str(part.metadata)
                            for message in history if isinstance(message, ModelRequest) for part in message.parts))
