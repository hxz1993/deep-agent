from __future__ import annotations

from typing import Any
from copy import deepcopy
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
    key = config.get("api_key")
    if key is not None:
        if not isinstance(key, str):
            raise ValueError("X-Deepfos-Key 必须是字符串")
        if any(char.isspace() or not char.isascii() or unicodedata.category(char).startswith("C") for char in key):
            raise ValueError("X-Deepfos-Key 不能包含空白、非 ASCII 或控制字符")
    if "clear_api_key" in config and not isinstance(config["clear_api_key"], bool):
        raise ValueError("clear_api_key 必须是布尔值")
    if config.get("clear_api_key") and key:
        raise ValueError("不能同时设置与清空 X-Deepfos-Key")
    return app_id if enabled else None


def channel_deepfos_key(settings: dict[str, Any] | None) -> str | None:
    if not channel_deepfos_app(settings):
        return None
    return (settings or {}).get("deepfos", {}).get("api_key") or None


def merge_channel_settings(settings: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Keep an unchanged key private; only an explicit clear removes it."""
    channel_deepfos_app(settings)
    result = deepcopy(settings)
    config = result.get("deepfos")
    if isinstance(config, dict):
        old = (previous or {}).get("deepfos") or {}
        clear = config.pop("clear_api_key", False)
        if clear:
            config.pop("api_key", None)
        elif not config.get("api_key"):
            config.pop("api_key", None)
            if old.get("api_key"):
                config["api_key"] = old["api_key"]
        config.pop("api_key_set", None)
        config.pop("api_key_masked", None)
    return result


def public_channel_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    from deep_agent.schemas.llm_provider import mask_api_key

    result = deepcopy(settings or {})
    config = result.get("deepfos")
    if isinstance(config, dict):
        key = config.pop("api_key", None)
        config.pop("clear_api_key", None)
        config["api_key_set"] = bool(key)
        config["api_key_masked"] = mask_api_key(key)
    return result
