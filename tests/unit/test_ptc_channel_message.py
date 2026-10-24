"""Channel-native/PTC parity with fake delivery and bounded-download seams only."""
from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from pydantic_ai import FunctionToolset, ModelRetry, RunContext
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.factory import _toolsets_for_spec, build_agent
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.registry import ToolPackage
from deep_agent.channels.message_tool import MessageComponent, build_channel_message_toolset
from deep_agent.channels.outbound import ChannelRunContext, MessageCapabilities, MessageTarget
from deep_agent.files.storage import LocalFileStore
from deep_agent.ptc.builtin import BuiltinPtcToolset
from deep_agent.schemas.attachment import AttachmentRef
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT


_URL = "https://public.example.test/chart.png"


def _source():
    return ChannelRunContext("account", "channel-run", "session", "agent", "user",
                             MessageTarget("account", "single", "peer", "incoming"))


def _store():
    """Exercise the real resolver against isolated account/run/session records."""
    return SimpleNamespace(
        get_account=AsyncMock(return_value=SimpleNamespace(
            enabled=True, agent_id="agent", session_user_id="user", channel_type="wecom")),
        get_run=AsyncMock(return_value=SimpleNamespace(
            binding_id="binding", account_id="account", session_id="session",
            processing_status="running", external_message_id="incoming", thread_id=None,
            source_metadata={})),
        get_binding_by_id=AsyncMock(return_value=SimpleNamespace(
            account_id="account", session_id="session", peer_type="single", peer_id="peer")),
        get_channel_session=AsyncMock(return_value=SimpleNamespace(agent_id="agent", user_id="user")),
    )


def _caps():
    return MessageCapabilities(("text", "image", "file"), True, 4096, 8192, 8192, 0, 0)


def _deps():
    return AgentDeps(session_id="session", agent_id="agent", user="user", run_id="run")


def _context():
    return RunContext(deps=_deps(), model=TestModel(), usage=RunUsage(), tool_call_id="call")


def _spec(*, ptc):
    return AgentSpec(name="channel fixture", model="test", instructions="Use only requested tools.",
                     capabilities=["builtin:ptc"] if ptc else [])


def _png():
    output = io.BytesIO()
    Image.new("RGB", (2, 2), (30, 80, 150)).save(output, "PNG")
    return output.getvalue()


def _receipt(kind="image"):
    return {"status": "completed", "components": [{"index": 0, "kind": kind,
            "requested_kind": kind, "state": "succeeded", "message_id": "outgoing"}],
            "visible_to_current_chat": True}


class ChannelPtcRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_native_definition_survives_both_ptc_modes(self):
        for ptc in (False, True):
            with self.subTest(ptc=ptc):
                source = build_channel_message_toolset(_store(), None, _source(), _caps())

                async def channel_helper() -> str:
                    return "helper"

                source = FunctionToolset([*source.tools.values(), channel_helper])
                original = (await source.get_tools(_context()))["channel_message"]
                assembled = _toolsets_for_spec(build_registry(), _spec(ptc=ptc), channel_toolset=source)
                tools = {}
                for toolset in assembled:
                    tools.update(await toolset.get_tools(_context()))
                tool = tools["channel_message"]
                self.assertNotIn("deepagent_code_mode", tools["channel_helper"].tool_def.metadata or {})
                self.assertNotIn("deepagent_ptc_builtin", tools["channel_helper"].tool_def.metadata or {})
                self.assertEqual(tool.tool_def.parameters_json_schema,
                                 original.tool_def.parameters_json_schema)
                self.assertIs(tool.args_validator, original.args_validator)
                self.assertEqual(tool.tool_def.return_schema, original.tool_def.return_schema)
                metadata = tool.tool_def.metadata or {}
                if ptc:
                    self.assertEqual(metadata["deepagent_code_mode"], "hybrid")
                    self.assertIs(metadata["deepagent_ptc_builtin"], True)
                else:
                    self.assertNotIn("deepagent_code_mode", metadata)
                    self.assertNotIn("deepagent_ptc_builtin", metadata)

    async def test_plain_console_does_not_acquire_channel_tool(self):
        for ptc in (False, True):
            with self.subTest(ptc=ptc):
                seen = set()

                def respond(messages, info):
                    seen.update(tool.name for tool in info.function_tools)
                    return ModelResponse(parts=[TextPart("ordinary answer")])

                with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(respond)):
                    agent = build_agent(_spec(ptc=ptc), build_registry())
                self.assertEqual((await agent.run("hello", deps=_deps())).output, "ordinary answer")
                self.assertNotIn("channel_message", seen)
                self.assertNotIn("execute_python", seen)

    async def test_same_name_in_untrusted_sources_is_not_marked_hybrid(self):
        async def channel_message(value: str) -> dict:
            return {"value": value}

        impostor = FunctionToolset([channel_message])
        spec = _spec(ptc=True)
        spec.capabilities.append("ui:fixture")
        cases = [
            {"file_toolset": impostor}, {"history_toolset": impostor},
            {"mcp_resource_toolset": impostor}, {"skill_resource_toolset": impostor},
            {"dynamic_ui_packages": [ToolPackage(code="ui:fixture", category="ui", toolset=impostor)]},
        ]
        for kwargs in cases:
            with self.subTest(source=next(iter(kwargs))):
                for toolset in _toolsets_for_spec(build_registry(), spec, **kwargs):
                    tool = (await toolset.get_tools(_context())).get("channel_message")
                    if tool is not None:
                        self.assertNotIn("deepagent_code_mode", tool.tool_def.metadata or {})
                        self.assertNotIn("deepagent_ptc_builtin", tool.tool_def.metadata or {})


