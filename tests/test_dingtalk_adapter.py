import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import unittest

from deep_agent.channels.adapters.dingtalk import DingTalkAdapter
from deep_agent.channels.adapters.dingtalk.api import DingTalkAPI
from deep_agent.channels.adapters.dingtalk.messages import parse_message
from deep_agent.channels.outbound import DeliveryError, MessageTarget, PreparedComponent
from deep_agent.channels.media_metadata import MediaMetadata


def incoming(**values):
    return {'msgtype': 'text', 'msgId': 'm1', 'senderId': 'raw', 'senderStaffId': 'staff',
        'senderNick': 'Alice', 'conversationId': 'cid', 'conversationType': '1',
        'text': {'content': 'hello'}, **values}


def with_adapter(fn):
    def test():
        import asyncio
        async def run():
            adapter = DingTalkAdapter(SimpleNamespace(id='a', bot_id='bot', secret='secret', settings={}))
            adapter._connected = True
            try:
                await fn(adapter)
            finally:
                await adapter.stop()
        asyncio.run(run())
    test.__name__ = fn.__name__
    return test


def test_audio_keeps_transcription_and_reference_attachment():
    result = parse_message(incoming(msgtype='audio', content={'recognition': '你好', 'downloadCode': 'd'}), 'bot')
    assert '你好' in result.text
    assert result.media[0].input_mode == 'reference'
    assert result.sender_id == 'raw'
    assert result.source_metadata['dingtalk_staff_id'] == 'staff'



def test_private_title_uses_inbound_nickname():
    from deep_agent.channels.metadata import channel_session_title
    message = parse_message(incoming(senderNick='周文俊'), 'bot', user_details_enabled=True)
    assert channel_session_title(message.peer_type, message.peer_id,
        message.peer_metadata, channel_type='dingtalk') == 'dingtalk｜User｜周文俊(raw)'


@with_adapter
async def test_context_keeps_trusted_dingtalk_name(adapter):
    from deep_agent.api.v1.chat import _current_channel_context
    account = SimpleNamespace(id='a', channel_type='dingtalk', enabled=True,
        agent_id='agent', session_user_id='owner', settings={'dingtalk': {'user_details_enabled': True}})
    record = SimpleNamespace(sender_id='raw', sender_name='周文俊', tenant_id='corp', thread_id=None)
    store = SimpleNamespace(get_account=AsyncMock(return_value=account),
        get_message=AsyncMock(return_value=record))
    payload = {'sender_id': 'raw', 'message_id': 'm1', 'name': 'forged'}
    body = SimpleNamespace(context_refs=[SimpleNamespace(type='channel.message_source', payload=payload)])
    await _current_channel_context(SimpleNamespace(stores=SimpleNamespace(channel=store)), body, account)
    assert payload['name'] == '周文俊'
    payload['sender_id'] = 'other'
    await _current_channel_context(SimpleNamespace(stores=SimpleNamespace(channel=store)), body, account)
    assert 'name' not in payload


def test_quote_is_not_current_instruction():
    result = parse_message(incoming(text={'content': 'summarize', 'isReplyMsg': True,
        'repliedMsg': {'msgType': 'text', 'content': json.dumps({'text': 'old'})}}), 'bot')
    assert result.text == 'summarize'
    assert result.quote.text == 'old'


def test_malformed_callback_is_rejected_without_crashing_reader():
    for payload in ([], incoming(text='bad'),
                    incoming(msgtype='richText', content={'richText': ['bad']}),
                    incoming(msgtype='richText', content={'richText': {'text': 'bad'}})):
        with unittest.TestCase().assertRaises(ValueError):
            parse_message(payload, 'bot')
    result = parse_message(incoming(text={'content': 'hello', 'isReplyMsg': True,
        'repliedMsg': {'msgType': 'richText', 'content': {'richText': None}}}), 'bot')
    assert result.text == 'hello'


@with_adapter
async def test_nickname_refresh_and_bounded_cache(adapter):
    adapter.account.settings = {'dingtalk': {'user_details_enabled': True}}
    adapter._remember(parse_message(incoming(), 'bot', user_details_enabled=True))
    adapter._remember(parse_message(incoming(senderNick='Bob'), 'bot', user_details_enabled=True))
    assert (await adapter.resolve_users(['raw']))['items'][0]['name'] == 'Bob'
    for i in range(2001):
        adapter._remember(parse_message(incoming(senderId=str(i)), 'bot', user_details_enabled=True))
    assert len(adapter._profiles) == 2000
    assert not (await adapter.resolve_users(['raw']))['complete']


