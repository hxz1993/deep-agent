"""Small, non-sensitive source facts shared by channel persistence and display."""
from __future__ import annotations

from typing import Any, Mapping


def normalize_peer_metadata(
    peer_type: str, peer_id: str, metadata: Mapping[str, Any] | None,
    *, legacy_display_name: str | None = None,
) -> dict[str, str]:
    result = {
        key: value.strip() for key, value in (metadata or {}).items()
        if key in {"user_id", "chat_id", "display_name"} and isinstance(value, str) and value.strip()
    }
    if isinstance(legacy_display_name, str) and legacy_display_name.strip() and not result.get("display_name"):
        result["display_name"] = legacy_display_name.strip()
    if peer_type in {"single", "userid"}:
        result["user_id"] = peer_id
    elif peer_type in {"group", "chatid"}:
        result.pop("user_id", None)
        result["chat_id"] = peer_id
    return result


def channel_session_title(peer_type: str, peer_id: str, metadata: Mapping[str, Any] | None) -> str:
    prefix = "Group" if peer_type in {"group", "chatid"} else "User"
    name = normalize_peer_metadata(peer_type, peer_id, metadata).get("display_name")
    return f"{prefix}｜{name}（{peer_id}）" if name else f"{prefix}｜{peer_id}"


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
