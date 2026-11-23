"""Reusable fixtures without TestCase inheritance or test methods."""
from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import event, insert

from deep_agent.channels.gateway.deepagent_client import StreamEvent
from deep_agent.channels.adapters.wecom.messages import parse_message
from deep_agent.channels.service import ChannelService
from deep_agent.db.dialect import DbDialect
from deep_agent.db.factory import create_engine
from deep_agent.db.models import Agent, Base, LlmModel, LlmProvider
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.schemas.channel import ChannelAccountCreate


class FakeWeComClient:
    def __init__(self) -> None:
        self.replies: list[tuple[str, bool]] = []

    async def reply_stream(self, _frame, _stream_id, content, finish=False):
        self.replies.append((content, finish))

    def new_reply_id(self):
        return "reply-id"

    async def reply_loading(self, message, reply_id, content=""):
        await self.reply(message, reply_id, f"{content}\n\n正在处理…" if content else "正在处理…", False)

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


class PresentationDeepAgentClient(FakeDeepAgentClient):
    def __init__(self, events):
        self.events = events
        self.calls = 0
        self.closed = False

    async def stream_chat(self, *_args, **_kwargs):
        self.calls += 1
        try:
            yield StreamEvent("session", {"run_id": "presentation-run"})
            for item in self.events:
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            self.closed = True


class PresentationWeComClient(FakeWeComClient):
    def __init__(self, *, fail_loading=False, fail_preview=False, fail_wait=False, fail_final=False):
        super().__init__()
        self.fail_loading = fail_loading
        self.fail_preview = fail_preview
        self.fail_wait = fail_wait
        self.fail_final = fail_final
        self.attempts = []

    async def reply_stream(self, _frame, stream_id, content, finish=False):
        self.attempts.append((stream_id, content, finish))
        if (
            (finish and self.fail_final)
            or (not finish and content == "正在处理…" and self.fail_loading)
            or (not finish and content != "正在处理…" and self.fail_preview)
            or (not finish and content.endswith("\n\n正在处理…") and self.fail_wait)
        ):
            raise ConnectionError("fixture reply transport unavailable")
        await super().reply_stream(_frame, stream_id, content, finish)


class StructuredOutputChannelAdapter(PresentationWeComClient):
    def __init__(self, *, text_limit=20480, fail_native=None):
        super().__init__()
        self.text_limit = text_limit
        self.fail_native = fail_native
        self.native_attempts = []
        self.native_delivered = []

    def message_capabilities(self):
        return SimpleNamespace(text_limit=self.text_limit)

    async def send_component(self, target, component, *, reply_to_current=False):
        from deep_agent.channels.outbound import ComponentReceipt
        request_id = f"native-request-{len(self.native_attempts) + 1}"
        self.native_attempts.append((request_id, target, component.text))
        if len(self.native_attempts) == self.fail_native:
            raise TimeoutError("synthetic uncertain send acknowledgment")
        self.native_delivered.append((request_id, target, component.text))
        return ComponentReceipt(kind="text", request_id=request_id)


class ChannelGatewayFixture:
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

    async def _run_presentation(self, events, *, message_id="presentation", adapter=None, stream_interval=0, sent_texts=()):
        message = parse_message({
            "headers": {"req_id": f"request-{message_id}"},
            "body": {
                "msgtype": "text", "msgid": message_id,
                "from": {"userid": "presentation-user"},
                "text": {"content": "本地展示测试，不执行业务修改"},
            },
        })
        agent = PresentationDeepAgentClient(events)
        adapter = adapter or PresentationWeComClient()
        service = ChannelService(self.store, agent, stream_interval=stream_interval)
        if sent_texts:
            original_stream = agent.stream_chat
            async def confirmed_send_stream(*args, **kwargs):
                async for event in original_stream(*args, **kwargs):
                    if event.event == "final":
                        job = service.peers[(self.account.id, message.peer_type, message.peer_id)].active
                        job.message_tool_sent = True
                        job.sent_text_hashes.update(hashlib.sha256(text.strip().encode()).hexdigest() for text in sent_texts)
                    yield event
            agent.stream_chat = confirmed_send_stream
        await service.receive(self.account, adapter, message)
        peer = service.peers[(self.account.id, message.peer_type, message.peer_id)]
        if peer.worker is not None:
            await asyncio.wait_for(peer.worker, timeout=3)
        row = await self.store.get_message(self.account.id, message_id)
        self.assertTrue(agent.closed)
        self.assertTrue(all(stream_id == "reply-id" for stream_id, _, _ in adapter.attempts))
        return service, agent, adapter, row, message
