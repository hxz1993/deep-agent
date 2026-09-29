"""Public session-creation source filter shared by session and diagnostics APIs."""
from typing import Literal, get_args

SessionSourceType = Literal[
    "internal", "channel:wecom", "channel:feishu", "channel:dingtalk", "channel:wecom_app",
]
CHANNEL_SOURCE_TYPES = tuple(value for value in get_args(SessionSourceType) if value.startswith("channel:"))
CHANNEL_TYPES = frozenset(value.split(":", 1)[1] for value in CHANNEL_SOURCE_TYPES)
