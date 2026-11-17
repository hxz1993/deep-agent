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
from deep_agent.conversation.projector import project_session_events
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.files.storage import FileStoreError, LocalFileStore
from deep_agent.remote_agents.contracts import ConnectionWrite, OutputFile, RemoteAgentError, RemoteResult, RemoteState
from deep_agent.remote_agents.journal import Invocation
from deep_agent.schemas.agent import AgentUpdate
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.schemas.chat import ChatRequest
from deep_agent.schemas.mcp import McpServerCreate
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
            mcp_config = {"protocol": "mcp", "mcp_server_name": "published", "publication_code": "budget"}
            await stores.mcp.create_unique(McpServerCreate(
                name="published", transport="url", config={"url": "https://agent.example/mcp"},
            ))
            rejected = await client.post(api, json={"name": "New MCP delegation", "config": mcp_config})
            self.assertEqual(rejected.status_code, 422)
            self.assertEqual(rejected.json()["detail"][0]["type"], "union_tag_invalid")
            self.assertEqual((await client.get(api)).json(), [])
            runtime = ctx.get_remote_agents()
            defaults = await client.get(f"{api}/workspace-defaults")
            self.assertEqual(defaults.status_code, 200, defaults.text)
            self.assertEqual(defaults.json(), {
                "local": "~/.deep-agent/remote-agent-workspaces",
                "ssh": "~/.deep-agent/remote-agent-workspaces",
            })
            self.assertIsNotNone(await stores.mcp.rename_with_agent_references("published", "standard-mcp"))
            self.assertTrue(await stores.mcp.delete("standard-mcp"))

            config = {"protocol": "a2a", "card_url": "https://agent.example/card.json",
                      "headers": {"Authorization": "Bearer private-value"}}
            create = await client.post(api, json={"name": "Remote review", "config": config})
            self.assertEqual(create.status_code, 201, create.text)
            row = create.json()
            url = f"{api}/{row['id']}"
            self.assertEqual(row["config"]["headers"], {"authorization": None})
            self.assertEqual(row["updated_by"], "owner")
            self.assertIsNone(row["inspection"])
            self.assertNotIn("private-value", (await client.get(url)).text)
            renamed = await client.put(url, headers={"user": "editor"},
                                       json={"name": "Review", "revision": row["revision"], "config": row["config"]})
            self.assertEqual(renamed.status_code, 200, renamed.text)
            saved = renamed.json()
            self.assertEqual(saved["updated_by"], "editor")
            self.assertEqual((await client.put(url, json={"name": "Stale", "revision": row["revision"],
                                                        "config": config})).status_code, 409)
            stored = await stores.remote_agent.get(row["id"])
            self.assertEqual(stored.config["headers"], {"authorization": "Bearer private-value"})

            adapter = AsyncMock()
            adapter.inspect.return_value = {"name": "Review"}
            with patch.object(runtime, "adapter", return_value=adapter):
                checked = (await client.post(url + "/check")).json()
            self.assertEqual(checked["updated_by"], "editor")
            self.assertEqual(checked["updated_at"], saved["updated_at"])
            self.assertEqual(set(checked["inspection"]), {"name", "status", "revision", "checked_at"})
            profiles = {"editor": {"username": "Editor display name"}}
            with patch("deep_agent.api.v1.remote_agents.resolve_diagnostic_users", return_value=profiles):
                listed = (await client.get(api)).json()
            self.assertEqual(listed[0]["updated_by_username"], "Editor display name")

            release = asyncio.Event()

            async def input_error(_request):
                await release.wait()
                raise RemoteAgentError("无法读取附件")

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
        background_ready = asyncio.Event()
        background_release = asyncio.Event()
        cancel_ready = asyncio.Event()
        canceled = asyncio.Event()
        partial_files = (
            OutputFile("accepted", "accepted.csv", "text/csv", b"accepted\n"),
            OutputFile("retry", "retry.csv", "text/csv", b"retry\n"),
        )

        class Adapter:
            async def refresh(self, state, checkpoint):
                if state.cursor.get("scenario") == "partial":
                    return RemoteResult(state, "Partial result", partial_files)
                return RemoteResult(state, "Region?")

            async def cancel(self, state):
                canceled.set()
                return RemoteResult(state.model_copy(update={"status": "canceled"}))

            async def run(self, state, message, attachments, mode, checkpoint, *, message_id):
                received.append((mode, message, [(file.name, file.data) for file in attachments]))
                await checkpoint(RemoteState(status="working", session_id="same-session"))
                step = len(received)
                if message == "Background":
                    background_ready.set()
                    await background_release.wait()
                if message == "Cancelable":
                    cancel_ready.set()
                    await asyncio.Future()
                if message == "Partial":
                    return RemoteResult(RemoteState(status="completed", session_id="same-session",
                                                   cursor={"scenario": "partial"}), "Partial result", partial_files)
                if step < 3:
                    return RemoteResult(RemoteState(status="input_required", session_id="same-session", question_id=str(step)),
                                        "Month?" if step == 1 else "Region?")
                return RemoteResult(RemoteState(status="completed", session_id="same-session"), "20", (
                    OutputFile("result", "result.csv", "text/csv", b"variance\n20\n"),
                ))

        selected_files = {}
        task_ids = []
        command_tasks = {}
        command_results = {}

        async def model(messages, info):
            tools = {tool.name: tool for tool in info.function_tools}
            if "delegate_task" not in tools:
                return ModelResponse(parts=[TextPart("Budget test")])
            for name in ("delegate_task", "answer_task", "list_tasks", "message_task", "cancel_task", "remote_task_result"):
                self.assertIn(name, tools)
                self.assertEqual("attachments" in tools[name].parameters_json_schema["properties"],
                                 name in {"delegate_task", "answer_task"})
            text = str(messages[-1].parts)
            results = [part for part in messages[-1].parts if isinstance(part, ToolReturnPart)]
            if results:
                command_results.update((part.tool_name, part.content) for part in results)
                return ModelResponse(parts=[TextPart("Step received")])
            if "Check budget" in text:
                return ModelResponse(parts=[ToolCallPart("delegate_task", {
                    "agent_name": "budget", "task": "Read budget",
                    "attachments": [{"id": selected_files["budget"], "name": "untrusted.csv"}],
                })])
            if "October" in text:
                args = {"task_id": task_ids[0], "answer": "model paraphrase",
                        "attachments": [{"id": selected_files["other"]}]}
                return ModelResponse(parts=[ToolCallPart("answer_task", args), ToolCallPart("answer_task", args)])
            if "East" in text:
                return ModelResponse(parts=[ToolCallPart("delegate_task", {
                    "agent_name": "budget", "task": "model paraphrase", "resume": task_ids[0],
                })])
            if "Parallel" in text:
                return ModelResponse(parts=[
                    ToolCallPart("delegate_task", {"agent_name": "budget", "task": "Parallel budget",
                        "attachments": [{"id": selected_files["budget"]}]}),
                    ToolCallPart("delegate_task", {"agent_name": "other", "task": "Parallel other",
                        "attachments": [{"id": selected_files["other"]}]}),
                ])
            if "List delegated tasks" in text:
                return ModelResponse(parts=[ToolCallPart("list_tasks", {})])
            if "Steer pending task" in text:
                return ModelResponse(parts=[ToolCallPart("message_task", {
                    "task_id": command_tasks["cancel"], "message": "unexpected input",
                })])
            if "Cancel pending task" in text:
                return ModelResponse(parts=[ToolCallPart("cancel_task", {
                    "task_id": command_tasks["cancel"], "force": True,
                })])
            if "Cancelable" in text:
                return ModelResponse(parts=[ToolCallPart("delegate_task", {
                    "agent_name": "budget", "task": "Cancelable", "background": True,
                })])
            if "Retry artifacts" in text:
                return ModelResponse(parts=[ToolCallPart("remote_task_result", {
                    "task_id": command_tasks["partial"], "refresh": True,
                })])
            if "Partial" in text:
                return ModelResponse(parts=[ToolCallPart("delegate_task", {
                    "agent_name": "budget", "task": "Partial",
                }, tool_call_id="reused-call")])
            if "Background" in text:
                return ModelResponse(parts=[ToolCallPart("delegate_task", {
                    "agent_name": "budget", "task": "Background", "background": True,
                }, tool_call_id="reused-call")])
            if "Forbidden" in text and "RetryPromptPart" not in text:
                return ModelResponse(parts=[ToolCallPart("delegate_task", {
                    "agent_name": "budget", "task": "Forbidden", "attachments": [{"id": "unknown-file"}],
                })])
            if "Follow-up" in text:
                return ModelResponse(parts=[ToolCallPart("delegate_task", {
                    "agent_name": "budget", "task": "Follow-up", "resume": task_ids[0],
                    "attachments": [{"id": selected_files["other"]}],
                })])
            return ModelResponse(parts=[TextPart("Please answer the question")])

        async def stream(messages, info):
            parts = (await model(messages, info)).parts
            for index, part in enumerate(parts):
                if isinstance(part, ToolCallPart):
                    yield {index: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args),
                                                tool_call_id=part.tool_call_id)}
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
                found = next((entry for entry in entries if entry.state.status == expected), None)
                if found is not None:
                    return found
                await asyncio.sleep(0.01)
            self.fail(f"No invocation with expected state {expected}")

        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(model, stream_function=stream)), \
                patch("deep_agent.remote_agents.runtime.RemoteAgentRuntime.adapter", return_value=Adapter()):
            budget = await upload("budget.csv", b"budget,actual\n100,120\n")
            other_file = await upload("other.csv", b"private\n")
            selected_files.update(budget=budget.id, other=other_file.id)
            await run("Check budget", [budget, other_file])
            first = await state("input_required")
            task_ids.append(first.id)
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
            await run("Follow-up")
            self.assertEqual(received, [
                ("start", "Read budget", [("budget.csv", b"budget,actual\n100,120\n")]),
                ("answer", "October", [("other.csv", b"private\n")]),
                ("answer", "East", []),
                ("resume", "Follow-up", [("other.csv", b"private\n")]),
            ])
            await run("Forbidden")
            self.assertEqual(len(received), 4)
            await run("Parallel")
            self.assertCountEqual(received[4:], [
                ("start", "Parallel budget", [("budget.csv", b"budget,actual\n100,120\n")]),
                ("start", "Parallel other", [("other.csv", b"private\n")]),
            ])
            await run("Background")
            try:
                await asyncio.wait_for(background_ready.wait(), 2)
                pending = await state("working")
                self.assertNotEqual(pending.id, first.id)
            finally:
                background_release.set()
            await asyncio.wait_for(next(iter(ctx.get_remote_agents().harness_sessions.values())).workers[pending.id], 3)
            events = await ctx.store.list_all_events("session")
            projected = project_session_events(events)
            background = next(item for item in projected.items if item.kind == "tool" and item.call_id == pending.tool_call_id)
            self.assertEqual((background.status, background.output["result"]), ("completed", "20"))
            self.assertEqual([file.file_name for file in background.artifacts], ["result.csv"])
            self.assertFalse(any(item.kind == "tool" and item.tool_name == "remote_task_result" for item in projected.items))

            await run("Cancelable")
            await asyncio.wait_for(cancel_ready.wait(), 2)
            cancelable = await state("working")
            command_tasks["cancel"] = cancelable.id
            await run("List delegated tasks")
            self.assertIn(cancelable.id, command_results["list_tasks"])
            await run("Steer pending task")
            self.assertIn("accepts replies", command_results["message_task"])
            await run("Cancel pending task")
            await asyncio.wait_for(canceled.wait(), 2)
            self.assertEqual((await state("canceled")).id, cancelable.id)

            upload_file = files.upload

            async def fail_retry_file(**kwargs):
                if kwargs["file_name"] == "retry.csv":
                    raise FileStoreError("simulated transient upload failure")
                return await upload_file(**kwargs)

            with patch.object(files, "upload", side_effect=fail_retry_file):
                await run("Partial")
            partial = await state("completed")
            self.assertEqual(partial.tool_call_id, pending.tool_call_id)
            self.assertEqual(partial.text, "Partial result")
            self.assertEqual(list(partial.files), ["accepted"])
            self.assertTrue(partial.warnings)
            command_tasks["partial"] = partial.id
            await run("Retry artifacts")
            restored = await state("completed")
            self.assertEqual(list(restored.files), ["accepted", "retry"])
            self.assertEqual(restored.files["accepted"], partial.files["accepted"])
            self.assertFalse(restored.warnings)
            await run("Retry artifacts")
            self.assertEqual((await state("completed")).files, restored.files)
            recovered_view = project_session_events(await ctx.store.list_all_events("session"))
            prior_background = next(item for item in recovered_view.items
                                    if item.kind == "tool" and item.run_id == pending.run_id and item.call_id == pending.tool_call_id)
            self.assertEqual(prior_background.output["result"], "20")
            self.assertEqual([file.file_name for file in prior_background.artifacts], ["result.csv"])
            self.assertEqual(sum(
                artifact.file_id == restored.files["retry"]["file_id"]
                for item in recovered_view.items if item.kind == "tool" for artifact in item.artifacts
            ), 1)
        self.assertEqual((final.id, final.state.session_id, final.text), (first.id, "same-session", "20"))
        result = final.files["result"]
        legacy_payload = final.model_dump(exclude={"file_message_ids"})
        self.assertEqual(Invocation.model_validate(legacy_payload).result_payload()["files"], [result])
        self.assertEqual(await files.download(result["file_id"], headers), b"variance\n20\n")
        with self.assertRaises(FileStoreError):
            await files.download(result["file_id"], {**headers, "user": "someone-else"})
        self.assertEqual(await ctx.get_remote_agents().journal.list(
            "session", user="someone-else", agent_id=agent_id, app_id="budget001", space_id="budget",
        ), [])
        history = await ctx.store.get_raw_history("session", agent_id=agent_id)
        self.assertTrue(any(isinstance(part, ToolReturnPart) and result["file_id"] in str(part.metadata)
                            for message in history if isinstance(message, ModelRequest) for part in message.parts))
