"""Shared user-profile lookup; history supplies IDs, never display names."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from deep_agent.channels.metadata import wecom_application_id
from deep_agent.services.channel_gateway_client import ChannelGatewayClient


def user_details_enabled(account) -> bool:
    if not account or not account.enabled:
        return False
    if account.channel_type in {"wecom", "wecom_app"}:
        return bool(wecom_application_id(account))
    if account.channel_type == "dingtalk":
        # No directory lookup: the live gateway owns nicknames received in messages.
        return ((account.settings or {}).get("dingtalk") or {}).get("user_details_enabled") is True
    config = (account.settings or {}).get("feishu") or {}
    return account.channel_type == "feishu" and config.get("user_details_enabled") is True


def profile_configuration(account):
    return (account.channel_type, account.bot_id, account.secret,
            (account.settings or {}).get(account.channel_type))


class ChannelProfileService:
    """No API-side name cache. Provider services own caching and credentials."""

    def __init__(self, ctx, *, gateway=None):
        self.ctx = ctx
        self.gateway = gateway or ChannelGatewayClient()

    async def resolve_many(self, account, ids, *, budget=3):
        account = await self.ctx.stores.channel.get_account(account.id)
        ids = list(dict.fromkeys(value for value in ids if isinstance(value, str) and value))
        enabled = user_details_enabled(account)
        items = {value: {"sender_id": value, "userid": None, "name": None,
                         "status": "unresolved" if enabled else "disabled"} for value in ids}
        if enabled and ids:
            deadline = asyncio.get_running_loop().time() + budget
            try:
                async with asyncio.timeout(budget):
                    if account.channel_type in {"wecom", "wecom_app"}:
                        profiles = self.ctx.get_wecom_profiles()
                        iterator = iter(ids)
                        async def worker():
                            for value in iterator:
                                try:
                                    result = await profiles.resolve(account, value, await_refresh=True)
                                    items[value].update({key: result.get(key) for key in
                                                        ("userid", "name", "status", "application_id", "application_revision")})
                                    items[value]["tenant_id"] = result.get("tenant_id")
                                except Exception:
                                    items[value]["status"] = "unavailable"
                        await asyncio.gather(*(worker() for _ in range(min(4, len(ids)))))
                    else:
                        # Keep IPC and HTTP packets bounded, preserve completed batches.
                        for start in range(0, len(ids), 100):
                            remaining = deadline - asyncio.get_running_loop().time() - 0.1
                            if remaining <= 0:
                                break
                            result = await self.gateway.resolve_users(account.id, ids[start:start + 100], timeout=min(10, remaining))
                            for row in result.get("items", []):
                                value = row.get("sender_id")
                                if value in items:
                                    items[value].update({key: row.get(key) for key in ("userid", "name", "status")})
            except Exception:
                # A missing profile must not suppress a valid conversation/statistic.
                pass
            current = await self.ctx.stores.channel.get_account(account.id)
            if not user_details_enabled(current) or profile_configuration(current) != profile_configuration(account):
                enabled = user_details_enabled(current)
                items = {value: {"sender_id": value, "userid": None, "name": None,
                                 "status": "unresolved" if enabled else "disabled"} for value in ids}
        unresolved = sum(not row.get("name") for row in items.values()) if enabled else 0
        return {"items": items, "user_details_enabled": enabled,
                "profiles_complete": unresolved == 0, "unresolved_count": unresolved}


    async def enrich_origins(self, origins, *, budget=3):
        """Decorate response copies only; persisted origin snapshots never change."""
        result = {}
        groups = {}
        for session_id, origin in origins.items():
            if not origin:
                continue
            origin = {**origin, "peer_metadata": dict(origin.get("peer_metadata") or {})}
            result[session_id] = origin
            if origin.get("peer_type") not in {"single", "userid"}:
                continue
            origin["peer_metadata"].pop("display_name", None)
            origin["peer_metadata"].pop("wecom_name", None)
            if origin.get("account_id") and origin.get("peer_id"):
                groups.setdefault(origin["account_id"], []).append((session_id, origin["peer_id"]))
        iterator = iter(groups.items())
        deadline = asyncio.get_running_loop().time() + budget
        async def worker():
            for account_id, pairs in iterator:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return
                try:
                    profiles = await self.resolve_many(SimpleNamespace(id=account_id), [raw for _, raw in pairs], budget=remaining)
                    for session_id, raw in pairs:
                        name = profiles["items"].get(raw, {}).get("name")
                        if name:
                            result[session_id]["peer_metadata"]["display_name"] = name
                except Exception:
                    pass
        try:
            async with asyncio.timeout(budget):
                await asyncio.gather(*(worker() for _ in range(min(4, len(groups)))))
        except TimeoutError:
            pass
        return result
