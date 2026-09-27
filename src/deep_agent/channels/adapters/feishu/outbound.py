"""Single-attempt sends through the existing official client, separate from streams."""
from __future__ import annotations

import json
import uuid

from deep_agent.channels.outbound import (
    ComponentReceipt, DeliveryError, MessageCapabilities, MessageTarget, PreparedComponent,
)


def message_capabilities() -> MessageCapabilities:
    # Importing this module for catalog discovery never imports the SDK/WS loop.
    return MessageCapabilities(
        kinds=("text", "image", "file", "audio", "video"), supports_reply=True,
        text_limit=20_000, file_limit=30 * 1024 * 1024,
        image_limit=10 * 1024 * 1024, audio_limit=30 * 1024 * 1024,
        video_limit=30 * 1024 * 1024, audio_formats=("opus",),
    )


async def send_component(client, target: MessageTarget, component: PreparedComponent,
                         *, reply_to_current: bool = False) -> ComponentReceipt:
    from lark_channel.channel.types import MediaSource
    from lark_channel.channel.errors import classify_error
    from lark_channel.api.im.v1.model.create_message_request import CreateMessageRequest
    from lark_channel.api.im.v1.model.create_message_request_body import CreateMessageRequestBody
    from lark_channel.api.im.v1.model.reply_message_request import ReplyMessageRequest
    from lark_channel.api.im.v1.model.reply_message_request_body import ReplyMessageRequestBody

    if component.kind not in message_capabilities().kinds:
        raise DeliveryError("unsupported_content", "飞书不支持此消息类型")
    use_reply = reply_to_current or bool(target.thread_id)
    if use_reply and not target.message_id:
        raise DeliveryError("invalid_target", "当前消息缺少飞书引用标识")
    if not use_reply and not target.chat_id:
        raise DeliveryError("invalid_target", "当前消息缺少飞书会话标识")
    if component.kind == "text":
        content = {"text": component.text}
    else:
        try:
            key = await client.upload_media(
                MediaSource(kind="buffer", buffer=component.data),
                kind="image" if component.kind == "image" else "file",
                file_name=component.filename,
                file_type={"audio": "opus", "video": "mp4"}.get(component.kind, "stream")
                if component.kind != "image" else None,
            )
        except Exception:
            raise DeliveryError("upload_failed", "飞书媒体上传失败，请检查文件及应用权限") from None
        if not key:
            raise DeliveryError("upload_failed", "飞书未返回媒体上传标识")
        content = {"image_key" if component.kind == "image" else "file_key": key}
    msg_type = "media" if component.kind == "video" else component.kind
    encoded = json.dumps(content, ensure_ascii=False)
    # The high-level send() retries and chunks internally. Use its same public
    # underlying SDK client so the shared service owns receipts/retries exactly.
    identifier = str(uuid.uuid4())
    try:
        if use_reply:
            body = (ReplyMessageRequestBody.builder().msg_type(msg_type).content(encoded)
                    .reply_in_thread(bool(target.thread_id)).uuid(identifier).build())
            request = (ReplyMessageRequest.builder().message_id(target.message_id)
                       .request_body(body).build())
            response = await client.client.im.v1.message.areply(request)
        else:
            chat_id = target.chat_id
            if not chat_id:
                raise DeliveryError("invalid_target", "当前消息缺少飞书会话标识")
            body = (CreateMessageRequestBody.builder().receive_id(chat_id).msg_type(msg_type)
                    .content(encoded).uuid(identifier).build())
            request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
            response = await client.client.im.v1.message.acreate(request)
    except DeliveryError:
        raise
    except Exception:
        raise DeliveryError("send_unknown", "飞书发送结果未知，请确认是否已收到，勿重复发送", unknown=True) from None
    if not response.success():
        raw_code = getattr(response, "code", None)
        code = classify_error(raw_code).code.value if isinstance(raw_code, int) else "unknown"
        if code == "unknown":
            raise DeliveryError("send_unknown", "飞书发送结果未知，请确认是否已收到，勿重复发送", unknown=True)
        messages = {"permission_denied": "飞书应用没有发送权限", "rate_limited": "飞书发送频率受限",
                    "target_revoked": "飞书引用消息或会话已不可用", "format_error": "飞书不接受此消息格式"}
        raise DeliveryError(code, messages.get(code, "飞书拒绝发送此消息"))
    message_id = getattr(getattr(response, "data", None), "message_id", None)
    if not message_id:
        raise DeliveryError("send_unknown", "飞书没有返回消息回执，请确认是否已收到", unknown=True)
    return ComponentReceipt(component.kind, message_id=message_id)
