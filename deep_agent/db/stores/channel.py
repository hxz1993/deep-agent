from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.models import (
    ChannelAccount,
    ChannelMessageRun,
    ChannelSessionBinding,
    ChatSession,
)
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.schemas.channel import ChannelAccountCreate, ChannelAccountUpdate


@dataclass(frozen=True)
class ResolvedChannelBinding:
    binding: ChannelSessionBinding
    session: ChatSession
    created_session: bool


@dataclass(frozen=True)
class ClaimedChannelMessage:
    run: ChannelMessageRun
    claimed: bool


class ChannelStore(SessionBoundMixin):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(session_factory)

    async def list_accounts(self) -> list[ChannelAccount]:
        stmt = select(ChannelAccount).order_by(ChannelAccount.name, ChannelAccount.id)
        async with self._read() as db:
            return list((await db.execute(stmt)).scalars().all())

    async def list_enabled_accounts(self) -> list[ChannelAccount]:
        async with self._read() as db:
            rows = await db.execute(
                select(ChannelAccount)
                .where(ChannelAccount.enabled.is_(True))
                .order_by(ChannelAccount.id)
            )
            return list(rows.scalars().all())

    async def get_account(self, account_id: str) -> ChannelAccount | None:
        async with self._read() as db:
            return await self._get_account(db, account_id)

    async def create_account(
        self, data: ChannelAccountCreate
    ) -> ChannelAccount:
        try:
            async with self._write() as db:
                row = ChannelAccount(
                    id=uuid4().hex,
                    **data.model_dump(),
                )
                db.add(row)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            raise ValueError("同一企微 Bot 只能配置一次") from exc

    async def update_account(
        self,
        account_id: str,
        data: ChannelAccountUpdate,
    ) -> ChannelAccount | None:
        try:
            async with self._write() as db:
                row = (
                    await db.execute(
                        select(ChannelAccount)
                        .where(
                            ChannelAccount.id == account_id,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None:
                    return None
                for field, value in data.model_dump(exclude_unset=True).items():
                    setattr(row, field, value)
                row.updated_at = datetime.now(timezone.utc)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            raise ValueError("同一企微 Bot 只能配置一次") from exc

    async def delete_account(self, account_id: str) -> bool:
        async with self._write() as db:
            row = (
                await db.execute(
                    select(ChannelAccount)
                    .where(
                        ChannelAccount.id == account_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            binding_count = int(
                (
                    await db.execute(
                        select(func.count()).select_from(ChannelSessionBinding).where(
                            ChannelSessionBinding.account_id == account_id
                        )
                    )
                ).scalar_one()
            )
            run_count = int(
                (
                    await db.execute(
                        select(func.count()).select_from(ChannelMessageRun).where(
                            ChannelMessageRun.account_id == account_id
                        )
                    )
                ).scalar_one()
            )
            if binding_count or run_count:
                raise ValueError("渠道已经产生会话或运行记录，请停用后保留配置")
            await db.execute(delete(ChannelAccount).where(ChannelAccount.id == account_id))
            return True

    async def list_referencing_agent(self, agent_id: str) -> list[ChannelAccount]:
        async with self._read() as db:
            return list(
                (
                    await db.execute(
                        select(ChannelAccount).where(ChannelAccount.agent_id == agent_id)
                    )
                ).scalars().all()
            )

    async def resolve_binding(
        self,
        *,
        account_id: str,
        peer_type: str,
        peer_id: str,
        peer_display_name: str | None = None,
    ) -> ResolvedChannelBinding:
        if peer_type not in {"userid", "chatid"}:
            raise ValueError(f"Unsupported peer type: {peer_type}")
        now = datetime.now(timezone.utc)
        async with self._write() as db:
            account = await self._get_account(db, account_id, for_update=True)
            if account is None:
                raise LookupError("Channel account not found")
            binding = (
                await db.execute(
                    select(ChannelSessionBinding)
                    .where(
                        ChannelSessionBinding.account_id == account_id,
                        ChannelSessionBinding.peer_type == peer_type,
                        ChannelSessionBinding.peer_id == peer_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            session = None
            if binding is not None and binding.session_id:
                session = (
                    await db.execute(
                        select(ChatSession).where(
                            ChatSession.session_id == binding.session_id
                        )
                    )
                ).scalar_one_or_none()
                if session is not None and (
                    session.agent_id != account.agent_id
                    or session.user_id != account.session_user_id
                ):
                    session = None
            created_session = session is None
            if created_session:
                label = (peer_display_name or peer_id).strip()
                prefix = "企微信群聊" if peer_type == "chatid" else "企微私聊"
                session = ChatSession(
                    session_id=uuid4().hex,
                    agent_id=account.agent_id,
                    user_id=account.session_user_id,
                    title=f"{prefix} · {label}",
                    next_sequence=1,
                    created_at=now,
                    updated_at=now,
                )
                db.add(session)
                await db.flush()
            if binding is None:
                binding = ChannelSessionBinding(
                    id=uuid4().hex,
                    account_id=account_id,
                    peer_type=peer_type,
                    peer_id=peer_id,
                    peer_display_name=peer_display_name,
                    session_id=session.session_id,
                    created_at=now,
                    updated_at=now,
                )
                db.add(binding)
            else:
                if created_session:
                    binding.session_id = session.session_id
                    binding.updated_at = now
            await db.flush()
            await db.refresh(binding)
            return ResolvedChannelBinding(binding, session, created_session)

    async def claim_message(
        self,
        *,
        account_id: str,
        binding_id: str,
        external_message_id: str,
        external_request_id: str | None,
        sender_id: str | None,
        session_id: str,
        payload_hash: str,
        stream_id: str | None = None,
    ) -> ClaimedChannelMessage:
        now = datetime.now(timezone.utc)
        try:
            async with self._write() as db:
                row = ChannelMessageRun(
                    id=uuid4().hex,
                    account_id=account_id,
                    binding_id=binding_id,
                    external_message_id=external_message_id,
                    external_request_id=external_request_id,
                    sender_id=sender_id,
                    session_id=session_id,
                    payload_hash=payload_hash,
                    stream_id=stream_id,
                    processing_status="received",
                    delivery_status="pending",
                    attempt_count=1,
                    created_at=now,
                )
                db.add(row)
                await db.flush()
                await db.refresh(row)
                return ClaimedChannelMessage(row, True)
        except IntegrityError:
            pass

        async with self._write() as db:
            row = (
                await db.execute(
                    select(ChannelMessageRun)
                    .where(
                        ChannelMessageRun.account_id == account_id,
                        ChannelMessageRun.external_message_id == external_message_id,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            retryable = self.can_retry(row)
            if retryable:
                row.processing_status = "received"
                row.delivery_status = "pending"
                row.attempt_count += 1
                row.error_code = None
                row.error_message = None
                row.finished_at = None
                row.stream_id = stream_id
                row.binding_id = binding_id
                row.session_id = session_id
                row.external_request_id = external_request_id
                await db.flush()
            return ClaimedChannelMessage(row, retryable)

    @staticmethod
    def can_retry(row: ChannelMessageRun) -> bool:
        return (
            row.processing_status in {"failed", "interrupted"}
            and not row.agent_run_id
            and row.error_code == "preflight_failed"
        )

    async def get_message(self, account_id: str, message_id: str) -> ChannelMessageRun | None:
        async with self._read() as db:
            return (await db.execute(select(ChannelMessageRun).where(
                ChannelMessageRun.account_id == account_id,
                ChannelMessageRun.external_message_id == message_id,
            ))).scalar_one_or_none()

    async def update_run(
        self,
        run_id: str,
        *,
        processing_status: str | None = None,
        delivery_status: str | None = None,
        agent_run_id: str | None = None,
        stream_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ChannelMessageRun | None:
        now = datetime.now(timezone.utc)
        values: dict[str, object] = {}
        if processing_status is not None:
            values["processing_status"] = processing_status
            if processing_status in {"completed", "failed", "interrupted"}:
                values["finished_at"] = now
        if delivery_status is not None:
            values["delivery_status"] = delivery_status
        if agent_run_id is not None:
            values["agent_run_id"] = agent_run_id
        if stream_id is not None:
            values["stream_id"] = stream_id
        if error_code is not None:
            values["error_code"] = error_code[:64]
        if error_message is not None:
            values["error_message"] = error_message[:1000]
        if not values:
            return None
        async with self._write() as db:
            await db.execute(
                update(ChannelMessageRun)
                .where(ChannelMessageRun.id == run_id)
                .values(**values)
            )
            return (
                await db.execute(
                    select(ChannelMessageRun).where(ChannelMessageRun.id == run_id)
                )
            ).scalar_one_or_none()

    async def interrupt_incomplete(self) -> int:
        now = datetime.now(timezone.utc)
        async with self._write() as db:
            result = await db.execute(
                update(ChannelMessageRun)
                .where(ChannelMessageRun.processing_status.in_({"received", "running"}))
                .values(
                    processing_status="interrupted",
                    delivery_status="failed",
                    error_code=case(
                        (ChannelMessageRun.processing_status == "received", "preflight_failed"),
                        else_="gateway_restarted",
                    ),
                    error_message="Gateway restarted before completion",
                    finished_at=now,
                )
            )
            return int(result.rowcount or 0)

    async def _get_account(
        self,
        db: AsyncSession,
        account_id: str,
        *,
        for_update: bool = False,
    ) -> ChannelAccount | None:
        stmt = select(ChannelAccount).where(ChannelAccount.id == account_id)
        if for_update:
            stmt = stmt.with_for_update()
        return (await db.execute(stmt)).scalar_one_or_none()


__all__ = [
    "ChannelStore",
    "ClaimedChannelMessage",
    "ResolvedChannelBinding",
]
