import asyncio
from typing import Dict, Optional

import aiohttp

from .utils import auto_setup

_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(connect=4, total=180)
_CONNECTOR_LIMIT = 100
_CONNECTOR_LIMIT_PER_HOST = 30


class AioHttpCli:
    _sessions: Dict[int, aiohttp.ClientSession] = {}
    _locks: Dict[int, asyncio.Lock] = {}

    @classmethod
    def _verify_ssl(cls) -> bool:
        from ..options import OPTION

        return OPTION.general.http_verify_ssl

    @classmethod
    def _create_session(cls) -> aiohttp.ClientSession:
        verify = cls._verify_ssl()
        connector = aiohttp.TCPConnector(
            limit=_CONNECTOR_LIMIT,
            limit_per_host=_CONNECTOR_LIMIT_PER_HOST,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
            ssl=verify,
        )
        return aiohttp.ClientSession(
            connector=connector,
            timeout=_DEFAULT_TIMEOUT,
        )

    @classmethod
    async def get_session(cls) -> aiohttp.ClientSession:
        loop = asyncio.get_running_loop()
        key = id(loop)
        session = cls._sessions.get(key)
        if session is None or session.closed:
            lock = cls._locks.setdefault(key, asyncio.Lock())
            async with lock:
                session = cls._sessions.get(key)
                if session is None or session.closed:
                    cls._sessions[key] = cls._create_session()
        return cls._sessions[key]

    @classmethod
    async def get(cls, url, *, allow_redirects=True, **kwargs):
        session = await cls.get_session()
        return await session.get(url, allow_redirects=allow_redirects, **kwargs)

    @classmethod
    async def post(cls, url, *, data=None, **kwargs):
        session = await cls.get_session()
        return await session.post(url, data=data, **kwargs)

    @classmethod
    async def put(cls, url, *, data=None, **kwargs):
        session = await cls.get_session()
        return await session.put(url, data=data, **kwargs)

    @classmethod
    async def delete(cls, url, *, data=None, **kwargs):
        session = await cls.get_session()
        return await session.delete(url, data=data, **kwargs)

    @classmethod
    async def close(cls):
        for session in list(cls._sessions.values()):
            if not session.closed:
                await session.close()
        cls._sessions.clear()
        cls._locks.clear()

    @classmethod
    async def close_current(cls):
        loop = asyncio.get_running_loop()
        key = id(loop)
        session: Optional[aiohttp.ClientSession] = cls._sessions.pop(key, None)
        cls._locks.pop(key, None)
        if session is not None and not session.closed:
            await session.close()

    @classmethod
    def reset_after_fork(cls):
        """Drop inherited session references after os.fork (invalid in child)."""
        cls._sessions.clear()
        cls._locks.clear()

