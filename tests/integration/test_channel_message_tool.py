"""Shared outbound boundary with real SQLite channel context, file store and gateway ASGI."""
import asyncio
import hashlib
import json
import tempfile
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.channels.gateway.app import GatewayContext, create_gateway_app
from deep_agent.channels.message_tool import MessageComponent, build_channel_message_toolset
from deep_agent.channels.operations import ChannelOperationService, MessageOperationRequest
from deep_agent.channels.outbound import ComponentReceipt, DeliveryError, MessageCapabilities, PreparedComponent, resolve_run_context
from deep_agent.files.storage import LocalFileStore
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.schemas.channel import ChannelAccountCreate, ChannelAccountUpdate
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class ChannelMessageIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, self.factory = await make_sqlite_stores()
        self.addAsyncCleanup(self.engine.dispose)
        self.agent_id = await seed_default_agent(self.stores)
        self.store = self.stores.channel
        self.account = await self.store.create_account(ChannelAccountCreate(name="fixture", bot_id="bot", secret="fixture-secret", agent_id=self.agent_id, session_user_id="owner", enabled=True))
        resolved = await self.store.resolve_binding(account_id=self.account.id, peer_type="single", peer_id="current-peer")
        self.session_id = resolved.session.session_id
        claim = await self.store.claim_message(account_id=self.account.id, binding_id=resolved.binding.id,
            external_message_id="current-message", external_request_id=None, sender_id="current-peer", session_id=self.session_id, payload_hash="f"*64)
        self.run = await self.store.update_run(claim.run.id, agent_run_id="agent-run", processing_status="running")
        self.job = SimpleNamespace(stopped=False, terminal=False, run_id=self.run.id, outbound_tasks=set(), sent_text_hashes=set(), message_tool_sent=False)
        self.caps = MessageCapabilities(("text", "image", "file", "audio", "video"), True, 20000, 10000, 2000, 2000, 2000, audio_formats=("opus",))
        self.sent = []
        async def send(target, component, **options):
            self.sent.append((target, component, options))
            return ComponentReceipt(component.kind, message_id=f"message-{len(self.sent)}")
        self.adapter = SimpleNamespace(status=lambda: {"authenticated": True}, message_capabilities=lambda: self.caps, send_component=AsyncMock(side_effect=send))
        self.managed = SimpleNamespace(stopping=False, adapter=self.adapter, fingerprint="fingerprint")
        self.runtime = SimpleNamespace(store=self.store, _accounts={self.account.id: self.managed},
            registry=SimpleNamespace(fingerprint=lambda _: "fingerprint"),
            service=SimpleNamespace(peers={(self.account.id, "single", "current-peer"): SimpleNamespace(active=self.job)}))
        self.operations = ChannelOperationService(self.runtime); self.runtime.operations = self.operations
        self.source = await resolve_run_context(self.store, account_id=self.account.id, channel_run_id=self.run.id, session_id=self.session_id, agent_id=self.agent_id, user_id="owner")

    def command(self, components, **changes):
        return MessageOperationRequest(**{**{key: getattr(self.source, key) for key in ("account_id", "channel_run_id", "session_id", "agent_id", "user_id")},
            "agent_run_id": "agent-run", "components": [{key: value for key,value in asdict(item).items() if key != "data"} for item in components], **changes})

    def items(self):
        return [PreparedComponent("text", text="first", requested_kind="text"), PreparedComponent("file", data=b"content", filename="test.txt", requested_kind="file"), PreparedComponent("text", text="last", requested_kind="text")]

    async def test_order_preserves_platform_results_without_receipt_identity(self):
        items = self.items(); command = self.command(items)
        result = await self.operations.send(command, items)
        self.assertNotIn("operation_id", result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([r["message_id"] for r in result["components"]], ["message-1", "message-2", "message-3"])
        self.assertEqual([c.text if c.kind == "text" else c.data for _, c, _ in self.sent], ["first", b"content", "last"])
        self.assertTrue(all(t.peer_id == "current-peer" and t.account_id == self.account.id for t, _, _ in self.sent))
        self.assertEqual(self.job.sent_text_hashes, {hashlib.sha256(x.encode()).hexdigest() for x in ("first", "last")})
    async def test_partial_failure_stops_rest_without_automatic_retry(self):
        self.adapter.send_component.side_effect = [ComponentReceipt("text", message_id="first"), DeliveryError("permission_denied", "拒绝发送")]
        result = await self.operations.send(self.command(self.items()), self.items())
        self.assertEqual(result["status"], "failed")
        self.assertEqual([r["state"] for r in result["components"]], ["succeeded", "failed", "skipped"])
        self.assertEqual(self.adapter.send_component.await_count, 2)

    async def test_unknown_send_is_not_retried_or_marked_visible(self):
        self.adapter.send_component.side_effect = DeliveryError("send_unknown", "结果未知", unknown=True)
        result = await self.operations.send(self.command(self.items()), self.items())
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["visible_to_current_chat"])
        self.assertEqual([r["state"] for r in result["components"]], ["unknown", "skipped", "skipped"])
        self.assertFalse(self.job.sent_text_hashes)

    async def test_entire_batch_is_prevalidated_before_first_side_effect(self):
        items = [self.items()[0], PreparedComponent("file", data=b"x"*10001, requested_kind="file")]
        with self.assertRaises(DeliveryError):
            await self.operations.send(self.command(items), items)
        self.adapter.send_component.assert_not_awaited()

    async def test_quote_unsupported_and_disconnected_adapter_do_not_send(self):
        from dataclasses import replace
        self.caps = replace(self.caps, supports_reply=False)
        with self.assertRaises(DeliveryError) as caught:
            await self.operations.send(self.command(self.items(), reply=True), self.items())
        self.assertEqual(caught.exception.code, "quote_unsupported")
        self.adapter.status = lambda: {"authenticated": False}
        with self.assertRaises(DeliveryError) as caught:
            await self.operations.send(self.command(self.items()), self.items())
        self.assertEqual(caught.exception.code, "channel_offline")
        self.adapter.send_component.assert_not_awaited()

    async def test_real_gateway_client_http_server_error_is_unknown(self):
        from aiohttp import web
        from deep_agent.services.channel_gateway_client import ChannelGatewayClient
        async def failed(request):
            await request.read()
            return web.json_response({"detail": "Internal failure after possible delivery"}, status=503)
        app = web.Application(); app.router.add_post("/messages/send", failed)
        runner = web.AppRunner(app); await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        try:
            await site.start()
            client = ChannelGatewayClient()
            client.base_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
            client.internal_token = "fixture"
            import aiohttp
            with patch("deep_agent.services.channel_gateway_client.aiohttp.ClientSession", wraps=aiohttp.ClientSession) as sessions:
                result = await client.send_message(self.source, "agent-run", self.items())
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(sessions.call_args.kwargs["timeout"].total, 390)
        finally:
            await runner.cleanup()

    async def test_account_run_and_owner_are_rechecked(self):
        command = self.command(self.items())
        for changes in ({"user_id": "another"}, {"session_id": "another"}, {"agent_run_id": "other-run"}, {"account_id": "other"}):
            with self.assertRaises(DeliveryError):
                await self.operations.send(command.model_copy(update=changes), self.items())
        await self.store.update_account(self.account.id, ChannelAccountUpdate(enabled=False))
        with self.assertRaises(DeliveryError):
            await self.operations.send(command, self.items())
        self.adapter.send_component.assert_not_awaited()

    async def test_session_owner_change_rejects_send_without_receipt_store(self):
        from sqlalchemy import update
        from deep_agent.db.models import ChatSession

        async with self.factory() as db:
            await db.execute(update(ChatSession).where(
                ChatSession.session_id == self.session_id
            ).values(user_id="another-owner"))
            await db.commit()
        with self.assertRaises(DeliveryError) as caught:
            await self.operations.send(self.command(self.items()), self.items())
        self.assertEqual(caught.exception.code, "source_unavailable")
        self.adapter.send_component.assert_not_awaited()

    async def test_mid_batch_account_change_stops_following_component(self):
        async def sent_then_disable(*args, **kwargs):
            await self.store.update_account(self.account.id, ChannelAccountUpdate(enabled=False))
            return ComponentReceipt("text", message_id="first")
        self.adapter.send_component.side_effect = sent_then_disable
        result = await self.operations.send(self.command(self.items()), self.items())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["components"][0]["state"], "succeeded")
        self.adapter.send_component.assert_awaited_once()

    async def test_cancel_inflight_stops_remaining_and_releases_tasks(self):
        entered = asyncio.Event()
        async def block(*args, **kwargs):
            entered.set(); await asyncio.Future()
        self.adapter.send_component.side_effect = block
        task = asyncio.create_task(self.operations.send(self.command(self.items()), self.items()))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.adapter.send_component.assert_awaited_once()
        self.assertFalse(self.operations.tasks); self.assertFalse(self.job.outbound_tasks)

    async def test_tool_reads_real_authorized_filestore_and_delivers_to_operations(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-message-tool-") as directory:
            files = LocalFileStore(directory); await files.initialize()
            artifact = await files.upload(file_type="attachment", file_name="result.txt", mime="text/plain", data=b"generated", request_headers={"user": "owner"})
            deps = AgentDeps(session_id=self.session_id, agent_id=self.agent_id, user="owner", run_id="agent-run", file_store=files, request_headers={"user": "owner"}, session_file_artifacts={artifact.id: {"file_name": artifact.file_name, "mime": artifact.mime}})
            async def gateway_send(source, run_id, components, *, reply):
                return await self.operations.send(self.command(components, reply=reply), components)
            gateway = SimpleNamespace(send_message=gateway_send)
            function = build_channel_message_toolset(self.store, gateway, self.source, self.caps).tools["channel_message"].function
            ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage(), tool_call_id="generated-file")
            result = await function(ctx, [MessageComponent(type="file", file_id=artifact.id)])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(self.sent[0][1].data, b"generated")
            self.assertEqual(self.sent[0][1].filename, "result.txt")

    async def test_gateway_multipart_auth_payload_and_missing_media(self):
        app = create_gateway_app()
        app.state.gateway = GatewayContext(SimpleNamespace(internal_token="fixture-token"), self.runtime, self.engine, "010")
        items = self.items(); metadata = self.command(items).model_dump_json()
        form = {"metadata": (None, metadata, "application/json"), "media_1": ("test.txt", b"content", "text/plain")}
        async with AsyncClient(transport=ASGITransport(app), base_url="http://fixture") as http:
            denied = await http.post("/messages/send", files=form)
            self.assertEqual(denied.status_code, 401)
            headers = {"Authorization": "Bearer fixture-token"}
            missing = await http.post("/messages/send", files={"metadata": (None, metadata)}, headers=headers)
            self.assertEqual(missing.status_code, 422)
            self.adapter.send_component.assert_not_awaited()
            result = await http.post("/messages/send", files=form, headers=headers)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["status"], "completed")
            self.assertEqual(self.sent[1][1].data, b"content")
            invalid = json.loads(metadata); invalid["target"] = "other"
            response = await http.post("/messages/send", files={"metadata": (None, json.dumps(invalid))}, headers=headers)
            self.assertEqual(response.status_code, 422)

    async def test_gateway_preserves_unknown_delivery_errors_as_503(self):
        app = create_gateway_app()
        self.runtime.operations = SimpleNamespace(send=AsyncMock(side_effect=
            DeliveryError("send_unknown", "发送结果无法确认，请勿重复发送", unknown=True)))
        app.state.gateway = GatewayContext(SimpleNamespace(internal_token="fixture-token"), self.runtime, self.engine, "010")
        items = [self.items()[0]]
        async with AsyncClient(transport=ASGITransport(app), base_url="http://fixture") as http:
            response = await http.post("/messages/send",
                files={"metadata": (None, self.command(items).model_dump_json(), "application/json")},
                headers={"Authorization": "Bearer fixture-token"})
        self.assertEqual(response.status_code, 503)

    async def test_tool_does_not_request_model_retry_for_unknown_delivery(self):
        deps = AgentDeps(session_id=self.session_id, agent_id=self.agent_id, user="owner", run_id="agent-run")
        gateway = SimpleNamespace(send_message=AsyncMock(side_effect=
            DeliveryError("unconfirmed", "发送结果未知，请勿重复发送", unknown=True)))
        function = build_channel_message_toolset(self.store, gateway, self.source, self.caps).tools["channel_message"].function
        ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage(), tool_call_id="unknown-test")
        result = await function(ctx, [MessageComponent(type="text", text="hello")])
        self.assertEqual(result["status"], "unknown")
        gateway.send_message.assert_awaited_once()

    async def test_ordinary_agent_runner_has_no_channel_tool_even_when_channel_type_given(self):
        from deep_agent.api.v1 import deps as api_deps
        from deep_agent.capabilities import build_registry
        from deep_agent.core.context import AppContext
        ctx = AppContext.for_testing(registry=build_registry(), stores=self.stores, db_engine=self.engine, db_session_factory=self.factory)
        with patch("deep_agent.agent.factory._resolve_model", return_value=TestModel()), patch.object(api_deps, "build_agent", wraps=api_deps.build_agent) as build:
            await api_deps.build_runner_for_agent(ctx, self.agent_id, channel_type="wecom")
            self.assertIsNone(build.call_args.kwargs["channel_toolset"])
            await api_deps.build_runner_for_agent(ctx, self.agent_id, channel_type="wecom", channel_context=self.source)
            self.assertIn("channel_message", build.call_args.kwargs["channel_toolset"].tools)
        await ctx.event_persister.aclose()
