from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import uuid4

from sqlalchemy import case, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.models import (
    Agent,
    ChannelAccount,
    ChannelMessageRun,
    ChannelSessionBinding,
    ChatSession,
    ChatSessionEvent,
    LlmModel,
    WecomApplication,
)
from deep_agent.db.reference_integrity import (
    ReferenceConflict, is_unique_conflict, lock_optional, lock_reference,
)
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.db.deletion import DeletionImpact, DeletionConfirmationRequired
from deep_agent.db.stores.wecom import validate_channel_wecom_settings
from deep_agent.db.stores.event.base import SessionEventStore
from deep_agent.channels.metadata import channel_session_title, normalize_channel_origin, normalize_peer_metadata, wecom_application_id
from deep_agent.channels.app_context import channel_deepfos_app, merge_channel_settings
from deep_agent.agent.request_context import derive_space_from_app
from deep_agent.schemas.channel import ChannelAccountCreate, ChannelAccountUpdate


@dataclass(frozen=True)
class ResolvedChannelBinding:
    binding: ChannelSessionBinding
    session: ChatSession
    created_session: bool
    account: ChannelAccount | None = None


@dataclass(frozen=True)
class ClaimedChannelMessage:
    run: ChannelMessageRun
    claimed: bool


class ChannelStore(SessionBoundMixin):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(session_factory)
        self._events = SessionEventStore(session_factory)

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
            async with self._write(exclusive=True) as db:
                await lock_reference(db, Agent, Agent.id, data.agent_id, "Agent")
                await validate_channel_wecom_settings(db, data.settings)
                row = ChannelAccount(
                    id=uuid4().hex,
                    **{**data.model_dump(), "settings": merge_channel_settings(data.settings)},
                )
                db.add(row)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            if not is_unique_conflict(exc):
                raise
            raise ValueError("同一企微 Bot 只能配置一次") from exc

    async def update_account(
        self,
        account_id: str,
        data: ChannelAccountUpdate,
    ) -> ChannelAccount | None:
        try:
            async with self._write(exclusive=True) as db:
                row = await self._lock_account_for_write(
                    db, account_id, new_agent_id=data.agent_id,
                )
                if row is None:
                    return None
                await validate_channel_wecom_settings(db, data.settings if "settings" in data.model_fields_set else row.settings)
                for field, value in data.model_dump(exclude_unset=True).items():
                    if field == "settings":
                        value = merge_channel_settings(value, row.settings)
                    setattr(row, field, value)
                row.updated_at = datetime.now(timezone.utc)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            if not is_unique_conflict(exc):
                raise
            raise ValueError("同一企微 Bot 只能配置一次") from exc

    async def _deletion_impact(self, db, account_id: str, user_id: str) -> DeletionImpact | None:
        if await self._lock_account_for_write(db, account_id) is None:
            return None
        bindings = (await db.execute(select(ChannelSessionBinding.id, ChannelSessionBinding.session_id)
            .where(ChannelSessionBinding.account_id == account_id).order_by(ChannelSessionBinding.id).with_for_update())).all()
        runs = (await db.execute(select(ChannelMessageRun.id, ChannelMessageRun.session_id)
            .where(ChannelMessageRun.account_id == account_id).order_by(ChannelMessageRun.id).with_for_update())).all()
        session_ids = {sid for _, sid in [*bindings, *runs] if sid}
        sessions = (await db.execute(select(ChatSession.session_id, ChatSession.user_id, ChatSession.created_at)
            .where(ChatSession.session_id.in_(session_ids)))).all() if session_ids else []
        return DeletionImpact.build("channel", account_id, user_id, sessions=[tuple(row) for row in sessions],
                                    bindings=[tuple(row) for row in bindings], runs=[tuple(row) for row in runs])

    async def deletion_impact(self, account_id: str, *, user_id: str) -> DeletionImpact | None:
        async with self._write() as db:
            return await self._deletion_impact(db, account_id, user_id)

    async def delete_account(self, account_id: str, *, user_id: str = "", confirmation_token: str | None = None) -> bool:
        async with self._write() as db:
            impact = await self._deletion_impact(db, account_id, user_id)
            if impact is None:
                return False
            if (impact.counts["binding_count"] or impact.counts["run_count"] or confirmation_token) and confirmation_token != impact.token:
                raise DeletionConfirmationRequired(impact)
            # Keep Agent and ChatSession history; only remove the channel ledger.
            await db.execute(delete(ChannelMessageRun).where(ChannelMessageRun.account_id == account_id))
            await db.execute(delete(ChannelSessionBinding).where(ChannelSessionBinding.account_id == account_id))
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

    async def get_binding(
        self, account_id: str, peer_type: str, peer_id: str,
    ) -> ChannelSessionBinding | None:
        async with self._read() as db:
            return await self._get_binding(db, account_id, peer_type, peer_id)

    async def account_summary(self, account_id: str) -> dict[str, str]:
        async with self._read() as db:
            row = (await db.execute(
                select(ChannelAccount, Agent, LlmModel)
                .join(Agent, Agent.id == ChannelAccount.agent_id)
                .outerjoin(LlmModel, LlmModel.id == Agent.model_id)
                .where(ChannelAccount.id == account_id)
            )).one_or_none()
            if row is None:
                raise LookupError("Channel account not found")
            account, agent, model = row
            return {
                "agent_name": agent.name or account.agent_id,
                "model_name": (model.name or model.model) if model is not None else "未配置",
            }

    async def ensure_binding(
        self, *, account_id: str, peer_type: str, peer_id: str,
        peer_metadata: dict[str, str] | None = None,
    ) -> ChannelSessionBinding:
        """Create a peer pointer without creating an empty model conversation."""
        self._validate_peer(peer_type, peer_id)
        async with self._write() as db:
            account = await self._lock_account_for_write(db, account_id)
            if account is None:
                raise LookupError("Channel account not found")
            return await self._ensure_binding(
                db, account_id, peer_type, peer_id, peer_metadata,
            )

    @staticmethod
    def _validate_peer(peer_type: str, peer_id: str) -> None:
        # The adapter owns conversation types; the common store only enforces
        # the existing physical field bounds, not a WeCom-specific enum.
        if not peer_type or len(peer_type) > 16 or not peer_id:
            raise ValueError("Channel peer type and id are required")

    async def _get_binding(
        self, db: AsyncSession, account_id: str, peer_type: str, peer_id: str,
        *, for_update: bool = False,
    ) -> ChannelSessionBinding | None:
        stmt = select(ChannelSessionBinding).where(
            ChannelSessionBinding.account_id == account_id,
            ChannelSessionBinding.peer_type == peer_type,
            ChannelSessionBinding.peer_id == peer_id,
        )
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def _ensure_binding(
        self, db: AsyncSession, account_id: str, peer_type: str, peer_id: str,
        peer_metadata: dict[str, str] | None,
    ) -> ChannelSessionBinding:
        if await self._get_account(db, account_id, for_update=True) is None:
            raise ReferenceConflict(f"Channel account not found: {account_id}")
        binding = await self._get_binding(
            db, account_id, peer_type, peer_id, for_update=True,
        )
        if binding is None:
            now = datetime.now(timezone.utc)
            binding = ChannelSessionBinding(
                id=uuid4().hex, account_id=account_id, peer_type=peer_type,
                peer_id=peer_id, peer_metadata=normalize_peer_metadata(peer_type, peer_id, peer_metadata),
                session_id=None, created_at=now, updated_at=now,
            )
            db.add(binding)
            await db.flush()
        else:
            self._update_peer_metadata(binding, peer_metadata)
            await db.flush()
        return binding

    @staticmethod
    def _update_peer_metadata(binding: ChannelSessionBinding, metadata: dict[str, str] | None) -> None:
        # Missing callback fields do not erase previously observed source facts.
        incoming = {key: value for key, value in (metadata or {}).items() if isinstance(value, str) and value.strip()}
        normalized = normalize_peer_metadata(
            binding.peer_type, binding.peer_id, {**(binding.peer_metadata or {}), **incoming},
        )
        if normalized != binding.peer_metadata:
            binding.peer_metadata = normalized
            binding.updated_at = datetime.now(timezone.utc)

    @staticmethod
    def session_origin(
        account: ChannelAccount, binding: ChannelSessionBinding,
    ) -> dict[str, Any]:
        return {
            "account_id": account.id, "account_name": account.name,
            "channel_type": account.channel_type,
            "peer_type": binding.peer_type, "peer_id": binding.peer_id,
            "peer_metadata": dict(binding.peer_metadata or {}),
        }

    @staticmethod
    def session_title(account: ChannelAccount, binding: ChannelSessionBinding) -> str:
        application_id = wecom_application_id(account)
        if application_id and binding.peer_type in {"single", "userid"}:
            metadata = binding.peer_metadata or {}
            name = metadata.get("wecom_name") if metadata.get("wecom_application_id") == application_id else None
            return channel_session_title(binding.peer_type, binding.peer_id, {"display_name": name})
        return channel_session_title(binding.peer_type, binding.peer_id, binding.peer_metadata)

    async def refresh_wecom_titles(
        self, account_id: str, *, application_id: str | None = None,
        sender_id: str | None = None, userid: str | None = None,
        name: str | None = None, session_id: str | None = None, revision: int | None = None,
    ) -> int:
        """Refresh active private bindings without rewriting user titles/history.

        No application_id means a settings change: clear previous enrichment.
        A background result must carry the application and original session ID
        to avoid changing a new conversation after /new or an account edit.
        """
        async with self._read() as db:
            peers = list((await db.execute(select(
                ChannelSessionBinding.peer_type, ChannelSessionBinding.peer_id,
            ).where(
                ChannelSessionBinding.account_id == account_id,
                ChannelSessionBinding.peer_type.in_({"single", "userid"}),
            ).order_by(ChannelSessionBinding.id))).all())
        changed = 0
        for peer_type, peer_id in peers:
            async with self._write() as db:
                account, expected_session = await self._lock_peer_account(db, account_id, peer_type, peer_id)
                if account is None or account.channel_type != "wecom":
                    continue
                if application_id and (not account.enabled or wecom_application_id(account) != application_id):
                    continue
                if application_id:
                    application = await lock_optional(db, WecomApplication, WecomApplication.id, application_id)
                    if application is None or (revision is not None and application.revision != revision):
                        continue
                binding = await self._get_binding(db, account_id, peer_type, peer_id, for_update=True)
                if binding is None:
                    continue
                self._check_binding_snapshot(binding, expected_session)
                if session_id is not None and binding.session_id != session_id:
                    continue
                metadata = dict(binding.peer_metadata or {})
                if application_id:
                    # Batch cache refresh can match an already confirmed userid;
                    # an initial query must match the original private peer ID.
                    if peer_id != sender_id and not (
                        userid and metadata.get("wecom_application_id") == application_id
                        and str(metadata.get("wecom_userid", "")).casefold() == userid.casefold()
                    ):
                        continue
                    if metadata.get("wecom_application_id") != application_id or (
                        userid and metadata.get("wecom_userid")
                        and str(metadata["wecom_userid"]).casefold() != userid.casefold()
                    ):
                        metadata.pop("wecom_name", None)
                        metadata.pop("wecom_userid", None)
                    metadata["wecom_application_id"] = application_id
                    if userid:
                        metadata["wecom_userid"] = userid
                    if name:
                        metadata["wecom_name"] = name
                else:
                    for key in ("wecom_application_id", "wecom_userid", "wecom_name"):
                        metadata.pop(key, None)
                if metadata != binding.peer_metadata:
                    binding.peer_metadata = metadata
                    binding.updated_at = datetime.now(timezone.utc)
                if not binding.session_id:
                    continue
                session = await lock_optional(db, ChatSession, ChatSession.session_id, binding.session_id)
                if session is None or session.agent_id != account.agent_id or session.user_id != account.session_user_id:
                    continue
                latest = await self._events._latest_title(db, session.session_id)
                title = self.session_title(account, binding)
                if latest is None or latest.payload.get("source") != "channel" or latest.payload.get("title") == title:
                    continue
                await self._events._append_title(
                    db, session, title=title, source="channel", source_event_id=None,
                    turn_id=None, run_id=None, details={"channel": self.session_origin(account, binding)},
                )
                changed += 1
        return changed

    async def _create_session(
        self, db: AsyncSession, account: ChannelAccount,
        binding: ChannelSessionBinding, peer_metadata: dict[str, str] | None,
    ) -> ChatSession:
        now = datetime.now(timezone.utc)
        self._update_peer_metadata(binding, peer_metadata)
        app_id = channel_deepfos_app(account.settings)
        session = ChatSession(
            session_id=uuid4().hex, agent_id=account.agent_id,
            user_id=account.session_user_id, next_sequence=1,
            app_id=app_id, space_id=derive_space_from_app(app_id) if app_id else None,
            created_at=now, updated_at=now,
        )
        db.add(session)
        await db.flush()
        await self._events._append_title(
            db, session, title=self.session_title(account, binding), source="channel",
            source_event_id=None, turn_id=None, run_id=None,
            details={"channel": self.session_origin(account, binding)}, now=now,
        )
        binding.session_id = session.session_id
        binding.updated_at = now
        await db.flush()
        return session

    async def resolve_binding(
        self,
        *,
        account_id: str,
        peer_type: str,
        peer_id: str,
        peer_metadata: dict[str, str] | None = None,
    ) -> ResolvedChannelBinding:
        self._validate_peer(peer_type, peer_id)
        async with self._write() as db:
            account, expected_session_id = await self._lock_peer_account(
                db, account_id, peer_type, peer_id,
            )
            if account is None:
                raise LookupError("Channel account not found")
            binding = await self._ensure_binding(
                db, account_id, peer_type, peer_id, peer_metadata,
            )
            self._check_binding_snapshot(binding, expected_session_id)
            session = None
            if binding.session_id:
                session = (
                    await db.execute(
                        select(ChatSession).where(
                            ChatSession.session_id == binding.session_id
                        ).with_for_update().execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if session is not None and (
                    session.agent_id != account.agent_id
                    or session.user_id != account.session_user_id
                ):
                    session = None
            created_session = session is None
            if created_session:
                session = await self._create_session(db, account, binding, peer_metadata)
            assert session is not None
            await db.flush()
            await db.refresh(binding)
            return ResolvedChannelBinding(binding, session, created_session, account)

    async def has_user_messages(self, session_id: str) -> bool:
        """Check persisted turns, not title events, Runs, or gateway memory."""
        async with self._read() as db:
            return (await db.scalar(select(ChatSessionEvent.event_id).where(
                ChatSessionEvent.session_id == session_id,
                ChatSessionEvent.event_type == "user/message",
            ).limit(1))) is not None

    async def rotate_binding(
        self, *, account_id: str, peer_type: str, peer_id: str,
        command_run_id: str, peer_metadata: dict[str, str] | None = None,
    ) -> ResolvedChannelBinding:
        """Commit /new's effect and command ledger together, before delivery."""
        self._validate_peer(peer_type, peer_id)
        async with self._write() as db:
            account, expected_session_id = await self._lock_peer_account(
                db, account_id, peer_type, peer_id,
            )
            if account is None:
                raise LookupError("Channel account not found")
            if not account.enabled:
                raise PermissionError("Channel account is disabled")
            binding = await self._get_binding(db, account_id, peer_type, peer_id, for_update=True)
            if binding is not None:
                self._check_binding_snapshot(binding, expected_session_id)
            command = (await db.execute(
                select(ChannelMessageRun).where(ChannelMessageRun.id == command_run_id)
                .with_for_update()
            )).scalar_one_or_none()
            if binding is None or command is None:
                raise LookupError("Channel binding or command not found")
            if command.account_id != account_id or command.binding_id != binding.id:
                raise PermissionError("Command belongs to another channel peer")
            if command.processing_status == "completed":
                session = await db.get(ChatSession, command.session_id) if command.session_id else None
                if session is None:
                    raise LookupError("Completed command session no longer exists")
                return ResolvedChannelBinding(binding, session, False, account)
            if command.processing_status not in {"received", "queued", "running"} or command.agent_run_id:
                raise ValueError("Command is not eligible for session rotation")
            session = await self._create_session(db, account, binding, peer_metadata)
            command.session_id = session.session_id
            command.processing_status = "completed"
            command.finished_at = datetime.now(timezone.utc)
            await db.flush()
            return ResolvedChannelBinding(binding, session, True, account)

    async def get_channel_session(self, account_id: str, session_id: str) -> ChatSession | None:
        """Resolve current or historical sessions, never by caller-supplied user."""
        bindings = select(ChannelSessionBinding.id).where(
            ChannelSessionBinding.account_id == account_id,
            ChannelSessionBinding.session_id == session_id,
        ).exists()
        runs = select(ChannelMessageRun.id).where(
            ChannelMessageRun.account_id == account_id,
            ChannelMessageRun.session_id == session_id,
        ).exists()
        async with self._read() as db:
            return (await db.execute(select(ChatSession).where(
                ChatSession.session_id == session_id, or_(bindings, runs),
            ))).scalar_one_or_none()

    async def adopt_session_scope(
        self, *, account_id: str, session_id: str, agent_id: str, user_id: str,
        app_id: str | None, space_id: str | None,
    ) -> bool:
        """Record the effective scope adopted by an authenticated channel Run.

        Only a session belonging to this channel and the authorized Run identity
        can change. Ordinary sessions keep their original scope semantics. Values
        come from the same request snapshot used by MCP, not a later config read.
        """
        bindings = select(ChannelSessionBinding.id).where(
            ChannelSessionBinding.account_id == account_id,
            ChannelSessionBinding.session_id == session_id,
        ).exists()
        runs = select(ChannelMessageRun.id).where(
            ChannelMessageRun.account_id == account_id,
            ChannelMessageRun.session_id == session_id,
        ).exists()
        async with self._write() as db:
            result = await db.execute(update(ChatSession).where(
                ChatSession.session_id == session_id,
                ChatSession.agent_id == agent_id,
                ChatSession.user_id == user_id,
                or_(bindings, runs),
            ).values(app_id=app_id, space_id=space_id))
            return bool(result.rowcount)

    async def get_session_origin(self, session_id: str) -> dict[str, Any] | None:
        return (await self.get_session_origins([session_id])).get(session_id)

    async def get_session_origins(self, session_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not session_ids:
            return {}
        async with self._read() as db:
            rows = (await db.execute(select(ChatSessionEvent).where(
                ChatSessionEvent.session_id.in_(session_ids),
                ChatSessionEvent.event_type == "session/title",
            ).order_by(ChatSessionEvent.sequence))).scalars().all()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            origin = row.payload.get("channel")
            if row.payload.get("source") == "channel" and isinstance(origin, dict) and row.session_id not in result:
                result[row.session_id] = normalize_channel_origin(origin)
        return result

    async def claim_message(
        self,
        *,
        account_id: str,
        binding_id: str,
        external_message_id: str,
        external_request_id: str | None,
        sender_id: str | None,
        session_id: str | None,
        payload_hash: str,
        stream_id: str | None = None,
    ) -> ClaimedChannelMessage:
        now = datetime.now(timezone.utc)
        try:
            async with self._write() as db:
                await self._lock_run_references(db, account_id, binding_id)
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
        except IntegrityError as exc:
            if not is_unique_conflict(exc):
                raise
            duplicate_error = exc

        async with self._write() as db:
            await self._lock_run_references(db, account_id, binding_id)
            row = (
                await db.execute(
                    select(ChannelMessageRun)
                    .where(
                        ChannelMessageRun.account_id == account_id,
                        ChannelMessageRun.external_message_id == external_message_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                # A different unique key (for example a run ID collision) must
                # not be mistaken for delivery of the same external message.
                raise duplicate_error
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
        session_id: str | None = None,
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
        if session_id is not None:
            values["session_id"] = session_id
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
                .where(ChannelMessageRun.processing_status.in_({"received", "queued", "running"}))
                .values(
                    processing_status="interrupted",
                    delivery_status="failed",
                    error_code=case(
                        (ChannelMessageRun.processing_status.in_({"received", "queued"}), "preflight_failed"),
                        else_="gateway_restarted",
                    ),
                    error_message="Gateway restarted before completion",
                    finished_at=now,
                )
            )
            return int(result.rowcount or 0)

    async def _lock_account_for_write(
        self, db: AsyncSession, account_id: str, *, new_agent_id: str | None = None,
        extra_agent_ids: Sequence[str] = (),
    ) -> ChannelAccount | None:
        # Snapshot IDs only; never retain an ORM object across a lock wait.
        old_agent_id = await db.scalar(select(ChannelAccount.agent_id).where(
            ChannelAccount.id == account_id,
        ))
        if old_agent_id is None:
            return None
        agent_ids = {old_agent_id, *extra_agent_ids}
        if new_agent_id is not None:
            agent_ids.add(new_agent_id)
        for agent_id in sorted(agent_ids):
            await lock_reference(db, Agent, Agent.id, agent_id, "Agent")
        account = await self._get_account(db, account_id, for_update=True)
        if account is not None and account.agent_id != old_agent_id:
            raise ReferenceConflict("Channel Agent changed concurrently; retry the operation")
        return account

    async def _lock_peer_account(
        self, db: AsyncSession, account_id: str, peer_type: str, peer_id: str,
    ) -> tuple[ChannelAccount | None, str | None]:
        # An account can be switched to another Agent while its binding still
        # points at the old Agent's session. Lock both parents in sorted order.
        session_id = await db.scalar(select(ChannelSessionBinding.session_id).where(
            ChannelSessionBinding.account_id == account_id,
            ChannelSessionBinding.peer_type == peer_type,
            ChannelSessionBinding.peer_id == peer_id,
        ))
        session_agent_id = await db.scalar(select(ChatSession.agent_id).where(
            ChatSession.session_id == session_id,
        )) if session_id else None
        account = await self._lock_account_for_write(
            db, account_id, extra_agent_ids=[session_agent_id] if session_agent_id else [],
        )
        if account is not None and session_id:
            await lock_optional(db, ChatSession, ChatSession.session_id, session_id)
        return account, session_id

    @staticmethod
    def _check_binding_snapshot(binding: ChannelSessionBinding, session_id: str | None) -> None:
        if binding.session_id != session_id and binding.session_id is not None:
            raise ReferenceConflict("Channel session changed concurrently; retry the operation")

    async def _lock_run_references(
        self, db: AsyncSession, account_id: str, binding_id: str,
    ) -> ChannelSessionBinding:
        account = await self._lock_account_for_write(db, account_id)
        if account is None:
            raise ReferenceConflict(f"Channel account not found: {account_id}")
        binding = await lock_reference(
            db, ChannelSessionBinding, ChannelSessionBinding.id, binding_id, "Channel binding",
        )
        if binding.account_id != account_id:
            raise ReferenceConflict("Channel binding belongs to another account")
        return binding

    async def _get_account(
        self,
        db: AsyncSession,
        account_id: str,
        *,
        for_update: bool = False,
    ) -> ChannelAccount | None:
        stmt = select(ChannelAccount).where(ChannelAccount.id == account_id)
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await db.execute(stmt)).scalar_one_or_none()


__all__ = [
    "ChannelStore",
    "ClaimedChannelMessage",
    "ResolvedChannelBinding",
]
