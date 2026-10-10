import asyncio
from collections import defaultdict
from typing import TypedDict, Literal, Type, Dict

from loguru import logger

from .discovery import ServiceDiscovery
from ..lib.cache import RankedCache, AbstractServiceCache
from ..lib.nacos import NacosAPI
from ..options import OPTION


class InstanceHostInfo(TypedDict, total=False):
    instanceId: str
    ip: str
    port: int
    weight: float
    healthy: bool
    enabled: bool
    ephemeral: bool
    clusterName: str
    serviceName: str
    metadata: dict
    instanceHeartBeatInterval: int
    instanceIdGenerator: str
    instanceHeartBeatTimeOut: int
    ipDeleteTimeout: int


class Nacos(ServiceDiscovery):
    def __init__(
        self,
        cache_class: Type[AbstractServiceCache] = RankedCache,
        interval: int = 5,
        debug: bool = False,
        strategy: Literal['lazy', 'eager'] = 'lazy',
    ):
        super().__init__(cache_class, interval)
        self._instance_lock: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._server_lock = asyncio.Lock()
        self._api = NacosAPI()
        self._debug = debug
        self._cares = set()
        self._strategy = strategy
        self._group = OPTION.nacos.group
        self._ns = OPTION.nacos.namespace
        self._cluster = OPTION.nacos.cluster

    async def on_close(self):
        self._cares.clear()
        self.server_cache.clear()
        self._instance_lock.clear()
        await self._api.close()

    async def on_startup(self):
        await self._api.maybe_login()

    async def on_interval(self):
        await self._update_cache(self._strategy)

    async def _update_cache(
        self,
        strategy: Literal['lazy', 'eager'] = 'lazy',
    ):
        logger.debug(f'update cache using strategy [{strategy}] ...')

        if strategy == 'eager':
            await self.update_services()
            services = self.server_cache.keys()
        else:
            services = self._cares

        if self._debug:
            services = list(services)
            logger.debug(f"Update cache for instance: {services}")

        await asyncio.gather(*(
            self.update_instance_for_service(sn)
            for sn in services
        ))

    async def update_service_cache(self, server_name: str):
        async with self._server_lock:
            if server_name not in self.server_cache:
                await self.update_services()

    async def update_instance_cache(self, server_name):
        self._cares.add(server_name)
        await self.update_instance_for_service(server_name)

    async def update_services(self):
        new_services = frozenset((await self._api.list_service(
            group=self._group, namespace=self._ns,
        ))['doms'])
        cur_services = frozenset(self.server_cache.keys())

        if added := new_services - cur_services:
            for srv in added:
                self.server_cache.__getitem__(srv)
            logger.debug(f"Added services: {added}")

        if removed := cur_services - new_services:
            for srv in removed:
                self.server_cache.pop(srv)
                self._instance_lock.pop(srv, None)  # noqa
            logger.debug(f"Removed servieces: {removed}")

    async def update_instance_for_service(self, server_name: str):
        async with self._instance_lock[server_name]:
            instance_info = await self._api.list_instance(
                server_name,
                group=self._group,
                namespace=self._ns,
                cluster=self._cluster
            )

        host: InstanceHostInfo
        cache = self.server_cache[server_name]

        for host in instance_info['hosts']:
            if not host['enabled']:
                continue

            addr = f"http://{host['ip']}:{host['port']}"
            cache.add(addr)
            if not host['healthy']:
                cache.punish(addr)
