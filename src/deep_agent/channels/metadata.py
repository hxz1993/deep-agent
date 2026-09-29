"""Small, non-sensitive source facts shared by channel persistence and display."""
from __future__ import annotations

from typing import Any, Mapping


def wecom_application_id(account: Any) -> str | None:
    """Only an explicit opt-in enables identity enrichment on a channel."""
    settings = getattr(account, "settings", None)
    channel_type = getattr(account, "channel_type", None)
    if channel_type == "wecom_app":
        config = settings.get("wecom_app") if isinstance(settings, dict) else None
        value = config.get("application_id") if isinstance(config, dict) else None
        return value if isinstance(value, str) and value else None
    wecom = settings.get("wecom") if isinstance(settings, dict) else None
    if getattr(account, "channel_type", None) != "wecom" or not isinstance(wecom, dict):
        return None
    application_id = wecom.get("application_id")
    if wecom.get("user_details_enabled") is True and isinstance(application_id, str) and application_id:
        return application_id
    return None


def normalize_peer_metadata(
    peer_type: str, peer_id: str, metadata: Mapping[str, Any] | None,
    *, legacy_display_name: str | None = None,
) -> dict[str, str]:
    result = {
        key: value.strip() for key, value in (metadata or {}).items()
        if key in {"user_id", "chat_id", "display_name", "wecom_application_id", "wecom_userid", "wecom_name", "tenant_id", "chat_name"}
        and isinstance(value, str) and value.strip()
    }
    if isinstance(legacy_display_name, str) and legacy_display_name.strip() and not result.get("display_name"):
        result["display_name"] = legacy_display_name.strip()
    if peer_type in {"single", "userid"}:
        result["user_id"] = peer_id
    elif peer_type in {"group", "chatid"}:
        result.pop("user_id", None)
        result["chat_id"] = peer_id
    return result


def channel_session_title(peer_type: str, peer_id: str, metadata: Mapping[str, Any] | None, *, channel_type: str | None = None) -> str:
    prefix = f"{channel_type}｜" if channel_type in {"wecom", "wecom_app", "feishu", "dingtalk"} else ""
    if peer_type in {"group", "chatid"}:
        return f"{prefix}Group｜{peer_id}"
    name = normalize_peer_metadata(peer_type, peer_id, metadata).get("display_name")
    return f"{prefix}User｜{name}({peer_id})" if name else f"{prefix}User｜{peer_id}"


def normalize_channel_origin(origin: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(origin)
    if result.get("channel_type") == "wecom":
        result["peer_type"] = {"userid": "single", "chatid": "group"}.get(result.get("peer_type"), result.get("peer_type"))
    result["peer_metadata"] = normalize_peer_metadata(
        str(result.get("peer_type") or ""), str(origin.get("peer_id") or ""),
        origin.get("peer_metadata") if isinstance(origin.get("peer_metadata"), dict) else None,
        legacy_display_name=origin.get("peer_display_name"),
    )
    result.pop("peer_display_name", None)
    return result
