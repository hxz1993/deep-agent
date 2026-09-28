from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import math
from dataclasses import replace
from pathlib import PurePath
from urllib.parse import urlparse
import uuid
from loguru import logger

from deep_agent.channels.outbound import ComponentReceipt, DeliveryError, MessageCapabilities, MessageTarget, PreparedComponent
from .api import DingTalkAPI
from .messages import MediaHandle, ReplyHandle, parse_message
from .worker import run
from .cards import StreamingCards


class DingTalkAdapter:
    def __init__(self, account, infinite_retry=True):
        self.account = account
        self.api = DingTalkAPI(account.bot_id, account.secret)
        self._process = self._pipe = self._reader = None
        self._callbacks = set()
        self._on_message = None
        self._connected = False
        self._profiles = OrderedDict()
        self._cards = StreamingCards(self.api, account.bot_id)
        self._updated_at = datetime.now(timezone.utc)

    @staticmethod
    def connection_fingerprint(account):
        settings = (account.settings or {}).get('dingtalk', {})
        return hashlib.sha256(json.dumps([account.channel_type, account.transport,
            account.bot_id, account.secret, settings], sort_keys=True).encode()).hexdigest()

    async def start(self, on_message):
        if self._process is not None:
            return
        self._on_message = on_message
        ctx = multiprocessing.get_context('spawn')
        self._pipe, child = ctx.Pipe(duplex=False)
        self._process = ctx.Process(target=run, args=(child, self.account.bot_id, self.account.secret), daemon=True)
        self._process.start()
        child.close()
        self._reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            while self._process and self._process.is_alive():
                if len(self._callbacks) >= 100 or not self._pipe.poll():
                    await asyncio.sleep(.05)
                    continue
                event = self._pipe.recv()
                if event['type'] == 'status':
                    self._connected = event['connected']
                    self._updated_at = datetime.now(timezone.utc)
                elif event['type'] == 'message':
                    try:
                        message = parse_message(event['data'], self.account.bot_id,
                            user_details_enabled=self.user_details_enabled)
                    except (ValueError, TypeError, KeyError):
                        logger.warning('DingTalk malformed or unsupported callback ignored')
                        continue
                    self._remember(message)
                    # Avoid blocking SDK ACK delivery behind a complete Agent turn.
                    task = asyncio.create_task(self._deliver(message))
                    self._callbacks.add(task)
                    task.add_done_callback(self._callbacks.discard)
        except (EOFError, OSError):
            pass
        finally:
            self._connected = False

    async def _deliver(self, message):
        if self._on_message:
            try:
                await self._on_message(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error('DingTalk channel callback failed')

    @property
    def user_details_enabled(self):
        return ((self.account.settings or {}).get('dingtalk') or {}).get('user_details_enabled') is True

    def _remember(self, message):
        if self.user_details_enabled and message.sender_name:
            self._profiles[message.sender_id] = message.sender_name
            self._profiles.move_to_end(message.sender_id)
            if len(self._profiles) > 2000:
                self._profiles.popitem(last=False)

    async def resolve_users(self, ids, timeout=3):
        items = []
        for sender_id in dict.fromkeys(ids):
            name = self._profiles.get(sender_id) if self.user_details_enabled else None
            if name:
                self._profiles.move_to_end(sender_id)
            items.append({'sender_id': sender_id, 'userid': None, 'name': name,
                'status': 'resolved' if name else 'unresolved'})
        return {'items': items, 'complete': all(item['name'] for item in items)}

    async def stop(self):
        self._on_message = None
        for task in [self._reader, *self._callbacks]:
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in [self._reader, *self._callbacks] if t), return_exceptions=True)
        if self._process:
            self._process.terminate()
            await asyncio.to_thread(self._process.join, 3)
            if self._process.is_alive():
                self._process.kill()
                await asyncio.to_thread(self._process.join, 3)
            self._process.close()
            self._process = None
        if self._pipe:
            self._pipe.close()
            self._pipe = None
        self._connected = False
        self._profiles.clear()
        self._cards.cards.clear()
        await self.api.close()

    def status(self):
        return {'state': 'connected' if self._connected else 'connecting' if self._process else 'closed',
            'authenticated': self._connected,
            'message': '已连接钉钉' if self._connected else '钉钉尚未连接',
            'updated_at': self._updated_at.isoformat()}

    async def test_connection(self, timeout):
        try:
            async with asyncio.timeout(timeout):
                await self.api.token()
            return {'account_id': self.account.id, **self.status(), 'success': True,
                'message': '应用凭据有效；Stream 连接状态请查看渠道列表'}
        except (DeliveryError, TimeoutError):
            return {'account_id': self.account.id, **self.status(), 'success': False,
                'message': '钉钉认证失败或超时，请检查应用凭据和网络'}

    @staticmethod
    def message_capabilities():
        return MessageCapabilities(('text', 'image', 'file', 'audio', 'video'), False, 20000,
            20 * 1024 * 1024, 20 * 1024 * 1024, 2 * 1024 * 1024, 20 * 1024 * 1024,
            audio_formats=('amr', 'opus'), video_formats=('mp4',),
            image_formats=('png', 'jpeg', 'gif', 'bmp'), media_fallback_to_file=False)

    async def send_component(self, target, component, *, reply_to_current=False):
        if target.account_id != self.account.id or target.peer_type not in {'single', 'group'}:
            raise DeliveryError('invalid_target', '钉钉发送目标与当前渠道不匹配')
        if reply_to_current:
            raise DeliveryError('unsupported_reply', '钉钉原生引用发送尚未接入')
        if not self._connected:
            raise DeliveryError('channel_offline', '钉钉尚未连接')
        handle = ReplyHandle(target.chat_id or target.peer_id, target.recipient_id or '', target.peer_type == 'group')
        if not handle.group and not handle.staff_id:
            raise DeliveryError('missing_staff_id', '钉钉未提供员工 ID，无法主动发送')
        if component.kind == 'text':
            key, params = 'sampleMarkdown', {'title': 'DeepAgent', 'text': component.text}
        elif component.kind in {'image', 'file'}:
            media_id = await self.api.upload(component)
            key = 'sampleImageMsg' if component.kind == 'image' else 'sampleFile'
            params = {'photoURL': media_id} if component.kind == 'image' else {
                'mediaId': media_id, 'fileName': component.filename,
                'fileType': PurePath(component.filename).suffix.lstrip('.')}
        elif component.kind in {'audio', 'video'}:
            from deep_agent.channels.media_metadata import inspect_media
            metadata = await inspect_media(component.data or b'', video=component.kind == 'video')
            if component.kind == 'audio' and metadata.duration_ms > 60000:
                raise DeliveryError('audio_too_long', '钉钉原生语音最长 60 秒，请缩短音频')
            if component.kind == 'audio':
                opus = (component.data or b'').startswith(b'OggS')
                component = replace(component,
                    filename=PurePath(component.filename).stem + ('.ogg' if opus else '.amr'),
                    mime='audio/ogg' if opus else 'audio/amr')
            media_id = await self.api.upload(component)
            if component.kind == 'audio':
                key, params = 'sampleAudio', {'mediaId': media_id, 'duration': str(metadata.duration_ms)}
            else:
                if not metadata.cover:
                    raise DeliveryError('missing_cover', '视频封面缺失，未发送消息')
                cover_id = await self.api.upload(replace(component, kind='image', data=metadata.cover, filename='cover.jpg', mime='image/jpeg'))
                key, params = 'sampleVideo', {'videoMediaId': media_id, 'picMediaId': cover_id,
                    'duration': str(math.ceil(metadata.duration_ms / 1000)), 'videoType': 'mp4',
                    'height': str(metadata.height), 'width': str(metadata.width)}
        else:
            raise DeliveryError('unsupported_kind', '不支持的钉钉消息类型')
        body = {'robotCode': self.account.bot_id, 'msgKey': key, 'msgParam': json.dumps(params, ensure_ascii=False)}
        if handle.group:
            path = '/v1.0/robot/groupMessages/send'
            body['openConversationId'] = handle.conversation_id
        else:
            if not handle.staff_id:
                raise DeliveryError('missing_staff_id', '钉钉未提供员工 ID，无法主动发送')
            path = '/v1.0/robot/oToMessages/batchSend'
            body['userIds'] = [handle.staff_id]
        data = await self.api.request('POST', path, json=body)
        if data.get('invalidUserIdList') or data.get('flowControlledStaffIdList'):
            raise DeliveryError('recipient_rejected', '钉钉接收人不可用或发送限流')
        receipt = data.get('processQueryKey')
        if not receipt:
            raise DeliveryError('missing_receipt', '钉钉未返回发送回执，结果未知', unknown=True)
        return ComponentReceipt(component.kind, request_id=receipt)

    async def download_media(self, media):
        if not isinstance(media.handle, MediaHandle) or media.handle.robot_code != self.account.bot_id:
            raise ValueError('Invalid DingTalk media handle')
        result = await self.api.request('POST', '/v1.0/robot/messageFiles/download',
            json={'downloadCode': media.handle.download_code, 'robotCode': self.account.bot_id})
        url = result.get('downloadUrl') or ''
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443) or not any((parsed.hostname or '').endswith('.' + domain) or parsed.hostname == domain
            for domain in ('dingtalk.com', 'alicdn.com', 'aliyuncs.com', 'alibaba.com')):
            raise ValueError('Untrusted DingTalk download URL')
        async with self.api.http.stream('GET', url) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    raise ValueError('DingTalk attachment exceeds 20 MiB')
                chunks.append(chunk)
            return b''.join(chunks), media.filename

    def new_reply_id(self):
        return uuid.uuid4().hex

    async def reply_loading(self, message, reply_id, content=''):
        await self.reply(message, reply_id, content or '正在处理…', False)

    async def reply(self, message, reply_id, content, finish):
        handle = message.reply_handle
        if not isinstance(handle, ReplyHandle):
            raise ValueError('Invalid DingTalk reply handle')
        await self._cards.update(reply_id, handle, content or '已处理完成。', finish=finish)


__all__ = ['DingTalkAdapter']
