from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import event, func, select

from deep_agent.api.v1.document_parsing import put_document_parsing_settings, router
from deep_agent.core.context import get_app_context
from deep_agent.db.factory import create_engine
from deep_agent.db.models import GlobalSetting
from deep_agent.db.stores.global_setting import GlobalSettingStore
from deep_agent.schemas.document_parsing import DocumentParsingSettingsWrite
from deep_agent.security import ConsoleAdminAuthorizer


SETTING_KEY = "document_parsing"


def write_body(key: str | None = None, *, provider: str = "paddle"):
    value = {"mode": "provider", "provider": provider}
    if key is not None:
        value["api_key"] = key
    return DocumentParsingSettingsWrite(value=value)


class GatedSettingStore(GlobalSettingStore):
    """Pause a real transaction boundary without replacing reads or writes."""

    def __init__(self, factory, *, after_lock=False):
        super().__init__(factory)
        self.after_lock = after_lock
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.exclusive = None

    @asynccontextmanager
    async def _write(self, *, exclusive=False):
        self.exclusive = exclusive
        if not self.after_lock:
            self.entered.set()
            await self.release.wait()
        async with super()._write(exclusive=exclusive) as db:
            if self.after_lock:
                self.entered.set()
                await self.release.wait()
            yield db


class DocumentConfigurationConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="TMP_to_delete_document_config_")
        url = f"sqlite+aiosqlite:///{Path(self.directory.name) / 'settings.db'}"
        # Independent engines/factories do not share the process-local SQLite lock.
        self.engine, self.factory = create_engine(url)
        self.other_engine, self.other_factory = create_engine(url)
        async with self.engine.begin() as db:
            await db.run_sync(GlobalSetting.__table__.create)
        self.store = GlobalSettingStore(self.factory)
        self.other_store = GlobalSettingStore(self.other_factory)
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.engine.dispose()
        await self.other_engine.dispose()
        self.directory.cleanup()

    def context(self, store):
        return SimpleNamespace(stores=SimpleNamespace(global_setting=store))

    def start_save(self, body, store):
        task = asyncio.create_task(put_document_parsing_settings(body, self.context(store)))
        self.tasks.append(task)
        return task

    async def test_omitted_key_save_uses_rotation_committed_while_waiting_to_write(self):
        await put_document_parsing_settings(write_body("old-fixture-key"), self.context(self.store))
        delayed = GatedSettingStore(self.factory)
        pending = self.start_save(write_body(), delayed)
        try:
            await asyncio.wait_for(delayed.entered.wait(), 2)
            await put_document_parsing_settings(write_body("new-fixture-key"), self.context(self.other_store))
        finally:
            delayed.release.set()
        response = await asyncio.wait_for(pending, 5)
        self.assertTrue(delayed.exclusive)
        self.assertTrue(response.value.api_key_set)
        stored = await self.other_store.get(SETTING_KEY)
        self.assertEqual(stored.value["api_key"], "new-fixture-key")

    async def test_concurrent_first_saves_serialize_across_independent_factories(self):
        holding = GatedSettingStore(self.factory, after_lock=True)
        first = self.start_save(write_body("first-fixture-key"), holding)
        attempted = asyncio.Event()

        def observe_begin(_connection, _cursor, statement, _parameters, _context, _many):
            if statement == "BEGIN IMMEDIATE":
                attempted.set()

        event.listen(self.other_engine.sync_engine, "before_cursor_execute", observe_begin)
        try:
            await asyncio.wait_for(holding.entered.wait(), 2)
            second = self.start_save(write_body("second-fixture-key"), self.other_store)
            await asyncio.wait_for(attempted.wait(), 2)
            self.assertFalse(second.done())
            self.assertTrue(holding.exclusive)
        finally:
            holding.release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 5)
        stored = await self.store.get(SETTING_KEY)
        self.assertEqual(stored.value["api_key"], "second-fixture-key")
        async with self.engine.connect() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(GlobalSetting)), 1)

    async def test_failed_provider_change_rolls_back_and_upsert_remains_replacement(self):
        await self.store.upsert(SETTING_KEY, value={
            "mode": "provider", "provider": "paddle", "api_key": "saved-fixture-key",
        }, version=1)
        with self.assertRaises(HTTPException) as failure:
            await put_document_parsing_settings(write_body(provider="glm"), self.context(self.other_store))
        self.assertEqual(failure.exception.status_code, 422)
        stored = await self.store.get(SETTING_KEY)
        self.assertEqual(stored.value["api_key"], "saved-fixture-key")
        await self.store.upsert(SETTING_KEY, value={}, version=1)
        self.assertEqual((await self.other_store.get(SETTING_KEY)).value, {})

    async def test_admin_http_save_is_masked_preserves_key_and_never_parses(self):
        async def checker(user, headers):
            return user == "enterprise-admin"

        ctx = self.context(self.store)
        ctx.console_admin_authorizer = ConsoleAdminAuthorizer(checker)
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_app_context] = lambda: ctx
        headers = {"user": "enterprise-admin", "authorization": "Bearer fixture-token"}
        url = "/api/v1/global-settings/document-parsing"
        key = "private-fixture-key-1234"
        with patch("deep_agent.api.v1.document_parsing.parse_document", new=AsyncMock()) as parser:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                unauthorized = await client.put(url, headers={"user": "ordinary-user", "authorization": "Bearer fixture-token"},
                                                json=write_body(key).model_dump())
                self.assertEqual(unauthorized.status_code, 403)
                self.assertIsNone(await self.store.get(SETTING_KEY))
                response = await client.put(url, headers=headers, json=write_body(key).model_dump())
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn(key, response.text)
                saved = await client.put(url, headers=headers, json=write_body().model_dump(exclude_none=True))
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertNotIn(key, saved.text)
                current = await client.get(url, headers=headers)
                self.assertEqual(current.status_code, 200, current.text)
                self.assertNotIn(key, current.text)
                self.assertTrue(current.json()["value"]["api_key_set"])
            parser.assert_not_awaited()
        self.assertEqual((await self.other_store.get(SETTING_KEY)).value["api_key"], key)
