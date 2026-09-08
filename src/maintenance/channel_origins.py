"""Idempotently repair local 0.7.0 channel origins; never runs Alembic.

The CLI is SQLite-only, read-only by default, and creates a full private backup
before --apply. Stop API/Gateway during apply to avoid racing title generation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text, update

from deep_agent.db.factory import create_engine
from deep_agent.db.models import ChannelAccount, ChannelMessageRun, ChannelSessionBinding, ChatSession, ChatSessionEvent
from deep_agent.db.stores.channel import ChannelStore


async def repair_wecom_peer_types(store: ChannelStore, *, apply: bool = False) -> dict[str, int]:
    """Correct legacy WeCom ID-key types without rewriting conversation facts.

    Validate all normalized unique keys before changing any row. Conflicting
    bindings require a separate human decision; never merge or delete them.
    """
    aliases = {"userid": "single", "chatid": "group"}
    report = {"bindings_changed": 0, "origins_changed": 0}
    scope = store._write if apply else store._read
    async with scope() as db:
        accounts_stmt = select(ChannelAccount.id).where(ChannelAccount.channel_type == "wecom")
        if apply:
            accounts_stmt = accounts_stmt.with_for_update()
        account_ids = list((await db.execute(accounts_stmt)).scalars())
        bindings = list((await db.execute(select(ChannelSessionBinding).where(
            ChannelSessionBinding.account_id.in_(account_ids),
        ))).scalars()) if account_ids else []
        normalized_keys: set[tuple[str, str, str]] = set()
        for binding in bindings:
            target = aliases.get(binding.peer_type, binding.peer_type)
            key = (binding.account_id, target, binding.peer_id)
            if key in normalized_keys:
                raise ValueError("Cannot repair WeCom peer types: conflicting bindings exist")
            normalized_keys.add(key)
        binding_changes = [(binding, aliases[binding.peer_type]) for binding in bindings if binding.peer_type in aliases]
        # Source snapshots outlive a Binding's current Session pointer. Inspect
        # title events directly so old sessions are corrected as well.
        events = (await db.execute(select(ChatSessionEvent).where(
            ChatSessionEvent.event_type == "session/title",
        ))).scalars().all()
        origin_changes = []
        for event in events:
            origin = event.payload.get("channel")
            if event.payload.get("source") != "channel" or not isinstance(origin, dict):
                continue
            if origin.get("channel_type") != "wecom" or origin.get("peer_type") not in aliases:
                continue
            payload = {**event.payload, "channel": {
                **origin, "peer_type": aliases[origin["peer_type"]],
            }}
            origin_changes.append((event, payload))
        report["bindings_changed"] = len(binding_changes)
        report["origins_changed"] = len(origin_changes)
        if apply:
            for binding, target in binding_changes:
                await db.execute(update(ChannelSessionBinding).where(
                    ChannelSessionBinding.id == binding.id,
                ).values(peer_type=target, updated_at=binding.updated_at))
            for event, payload in origin_changes:
                await db.execute(update(ChatSessionEvent).where(
                    ChatSessionEvent.event_id == event.event_id,
                ).values(payload=payload, updated_at=event.updated_at))
    return report


async def repair_channel_origins(store: ChannelStore, *, apply: bool = False) -> dict[str, int]:
    """Backfill only sessions with an unambiguous persisted channel association."""
    report = {"candidates": 0, "already_present": 0, "repaired": 0, "ambiguous": 0}
    scope = store._write if apply else store._read
    async with scope() as db:
        current = (await db.execute(
            select(ChatSession, ChannelAccount, ChannelSessionBinding)
            .join(ChannelSessionBinding, ChannelSessionBinding.session_id == ChatSession.session_id)
            .join(ChannelAccount, ChannelAccount.id == ChannelSessionBinding.account_id)
        )).all()
        historical = (await db.execute(
            select(ChatSession, ChannelAccount, ChannelSessionBinding)
            .join(ChannelMessageRun, ChannelMessageRun.session_id == ChatSession.session_id)
            .join(ChannelSessionBinding, ChannelSessionBinding.id == ChannelMessageRun.binding_id)
            .join(ChannelAccount, ChannelAccount.id == ChannelMessageRun.account_id)
        )).all()
        candidates: dict[str, tuple] = {}
        ambiguous: set[str] = set()
        for session, account, binding in [*current, *historical]:
            previous = candidates.get(session.session_id)
            if previous is not None and previous[2].id != binding.id:
                ambiguous.add(session.session_id)
            candidates.setdefault(session.session_id, (session, account, binding))
        report["candidates"] = len(candidates)
        report["ambiguous"] = len(ambiguous)
        for session_id, (session, account, binding) in candidates.items():
            if session_id in ambiguous:
                continue
            # The first channel title event is the permanent origin snapshot;
            # manual renames never replace it.
            titles = (await db.execute(select(ChatSessionEvent).where(
                ChatSessionEvent.session_id == session_id,
                ChatSessionEvent.event_type == "session/title",
            ))).scalars().all()
            if any(event.payload.get("source") == "channel" for event in titles):
                report["already_present"] += 1
                continue
            report["repaired"] += 1
            if not apply:
                continue
            latest = await store._events._latest_title(db, session_id)
            # Only replace titles known to be automatic. A legacy direct title
            # without an audit event may have been edited manually.
            preserve_existing = latest is None or latest.payload.get("source") == "user"
            title = session.title if preserve_existing and session.title else store.session_title(account, binding)
            prior_activity = session.updated_at
            await store._events._append_title(
                db, session, title=title, source="channel", source_event_id=None,
                turn_id=None, run_id=None,
                details={
                    "channel": store.session_origin(account, binding),
                    "origin_backfilled": True,
                    "preserved_existing_title": preserve_existing,
                },
            )
            # Maintenance must not reorder historical sessions as new activity.
            session.updated_at = prior_activity
    return report


async def _run(path: Path, *, apply: bool, peer_types_only: bool = False) -> dict[str, object]:
    engine, factory = create_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with engine.connect() as db:
            revision = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            if revision != "002":
                raise ValueError("Channel origin repair requires database revision 002")
        backup = None
        if apply:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = path.with_name(f"{path.stem}-channel-origins-TMP_to_delete-{timestamp}.db")
            if backup.exists():
                raise FileExistsError(backup)
            with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as source:
                with sqlite3.connect(backup) as target:
                    os.chmod(backup, 0o600)
                    source.backup(target)
        store = ChannelStore(factory)
        report: dict[str, object] = await repair_wecom_peer_types(store, apply=apply)
        if not peer_types_only:
            report.update(await repair_channel_origins(store, apply=apply))
        report["applied"] = apply
        if backup:
            report["backup"] = str(backup)
        return report
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, type=Path, help="Existing local SQLite database")
    parser.add_argument("--apply", action="store_true", help="Back up and apply; default is dry-run")
    parser.add_argument("--peer-types-only", action="store_true", help="Only correct legacy WeCom peer types; never change titles or add events")
    args = parser.parse_args()
    path = args.sqlite.expanduser().resolve(strict=True)
    if not path.is_file():
        parser.error("--sqlite must name an existing file")
    print(json.dumps(asyncio.run(_run(path, apply=args.apply, peer_types_only=args.peer_types_only)), ensure_ascii=False))


if __name__ == "__main__":
    main()
