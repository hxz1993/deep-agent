"""Read-only guidance for the current trusted channel."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic_ai import RunContext
from sqlalchemy.exc import SQLAlchemyError

from deep_agent.agent.deps import AgentDeps
from deep_agent.files.public_download import MAX_PUBLIC_FILE_BYTES
from deep_agent.files.upload_settings import UploadSettings, UploadSettingsError, load_upload_settings_for_tool

from .commands import help_text
from .instructions import CHANNEL_NAMES
from .outbound import MessageCapabilities


# Describe what the inbound adapters actually pass to DeepAgent, including
# the difference between an incoming quote and an outgoing native reply.
_INBOUND_GUIDANCE = {
    "wecom": (
        "支持群聊和私聊。可接收文字、图片、图文混排、文件、视频，以及平台提供的语音转写或音频附件。"
        "可读取回调提供的引用文字及支持的引用附件。"
    ),
    "wecom_app": (
        "通过企业微信应用接收私聊。可接收文字和平台投递的图片、语音、文件。"
        "语音回调支持 AMR、WAV、MP3、Speex，能否听取音频取决于处理结果和当前模型。"
        "当前应用回调不提供独立的引用内容解析。"
    ),
    "feishu": (
        "支持群聊和私聊，群聊中与机器人对话需要 @机器人。可接收文字、富文本、图片、文件、音频和视频。"
        "引用内容需能从同一聊天取到，可包含引用正文及支持的附件。"
    ),
    "dingtalk": (
        "支持群聊和私聊。可接收文字、富文本图片、图片、文件和视频。"
        "语音使用平台转写，有原音频时仅保留为附件。没有转写时请补充文字。"
        "可读取引用里的文字或转写，引用附件不随引用读取。"
    ),
}


def build_channel_doc(
    channel_type: str, caps: MessageCapabilities,
) -> Callable[[RunContext[AgentDeps]], Awaitable[str]]:
    async def channel_doc(ctx: RunContext[AgentDeps]) -> str:
        """查询当前渠道的快捷命令、收发图片和语音的限制，以及引用和转发支持情况。"""
        try:
            policy = await load_upload_settings_for_tool(ctx)
        except (UploadSettingsError, SQLAlchemyError):
            policy = None
        return _render_channel_doc(channel_type, caps, policy)

    return channel_doc


def _render_channel_doc(channel_type: str, caps: MessageCapabilities, policy: UploadSettings | None) -> str:
    outbound = []
    for kind, label in (("image", "图片"), ("audio", "语音"), ("video", "视频"), ("file", "文件")):
        limit = getattr(caps, f"{kind}_limit")
        if kind not in caps.kinds or limit <= 0:
            continue
        if policy is not None:
            limit = min(limit, policy.max_bytes)
        formats = "、".join(value.upper() for value in getattr(caps, f"{kind}_formats", ()))
        format_hint = f"{formats}，" if formats else ""
        duration_hint = f"，最长 {caps.audio_max_seconds} 秒" if kind == "audio" and caps.audio_max_seconds else ""
        outbound.append(f"- {label}：{format_hint}最多 {limit / (1024 * 1024):g} MiB{duration_hint}。")
    if "textcard" in caps.kinds and "news" in caps.kinds:
        outbound.append("- 支持文字卡片和图文消息。")
    reply = "支持引用当前消息回复。" if caps.supports_reply else "不支持原生引用回复。"
    fallback = (
        "格式不适合原生发送时，符合文件限额可作为普通附件发送。"
        if caps.media_fallback_to_file else
        "格式不适合原生发送时，可在文件限额内选择按普通附件发送。"
    )
    if policy is None:
        limits = "暂时无法读取服务端附件配置。下列大小是渠道上限，实际可用上限可能更低。"
    else:
        url_limit = min(policy.max_bytes, MAX_PUBLIC_FILE_BYTES) / (1024 * 1024)
        limits = (
            f"服务端单个入站附件上限为 {policy.max_size_mb} MiB。"
            f"每次通过 channel_message 发送的附件合计最多 {policy.max_size_mb} MiB，"
            f"公开链接取件还受 {url_limit:g} MiB 上限约束。"
        )
    return "\n".join([
        f"# {CHANNEL_NAMES[channel_type]}（{channel_type}）",
        "",
        f"## {help_text(channel_type=channel_type)}",
        "",
        "## 能收到的内容",
        _INBOUND_GUIDANCE[channel_type],
        "转发如果以支持的普通消息投递，可以读取对应内容。合并转发不会展开为多条历史消息。"
        "入站附件还受平台投递和服务端上传限制，读取音频或图片取决于模型能力。",
        "",
        "## 发送限制",
        "普通文字回答自动发送到当前聊天。",
        limits,
        *outbound,
        f"{reply}{fallback}",
    ])
