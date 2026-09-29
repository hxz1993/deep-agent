"""HTTP application adapter. Credentials and enterprise HTTP calls stay in API."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
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


class WecomAppAdapter:
    def __init__(self, account, infinite_retry=True):
        self.account = account
        self.client = None
        self._active = False
        self._runs: dict[str, str] = {}
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
        return MessageCapabilities(kinds=('text',), supports_reply=False, text_limit=2048,
                                   file_limit=0, image_limit=0, audio_limit=0, video_limit=0,
                                   media_fallback_to_file=False)

    async def start(self, on_message):
        self._active = True
        self._updated_at = datetime.now(timezone.utc)

    async def stop(self):
        self._active = False
        self._runs.clear()

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

    def release_run(self, message):
        self._runs.pop(message.message_id, None)

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
        return None

    async def reply(self, message, reply_id, content, finish):
        if finish:
            await self._send(message.message_id, content)

    async def _send(self, message_id, content):
        run_id = self._run(message_id)
        log_event('webhook_reply_started', account_id=self.account.id, channel_run_id=run_id)
        result = await self.client.wecom_app_send_text(self.account, run_id, content)
        log_event('webhook_reply_completed', account_id=self.account.id, channel_run_id=run_id)
        return result

    async def send_component(self, target, component, *, reply_to_current=False):
        if component.kind != 'text' or reply_to_current:
            raise DeliveryError('unsupported_kind', '企微应用当前仅支持文本发送')
        result = await self._send(target.message_id, component.text)
        return ComponentReceipt('text', message_id=result.get('message_id'))


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
