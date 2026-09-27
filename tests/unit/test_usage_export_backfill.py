from __future__ import annotations

import csv
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deep_agent.api.v1.diagnostic_usage import export_details
from deep_agent.db.models import Base, ChatSession, ChatSessionEvent
from maintenance.usage_backfill import backfill


class UsageExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_neutralizes_formulas_and_preserves_missing_average(self):
        detail = {"items": [{"key": "=ID()", "requests": 2, "total_tokens": 20,
            "missing_requests": 1}], "users": {"=ID()": {
                "username": "  +FORMULA()", "email": "@example"}},
            "summary": {"total_tokens": 40}}
        with patch("deep_agent.api.v1.diagnostic_usage.detail_data", AsyncMock(return_value=detail)):
            response = await export_details(None, None, None, None, group_by="user")
        row = list(csv.reader(StringIO(response.body.decode("utf-8-sig"))))[1]
        self.assertEqual(row[1:4], ["'=ID()", "'  +FORMULA()", "'@example"])
        self.assertEqual(row[5:], ["2", "20", "", "50.0%", "1"])


class UsageBackfillTests(unittest.IsolatedAsyncioTestCase):
    async def test_backfill_is_dry_by_default_idempotent_and_preserves_existing_usage(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-usage-") as directory:
            url = f"sqlite+aiosqlite:///{Path(directory) / 'usage.db'}"
            engine = create_async_engine(url)
            try:
                async with engine.begin() as db:
                    await db.run_sync(Base.metadata.create_all)
                    await db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
                    await db.execute(text("INSERT INTO alembic_version VALUES ('007')"))
                factory = async_sessionmaker(engine, expire_on_commit=False)
                existing = {"version": 1, "calls": [{"request_id": "native", "kind": "model"}]}
                async with factory() as db:
                    db.add(ChatSession(session_id="session", user_id="user", agent_id=""))
                    for index in range(3):
                        db.add(ChatSessionEvent(event_id=f"event-{index}", session_id="session",
                            sequence=index + 1, event_type="assistant/message", run_id="run",
                            payload={"source": {"message_ordinal": 0 if index < 2 else 1},
                                "message": {"kind": "response", "usage": {"input_tokens": 10, "output_tokens": 2}}},
                            usage=existing if index == 2 else None))
                    await db.commit()
                self.assertEqual((await backfill(url, batch_size=1))["applied"], 0)
                self.assertEqual((await backfill(url, apply=True, batch_size=1))["applied"], 1)
                self.assertEqual((await backfill(url, apply=True, batch_size=1))["applied"], 0)
                async with factory() as db:
                    rows = (await db.execute(select(ChatSessionEvent).order_by(ChatSessionEvent.sequence))).scalars().all()
                    self.assertEqual(rows[0].usage["calls"][0]["input_tokens"], 10)
                    self.assertIsNone(rows[1].usage)
                    self.assertEqual(rows[2].usage, existing)
            finally:
                await engine.dispose()
