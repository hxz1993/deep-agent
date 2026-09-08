"""One read-only catalogue for channel ingress and Console. No global chat hook."""
from __future__ import annotations

import re

from .contracts import InboundMessage

COMMANDS = (
    ("/help", "查看快捷命令和使用说明。"),
    ("/new", "停止当前任务并取消已排队消息，新开会话；原会话和历史仍保留。"),
    ("/stop", "停止当前任务并取消已排队消息，保留当前会话；不会撤销已完成的操作。"),
    ("/status", "查看绑定 Agent、默认模型、当前会话和任务状态。"),
    ("/whoami", "查看发送者 User ID、群聊 Chat ID 和记录会话的平台用户。"),
)
NOTES = (
    "请单独发送一条纯文本命令，不附带参数或附件。",
    "只识别正文为命令本身的消息，不剥离 @ 提及或引用；企微群聊若必须 @ 才能送达，请在私聊使用快捷命令。",
    "群聊共用一个会话；群内成员执行 /new 或 /stop 会影响整个群会话。",
    "停止是请求中止任务，不会撤销工具已经完成的外部操作。",
    "快捷命令仅适用于渠道入口，不改变控制台或其他客户端的聊天规则。",
)
_CANDIDATE = re.compile(r"^/[A-Za-z][A-Za-z0-9_-]*(?:[ \t]+[^\r\n]*)?$")


def command_catalog() -> dict:
    return {"commands": [{"command": name, "description": description} for name, description in COMMANDS], "notes": list(NOTES)}


def help_text() -> str:
    return "快捷命令\n\n" + "\n".join(f"{name} — {description}" for name, description in COMMANDS) + "\n\n" + "\n".join(NOTES)


def parse_command(message: InboundMessage) -> str | None:
    """None means ordinary chat; 'unknown' is an unsupported command/arguments."""
    text = message.text.strip()
    if not message.command_eligible or message.message_type != "text" or message.media or message.quote is not None:
        return None
    if not _CANDIDATE.fullmatch(text):
        return None
    return text if text in dict(COMMANDS) else "unknown"
