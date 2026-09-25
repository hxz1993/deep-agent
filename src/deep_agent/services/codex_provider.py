"""Experimental Codex subscription authorization and credential persistence.

No credential is accepted from, or returned to, the browser. The public Codex
client OAuth flow is supplied by Pydantic AI; only the callback URL is parsed
here because the console does not run a localhost callback listener.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from weakref import WeakKeyDictionary

from pydantic_ai.models.openai_codex import OpenAICodexModel
from pydantic_ai.providers.openai_codex import (
    OpenAICodexCredentials,
    OpenAICodexOAuthFlow,
    OpenAICodexProvider,
)
from sqlalchemy import update

from deep_agent.db.models import LlmProvider
from deep_agent.db.stores._session import serialized_sqlite_session


def codex_enabled() -> bool:
    return os.getenv("DEEP_AGENT_ENABLE_CODEX_PROVIDER", "").strip().lower() in {"1", "true", "yes", "on"}


def require_codex_enabled() -> None:
    if not codex_enabled():
        raise CodexUnavailable("Codex 订阅实验功能未开启")


class CodexUnavailable(ValueError):
    pass


def credential_payload(credentials: OpenAICodexCredentials) -> dict[str, str | int]:
    return {
        "schema_version": 1,
        "access_token": credentials.access_token,
        "refresh_token": credentials.refresh_token,
        "account_id": credentials.account_id,
    }


def credentials_from_payload(value: dict | None) -> OpenAICodexCredentials:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise CodexUnavailable("Codex 授权凭据无效，请重新授权")
    fields = ("access_token", "refresh_token", "account_id")
    if any(not isinstance(value.get(field), str) or not value[field] for field in fields):
        raise CodexUnavailable("Codex 授权凭据无效，请重新授权")
    return OpenAICodexCredentials(
        access_token=value["access_token"],
        refresh_token=value["refresh_token"],
        account_id=value["account_id"],
    )


@dataclass
class _PendingFlow:
    flow: OpenAICodexOAuthFlow
    owner: str
    provider_id: str | None
    deadline: float
    credentials: OpenAICodexCredentials | None = None
    used: bool = False


class CodexFlowStore:
    def __init__(self) -> None:
        self._flows: dict[str, _PendingFlow] = {}
        self._lock = asyncio.Lock()

    def _get(self, flow_id: str, owner: str, provider_id: str | None) -> _PendingFlow:
        item = self._flows.get(flow_id)
        if item is None or item.used or item.deadline < time.monotonic():
            self._flows.pop(flow_id, None)
            raise CodexUnavailable("授权流程已过期，请重新开始")
        if item.owner != owner or item.provider_id != provider_id:
            raise CodexUnavailable("授权流程与当前管理员或供应商不匹配")
        return item

    async def start(self, owner: str, provider_id: str | None) -> tuple[str, str]:
        require_codex_enabled()
        _claim_single_owner()
        async with self._lock:
            self._flows = {key: value for key, value in self._flows.items() if not value.used and value.deadline >= time.monotonic()}
            flow_id = secrets.token_urlsafe(32)
            flow = OpenAICodexOAuthFlow()
            self._flows[flow_id] = _PendingFlow(flow, owner, provider_id, time.monotonic() + 600)
            return flow_id, flow.authorization_url()

    async def verify(self, flow_id: str, owner: str, provider_id: str | None, callback_url: str) -> str:
        require_codex_enabled()
        async with self._lock:
            item = self._get(flow_id, owner, provider_id)
            if item.credentials is not None:
                raise CodexUnavailable("此授权回调已验证，请直接保存")
            try:
                parsed = urlsplit(callback_url.strip())
                expected = urlsplit(item.flow.redirect_uri)
                callback_target = (parsed.scheme, parsed.hostname, parsed.port, parsed.path)
                expected_target = (expected.scheme, expected.hostname, expected.port, expected.path)
            except ValueError:
                raise CodexUnavailable("请粘贴完整的 Codex 授权回调地址") from None
            if callback_target != expected_target or parsed.fragment or parsed.username or parsed.password:
                raise CodexUnavailable("请粘贴完整的 Codex 授权回调地址")
            query = parse_qs(parsed.query, keep_blank_values=True)
            if set(query) - {"code", "state", "scope"} or any(len(values) != 1 for values in query.values()):
                raise CodexUnavailable("授权回调参数无效")
            if query.get("state") != [item.flow.state] or not query.get("code", [""])[0]:
                raise CodexUnavailable("授权回调 state 不匹配或缺少授权码")
            try:
                item.credentials = await item.flow.exchange_code(query["code"][0])
            except Exception:
                # Upstream errors may contain submitted codes or token payloads.
                raise CodexUnavailable("授权验证失败，请重新开始授权") from None
            return item.credentials.account_id

    async def commit(self, flow_id: str, owner: str, provider_id: str | None, write):
        require_codex_enabled()
        async with self._lock:
            item = self._get(flow_id, owner, provider_id)
            if item.credentials is None:
                raise CodexUnavailable("请先验证授权回调")
            result = await write(item.credentials)
            item.used = True
            self._flows.pop(flow_id, None)
            return result

    async def cancel(self, flow_id: str, owner: str, provider_id: str | None) -> None:
        async with self._lock:
            self._get(flow_id, owner, provider_id)
            self._flows.pop(flow_id, None)


FLOWS = CodexFlowStore()
_owner_file = None
_models: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, dict]] = WeakKeyDictionary()


def _claim_single_owner() -> None:
    global _owner_file
    if _owner_file is not None:
        return
    path = Path.home() / ".config" / "deep-agent" / "codex-provider-owner.lock"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    file = path.open("a+")
    os.chmod(path, 0o600)
    try:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        file.close()
        raise CodexUnavailable("Codex 订阅已由本机另一运行进程使用") from exc
    _owner_file = file


class DatabaseCodexCredentialSource:
    def __init__(self, provider_id: str, revision: int) -> None:
        self.provider_id = provider_id
        self.revision = revision

    async def load(self) -> OpenAICodexCredentials:
        require_codex_enabled()
        from deep_agent.core.context import get_context
        factory = get_context().db_session_factory
        async with serialized_sqlite_session(factory):
            async with factory() as db:
                row = await db.get(LlmProvider, self.provider_id)
                if row is None or row.auth_method != "oauth" or row.auth_status != "connected":
                    raise CodexUnavailable("Codex 授权已断开，请重新授权")
                if row.auth_revision != self.revision:
                    raise CodexUnavailable("授权已改变，请重新开始模型请求")
                return credentials_from_payload(row.credential_json)

    async def save(self, credentials: OpenAICodexCredentials) -> None:
        require_codex_enabled()
        from deep_agent.core.context import get_context
        factory = get_context().db_session_factory
        async with serialized_sqlite_session(factory):
            async with factory.begin() as db:
                result = await db.execute(
                    update(LlmProvider)
                    .where(LlmProvider.id == self.provider_id,
                           LlmProvider.auth_method == "oauth",
                           LlmProvider.auth_status == "connected",
                           LlmProvider.auth_revision == self.revision)
                    .values(credential_json=credential_payload(credentials), auth_revision=self.revision + 1)
                )
                if result.rowcount != 1:
                    raise CodexUnavailable("授权已改变，旧凭据的刷新结果未写入")
                self.revision += 1


def codex_model(model_name: str, provider_id: str, revision: int) -> OpenAICodexModel:
    require_codex_enabled()
    _claim_single_owner()
    loop = asyncio.get_running_loop()
    bucket = _models.setdefault(loop, {"providers": {}, "models": {}})
    provider = bucket["providers"].get(provider_id)
    if provider is None:
        source = DatabaseCodexCredentialSource(provider_id, revision)
        provider = OpenAICodexProvider(credential_source=source)
        bucket["providers"][provider_id] = provider
    key = (provider_id, model_name)
    model = bucket["models"].get(key)
    if model is None:
        model = OpenAICodexModel(model_name, provider=provider)
        bucket["models"][key] = model
    return model


def evict_provider(provider_id: str) -> None:
    for bucket in _models.values():
        bucket["providers"].pop(provider_id, None)
        for key in list(bucket["models"]):
            if key[0] == provider_id:
                bucket["models"].pop(key, None)


def evict_all_providers() -> None:
    _models.clear()
