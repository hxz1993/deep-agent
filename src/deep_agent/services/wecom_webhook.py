"""WeCom application protocol boundary. No platform credentials or directory lookup."""
from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import re
import time
from pathlib import PurePosixPath

from defusedxml.ElementTree import fromstring
from wechatpy.enterprise.crypto import WeChatCrypto

MAX_WEBHOOK_BYTES = 256 * 1024


class WebhookError(ValueError):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


def application_id(account) -> str | None:
    settings = getattr(account, "settings", None) or {}
    config = settings.get("wecom_app")
    value = config.get("application_id") if isinstance(config, dict) else None
    return value if isinstance(value, str) and value else None


def account_fingerprint(account) -> str:
    # Internal equality token; never a substitute for request authentication.
    values = [account.id, account.channel_type, account.transport, account.agent_id,
              account.session_user_id, account.webhook_token, account.webhook_aes_key,
              application_id(account)]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def xml_fields(raw: str | bytes, *, allow_event_children: bool = False) -> dict[str, str]:
    try:
        if len(raw) > MAX_WEBHOOK_BYTES:
            raise WebhookError("body_too_large", 413)
        root = fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        if root.tag != "xml":
            raise ValueError()
        result = {}
        ignored_event = allow_event_children and root.findtext("MsgType") == "event"
        for item in root:
            nested_event_detail = ignored_event and item.tag not in {"ToUserName", "AgentID", "MsgType"}
            if item.tag in result or (list(item) and not nested_event_detail):
                raise ValueError()
            result[item.tag] = item.text or ""
        return result
    except WebhookError:
        raise
    except Exception:
        raise WebhookError("invalid_xml") from None


def signature_params(query, *, now: float | None = None) -> tuple[str, str, str]:
    values = []
    for name in ("msg_signature", "timestamp", "nonce"):
        candidates = query.getlist(name)
        if len(candidates) != 1 or not candidates[0] or len(candidates[0]) > 128:
            raise WebhookError("invalid_parameters")
        values.append(candidates[0])
    sig, timestamp, nonce = values
    if not re.fullmatch(r"[a-fA-F0-9]{40}", sig) or not re.fullmatch(r"[0-9]{1,12}", timestamp):
        raise WebhookError("invalid_parameters")
    # Covers normal delivery retries, but does not replace MsgId deduplication.
    if abs((time.time() if now is None else now) - int(timestamp)) > 600:
        raise WebhookError("timestamp_expired", 403)
    return sig.lower(), timestamp, nonce


def decrypt(account, app, encrypted: str, signature: str, timestamp: str, nonce: str) -> str:
    if not encrypted or len(encrypted) > MAX_WEBHOOK_BYTES:
        raise WebhookError("invalid_ciphertext")
    expected = hashlib.sha1("".join(sorted((account.webhook_token, timestamp, nonce, encrypted))).encode()).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise WebhookError("invalid_signature", 403)
    try:
        crypto = WeChatCrypto(account.webhook_token, account.webhook_aes_key, app.corp_id)
        result = crypto.check_signature(signature, timestamp, nonce, encrypted)
        return result.decode("utf-8") if isinstance(result, bytes) else result
    except Exception:
        raise WebhookError("invalid_encryption_or_receiver", 403) from None


def parse_message(raw: str, account, app, *, request_id: str) -> dict | None:
    data = xml_fields(raw, allow_event_children=True)
    if data.get("ToUserName") != app.corp_id or data.get("AgentID") != str(app.wecom_agent_id):
        raise WebhookError("receiver_mismatch", 403)
    kind = data.get("MsgType", "")
    if kind == "event":
        # No event business handlers yet; importantly, never manufacture a question.
        return None
    sender = data.get("FromUserName", "")
    message_id = data.get("MsgId", "")
    if not sender or len(sender) > 191 or not re.fullmatch(r"[0-9]{1,32}", message_id):
        raise WebhookError("invalid_message_identity")
    try:
        created = int(data["CreateTime"])
        if created < 0:
            raise ValueError()
    except (KeyError, ValueError):
        raise WebhookError("invalid_message_time") from None
    text = data.get("Content", "") if kind == "text" else ""
    media = []
    metadata = {"wecom_app_application_id": app.id, "wecom_app_corp_id": app.corp_id,
                "wecom_app_agent_id": str(app.wecom_agent_id), "wecom_app_revision": app.revision,
                "wecom_app_created_at": created, "wecom_app_request_id": request_id,
                "wecom_app_fingerprint": account_fingerprint(account)}
    if kind in {"image", "voice", "file"}:
        media_id = data.get("MediaId", "")
        if not media_id or len(media_id) > 1024:
            raise WebhookError("invalid_media_reference")
        ext = {"image": "jpg", "voice": "amr", "file": "bin"}[kind]
        if kind == "voice":
            fmt = data.get("Format", "amr").lower()
            if fmt not in {"amr", "wav", "mp3", "speex"}:
                raise WebhookError("unsupported_audio_format", 422)
            ext = fmt
        filename = PurePosixPath(data.get("FileName", "").replace("\\", "/")).name[:180] or f"{kind}-{message_id}.{ext}"
        mime = {"image": "image/jpeg", "voice": {"amr": "audio/amr", "wav": "audio/wav", "mp3": "audio/mpeg", "speex": "audio/speex"}.get(ext)}.get(kind)
        mime = mime or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        media.append({"kind": kind, "filename": filename, "mime": mime, "media_id": media_id})
        metadata["wecom_app_media_id"] = media_id
    elif kind != "text":
        metadata["wecom_app_unsupported"] = True
        text = "当前企微应用暂不支持这类消息，请发送文本、图片或语音。"
        kind = "unsupported"
    if kind == "text" and (not text.strip() or len(text.encode()) > 64 * 1024):
        raise WebhookError("invalid_text")
    # Hash the validated message facts, not nonce/ciphertext/request-id, which
    # may differ on upstream retries.
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"message_type": kind, "message_id": message_id, "request_id": request_id,
            "sender_id": sender, "peer_id": sender, "text": text, "media": media,
            "tenant_id": app.corp_id, "source_metadata": metadata, "payload_hash": digest,
            "expected_fingerprint": account_fingerprint(account)}
