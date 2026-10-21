"""Real Runner JSON through the channel consumer, commands and /new lifecycle."""
from __future__ import annotations

from dataclasses import replace
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from deep_agent.channels.contracts import InboundMedia
from deep_agent.channels.gateway.deepagent_client import StreamEvent
from deep_agent.conversation.projector import project_session_events
from deep_agent.schemas.agent import AgentUpdate
from deep_agent.schemas.attachment import AttachmentRef
from tests.unit import test_channel_commands as commands


class StructuredOutputChannelLifecycleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = commands.ChannelCommandTests.asyncSetUp
    asyncTearDown = commands.ChannelCommandTests.asyncTearDown
    message = commands.ChannelCommandTests.message
    service = commands.ChannelCommandTests.service
    wait_idle = commands.ChannelCommandTests.wait_idle

    async def test_new_text_attachment_mode_switch_and_commands_use_real_runner(self):
        """CHN-05/06/07: first real user freezes mode, controls remain controls."""
        persister = EventPersister(self.stores.coordinator)
        await persister.start()
        services = []
        requests = []
        def respond(messages, info):
            requests.append(info)
            if info.output_tools:
                name = info.output_tools[0].name
                value = {"new": False} if name == "changed_result" else {"text": f"native-{len(requests)}"}
                return ModelResponse(parts=[ToolCallPart(name, value, f"output-{len(requests)}")])
            return ModelResponse(parts=[TextPart(f"plain-{len(requests)}")])
        async def stream(messages, info):
            for i, part in enumerate(respond(messages, info).parts):
                if isinstance(part, TextPart):
                    yield part.content
                else:
                    yield {i: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id)}
        runner = AgentRunner(Agent(FunctionModel(respond, stream_function=stream), deps_type=AgentDeps),
            self.stores.coordinator, UsageLimits(request_limit=8))
        async def load_config():
            row, config = await self.stores.agent.get_output_configuration(self.account.agent_id)
            return ({"name": config.name, "description": config.description, "json_schema": config.json_schema}
                    if row.structured_output_enabled else None)
        runner._output_config_loader = load_config
        async def configure(enabled=True, changed=False):
            await self.stores.agent.update_unique(self.account.agent_id, AgentUpdate(
                structured_output_enabled=enabled,
                output_config={"name": "changed_result" if changed else "channel_result",
                    "description": "synthetic channel contract", "json_schema": {
                        "type": "object", **({"properties": {"new": {"type": "boolean"}}, "required": ["new"]}
                                               if changed else {})}}), user_id="channel-test-operator")
        try:
            with patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock):
                for attachment in (False, True):
                    with self.subTest(attachment=attachment):
                        await configure()
                        service, adapter, client = self.service()
                        services.append(service)
                        adapter.message_capabilities = lambda: SimpleNamespace(text_limit=20 * 1024)
                        async def real_chat(account, *, session_id, message, attachments, **kwargs):
                            client.calls.append((session_id, message))
                            deps = AgentDeps(session_id=session_id, agent_id=account.agent_id, user=account.session_user_id)
                            refs = [AttachmentRef.model_validate(a) for a in attachments]
                            async for event in runner.run_stream(session_id, message, deps, attachments=refs, persister=persister):
                                yield StreamEvent(event["type"], event)
                        client.stream_chat = real_chat
                        client.upload_media = AsyncMock(return_value={"id": "channel-fixture-file", "name": "fixture.txt", "mime": "text/plain"})
                        adapter.download_media = AsyncMock(return_value=(b"synthetic attachment", "fixture.txt"))
                        peer = f"lifecycle-{attachment}"
                        await service.receive(self.account, adapter, self.message("old question", peer=peer))
                        await self.wait_idle(service)
                        binding = await self.store.get_binding(self.account.id, "single", peer)
                        old_sid = binding.session_id
                        old_outputs = [r.public_dict() for r in await self.stores.coordinator.list_all_events(old_sid)
                                       if r.event_type == "output/attempt"]
                        self.assertEqual(len(old_outputs), 1)
                        count = len(requests)
                        for command in ("/help", "/status", "/whoami", "/stop"):
                            identifier = f"{peer}-{command}"
                            await service.receive(self.account, adapter, self.message(command, peer=peer, identifier=identifier))
                            self.assertEqual(len(requests), count)
                            reply = next(text for mid, text, done in reversed(adapter.replies) if mid == identifier and done)
                            self.assertNotIn("```json", reply)
                            self.assertNotIn("channel_result", reply)
                        await service.receive(self.account, adapter, self.message("/new", peer=peer))
                        await self.wait_idle(service)
                        current = await self.store.get_binding(self.account.id, "single", peer)
                        new_sid = current.session_id
                        self.assertNotEqual(new_sid, old_sid)
                        self.assertEqual(len(requests), count)
                        rows = await self.stores.coordinator.list_all_events(new_sid)
                        self.assertFalse([r for r in rows if r.event_type in {"user/message", "assistant/message", "output/attempt"}])
                        first = self.message("first new question", peer=peer, identifier=f"{peer}-first")
                        if attachment:
                            first = replace(first, media=(InboundMedia("file", "fixture.txt", "text/plain", object()),))
                        await service.receive(self.account, adapter, first)
                        await self.wait_idle(service)
                        first_final = next(text for mid, text, done in reversed(adapter.replies) if mid == first.message_id and done)
                        self.assertTrue(first_final.startswith("channel_result\n```json\n"), first_final)
                        rows = await self.stores.coordinator.list_all_events(new_sid)
                        if attachment:
                            self.assertIn("channel-fixture-file", str([r.payload for r in rows if r.event_type == "user/message"]))
                            client.upload_media.assert_awaited_once()
                        await configure(False)
                        await service.receive(self.account, adapter, self.message("ordinary continue", peer=peer, identifier=f"{peer}-off"))
                        await self.wait_idle(service)
                        plain = next(text for mid, text, done in reversed(adapter.replies) if mid == f"{peer}-off" and done)
                        self.assertTrue(plain.startswith("plain-"), plain)
                        self.assertNotIn("```json", plain)
                        await configure(True, changed=True)
                        await service.receive(self.account, adapter, self.message("structured continue", peer=peer, identifier=f"{peer}-on"))
                        await self.wait_idle(service)
                        new_final = next(text for mid, text, done in reversed(adapter.replies) if mid == f"{peer}-on" and done)
                        self.assertTrue(new_final.startswith("changed_result\n```json\n"), new_final)
                        self.assertNotIn("native-", new_final)
                        history = project_session_events(await self.stores.coordinator.list_all_events(new_sid))
                        outputs = [item for item in history.items if item.kind == "structured_output"]
                        self.assertEqual(len(outputs), 2)
                        self.assertEqual(outputs[-1].output, {"new": False})
                        self.assertEqual((await self.store.get_binding(self.account.id, "single", peer)).session_id, new_sid)
                        self.assertEqual([r.public_dict() for r in await self.stores.coordinator.list_all_events(old_sid)
                                          if r.event_type == "output/attempt"], old_outputs)
        finally:
            for service in services:
                await service.close_account(self.account.id)
            await persister.aclose()
