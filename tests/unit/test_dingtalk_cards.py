import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from deep_agent.channels.adapters.dingtalk import DingTalkAdapter
from deep_agent.channels.adapters.dingtalk.cards import StreamingCards, TEMPLATE_ID
from deep_agent.channels.adapters.dingtalk.messages import ReplyHandle, parse_message
from deep_agent.channels.app_context import validate_channel_settings
from deep_agent.channels.outbound import DeliveryError


class DingTalkCardsTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_lifecycle_and_final_content(self):
        api = SimpleNamespace(request=AsyncMock(return_value={}))
        cards = StreamingCards(api, 'bot')
        target = ReplyHandle('cid', 'staff', False)
        await cards.update('reply', target, '开始')
        await cards.update('reply', target, '完成\n第二行', finish=True)
        calls = api.request.call_args_list
        self.assertEqual(calls[0].kwargs['json']['cardTemplateId'], TEMPLATE_ID)
        self.assertEqual(calls[1].kwargs['json']['openSpaceId'], 'dtv1.card//IM_ROBOT.staff')
        self.assertEqual(calls[2].kwargs['json']['cardData']['cardParamMap']['flowStatus'], '2')
        self.assertTrue(calls[-2].kwargs['json']['isFinalize'])
        final = calls[-1].kwargs['json']['cardData']['cardParamMap']
        self.assertEqual(final['msgContent'], '完成<br>第二行')
        self.assertEqual(final['flowStatus'], '3')
        self.assertEqual(final['sys_full_json_obj'], '{"order": ["msgContent"]}')
        self.assertFalse(cards.cards)
        self.assertEqual(sum(c.args[0] == 'POST' for c in calls), 2)

    async def test_two_replies_isolated_and_group_target(self):
        api = SimpleNamespace(request=AsyncMock(return_value={}))
        cards = StreamingCards(api, 'bot')
        await cards.update('a', ReplyHandle('cid-a', '', True), 'A')
        await cards.update('b', ReplyHandle('cid-b', '', True), 'B', finish=True)
        self.assertIn('a', cards.cards)
        self.assertNotIn('b', cards.cards)
        delivers = [c.kwargs['json'] for c in api.request.call_args_list if c.args[1].endswith('/deliver')]
        self.assertEqual([c['openSpaceId'] for c in delivers], ['dtv1.card//IM_GROUP.cid-a', 'dtv1.card//IM_GROUP.cid-b'])
        self.assertNotEqual(delivers[0]['outTrackId'], delivers[1]['outTrackId'])

    async def test_failed_creation_not_repeated_or_replaced_with_plain_message(self):
        adapter = DingTalkAdapter(SimpleNamespace(bot_id='b', secret='s', settings={}, id='a'))
        self.addAsyncCleanup(adapter.stop)
        adapter._connected = True
        adapter.api.request = AsyncMock(side_effect=DeliveryError('platform_rejected', '拒绝'))
        message = SimpleNamespace(reply_handle=ReplyHandle('cid','staff',False), peer_id='raw', message_id='m')
        with self.assertRaises(DeliveryError):
            await adapter.reply_loading(message, 'r')
        adapter.api.request = AsyncMock(return_value={'processQueryKey':'receipt'})
        with self.assertRaises(DeliveryError):
            await adapter.reply(message, 'r', '最终正文', True)
        adapter.api.request.assert_not_called()
        self.assertFalse(adapter._cards.cards)

    async def test_final_update_error_does_not_send_duplicate_body(self):
        adapter = DingTalkAdapter(SimpleNamespace(bot_id='b', secret='s', settings={}, id='a'))
        self.addAsyncCleanup(adapter.stop)
        adapter.api.request = AsyncMock(return_value={})
        message = SimpleNamespace(reply_handle=ReplyHandle('cid','staff',False))
        await adapter.reply(message, 'r', '部分', False)
        adapter.api.request = AsyncMock(side_effect=DeliveryError('request_unconfirmed','未知',unknown=True))
        with self.assertRaises(DeliveryError):
            await adapter.reply(message, 'r', '完成', True)
        self.assertEqual(adapter.api.request.call_count, 1)
        self.assertFalse(adapter._cards.cards)

    async def test_details_disabled_does_not_cache_or_disclose_names(self):
        adapter = DingTalkAdapter(SimpleNamespace(bot_id='b', secret='s', settings={}, id='a'))
        self.addAsyncCleanup(adapter.stop)
        data = {'msgtype':'text','msgId':'m','senderId':'u','conversationId':'c',
                'senderNick':'Alice','text':{'content':'hi'}}
        message = parse_message(data, 'b')
        self.assertIsNone(message.sender_name)
        self.assertNotIn('display_name', message.peer_metadata)
        adapter._remember(parse_message(data, 'b', user_details_enabled=True))
        self.assertFalse(adapter._profiles)
        self.assertIsNone((await adapter.resolve_users(['u']))['items'][0]['name'])

    def test_switches_are_strict_and_optional(self):
        validate_channel_settings('dingtalk', {})
        validate_channel_settings('dingtalk', {'dingtalk': {'user_details_enabled': False}})
        for key in ('unknown_option','user_details_enabled'):
            with self.assertRaises(ValueError):
                validate_channel_settings('dingtalk', {'dingtalk': {key:'true'}})


    async def test_concurrent_final_waits_for_delivery_and_previews_cannot_overwrite_final(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def request(method, path, **kwargs):
            calls.append((method, path, kwargs["json"]))
            if method == "POST" and path == "/v1.0/card/instances":
                entered.set()
                await release.wait()
            return {}
        cards = StreamingCards(SimpleNamespace(request=request), "bot")
        target = ReplyHandle("cid", "staff", False)
        preview = asyncio.create_task(cards.update("reply", target, "first"))
        await entered.wait()
        final = asyncio.create_task(cards.update("reply", target, "last", finish=True))
        stale = asyncio.create_task(cards.update("reply", target, "stale"))
        await asyncio.sleep(.01)
        self.assertEqual(len(calls), 1)
        release.set()
        await asyncio.gather(preview, final)
        with self.assertRaises(DeliveryError) as caught:
            await stale
        self.assertEqual(caught.exception.code, "card_finalized")
        self.assertEqual(calls[-1][2]["cardData"]["cardParamMap"]["msgContent"], "last")
        self.assertEqual(sum(method == "POST" for method, _, _ in calls), 2)
        self.assertFalse(cards.cards)

    async def test_cancelled_create_is_not_repeated_when_final_arrives(self):
        entered = asyncio.Event()
        async def request(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        api = SimpleNamespace(request=AsyncMock(side_effect=request))
        cards = StreamingCards(api, "bot")
        target = ReplyHandle("cid", "staff", False)
        task = asyncio.create_task(cards.update("reply", target, "first"))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        with self.assertRaises(DeliveryError):
            await cards.update("reply", target, "final", finish=True)
        self.assertEqual(api.request.call_count, 1)
        self.assertFalse(cards.cards)
