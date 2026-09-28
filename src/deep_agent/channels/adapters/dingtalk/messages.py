"""DingTalk callback parsing. Identifiers are preserved, never inferred from names."""
from dataclasses import dataclass
import hashlib
import json
import mimetypes
from pathlib import PurePath

from deep_agent.channels.contracts import InboundMedia, InboundMessage, InboundQuote


@dataclass(frozen=True)
class MediaHandle:
    download_code: str
    robot_code: str


@dataclass(frozen=True)
class ReplyHandle:
    conversation_id: str
    staff_id: str
    group: bool


def parse_message(data: dict, robot_code: str, *, user_details_enabled: bool = False) -> InboundMessage:
    if not isinstance(data, dict):
        raise ValueError('Invalid DingTalk callback')
    kind = data.get('msgtype')
    sender = str(data.get('senderId') or '')
    mid = str(data.get('msgId') or '')
    group = str(data.get('conversationType')) == '2'
    conversation = str(data.get('conversationId') or '')
    if not sender or not mid or not conversation:
        raise ValueError('Incomplete DingTalk source')
    text = ''
    media = []
    content = data.get('content') or {}
    if isinstance(content, str):
        content = json.loads(content)
    if not isinstance(content, dict):
        raise ValueError('Invalid DingTalk content')

    def attachment(value, media_kind, default):
        code = value.get('downloadCode') or value.get('pictureDownloadCode')
        if not code:
            raise ValueError('Missing DingTalk media download code')
        name = PurePath(str(value.get('fileName') or default)).name
        mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        media.append(InboundMedia(media_kind, name, mime, MediaHandle(str(code), robot_code),
            input_mode='reference' if media_kind == 'voice' else 'auto'))

    if kind == 'text':
        text_content = data.get('text') or {}
        if not isinstance(text_content, dict):
            raise ValueError('Invalid DingTalk text')
        text = str(text_content.get('content') or '').strip()
    elif kind == 'richText':
        parts = content.get('richText') or []
        if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
            raise ValueError('Invalid DingTalk rich text')
        text_parts = []
        for part in parts:
            if part.get('type') == 'picture':
                attachment(part, 'image', 'image.jpg')
            elif part.get('text'):
                text_parts.append(str(part['text']))
        text = ''.join(text_parts).strip()
    elif kind == 'picture':
        attachment(content, 'image', 'image.jpg')
    elif kind in {'audio', 'voice'}:
        # Official connector accepts legacy data.audio.recognition. Keep the
        # associated original download metadata from that same media container.
        legacy_audio = data.get('audio') if isinstance(data.get('audio'), dict) else {}
        content = {**legacy_audio, **{key: value for key, value in content.items() if value not in (None, '')}}
        text = str(content.get('recognition') or '').strip()
        ext = str(content.get('fileExtension') or 'amr').lstrip('.')
        if content.get('downloadCode'):
            attachment(content, 'voice', f'voice.{ext}')
        text = '[钉钉语音转写]\n' + text if text else '[平台状态：钉钉未提供语音转写；如已附原音频，仅保留为附件，请用户补充文字。]'
    elif kind in {'file', 'video'}:
        attachment(content, kind, 'video.mp4' if kind == 'video' else 'attachment')
    else:
        raise ValueError('Unsupported DingTalk message')
    quote = parse_quote(data.get('text') if kind == 'text' else content)
    name = (str(data.get('senderNick') or '').strip() or None) if user_details_enabled else None
    return InboundMessage(
        message_type=str(kind), message_id=mid, request_id=None, sender_id=sender,
        peer_type='group' if group else 'single', peer_id=conversation if group else sender,
        peer_metadata={'chat_name': str(data.get('conversationTitle') or '')} if group else ({'display_name': name} if name else {}),
        text=text, media=tuple(media), sender_name=name, quote=quote,
        tenant_id=str(data.get('senderCorpId') or '') or None,
        source_metadata={k: v for k, v in {
            'dingtalk_conversation_id': conversation,
            'dingtalk_staff_id': str(data.get('senderStaffId') or ''),
            'dingtalk_robot_code': robot_code,
        }.items() if v},
        reply_handle=ReplyHandle(conversation, str(data.get('senderStaffId') or ''), group),
        command_eligible=kind == 'text',
        payload_hash=hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
    )


def parse_quote(container):
    """Keep quoted content separate from the current sender instruction."""
    if not isinstance(container, dict) or not container.get('isReplyMsg'):
        return None
    quoted = container.get('repliedMsg')
    if not isinstance(quoted, dict):
        return None
    content = quoted.get('content') or {}
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            content = {}
    if not isinstance(content, dict):
        content = {}
    kind = str(quoted.get('msgType') or 'text')
    text = content.get('text') or quoted.get('text') or content.get('recognition') or ''
    if kind == 'richText':
        parts = content.get('richText') or []
        text = ''.join(str(p.get('text') or '') for p in parts if isinstance(p, dict)) if isinstance(parts, list) else ''
    return InboundQuote(kind, str(text) or '[引用的' + kind + '消息]')