@with_adapter
async def test_send_uses_trusted_target_without_profile_cache(adapter):
    adapter.api.request = AsyncMock(return_value={'processQueryKey': 'ack'})
    target = MessageTarget('a', 'single', 'raw', 'm1', recipient_id='trusted-staff')
    receipt = await adapter.send_component(target, PreparedComponent('text', text='hello'))
    assert receipt.message_id is None and receipt.request_id == 'ack'
    assert adapter.api.request.call_args.kwargs['json']['userIds'] == ['trusted-staff']


@with_adapter
async def test_native_media_duration_and_cover(adapter):
    adapter.api.request = AsyncMock(return_value={'processQueryKey': 'ack'})
    adapter.api.upload = AsyncMock(side_effect=['video-id', 'cover-id'])
    with patch('deep_agent.channels.media_metadata.inspect_media', AsyncMock(return_value=MediaMetadata(1200, 20, 20, b'jpg'))):
        receipt = await adapter.send_component(MessageTarget('a', 'group', 'cid', 'm'),
            PreparedComponent('video', data=b'mp4', filename='a.mp4'))
    params = json.loads(adapter.api.request.call_args.kwargs['json']['msgParam'])
    assert params == {'videoMediaId': 'video-id', 'picMediaId': 'cover-id', 'duration': '2', 'videoType': 'mp4', 'height': '20', 'width': '20'}
    assert receipt.kind == 'video'


@with_adapter
async def test_missing_receipt_not_success(adapter):
    adapter.api.request = AsyncMock(return_value={})
    with unittest.TestCase().assertRaises(DeliveryError) as error:
        await adapter.send_component(MessageTarget('a', 'group', 'cid', 'm'), PreparedComponent('text', text='hi'))
    assert error.exception.unknown



def test_platform_error_does_not_leak_secret():
    response = httpx.Response(200, json={'errcode': 42, 'errmsg': 'secret'})
    with unittest.TestCase().assertRaises(DeliveryError) as err:
        DingTalkAPI.checked(response)
    assert 'secret' not in str(err.exception)

@with_adapter
async def test_native_voice_duration_and_error(adapter):
    adapter.api.request = AsyncMock(return_value={'processQueryKey': 'ack'})
    adapter.api.upload = AsyncMock(return_value='voice-id')
    with patch('deep_agent.channels.media_metadata.inspect_media', AsyncMock(return_value=MediaMetadata(1234))):
        await adapter.send_component(MessageTarget('a', 'group', 'cid', 'm'),
            PreparedComponent('audio', data=b'amr', filename='voice.amr'))
    body = adapter.api.request.call_args.kwargs['json']
    assert body['msgKey'] == 'sampleAudio'
    assert json.loads(body['msgParam'])['duration'] == '1234'
    adapter.api.upload.reset_mock()
    with patch('deep_agent.channels.media_metadata.inspect_media', AsyncMock(return_value=MediaMetadata(61000))):
        with unittest.TestCase().assertRaises(DeliveryError):
            await adapter.send_component(MessageTarget('a', 'group', 'cid', 'm'),
                PreparedComponent('audio', data=b'amr', filename='voice.amr'))
    adapter.api.upload.assert_not_called()


@with_adapter
async def test_upload_error_never_sends_message(adapter):
    adapter.api.upload = AsyncMock(side_effect=DeliveryError('upload_failed', 'failed'))
    adapter.api.request = AsyncMock()
    with unittest.TestCase().assertRaises(DeliveryError):
        await adapter.send_component(MessageTarget('a', 'group', 'cid', 'm'),
            PreparedComponent('image', data=b'png', filename='a.png'))
    adapter.api.request.assert_not_called()


@with_adapter
async def test_media_download_rejects_untrusted_platform_url(adapter):
    adapter.api.request = AsyncMock(return_value={'downloadUrl': 'http://127.0.0.1/private'})
    message = parse_message(incoming(msgtype='picture', content={'downloadCode': 'code'}), 'bot')
    with unittest.TestCase().assertRaises(ValueError):
        await adapter.download_media(message.media[0])


@with_adapter
async def test_stop_reaps_sdk_process(adapter):
    from unittest.mock import Mock
    process = Mock()
    process.is_alive.return_value = False
    adapter._process = process
    adapter._pipe = Mock()
    pipe = adapter._pipe
    adapter.api.request = AsyncMock(return_value={})
    await adapter.stop()
    process.terminate.assert_called_once()
    process.join.assert_called_once()
    process.close.assert_called_once()
    pipe.close.assert_called_once()
    adapter.api.request.assert_not_called()
    assert not adapter._profiles


