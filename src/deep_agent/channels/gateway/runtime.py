from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from deep_agent.agent.observability import log_event

from deep_agent.channels.gateway.config import GatewayConfig
from deep_agent.channels.gateway.deepagent_client import DeepAgentChannelClient
from deep_agent.channels.contracts import ChannelAdapter
from deep_agent.channels.registry import AdapterRegistry, default_registry
from deep_agent.channels.service import ChannelService
from deep_agent.db.stores.channel import ChannelStore
from deep_agent.channels.operations import ChannelOperationService


@dataclass
class AccountRuntime:
    account: Any
    adapter: ChannelAdapter
    fingerprint: str
    stopping: bool = False


class ChannelRuntime:
    """Optional host process. Transport details belong to registered adapters."""

    def __init__(self, store: ChannelStore, config: GatewayConfig, *, registry: AdapterRegistry | None = None, deep_agent=None) -> None:
        self.store = store
        self.config = config
        self.registry = registry if registry is not None else default_registry()
        self.deep_agent = deep_agent if deep_agent is not None else DeepAgentChannelClient(config)
        self.service = ChannelService(store, self.deep_agent, stream_interval=config.stream_interval)
        self.operations = ChannelOperationService(self)
        self._accounts: dict[str, AccountRuntime] = {}
        self._disabled: dict[str, datetime] = {}
        self._failed: dict[str, dict] = {}
        self._poll_task: asyncio.Task | None = None
        self._reconcile_lock = asyncio.Lock()
        self._test_locks: dict[str, asyncio.Lock] = {}
        self._stopping = False

    async def start(self) -> None:
        interrupted = await self.store.interrupt_incomplete()
        if interrupted:
            logger.warning("Channel Gateway recovered {} interrupted records", interrupted)
        await self.deep_agent.start()
        await self.reconcile()
        self._poll_task = asyncio.create_task(self._poll(), name="channel-account-poller")

    async def stop(self) -> None:
        self._stopping = True
        if self._poll_task is not None:
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
        for account_id in list(self._accounts):
            await self._stop_account(account_id)
        await self.deep_agent.close()

    async def _poll(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(self.config.poll_interval)
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_event("channel_reconcile_failed", error_type=type(exc).__name__)

    async def reconcile(self) -> None:
        async with self._reconcile_lock:
            rows = await self.store.list_accounts()
            live_ids = {row.id for row in rows}
            for stale_id in set(self._accounts) - live_ids:
                await self._stop_account(stale_id)
            self._disabled = {row.id: datetime.now(timezone.utc) for row in rows if not row.enabled}
            self._failed.clear()
            for row in rows:
                if not row.enabled:
                    await self._stop_account(row.id)
                    continue
                try:
                    fingerprint = self.registry.fingerprint(row)
                    current = self._accounts.get(row.id)
                    if current is not None and not current.stopping and current.fingerprint == fingerprint:
                        current.account = row
                        continue
                    if current is not None:
                        await self._stop_account(row.id)
                    adapter = self.registry.create(row)
                    managed = AccountRuntime(row, adapter, fingerprint)
                    self._accounts[row.id] = managed
                    self.service.open_account(row.id)

                    async def on_message(message, managed=managed):
                        await self.service.receive(managed.account, managed.adapter, message)

                    await adapter.start(on_message)
                    log_event("channel_adapter_started", account_id=row.id)
                except Exception as exc:
                    log_event("channel_adapter_start_failed", account_id=row.id, error_type=type(exc).__name__)
                    await self._stop_account(row.id)
                    self._failed[row.id] = {"account_id": row.id, "state": "failed", "message": "渠道适配器启动失败，请检查部署配置", "authenticated": False}

    async def _stop_account(self, account_id: str) -> None:
        managed = self._accounts.get(account_id)
        if managed is not None:
            managed.stopping = True
            try:
                await self.operations.close_account(account_id)
                await self.service.close_account(account_id)
            finally:
                await managed.adapter.stop()
                # Retain a failed-to-close adapter for a subsequent retry. Never
                # lose its handle and create a second client for the same Bot.
                self._accounts.pop(account_id, None)
                log_event("channel_adapter_stopped", account_id=account_id)

    def status_payload(self) -> dict[str, Any]:
        accounts = [{"account_id": key, **managed.adapter.status()} for key, managed in sorted(self._accounts.items())]
        accounts.extend({"account_id": key, "state": "closed", "message": "渠道已关闭", "authenticated": False, "updated_at": updated.isoformat()} for key, updated in sorted(self._disabled.items()))
        accounts.extend(self._failed.values())
        return {"state": "connected", "message": "渠道网关运行正常", "accounts": accounts}

    async def resolve_users(self, account_id: str, user_ids: list[str], *, timeout: float = 3.0) -> dict:
        ids = list(dict.fromkeys(user_ids))
        def unavailable(state="unavailable"):
            return {"items": [{"sender_id": uid, "userid": None, "name": None, "status": state}
                              for uid in ids], "complete": not ids}

        account = await self.store.get_account(account_id)
        if account is None:
            raise LookupError("Channel account not found")
        if not account.enabled or account.channel_type != "feishu":
            return unavailable("disabled")
        if ((account.settings or {}).get("feishu") or {}).get("user_details_enabled") is not True:
            return unavailable("disabled")
        current = self._accounts.get(account_id)
        fingerprint = self.registry.fingerprint(account)
        if current is None or current.stopping or current.fingerprint != fingerprint:
            return unavailable()
        try:
            result = await current.adapter.resolve_users(ids, timeout=timeout)
        except Exception:
            return unavailable()
        # Configuration can change while the provider is queried. Never return
        # names from a disabled, removed, rotated or replaced account instance.
        fresh = await self.store.get_account(account_id)
        if (fresh is None or not fresh.enabled or self._accounts.get(account_id) is not current
                or current.stopping or self.registry.fingerprint(fresh) != fingerprint):
            return unavailable()
        return result

    async def test_account(self, account_id: str) -> dict[str, Any]:
        # A temporary test must not race reconciliation or another test into
        # creating a second client for the same Bot.
        async with self._test_locks.setdefault(account_id, asyncio.Lock()):
            await self.reconcile()
            async with self._reconcile_lock:
                account = await self.store.get_account(account_id)
                if account is None:
                    raise LookupError("Channel account not found")
                current = self._accounts.get(account_id)
                if current is not None:
                    result = await current.adapter.test_connection(self.config.test_timeout)
                    return {"account_id": account_id, **result}
                adapter = self.registry.create(account, infinite_retry=False)

                async def ignore_message(_message):
                    return None

                try:
                    if account.channel_type == "feishu":
                        result = await adapter.test_connection(self.config.test_timeout)
                        return {"account_id": account_id, **result}
                    await adapter.start(ignore_message)
                    result = await adapter.test_connection(self.config.test_timeout)
                    return {"account_id": account_id, **result, "warning": "临时测试结束后会断开连接；同一机器人在其他地方的长连接可能被中断。"}
                finally:
                    await adapter.stop()


__all__ = ["AccountRuntime", "ChannelRuntime"]
