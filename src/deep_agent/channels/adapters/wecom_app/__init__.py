"""HTTP application adapter. Credentials and enterprise HTTP calls stay in API."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import uuid

from deep_agent.agent.observability import log_event
from deep_agent.channels.outbound import ComponentReceipt, DeliveryError, MessageCapabilities


@dataclass(frozen=True)
class MediaHandle:
    message_id: str
    media_id: str


class WecomVoiceError(ValueError):
    """Only fixed, safe user-facing media errors."""


LOADING_DELAY_SECONDS = 3.0
LOADING_TEXT = "已收到，正在处理，请稍候。"


class WecomAppAdapter:
    def __init__(self, account, infinite_retry=True):
        self.account = account
        self.client = None
        self._active = False
        self._runs: dict[str, str] = {}
        self._loading: dict[str, asyncio.Task] = {}
        self._loading_sending: set[str] = set()
        self._finished: set[str] = set()
        self._loading_tasks: set[asyncio.Task] = set()
        self._updated_at = datetime.now(timezone.utc)

    @staticmethod
    def connection_fingerprint(account):
        return hashlib.sha256(json.dumps([
            account.id, account.channel_type, account.transport, account.agent_id,
            account.session_user_id, getattr(account, 'webhook_token', None),
            getattr(account, 'webhook_aes_key', None),
            (account.settings or {}).get('wecom_app', {}),
        ], sort_keys=True).encode()).hexdigest()

    @staticmethod
    def message_capabilities():
        return MessageCapabilities(kinds=('text', 'image', 'file', 'audio', 'video', 'textcard', 'news'),
                                   supports_reply=False, text_limit=2048,
                                   file_limit=20 * 1024 * 1024, image_limit=10 * 1024 * 1024,
                                   audio_limit=2 * 1024 * 1024, video_limit=10 * 1024 * 1024,
                                   audio_formats=('amr',), image_formats=('png', 'jpeg'),
                                   video_formats=('mp4',), media_fallback_to_file=True)

    async def start(self, on_message):
        self._active = True
        self._updated_at = datetime.now(timezone.utc)

    async def stop(self):
        self._active = False
        self._runs.clear()
        tasks = list(self._loading_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._loading.clear()
        self._loading_sending.clear()
        self._finished.clear()

    def status(self):
        return {'state': 'connected' if self._active else 'closed',
                'message': 'Webhook 接收已就绪' if self._active else '渠道已关闭',
                'authenticated': False, 'updated_at': self._updated_at.isoformat()}

    async def test_connection(self, timeout):
        async with asyncio.timeout(timeout):
            await self.client.wecom_app_info(self.account)
        return {'ok': True, 'message': '应用配置可用，请在企微后台验证回调地址'}

    def bind_run(self, message, run_id):
        self._runs[message.message_id] = run_id
        self._finished.discard(message.message_id)

    def release_run(self, message):
        message_id = message.message_id
        self._runs.pop(message_id, None)
        self._finished.discard(message_id)
        task = self._loading.pop(message_id, None)
        if task is not None:
            task.cancel()

    def _run(self, message_id):
        run_id = self._runs.get(message_id)
        if not self._active or not run_id:
            raise DeliveryError('source_unavailable', '当前渠道消息已结束或不可用')
        return run_id

    async def download_media(self, media):
        if not isinstance(media.handle, MediaHandle):
            raise ValueError('Invalid application media handle')
        run_id = self._run(media.handle.message_id)
        log_event('webhook_media_download_started', account_id=self.account.id, channel_run_id=run_id)
        data, filename = await self.client.wecom_app_media(self.account, run_id, media.handle.media_id)
        filename = filename or media.filename
        # Existing uploads accept AMR; providers often require WAV. Normalize
        # voice at the transport boundary while retaining the media pipeline.
        if media.kind == 'voice' and (data.startswith(b'#!AMR') or data.startswith(b'OggS') or media.mime == 'audio/ogg' or filename.lower().endswith(('.amr', '.speex', '.spx'))):
            log_event('webhook_audio_conversion_started', account_id=self.account.id, channel_run_id=run_id)
            try:
                data = await _amr_to_wav(data)
            except Exception as exc:
                log_event('webhook_audio_conversion_failed', account_id=self.account.id,
                          channel_run_id=run_id, error_type=type(exc).__name__)
                raise
            log_event('webhook_audio_conversion_completed', account_id=self.account.id, channel_run_id=run_id)
            filename = Path(filename).stem + '.wav'
        log_event('webhook_media_download_completed', account_id=self.account.id,
                  channel_run_id=run_id, byte_size=len(data))
        return data, filename

    def new_reply_id(self):
        return uuid.uuid4().hex

    async def reply_loading(self, message, reply_id, content=''):
        message_id = message.message_id
        self._run(message_id)
        if message_id in self._loading or message_id in self._finished:
            return
        task = asyncio.create_task(self._delayed_loading(message_id), name="wecom-app-loading")
        self._loading[message_id] = task
        self._loading_tasks.add(task)
        task.add_done_callback(self._loading_done)

    def _loading_done(self, task):
        self._loading_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _delayed_loading(self, message_id):
        try:
            await asyncio.sleep(LOADING_DELAY_SECONDS)
            if message_id in self._finished:
                return
            self._loading_sending.add(message_id)
            await self._send(message_id, LOADING_TEXT, message_type='text')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A failed notice never aborts or retries the actual answer.
            log_event('webhook_loading_failed', account_id=self.account.id,
                      channel_run_id=self._runs.get(message_id), error_type=type(exc).__name__)
        finally:
            self._loading_sending.discard(message_id)

    async def _finish_loading(self, message_id):
        self._finished.add(message_id)
        task = self._loading.get(message_id)
        if task is None:
            return
        # Cancel the timer, but serialize an already-started send with the final
        # answer. Cancelling HTTP I/O cannot retract a notice accepted upstream.
        if message_id not in self._loading_sending:
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def reply(self, message, reply_id, content, finish):
        if finish:
            await self._finish_loading(message.message_id)
            await self._send(message.message_id, content)

    async def _send(self, message_id, content, *, message_type="markdown"):
        run_id = self._run(message_id)
        log_event('webhook_reply_started', account_id=self.account.id, channel_run_id=run_id,
                  message_type=message_type)
        result = await self.client.wecom_app_send_message(self.account, run_id, content, message_type=message_type)
        log_event('webhook_reply_completed', account_id=self.account.id, channel_run_id=run_id,
                  message_type=message_type)
        return result

    async def send_component(self, target, component, *, reply_to_current=False):
        if reply_to_current or component.kind not in self.message_capabilities().kinds:
            raise DeliveryError('unsupported_kind', '企微应用不支持此消息类型或引用回复')
        if component.kind == 'text':
            result = await self._send(target.message_id, component.text)
        else:
            if component.kind in {'image', 'audio', 'video'}:
                from deep_agent.services.wecom_client import validate_outbound_media, WecomError
                try:
                    validate_outbound_media(component.kind, component.data or b'')
                except WecomError:
                    component = replace(component, kind='file')
            if component.kind == 'file':
                from deep_agent.services.wecom_client import validate_outbound_media, WecomError
                try:
                    validate_outbound_media('file', component.data or b'')
                except WecomError:
                    raise DeliveryError('invalid_content', '企微应用文件需大于5字节且不超过20MB') from None
            run_id = self._run(target.message_id)
            result = await self.client.wecom_app_send_component(self.account, run_id, component)
        return ComponentReceipt(component.kind, message_id=result.get('message_id'))


async def _amr_to_wav(data: bytes) -> bytes:
    if data.startswith((b'#!AMR\n', b'#!AMR-WB\n')):
        demuxer = 'amr'
    elif data.startswith(b'OggS') and b'Speex   ' in data[:512]:
        demuxer = 'ogg'
    else:
        raise WecomVoiceError('当前语音封装格式暂不支持，请改发 AMR、WAV 或 MP3 语音。')
    executable = shutil.which('ffmpeg')
    if not executable:
        raise WecomVoiceError('服务尚未安装语音转码组件，请联系管理员安装 ffmpeg，或改发 WAV、MP3。')
    with tempfile.TemporaryDirectory(prefix='deep-agent-voice-') as directory:
        source, dest = Path(directory) / 'input.amr', Path(directory) / 'output.wav'
        source.write_bytes(data)
        process = await asyncio.create_subprocess_exec(
            executable, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-format_whitelist', demuxer, '-f', demuxer, '-i', str(source), '-fs', str(32 * 1024 * 1024), '-ac', '1', '-ar', '16000', str(dest),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            async with asyncio.timeout(30):
                result = await process.wait()
            if result or not dest.exists() or dest.stat().st_size >= 32 * 1024 * 1024:
                raise WecomVoiceError('语音转码失败或语音过大，请改发 WAV、MP3 或缩短语音。')
            return dest.read_bytes()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