class ChannelPtcExecutionTests(unittest.IsolatedAsyncioTestCase):
    def _agent(self, *, route, components, gateway, store=None, ptc=True, expected="completed", code=None):
        calls = 0
        tool_name = "run_code" if route == "ptc" else "channel_message"
        arguments = {"components": components}
        if route == "ptc":
            arguments = {"code": code or f"receipt = await channel_message(components={components!r})\nreceipt"}

        def respond(messages, info):
            nonlocal calls
            calls += 1
            names = {tool.name for tool in info.function_tools}
            self.assertIn("channel_message", names)
            self.assertEqual("run_code" in names, ptc)
            self.assertNotIn("execute_python", names)
            if calls == 1:
                return ModelResponse(parts=[ToolCallPart(tool_name, arguments, "delivery")])
            self.assertEqual(calls, 2, "a failed or unknown send must not be automatically repeated")
            if expected is not None:
                returned = [part for message in messages for part in message.parts
                            if isinstance(part, ToolReturnPart) and part.tool_call_id == "delivery"]
                self.assertEqual(len(returned), 1)
                self.assertEqual(returned[0].outcome, "success")
                self.assertIn(expected, str(returned[0].content))
                if expected == "unknown":
                    self.assertNotIn("'status': 'completed'", str(returned[0].content))
            return ModelResponse(parts=[TextPart("done")])

        source = build_channel_message_toolset(store or _store(), gateway, _source(), _caps())
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(respond)):
            return build_agent(_spec(ptc=ptc), build_registry(), channel_toolset=source)

    async def test_real_monty_sends_url_directly_without_python_or_file_store(self):
        data = _png()
        gateway = SimpleNamespace(send_message=AsyncMock(return_value=_receipt()))
        store = _store()
        agent = self._agent(route="ptc", components=[{"type": "image", "url": _URL}],
                            gateway=gateway, store=store)
        deps = _deps()
        with patch("deep_agent.channels.message_tool.load_public_file",
                   AsyncMock(return_value=(data, "chart.png", "image/png", _URL))) as download:
            result = await agent.run("Send the already public image to this conversation.", deps=deps)
        self.assertEqual(result.output, "done")
        download.assert_awaited_once()
        self.assertEqual(download.await_args.args, (_URL,))
        self.assertLessEqual(download.await_args.kwargs["max_bytes"], _caps().image_limit)
        gateway.send_message.assert_awaited_once()
        sent_source, sent_run, prepared = gateway.send_message.await_args.args
        self.assertEqual((sent_source, sent_run), (_source(), "run"))
        self.assertEqual((prepared[0].kind, prepared[0].data, prepared[0].filename),
                         ("image", data, "chart.png"))
        self.assertIsNone(deps.file_store)
        self.assertEqual(deps.session_file_artifacts, {})
        store.get_channel_session.assert_awaited_once_with("account", "session")
        outer = [part for message in result.new_messages() for part in message.parts
                 if isinstance(part, ToolReturnPart) and part.tool_call_id == "delivery"]
        self.assertEqual(len(outer), 1)
        self.assertEqual(outer[0].metadata["ptc_diagnostics"]["steps"][0]["tool_name"], "channel_message")

    async def test_native_url_send_works_with_ptc_enabled_or_disabled(self):
        for ptc in (False, True):
            with self.subTest(ptc=ptc):
                gateway = SimpleNamespace(send_message=AsyncMock(return_value=_receipt()))
                agent = self._agent(route="native", components=[{"type": "image", "url": _URL}],
                                    gateway=gateway, ptc=ptc)
                with patch("deep_agent.channels.message_tool.load_public_file",
                           AsyncMock(return_value=(_png(), "chart.png", "image/png", _URL))) as download:
                    self.assertEqual((await agent.run("send image", deps=_deps())).output, "done")
                download.assert_awaited_once()
                gateway.send_message.assert_awaited_once()

    async def test_authorized_file_id_is_usable_natively_and_from_real_monty(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-ptc-channel-files-") as temporary:
            file_store = LocalFileStore(Path(temporary) / "files")
            await file_store.initialize()
            for route in ("native", "ptc"):
                data = b"authorized report\n"
                gateway = SimpleNamespace(send_message=AsyncMock(return_value=_receipt("file")))
                deps = _deps()
                deps.request_headers = {"user": "user", "app": "fixture001", "space": "fixture"}
                uploaded = await file_store.upload(file_type=FILE_TYPE_ATTACHMENT, file_name="report.txt",
                    mime="text/plain", data=data, request_headers=deps.request_headers)
                deps.attachment_by_id = {uploaded.id: AttachmentRef(
                    id=uploaded.id, name="report.txt", mime="text/plain")}
                deps.file_store = file_store
                agent = self._agent(route=route, components=[{"type": "file", "file_id": uploaded.id}],
                                    gateway=gateway)
                with patch("deep_agent.channels.message_tool.load_public_file", AsyncMock()) as download, \
                        patch.object(file_store, "download_bounded", wraps=file_store.download_bounded) as stored_download:
                    self.assertEqual((await agent.run("send authorized report", deps=deps)).output, "done")
                download.assert_not_awaited()
                stored_download.assert_awaited_once_with(uploaded.id, deps.request_headers,
                                                        max_bytes=_caps().file_limit)
                self.assertEqual(gateway.send_message.await_args.args[2][0].data, data)
                gateway.send_message.assert_awaited_once()

    async def test_real_monty_cannot_bypass_file_or_channel_session_authority(self):
        for rejected in ("foreign-file", "changed-owner"):
            with self.subTest(rejected=rejected):
                store = _store()
                if rejected == "changed-owner":
                    store.get_channel_session.return_value.user_id = "other-user"
                component = ({"type": "file", "file_id": "foreign"} if rejected == "foreign-file"
                             else {"type": "image", "url": _URL})
                gateway = SimpleNamespace(send_message=AsyncMock())
                agent = self._agent(route="ptc", components=[component], gateway=gateway,
                                    store=store, expected=None)
                with patch("deep_agent.channels.message_tool.load_public_file", AsyncMock()) as download:
                    result = await agent.run("attempt unauthorized send", deps=_deps())
                failures = [part for message in result.new_messages() for part in message.parts
                            if isinstance(part, ToolReturnPart) and part.tool_call_id == "delivery"]
                self.assertEqual(len(failures), 1)
                self.assertEqual(failures[0].outcome, "failed")
                download.assert_not_awaited()
                gateway.send_message.assert_not_awaited()

    async def test_public_download_rejection_and_unknown_delivery_are_not_sent_success(self):
        gateway = SimpleNamespace(send_message=AsyncMock())
        tool = build_channel_message_toolset(_store(), gateway, _source(), _caps()).tools["channel_message"].function
        with patch("deep_agent.channels.message_tool.load_public_file",
                   AsyncMock(side_effect=ModelRetry("URL resolves to a private network address"))):
            with self.assertRaises(ModelRetry):
                await tool(_context(), [MessageComponent(type="image", url="http://127.0.0.1/private.png")])
        gateway.send_message.assert_not_awaited()

    async def test_unknown_gateway_outcomes_do_not_become_success_or_retry(self):
        for behavior in ("gateway-exception", "gateway-unknown"):
            with self.subTest(behavior=behavior):
                send = AsyncMock(side_effect=RuntimeError("isolated gateway lost acknowledgement")) if behavior == "gateway-exception" else AsyncMock(return_value={"status": "unknown"})
                gateway = SimpleNamespace(send_message=send)
                agent = self._agent(route="ptc", components=[{"type": "image", "url": _URL}],
                                    gateway=gateway, expected="unknown")
                with patch("deep_agent.channels.message_tool.load_public_file",
                           AsyncMock(return_value=(_png(), "chart.png", "image/png", _URL))):
                    self.assertEqual((await agent.run("send once", deps=_deps())).output, "done")
                send.assert_awaited_once()

    async def test_partial_delivery_preserves_each_component_and_unknown_status(self):
        receipt = {"status": "unknown", "visible_to_current_chat": True, "components": [
            {"index": 0, "kind": "text", "requested_kind": "text", "state": "succeeded", "message_id": "first"},
            {"index": 1, "kind": "image", "requested_kind": "image", "state": "unknown", "error_code": "send_timeout"},
            {"index": 2, "kind": "text", "requested_kind": "text", "state": "skipped"},
        ]}
        gateway = SimpleNamespace(send_message=AsyncMock(return_value=receipt))
        components = [{"type": "text", "text": "first"}, {"type": "image", "url": _URL},
                      {"type": "text", "text": "last"}]
        agent = self._agent(route="ptc", components=components, gateway=gateway, expected="unknown")
        with patch("deep_agent.channels.message_tool.load_public_file",
                   AsyncMock(return_value=(_png(), "chart.png", "image/png", _URL))):
            result = await agent.run("send ordered components once", deps=_deps())
        gateway.send_message.assert_awaited_once()
        self.assertEqual([item.kind for item in gateway.send_message.await_args.args[2]], ["text", "image", "text"])
        outer = next(part for message in result.new_messages() for part in message.parts
                     if isinstance(part, ToolReturnPart) and part.tool_call_id == "delivery")
        self.assertEqual(outer.metadata["ptc_diagnostics"]["steps"][0]["output"]["value"], receipt)

    async def test_parallel_monty_channel_calls_do_not_overlap_at_gateway(self):
        first_entered, release_first, both_invoked = asyncio.Event(), asyncio.Event(), asyncio.Event()
        active = 0
        peak = 0
        invoked = 0
        order = []

        original_call = BuiltinPtcToolset.call_tool

        async def observe_call(toolset, name, tool_args, ctx, tool):
            nonlocal invoked
            if name == "channel_message":
                invoked += 1
                if invoked == 2:
                    both_invoked.set()
            return await original_call(toolset, name, tool_args, ctx, tool)

        async def send(source, run_id, components, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            text = components[0].text
            order.append(text)
            try:
                if text == "first":
                    first_entered.set()
                    await release_first.wait()
                return _receipt("text")
            finally:
                active -= 1

        gateway = SimpleNamespace(send_message=AsyncMock(side_effect=send))
        code = ("import asyncio\n"
                "await asyncio.gather(\n"
                "    channel_message(components=[{'type': 'text', 'text': 'first'}]),\n"
                "    channel_message(components=[{'type': 'text', 'text': 'second'}]),\n"
                ")")
        agent = self._agent(route="ptc", components=[], gateway=gateway, code=code)
        with patch.object(BuiltinPtcToolset, "call_tool", observe_call):
            task = asyncio.create_task(agent.run("send both in order", deps=_deps()))
            try:
                await asyncio.wait_for(first_entered.wait(), timeout=3)
                # Observe, but do not replace, original host dispatch. Both real
                # child calls must arrive before releasing the first send.
                await asyncio.wait_for(both_invoked.wait(), timeout=3)
                self.assertEqual(order, ["first"])
                release_first.set()
                self.assertEqual((await asyncio.wait_for(task, timeout=3)).output, "done")
            finally:
                release_first.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(order, ["first", "second"])
        self.assertEqual((peak, active, gateway.send_message.await_count), (1, 0, 2))

    async def test_ptc_deadline_after_dispatch_does_not_retry_or_report_success(self):
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def send(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        gateway = SimpleNamespace(send_message=AsyncMock(side_effect=send))
        agent = self._agent(route="ptc", components=[{"type": "text", "text": "send once"}],
                            gateway=gateway, expected=None)
        with patch("deep_agent.ptc.toolset.PTC_WALL_TIMEOUT_SECONDS", 1):
            result = await asyncio.wait_for(agent.run("bounded send", deps=_deps()), timeout=3)
        self.assertTrue(entered.is_set(), "timeout must occur after gateway dispatch")
        self.assertTrue(stopped.is_set(), "timed-out host callback must be joined")
        gateway.send_message.assert_awaited_once()
        outer = next(part for message in result.new_messages() for part in message.parts
                     if isinstance(part, ToolReturnPart) and part.tool_call_id == "delivery")
        self.assertEqual(outer.outcome, "failed")
        self.assertIn("total time limit", str(outer.content))
        self.assertIn("outcome may be unknown", outer.content["guidance"])
        self.assertIn("Do not resend", outer.content["guidance"])
        self.assertNotIn("'status': 'completed'", str(outer.content))
        self.assertEqual(outer.metadata["ptc_diagnostics"]["steps"][0]["status"], "interrupted")

    async def test_cancelling_inflight_download_never_calls_gateway(self):
        for route in ("native", "ptc"):
            with self.subTest(route=route):
                entered = asyncio.Event()

                async def download(*args, **kwargs):
                    entered.set()
                    await asyncio.Event().wait()

                gateway = SimpleNamespace(send_message=AsyncMock())
                agent = self._agent(route=route, components=[{"type": "image", "url": _URL}],
                                    gateway=gateway, expected=None)
                with patch("deep_agent.channels.message_tool.load_public_file", download):
                    task = asyncio.create_task(agent.run("cancel during download", deps=_deps()))
                    try:
                        await asyncio.wait_for(entered.wait(), timeout=3)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(task, timeout=3)
                    finally:
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                gateway.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