@with_adapter
async def test_opus_normalization_reaches_native_audio_upload(adapter):
    from deep_agent.channels.message_tool import normalize_media
    component = normalize_media(PreparedComponent('audio', data=b'OggS' + b'\0' * 25 + b'OpusHead',
        filename='recording.bin', mime='application/octet-stream', requested_kind='audio'), adapter.message_capabilities())
    adapter.api.upload = AsyncMock(return_value='voice')
    adapter.api.request = AsyncMock(return_value={'processQueryKey': 'receipt'})
    with patch('deep_agent.channels.media_metadata.inspect_media', AsyncMock(return_value=MediaMetadata(1234))):
        await adapter.send_component(MessageTarget('a', 'group', 'cid', 'm'), component)
    uploaded = adapter.api.upload.call_args.args[0]
    assert uploaded.kind == 'audio' and uploaded.filename == 'recording.ogg' and uploaded.mime == 'audio/ogg'
    assert adapter.api.request.call_args.kwargs['json']['msgKey'] == 'sampleAudio'


@with_adapter
async def test_private_route_checked_before_upload(adapter):
    adapter.api.upload = AsyncMock()
    with unittest.TestCase().assertRaises(DeliveryError):
        await adapter.send_component(MessageTarget('a', 'single', 'raw', 'm'),
            PreparedComponent('image', data=b'png', filename='image.png'))
    adapter.api.upload.assert_not_called()


def test_legacy_audio_container_keeps_text_and_file():
    result = parse_message(incoming(msgtype='audio', audio={'recognition': 'legacy text',
        'downloadCode': 'legacy-code', 'fileName': 'original.amr'}), 'bot')
    assert 'legacy text' in result.text
    assert result.media[0].handle.download_code == 'legacy-code'
    assert result.media[0].input_mode == 'reference'
    preferred = parse_message(incoming(msgtype='audio', audio={'recognition': 'old'},
        content={'recognition': 'new', 'downloadCode': 'new-code'}), 'bot')
    assert 'new' in preferred.text and 'old' not in preferred.text


@with_adapter
async def test_download_rejects_credentials_and_non_https_port(adapter):
    message = parse_message(incoming(msgtype='picture', content={'downloadCode': 'd'}), 'bot')
    for url in ('https://user:password@files.dingtalk.com/a', 'https://files.dingtalk.com:8443/a',
                'https://files.dingtalk.com:invalid/a'):
        adapter.api.request = AsyncMock(return_value={'downloadUrl': url})
        with unittest.TestCase().assertRaises(ValueError):
            await adapter.download_media(message.media[0])



def test_http_server_failures_are_unknown_but_redirects_are_not_success():
    for status in (408, 500, 502, 503):
        with unittest.TestCase().assertRaises(DeliveryError) as caught:
            DingTalkAPI.checked(httpx.Response(status, json={}))
        assert caught.exception.unknown
    with unittest.TestCase().assertRaises(DeliveryError):
        DingTalkAPI.checked(httpx.Response(302, json={}))
    for data in ([], None, 'unexpected'):
        with unittest.TestCase().assertRaises(DeliveryError) as caught:
            DingTalkAPI.checked(httpx.Response(200, content=json.dumps(data)))
        assert caught.exception.code == 'invalid_response'
        assert caught.exception.unknown


@with_adapter
async def test_upload_server_failure_cannot_claim_a_message_was_sent(adapter):
    adapter.api.token = AsyncMock(return_value="fixture-token")
    adapter.api.http.post = AsyncMock(return_value=httpx.Response(502, json={}))
    with unittest.TestCase().assertRaises(DeliveryError) as caught:
        await adapter.api.upload(PreparedComponent('file', data=b'file', filename='a.txt'))
    assert caught.exception.code == 'upload_failed'
    assert not caught.exception.unknown


def test_http_info_logging_redacts_upload_and_download_credentials():
    import logging
    from deep_agent.channels.adapters.dingtalk.api import _DingTalkRequestLogFilter
    for url in ('https://oapi.dingtalk.com/media/upload?access_token=fixture-secret',
                'https://files.alicdn.com/signed-fixture-secret'):
        record = logging.LogRecord('httpx', logging.INFO, '', 0, 'HTTP Request: %s %s', ('POST', httpx.URL(url)), None)
        assert _DingTalkRequestLogFilter().filter(record)
        assert 'fixture-secret' not in record.getMessage()
    record = logging.LogRecord('httpx', logging.INFO, '', 0, 'ordinary request', (), None)
    assert _DingTalkRequestLogFilter().filter(record)
    assert record.getMessage() == 'ordinary request'


def load_tests(loader, tests, pattern):
    """Repository unittest discovery runs the same assertions as pytest."""
    return unittest.TestSuite(unittest.FunctionTestCase(fn) for name, fn in sorted(globals().items())
        if name.startswith('test_') and callable(fn))


if __name__ == '__main__':
    unittest.main()
