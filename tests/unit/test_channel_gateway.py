from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event, insert, select

from deep_agent.channel_gateway.app import create_gateway_app
from deep_agent.channel_gateway.config import GatewayConfig
from deep_agent.channel_gateway.deepagent_client import DeepAgentChannelClient, StreamEvent
from deep_agent.channel_gateway.messages import parse_message
from deep_agent.channel_gateway.runtime import AccountRuntime, ChannelRuntime
from deep_agent.channels.service import ChannelService
from deep_agent.db.dialect import DbDialect
from deep_agent.db.factory import create_engine
from deep_agent.db.models import Agent, Base, ChannelMessageRun, ChatSession, LlmModel, LlmProvider
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.schemas.channel import ChannelAccountCreate, ChannelAccountUpdate


class FakeWeComClient:
    def __init__(self) -> None:
        self.replies: list[tuple[str, bool]] = []

    async def reply_stream(self, _frame, _stream_id, content, finish=False):
        self.replies.append((content, finish))

    def new_reply_id(self):
        return "reply-id"

    async def reply(self, message, reply_id, content, finish):
        await self.reply_stream(None, reply_id, content, finish)


class FakeDeepAgentClient:
    async def upload_media(self, *_args, **_kwargs):
        raise AssertionError("text test must not upload media")

    async def stream_chat(self, *_args, **_kwargs):
        yield StreamEvent("session", {"run_id": "run-1"})
        yield StreamEvent("thinking", {"delta": "hidden"})
        yield StreamEvent("tool_call", {"tool": "hidden_tool"})
        yield StreamEvent("text", {"delta": "你"})
        yield StreamEvent("text", {"delta": "好"})
        yield StreamEvent("final", {"status": "completed"})


class ChannelGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        path = Path(self.tmp.name) / "channel.db"
        self.engine, self.factory = create_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.factory.begin() as db:
            await db.execute(
                insert(LlmProvider.__table__),
                {
                    "id": "1" * 32,
                    "name": "provider",
                    "provider_type": "openai",
                    "use_default_base_url": True,
                    "base_url": "https://example.test/v1",
                },
            )
            await db.execute(
                insert(LlmModel.__table__),
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "provider_id": "1" * 32,
                    "name": "model",
                    "model": "openai:test",
                    "enabled": True,
                },
            )
            for number in (1, 2):
                await db.execute(
                    insert(Agent.__table__),
                    {
                        "id": f"00000000-0000-0000-0000-00000000000{number}",
                        "name": f"agent-{number}",
                        "model_id": "00000000-0000-0000-0000-000000000001",
                        "capabilities": [],
                        "skills": [],
                        "prompt": "test",
                    },
                )
        self.stores = create_data_stores(DbDialect.SQLITE, self.factory)
        assert self.stores.channel is not None
        self.store = self.stores.channel
        self.account = await self.store.create_account(
            ChannelAccountCreate(
                name="企微测试",
                bot_id="bot",
                secret="secret",
                agent_id="00000000-0000-0000-0000-000000000001",
                session_user_id="owner",
                enabled=True,
            ),
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self.tmp.cleanup()

    async def test_binding_reuses_session_and_rotates_after_agent_change_or_delete(self) -> None:
        first = await self.store.resolve_binding(
            account_id=self.account.id,
            peer_type="single",
            peer_id="user-1",
        )
        second = await self.store.resolve_binding(
            account_id=self.account.id,
            peer_type="single",
            peer_id="user-1",
        )
        self.assertEqual(first.session.session_id, second.session.session_id)
        self.assertFalse(second.created_session)
        self.assertEqual(first.binding.updated_at, second.binding.updated_at)
        self.assertFalse(hasattr(second.binding, "generation"))
        self.assertFalse(hasattr(second.binding, "last_active_at"))

        await self.store.update_account(
            self.account.id,
            ChannelAccountUpdate(agent_id="00000000-0000-0000-0000-000000000002"),
        )
        rotated = await self.store.resolve_binding(
            account_id=self.account.id,
            peer_type="single",
            peer_id="user-1",
        )
        self.assertNotEqual(rotated.session.session_id, first.session.session_id)
        self.assertEqual(rotated.binding.id, first.binding.id)

        async with self.factory.begin() as db:
            await db.execute(
                delete(ChatSession).where(
                    ChatSession.session_id == rotated.session.session_id
                )
            )
        recreated = await self.store.resolve_binding(
            account_id=self.account.id,
            peer_type="single",
            peer_id="user-1",
        )
        self.assertNotEqual(recreated.session.session_id, rotated.session.session_id)
        self.assertEqual(recreated.binding.id, first.binding.id)

    async def test_session_user_change_creates_session_without_reassigning_old_history(self):
        first = await self.store.resolve_binding(account_id=self.account.id, peer_type="single", peer_id="u")
        await self.store.update_account(self.account.id, ChannelAccountUpdate(session_user_id="operator"))
        second = await self.store.resolve_binding(account_id=self.account.id, peer_type="single", peer_id="u")
        self.assertNotEqual(first.session.session_id, second.session.session_id)
        self.assertEqual(second.session.user_id, "operator")
        async with self.factory() as db:
            old = await db.get(ChatSession, first.session.session_id)
            self.assertEqual(old.user_id, "owner")

    async def test_restart_does_not_retry_dispatched_request_without_sse_run_id(self):
        binding = await self.store.resolve_binding(account_id=self.account.id, peer_type="single", peer_id="u")
        fields = dict(account_id=self.account.id, binding_id=binding.binding.id,
                      external_message_id="lost-sse", external_request_id="r", sender_id="u",
                      session_id=binding.session.session_id, payload_hash="a" * 64)
        first = await self.store.claim_message(**fields)
        await self.store.update_run(first.run.id, processing_status="running")
        await self.store.interrupt_incomplete()
        self.assertFalse((await self.store.claim_message(**fields)).claimed)
        fields["external_message_id"] = "before-http"
        await self.store.claim_message(**fields)
        await self.store.interrupt_incomplete()
        self.assertTrue((await self.store.claim_message(**fields)).claimed)

    def test_deepagent_headers_only_carry_owner_and_internal_auth(self) -> None:
        config = GatewayConfig(
            api_url="http://127.0.0.1:8000",
            internal_token="local-test",
            poll_interval=3,
            stream_interval=1,
            test_timeout=3,
            host="127.0.0.1",
            port=8010,
            platform_authorization="",
            platform_cookie="",
        )
        headers = DeepAgentChannelClient(config)._headers(self.account)
        self.assertEqual(
            headers,
            {
                "user": "owner",
                "X-Deep-Agent-Channel-Token": "local-test",
                "X-Deep-Agent-Channel-Account": self.account.id,
            },
        )

    async def test_message_claim_is_idempotent_and_only_unknown_pre_run_can_retry(self) -> None:
        binding = await self.store.resolve_binding(
            account_id=self.account.id,
            peer_type="group",
            peer_id="chat-1",
        )
        fields = dict(
            account_id=self.account.id,
            binding_id=binding.binding.id,
            external_message_id="msg-1",
            external_request_id="req-1",
            sender_id="user-1",
            session_id=binding.session.session_id,
            payload_hash="a" * 64,
        )
        first = await self.store.claim_message(**fields)
        duplicate = await self.store.claim_message(**fields)
        self.assertTrue(first.claimed)
        self.assertFalse(duplicate.claimed)

        await self.store.update_run(
            first.run.id,
            processing_status="failed",
            delivery_status="failed",
            error_code="preflight_failed",
        )
        retry = await self.store.claim_message(**fields)
        self.assertTrue(retry.claimed)
        self.assertEqual(retry.run.attempt_count, 2)
        await self.store.update_run(
            retry.run.id,
            processing_status="running",
            agent_run_id="agent-run",
        )
        await self.store.update_run(retry.run.id, processing_status="interrupted")
        unknown_result = await self.store.claim_message(**fields)
        self.assertFalse(unknown_result.claimed)

    async def test_text_message_streams_only_agent_text_and_completes_run(self) -> None:
        frame = {
            "headers": {"req_id": "req-1"},
            "body": {
                "msgtype": "text",
                "msgid": "msg-stream",
                "from": {"userid": "user-1"},
                "text": {"content": "你好"},
            },
        }
        message = parse_message(frame)
        config = GatewayConfig(
            api_url="http://127.0.0.1:8000",
            internal_token="local-test",
            poll_interval=3,
            stream_interval=999,
            test_timeout=3,
            host="127.0.0.1",
            port=8010,
            platform_authorization="",
            platform_cookie="",
        )
        service = ChannelService(self.store, FakeDeepAgentClient(), stream_interval=999)
        client = FakeWeComClient()
        statements = []
        def record(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement.upper())
        event.listen(self.engine.sync_engine, "before_cursor_execute", record)
        try:
            await service.receive(self.account, client, message)
            await service.peers[(self.account.id, message.peer_type, message.peer_id)].worker
            run_updates = [s for s in statements if s.startswith("UPDATE CHANNEL_MESSAGE_RUNS")]
            self.assertLessEqual(len(run_updates), 6)  # queue/session/dispatch/id/result/delivery; never per token
            statements.clear()
            await service.receive(self.account, client, message)
            self.assertFalse(any(s.startswith(("UPDATE ", "INSERT ")) for s in statements))
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", record)
        self.assertEqual(client.replies[0], ("正在处理…", False))
        self.assertEqual(client.replies[-1], ("你好", True))
        self.assertNotIn("hidden", "".join(content for content, _ in client.replies))
        row = await self.store.get_message(self.account.id, "msg-stream")
        self.assertEqual((row.processing_status, row.delivery_status), ("completed", "sent"))
        self.assertIsNotNone(row.finished_at)
        self.assertIsNotNone(row.created_at)
        self.assertFalse(hasattr(row, "updated_at"))

    def test_peer_type_records_upstream_id_key(self):
        body = {"msgtype": "text", "msgid": "m", "chattype": "single",
                "chatid": "ignored-private-chat", "from": {"userid": "u"}, "text": {"content": "hello"}}
        message = parse_message({"body": body})
        self.assertEqual((message.peer_type, message.peer_id), ("single", "u"))
        body.update(chattype="group", chatid="g")
        message = parse_message({"body": body})
        self.assertEqual((message.peer_type, message.peer_id), ("group", "g"))
        body.pop("chatid")
        with self.assertRaises(ValueError):
            parse_message({"body": body})

    def test_parser_supports_group_mixed_and_generic_video(self) -> None:
        mixed = parse_message(
            {
                "headers": {"req_id": "r"},
                "body": {
                    "msgtype": "mixed",
                    "msgid": "m",
                    "chatid": "group-1",
                    "from": {"userid": "u"},
                    "mixed": {
                        "msg_item": [
                            {"msgtype": "text", "text": {"content": "说明"}},
                            {"msgtype": "image", "image": {"url": "https://media", "aeskey": "key"}},
                        ]
                    },
                },
            }
        )
        self.assertEqual((mixed.peer_type, mixed.peer_id, mixed.text), ("group", "group-1", "说明"))
        self.assertEqual(mixed.media[0].kind, "image")
        video = parse_message(
            {
                "headers": {"req_id": "r2"},
                "body": {
                    "msgtype": "video",
                    "msgid": "m2",
                    "from": {"userid": "u"},
                    "video": {"url": "https://media", "aeskey": "key"},
                },
            }
        )
        self.assertEqual(video.media[0].mime, "video/mp4")


class ChannelGatewayApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_routes_require_token_and_do_not_expect_context_query(self) -> None:
        app = create_gateway_app()
        runtime = SimpleNamespace(
            status_payload=lambda: {
                "state": "connected",
                "message": "ok",
                "accounts": [],
            },
            test_account=AsyncMock(
                return_value={
                    "account_id": "account-1",
                    "success": True,
                    "state": "connected",
                    "message": "ok",
                    "warning": None,
                }
            ),
        )
        app.state.gateway = SimpleNamespace(
            config=SimpleNamespace(internal_token="gateway-token"),
            runtime=runtime,
        )
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://gateway") as client:
            missing = await client.get("/health")
            health = await client.get(
                "/health", headers={"Authorization": "Bearer gateway-token"}
            )
            statuses = await client.get(
                "/accounts/status",
                headers={"Authorization": "Bearer gateway-token"},
            )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(health.status_code, 200, health.text)
        self.assertEqual(health.json()["database_revision"], "002")
        self.assertEqual(statuses.json()["state"], "connected")


if __name__ == "__main__":
    unittest.main()
