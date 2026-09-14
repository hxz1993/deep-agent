from __future__ import annotations
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import insert, select
from deep_agent.db.factory import create_engine
from deep_agent.db.models import Agent, Base, ChannelAccount, WecomApplication, WecomUserProfile
from deep_agent.db.stores.wecom import WecomStore, wecom_settings
from deep_agent.db.stores.channel import ChannelStore
from deep_agent.db.reference_schema import logical_reference_violations
from deep_agent.db.configuration_transfer import configuration_table_names
from deep_agent.schemas.channel import ChannelAccountCreate, ChannelAccountUpdate
from deep_agent.schemas.llm_provider import mask_api_key


class WecomStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TMP_to_delete-wecom-store-")
        self.engine, self.factory = create_engine(f"sqlite+aiosqlite:///{Path(self.tmp.name) / 'test.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(insert(Agent), dict(id="a" * 36, name="fixture", model_id=None, capabilities=[], skills=[], prompt=""))
        self.store = WecomStore(self.factory)
        self.channels = ChannelStore(self.factory)
        self.app = await self.store.create_application(name="应用", corp_id="corp-fixture", wecom_agent_id="100", app_secret="fixture-secret-value")

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tmp.cleanup()

    async def account(self, settings):
        return await self.channels.create_account(ChannelAccountCreate(name="渠道", bot_id="bot-fixture", secret="fixture", agent_id="a"*36, session_user_id="fixture", settings=settings))

    async def test_identity_merge_both_orders_concurrent_and_case(self):
        rows = await asyncio.gather(self.store.upsert_identity(self.app.id, 1, "Account"), self.store.upsert_identity(self.app.id, 1, "ACCOUNT", "opaque-A"))
        self.assertEqual(rows[0].id, rows[1].id)
        self.assertEqual(len(await self.store.list_profiles(self.app.id)), 1)
        self.assertEqual((await self.store.find_profile(self.app.id, "account")).open_userid, "opaque-A")
        self.assertIsNone(await self.store.find_profile(self.app.id, "opaque-a"))
        await self.store.upsert_identity(self.app.id, 1, "Other", "opaque-B")
        self.assertEqual((await self.store.upsert_identity(self.app.id, 1, "other")).open_userid, "opaque-B")
        with self.assertRaises(ValueError):
            await self.store.upsert_identity(self.app.id, 1, "Different", "opaque-A")
        with self.assertRaises(ValueError):
            await self.store.upsert_identity(self.app.id, 1, "Account", "opaque-C")

    async def test_week_expiration_failure_and_revision_fencing(self):
        await self.store.upsert_identity(self.app.id, 1, "Account", "opaque")
        self.assertEqual((await self.store.cache_stats(self.app.id))["pending"], 1)
        profile = await self.store.save_profile(self.app.id, 1, "Account", "姓名")
        self.assertEqual(profile.expires_at-profile.profile_synced_at, timedelta(days=7))
        await self.store.record_failure(self.app.id, 1, "Account", "profile", "60111")
        self.assertEqual((await self.store.find_profile(self.app.id, "opaque")).name, "姓名")
        self.assertEqual((await self.store.cache_stats(self.app.id))["pending"], 0)
        app = await self.store.invalidate_cache(self.app.id)
        self.assertEqual(app.revision, 2)
        self.assertEqual((await self.store.cache_stats(self.app.id))["pending"], 1)
        self.assertIsNone(await self.store.save_profile(self.app.id, 1, "Account", "迟到"))
        self.assertIsNone(await self.store.upsert_identity(self.app.id, 1, "Late"))
        self.assertEqual((await self.store.find_profile(self.app.id, "opaque")).name, "姓名")

    async def test_config_secret_identity_and_delete_reference(self):
        for secret in (None, "", mask_api_key(self.app.app_secret)):
            app = await self.store.update_application(self.app.id, app_secret=secret)
            self.assertEqual(app.app_secret, self.app.app_secret)
            self.assertEqual(app.revision, 1)
        with self.assertRaises(ValueError):
            await self.store.update_application(self.app.id, corp_id="another")
        self.assertEqual((await self.store.update_application(self.app.id, app_secret="new-fixture")).revision, 2)
        row = await self.account({"other": 5, "wecom": {"application_id": self.app.id}})
        self.assertEqual(wecom_settings(row.settings), (False, self.app.id))
        self.assertEqual(await self.store.application_channel_count(self.app.id), 1)
        with self.assertRaises(ValueError):
            await self.store.delete_application(self.app.id)
        await self.channels.update_account(row.id, ChannelAccountUpdate(settings={"other": 5}))
        self.assertTrue(await self.store.delete_application(self.app.id))

    async def test_invalid_settings_and_json_reference_validation(self):
        for settings in ({"wecom": {"user_details_enabled": True}}, {"wecom": {"application_id": "missing"}}, {"wecom": {"user_details_enabled": "false"}}):
            with self.assertRaises(ValueError):
                await self.account(settings)
        await self.account({})
        async with self.engine.begin() as conn:
            await conn.execute(ChannelAccount.__table__.update().values(settings={"wecom":{"application_id":"missing"}}))
            problems = await conn.run_sync(logical_reference_violations)
        self.assertEqual(problems[0]["column"], "settings.wecom.application_id")

    async def test_cache_excluded_and_app_isolated(self):
        names = configuration_table_names()
        self.assertIn("wecom_applications", names)
        self.assertNotIn("wecom_user_profiles", names)
        second = await self.store.create_application(name="应用2", corp_id="corp-fixture", wecom_agent_id="101", app_secret="fixture")
        a = await self.store.upsert_identity(self.app.id, 1, "account", "same-opaque")
        b = await self.store.upsert_identity(second.id, 1, "account", "same-opaque")
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(await self.store.list_profiles(self.app.id, created_before=datetime(2000,1,1,tzinfo=timezone.utc)), [])
