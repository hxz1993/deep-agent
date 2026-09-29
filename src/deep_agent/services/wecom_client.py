"""Application API client. Credentials and upstream error text never escape here."""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass

import aiohttp


class WecomError(Exception):
    def __init__(self, stage: str, code: str | int):
        self.stage = stage
        self.code = str(code)
        super().__init__(f"企微查询失败（{stage}: {self.code}）")


@dataclass
class _Token:
    value: str
    deadline: float


class WecomClient:
    BASE_URL = "https://qyapi.weixin.qq.com/cgi-bin"
    TOKEN_ERRORS = {40014, 42001}

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._tokens: dict[tuple[str, int], _Token] = {}
        self._token_tasks: dict[tuple[str, int], asyncio.Task] = {}
        self._slots = asyncio.Semaphore(4)
        self._closed = False

    async def close(self):
        self._closed = True
        tasks = list(self._token_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._token_tasks.clear()
        self._tokens.clear()
        if self._session is not None:
            await self._session.close()
            self._session = None

    def invalidate(self, application_id: str):
        for key in list(self._tokens):
            if key[0] == application_id:
                self._tokens.pop(key, None)
        for key, task in list(self._token_tasks.items()):
            if key[0] == application_id:
                task.cancel()
                self._token_tasks.pop(key, None)

    async def _request(self, method, path, *, stage, params=None, body=None):
        if self._closed:
            raise WecomError(stage, "service_closed")
        if self._session is None:
            self._session = aiohttp.ClientSession(
                trust_env=True,
                timeout=aiohttp.ClientTimeout(total=15, connect=5),
                connector=aiohttp.TCPConnector(limit=4),
            )
        try:
            async with self._slots:
                if self._closed:
                    raise WecomError(stage, "service_closed")
                async with self._session.request(
                    method, self.BASE_URL + path, params=params, json=body,
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise WecomError(stage, f"http_{response.status}")
                    data = await response.json(content_type=None)
            if not isinstance(data, dict):
                raise WecomError(stage, "invalid_response")
            return data
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            # ClientError strings may contain the complete credential-bearing URL.
            raise WecomError(stage, "network_error") from None

    async def _fetch_token(self, app, key):
        try:
            data = await self._request("GET", "/gettoken", stage="token", params={
                "corpid": app.corp_id, "corpsecret": app.app_secret,
            })
            code = data.get("errcode", 0)
            if type(code) is not int or code != 0:
                raise WecomError("token", code if type(code) is int else "invalid_response")
            value = data.get("access_token")
            if not isinstance(value, str) or not value:
                raise WecomError("token", "invalid_response")
            try:
                ttl = float(data.get("expires_in", 0))
            except (TypeError, ValueError, OverflowError):
                raise WecomError("token", "invalid_response") from None
            if not math.isfinite(ttl) or ttl <= 0:
                raise WecomError("token", "invalid_response")
            ttl = max(0, ttl - 60)
            self._tokens[key] = _Token(value, time.monotonic() + ttl)
            return value
        finally:
            if self._token_tasks.get(key) is asyncio.current_task():
                self._token_tasks.pop(key, None)

    async def token(self, app, *, force=False):
        if self._closed:
            raise WecomError("token", "service_closed")
        if not app.app_secret:
            raise WecomError("token", "secret_not_configured")
        key = (app.id, app.revision)
        if force:
            self._tokens.pop(key, None)
        cached = self._tokens.get(key)
        if cached and cached.deadline > time.monotonic():
            return cached.value
        task = self._token_tasks.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch_token(app, key))
            # Retrieve exceptions even if the requesting job has timed out.
            task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
            self._token_tasks[key] = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Rotating a credential cancels its old token fetch, but must not
            # masquerade as cancellation of an unrelated chat/HTTP request.
            current = asyncio.current_task()
            if task.cancelled() and current is not None and not current.cancelling():
                raise WecomError("token", "configuration_changed") from None
            raise

    async def call(self, app, method, path, *, stage, params=None, body=None, before_request=None):
        for attempt in range(2):
            token = await self.token(app)
            if before_request is not None:
                await before_request()
            data = await self._request(method, path, stage=stage,
                params={**(params or {}), "access_token": token}, body=body)
            code = data.get("errcode")
            if type(code) is int and code == 0:
                return data
            if type(code) is int and code in self.TOKEN_ERRORS and attempt == 0:
                key = (app.id, app.revision)
                cached = self._tokens.get(key)
                if cached and cached.value == token:
                    self._tokens.pop(key, None)
                continue
            raise WecomError(stage, code if type(code) is int else "invalid_response")

    async def convert(self, app, raw_id: str) -> tuple[str | None, bool]:
        data = await self.call(app, "POST", "/batch/openuserid_to_userid", stage="convert",
            body={"open_userid_list": [raw_id]})
        users = data.get("userid_list", [])
        invalid = data.get("invalid_open_userid_list", [])
        if not isinstance(users, list) or not isinstance(invalid, list):
            raise WecomError("convert", "invalid_response")
        for item in users:
            if isinstance(item, dict) and item.get("open_userid") == raw_id:
                userid = item.get("userid")
                if not isinstance(userid, str) or not userid.strip():
                    raise WecomError("convert", "invalid_response")
                return userid, False
        # Only an explicit, successful invalid list permits the plaintext probe.
        return None, raw_id in invalid

    async def member(self, app, userid: str) -> tuple[str, str | None]:
        data = await self.call(app, "GET", "/user/get", stage="profile", params={"userid": userid})
        actual = data.get("userid")
        if not isinstance(actual, str) or actual.casefold() != userid.casefold():
            raise WecomError("profile", "identity_mismatch")
        name = data.get("name")
        return actual, name.strip() if isinstance(name, str) and name.strip() else None

    async def media(self, app, media_id: str, *, limit: int) -> tuple[bytes, str | None, str]:
        """Download only the official media endpoint; never follow a supplied URL."""
        for attempt in range(2):
            token = await self.token(app)
            # token() has initialized the shared, bounded session.
            if self._session is None or self._closed:
                raise WecomError('media', 'service_closed')
            try:
                async with self._slots:
                    async with self._session.get(self.BASE_URL + '/media/get',
                            params={'access_token': token, 'media_id': media_id},
                            allow_redirects=False) as response:
                        if response.status != 200:
                            raise WecomError('media', f'http_{response.status}')
                        if response.content_length is not None and response.content_length > limit:
                            raise WecomError('media', 'size_limit')
                        chunks, size = [], 0
                        async for chunk in response.content.iter_chunked(65536):
                            size += len(chunk)
                            if size > limit:
                                raise WecomError('media', 'size_limit')
                            chunks.append(chunk)
                        data = b''.join(chunks)
                        mime = response.content_type
                        disposition = response.content_disposition
                        filename = disposition.filename if disposition else None
                if mime in {'application/json', 'text/json'} and not filename:
                    import json
                    error = json.loads(data)
                    code = error.get('errcode') if isinstance(error, dict) else None
                    if type(code) is int and code in self.TOKEN_ERRORS and attempt == 0:
                        cached = self._tokens.get((app.id, app.revision))
                        if cached and cached.value == token:
                            self._tokens.pop((app.id, app.revision), None)
                        continue
                    raise WecomError('media', code if type(code) is int else 'invalid_response')
                if not data:
                    raise WecomError('media', 'empty_media')
                return data, filename, mime
            except (aiohttp.ClientError, TimeoutError, ValueError):
                raise WecomError('media', 'network_error') from None
        raise WecomError('media', 'invalid_response')

    async def send_message(self, app, recipient: str, text: str, *, message_type: str, before_send=None) -> dict:
        # Application text and Markdown limits are UTF-8 bytes, not character counts.
        if not text.strip():
            raise WecomError('send', 'empty_text')
        agent_id = str(app.wecom_agent_id)
        if not agent_id.isascii() or not agent_id.isdecimal() or len(agent_id) > 12:
            raise WecomError('send', 'invalid_agent_id')
        if message_type not in {'text', 'markdown'}:
            raise WecomError('send', 'unsupported_message_type')
        from deep_agent.services.wecom_app_format import format_markdown, split_text
        chunks = format_markdown(text) if message_type == 'markdown' else split_text(text)
        if not chunks or not any(chunk.strip() for chunk in chunks):
            raise WecomError('send', 'empty_text')
        ids = []
        for chunk in chunks:
            result = await self.call(app, 'POST', '/message/send', stage='send', body={
                'touser': recipient, 'msgtype': message_type, 'agentid': int(app.wecom_agent_id),
                message_type: {'content': chunk},
            }, **({'before_request': before_send} if before_send is not None else {}))
            if result.get('invaliduser') or result.get('unlicenseduser'):
                raise WecomError('send', 'recipient_unavailable')
            if result.get('msgid'):
                ids.append(str(result['msgid']))
        return {'status': 'sent', 'message_id': ids[-1] if ids else None, 'part_count': len(chunks)}
