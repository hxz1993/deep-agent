from __future__ import annotations

from typing import Any
import unicodedata

from deep_agent.agent.request_context import derive_space_from_app


def channel_deepfos_app(settings: dict[str, Any] | None) -> str | None:
    """Validate the optional association and return its effective application.

    Missing settings remain disabled for existing accounts. The configured ID
    can be retained while disabled, without becoming an effective app context.
    Unknown settings are deliberately left intact for other extensions.
    """
    config = (settings or {}).get("deepfos")
    if config is None:
        return None
    if not isinstance(config, dict):
        raise ValueError("settings.deepfos 必须是对象")
    enabled = config.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("settings.deepfos.enabled 必须是布尔值")
    app_id = config.get("app_id")
    if app_id is not None:
        if not isinstance(app_id, str):
            raise ValueError("DeepFOS App ID 必须是字符串")
        if any(char.isspace() or unicodedata.category(char).startswith("C") for char in app_id):
            raise ValueError("DeepFOS App ID 不能包含空白或控制字符")
        if app_id:
            derive_space_from_app(app_id)
    if enabled and not app_id:
        raise ValueError("开启关联 DeepFOS 应用后必须填写 App ID")
    return app_id if enabled else None
