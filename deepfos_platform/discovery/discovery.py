import asyncio

from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Dict, Type
from functools import partial
from urllib.parse import urlparse

from loguru import logger
from starlette.datastructures import Address

from ..errors.classes import ServerNotFound, ServerInstanceNotFound
from ..lib.cache import (
    AbstractServiceCache,
    RankedCache,
    RoundRobinCache,
    RandomCache
)
from ..options import OPTION

CACHE_STRATEGY = {
    'ranked': RankedCache,
    'roundrobin': RoundRobinCache,
    'random': RandomCache,
}


async def poll_event(
    event: asyncio.Event,
    interval: int
):
    fut = asyncio.ensure_future(event.wait())

    async def poller():
        done, _ = await asyncio.wait(
            [fut],
            timeout=interval
        )
        return bool(done)

    while not (await poller()):
        try:
            yield
        except Exception:  # noqa
            logger.exception("Exception occurs while polling event.")


class ServiceDiscovery(ABC):
    __impl__: Dict[str, Type['ServiceDiscovery']] = {}
    __ins__: 'ServiceDiscovery' = None
    has_instance_cache = True

    def __init__(
        self,
        cache_class: Type[AbstractServiceCache] = RankedCache,
        interval: int = None,
        **kwargs,
    ):
        # 本地缓存的服务信息
        self.server_cache: Dict[str, AbstractServiceCache] = defaultdict(cache_class)
        self.interval = interval or OPTION.srvdis.heartbeat
        self._closed = None

    @property
    def closed(self):
        if self._closed is None:
            return True
        return self._closed.is_set()

    async def on_close(self):
        pass

    async def close(self):
        logger.debug('Closing service discovery ...')
        if self._closed is not None:
            self._closed.set()
        await self.on_close()

    async def init(self):
        """will block util close is called"""
        if not self.closed:
            logger.warning(f"{self.__class__.__name__} already running.")
            return

        self._closed = closed = asyncio.Event()

        if not self.server_cache:
            await self.on_startup()

        async for _ in poll_event(closed, self.interval):
            try:
                await self.on_interval()
            except Exception:   # noqa
                logger.exception('Exception occurs on interval.')

        logger.debug(f'{self.__class__.__name__} stopped.')

    @abstractmethod
    async def on_interval(self): pass

    @abstractmethod
    async def on_startup(self): pass

    @abstractmethod
    async def update_service_cache(self, server_name: str): pass

    @abstractmethod
    async def update_instance_cache(self, server_name: str): pass

    def on_failure(self, server_name: str, addr):
        self.server_cache[server_name].punish(addr)

    def on_success(self, server_name: str, addr):
        self.server_cache[server_name].reward(addr)

    async def contain_addr(self, server_name: str, addr: Address) -> bool:
        if server_name not in self.server_cache:
            await self.update_service_cache(server_name)

        if server_name not in self.server_cache:
            return False

        server_list = self.server_cache[server_name]
        if not server_list and self.has_instance_cache:
            await self.update_instance_cache(server_name)

        for server in server_list:
            try:
                if netloc := urlparse(server).netloc:
                    host, port = netloc.split(':')
                    if addr.host == host:
                        return True
            except ValueError:
                continue

        return False

    async def get_url(self, server_name: str):
        if server_name not in self.server_cache:
            await self.update_service_cache(server_name)

        if server_name not in self.server_cache:
            raise ServerNotFound(server_name)

        server_list = self.server_cache[server_name]
        if not server_list and self.has_instance_cache:
            await self.update_instance_cache(server_name)

        if not server_list:
            raise ServerInstanceNotFound(server_name)

        url = server_list.pick_best()
        logger.debug(f'Get url in discovery for {server_name}: {url}')
        return (
            partial(self.on_success, server_name, url),
            partial(self.on_failure, server_name, url),
            url
        )

    def __init_subclass__(cls, **kwargs):
        assert cls.__name__ not in ServiceDiscovery.__impl__
        ServiceDiscovery.__impl__[cls.__name__.lower()] = cls

    @staticmethod
    def _ensure_module_is_imported(mod):
        if mod == 'eureka':
            from .eureka import Eureka  # noqa
        elif mod == 'nacos':
            from .nacos import Nacos  # noqa
        elif mod == 'k8s':
            from .k8s import K8s  # noqa
        elif mod == 'local':
            from .local import Local  # noqa

    @classmethod
    def instantiate(cls):
        if cls.__ins__ is None:
            impl = OPTION.srvdis.implementation
            cls._ensure_module_is_imported(impl)
            cls.__ins__ = cls.__impl__[impl](
                cache_class=CACHE_STRATEGY[OPTION.srvdis.cache_strategy]
            )

        return cls.__ins__

    @classmethod
    async def start(cls):
        asyncio.create_task(cls.instantiate().init())
        await asyncio.sleep(0)

    @classmethod
    async def stop(cls):
        if (
            cls.__ins__ is None
            or cls.__ins__.closed
        ):
            return
        await cls.__ins__.close()
        cls.__ins__ = None
