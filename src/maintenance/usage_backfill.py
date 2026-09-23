"""Idempotent 007 backfill of verifiable historical model response usage.

Dry-run by default. Existing events, timestamps and payloads remain unchanged.
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deep_agent.db.models import ChatSessionEvent
from deep_agent.usage import merge_calls, response_call
from maintenance.config_backup import database_url_from_config


async def backfill(database_url: str, *, apply: bool = False, batch_size: int = 500):
    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    scanned = recorded = 0
    cursor = None
    last_key = None
    try:
        async with factory() as db:
            revision = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            if revision != "007": raise RuntimeError(f"Expected database revision 007, found {revision}")
        while True:
            async with factory() as db:
                query = select(ChatSessionEvent).where(ChatSessionEvent.event_type == "assistant/message")
                if cursor:
                    query = query.where((ChatSessionEvent.session_id > cursor[0]) |
                        ((ChatSessionEvent.session_id == cursor[0]) & (ChatSessionEvent.sequence > cursor[1])))
                rows = (await db.execute(query.order_by(ChatSessionEvent.session_id,
                    ChatSessionEvent.sequence).limit(batch_size))).scalars().all()
                if not rows: break
                for row in rows:
                    scanned += 1
                    source = row.payload.get("source") or {}
                    ordinal = source.get("message_ordinal", row.sequence)
                    key = (row.session_id, row.run_id, ordinal)
                    if key == last_key: continue
                    response = row.payload.get("message") or {}
                    if response.get("kind") != "response": continue
                    request_id = f"{row.run_id}:{ordinal}" if row.run_id else row.event_id
                    call = response_call(response, request_id=request_id,
                        model_id=None, provider_id=None, purpose="chat")
                    if call["state"] == "unreported": continue
                    last_key = key
                    if row.usage is not None: continue
                    recorded += 1
                    if apply:
                        await db.execute(update(ChatSessionEvent)
                            .where(ChatSessionEvent.event_id == row.event_id)
                            .values(usage=merge_calls(None, [call]), updated_at=row.updated_at))
                cursor = (rows[-1].session_id, rows[-1].sequence)
                if apply: await db.commit()
                if len(rows) < batch_size: break
        return {"scanned": scanned, "eligible": recorded, "applied": recorded if apply else 0}
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Database profile path")
    parser.add_argument("--apply", action="store_true", help="Write eligible usage; default is dry-run")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 2000: parser.error("batch-size must be 1..2000")
    print(asyncio.run(backfill(database_url_from_config(args.config),
        apply=args.apply, batch_size=args.batch_size)))


if __name__ == "__main__": main()
