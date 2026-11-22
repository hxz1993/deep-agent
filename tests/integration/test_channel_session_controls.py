from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from deep_agent.core.context import get_context
from deep_agent.schemas.channel import ChannelAccountUpdate
from deep_agent.security import ConsoleAdminAuthorizer
from tests.helpers.channel_accounts import ChannelAccountsFixture
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events


class ChannelSessionControlTests(ChannelAccountsFixture, unittest.IsolatedAsyncioTestCase):

    async def prepare(self):
        payload = self.payload()
        payload["enabled"] = True
        created = await self.request("POST", "/api/v1/channel-accounts", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        account_id = created.json()["id"]
        resolved = await self.stores.channel.resolve_binding(account_id=account_id, peer_type="single", peer_id="external")
        headers = {"user": "not-the-session-owner", "X-Deep-Agent-Channel-Account": account_id, "X-Deep-Agent-Channel-Token": "fixture-internal"}
        return account_id, resolved.session, headers

    async def test_channel_title_stays_deterministic_without_title_model_call(self):
        _, session, headers = await self.prepare()
        with patch.dict(os.environ, {"DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "fixture-internal"}), \
             patch("deep_agent.agent.factory._resolve_model", return_value=TestModel(call_tools=[], custom_output_text="渠道回复")), \
             patch("deep_agent.agent.title_generator.generate_title", new_callable=AsyncMock) as title, \
             mock_request_disconnect():
            async with AsyncClient(transport=self.transport, base_url="http://test") as client:
                response = await client.post("/api/v1/chat/stream", headers=headers,
                    json={"agent_id": self.agent_id, "session_id": session.session_id, "message": "测试渠道标题", "capabilities": []})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(any(event["type"] == "final" for event in parse_sse_events(response.text)), response.text)
        title.assert_not_awaited()
        events = await self.stores.coordinator.list_events(session.session_id, event_type="session/title")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["source"], "channel")
        self.assertEqual(events[0].payload["title"], "wecom｜User｜external")

    async def test_both_chat_endpoints_assemble_tools_only_for_trusted_channel_run(self):
        account_id, session, headers = await self.prepare()
        binding = await self.stores.channel.get_binding(account_id, "single", "external")
        claim = await self.stores.channel.claim_message(
            account_id=account_id, binding_id=binding.id, external_message_id="inbound",
            external_request_id=None, sender_id="external", session_id=session.session_id, payload_hash="f" * 64,
        )
        await self.stores.channel.update_run(claim.run.id, processing_status="running")
        run_headers = {**headers, "X-Deep-Agent-Channel-Run": claim.run.id}
        requests = []

        def respond(messages, info):
            requests.append(info)
            return ModelResponse(parts=[TextPart("渠道回复")])

        async def stream(messages, info):
            yield respond(messages, info).parts[0].content

        body = {"agent_id": self.agent_id, "session_id": session.session_id, "message": "渠道测试",
                "capabilities": ["ui:builtin"]}
        model = FunctionModel(respond, stream_function=stream)
        with patch.dict(os.environ, {"DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "fixture-internal"}), \
             patch("deep_agent.agent.factory._resolve_model", return_value=model), mock_request_disconnect():
            async with AsyncClient(transport=self.transport, base_url="http://test") as client:
                for endpoint in ("/api/v1/chat", "/api/v1/chat/stream"):
                    with self.subTest(endpoint=endpoint):
                        response = await client.post(endpoint, headers=run_headers, json=body)
                        self.assertEqual(response.status_code, 200, response.text)
                        if endpoint.endswith("/stream"):
                            self.assertTrue(any(event["type"] == "final" for event in parse_sse_events(response.text)))
                        else:
                            self.assertEqual(response.json()["status"], "completed")
                        info = requests.pop()
                        tools = {tool.name for tool in (*info.function_tools, *info.output_tools)}
                        self.assertTrue({"channel_message", "channel_doc"} <= tools)
                        self.assertTrue({"present", "builtin__confirm", "builtin__choice"}.isdisjoint(tools))
                        self.assertIn("本轮消息来自企业微信机器人", info.instructions)
                        self.assertIn("直接在当前渠道的回复正文", info.instructions)
                        denied = await client.post(endpoint, json=body, headers={
                            "user": "session-user", "X-Deep-Agent-Channel-Run": claim.run.id,
                        })
                        self.assertEqual(denied.status_code, 403, denied.text)
                        self.assertEqual(requests, [])

    async def test_commands_catalog_has_admin_gate_and_no_gateway_dependency(self):
        with patch.dict(os.environ, {"DEEP_AGENT_CHANNEL_GATEWAY_URL": ""}):
            response = await self.request("GET", "/api/v1/channel-commands")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["command"] for item in response.json()["commands"]], ["/help", "/new", "/stop", "/status", "/whoami"])
        self.app.dependency_overrides.clear()
        get_context().console_admin_authorizer = ConsoleAdminAuthorizer(AsyncMock(return_value=False))
        denied = await self.request("GET", "/api/v1/channel-commands")
        self.assertIn(denied.status_code, (401, 403, 503))

    async def test_gateway_controls_owned_session_using_original_identity(self):
        account_id, session, headers = await self.prepare()
        active = get_context().active_runs.register(agent_id=self.agent_id, session_id=session.session_id, user_id="session-user", run_id="fixture-run")
        self.addCleanup(get_context().active_runs.unregister, active)
        await self.stores.channel.update_account(account_id, ChannelAccountUpdate(session_user_id="different-user", enabled=False))
        with patch.dict(os.environ, {"DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "fixture-internal"}):
            async with AsyncClient(transport=self.transport, base_url="http://test") as client:
                url = f"/api/v1/sessions/{session.session_id}"
                state = await client.get(url + "/run-status", headers=headers)
                self.assertEqual(state.status_code, 200, state.text)
                self.assertEqual(state.json()["run_id"], "fixture-run")
                cancelled = await client.post(url + "/cancel", headers=headers)
                self.assertEqual(cancelled.status_code, 200, cancelled.text)
                self.assertEqual(cancelled.json()["status"], "cancel_requested")
                state = await client.get(url + "/run-status", headers=headers)
                self.assertEqual(state.json()["status"], "cancel_requested")

    async def test_invalid_token_cross_channel_and_normal_user_are_denied(self):
        account_id, session, headers = await self.prepare()
        other = self.payload()
        other.update(name="second", bot_id="another", enabled=True)
        second = await self.request("POST", "/api/v1/channel-accounts", json=other)
        with patch.dict(os.environ, {"DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "fixture-internal"}):
            async with AsyncClient(transport=self.transport, base_url="http://test") as client:
                for suffix, method in (("run-status", "GET"), ("cancel", "POST")):
                    path = f"/api/v1/sessions/{session.session_id}/{suffix}"
                    denied = await client.request(method, path, headers={**headers, "X-Deep-Agent-Channel-Token": "wrong"})
                    self.assertEqual(denied.status_code, 401)
                    denied = await client.request(method, path, headers={**headers, "X-Deep-Agent-Channel-Account": second.json()["id"]})
                    self.assertEqual(denied.status_code, 404, denied.text)
                    denied = await client.request(method, path, headers={"user": "wrong-user"})
                    self.assertEqual(denied.status_code, 404)
                    allowed = await client.request(method, path, headers={"user": "session-user"})
                    self.assertEqual(allowed.status_code, 200, allowed.text)

    async def test_rejected_duplicate_preserves_active_channel_run_identity(self):
        account_id, session, headers = await self.prepare()
        resolved = await self.stores.channel.resolve_binding(account_id=account_id, peer_type="single", peer_id="external")
        claim = await self.stores.channel.claim_message(account_id=account_id,
            binding_id=resolved.binding.id, external_message_id="inbound", external_request_id=None,
            sender_id="external", session_id=session.session_id, payload_hash="f" * 64)
        await self.stores.channel.update_run(claim.run.id, processing_status="running", agent_run_id="original-run")
        active = get_context().active_runs.register(agent_id=self.agent_id, session_id=session.session_id,
            user_id="session-user", run_id="original-run", channel_account_id=account_id)
        self.addCleanup(get_context().active_runs.unregister, active)
        headers["X-Deep-Agent-Channel-Run"] = claim.run.id
        with patch.dict(os.environ, {"DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "fixture-internal"}), \
             patch("deep_agent.api.v1.chat.build_runner_for_agent", new_callable=AsyncMock), \
             mock_request_disconnect():
            async with AsyncClient(transport=self.transport, base_url="http://test") as client:
                for endpoint in ("/api/v1/chat", "/api/v1/chat/stream"):
                    response = await client.post(endpoint, headers=headers,
                        json={"agent_id": self.agent_id, "session_id": session.session_id, "message": "duplicate"})
                    if endpoint.endswith("stream"):
                        self.assertTrue(any(event["type"] == "error" for event in parse_sse_events(response.text)), response.text)
                    else:
                        self.assertEqual(response.status_code, 409, response.text)
                    record = await self.stores.channel.get_run(claim.run.id)
                    self.assertEqual(record.agent_run_id, "original-run")

    async def test_session_list_and_detail_return_persisted_origin(self):
        account_id, session, _ = await self.prepare()
        detail = await self.request("GET", f"/api/v1/sessions/{session.session_id}", user="session-user")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["channel"]["account_id"], account_id)
        listing = await self.request("GET", "/api/v1/sessions", user="session-user")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.json()["items"][0]["channel"]["peer_id"], "external")
