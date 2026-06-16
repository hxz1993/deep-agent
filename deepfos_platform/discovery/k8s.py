import asyncio
from collections import defaultdict
from functools import cached_property
from typing import TypedDict, Literal, Type, Dict, FrozenSet

from loguru import logger
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client import (
    V1ServiceList,
    V1Endpoints,
    V1EndpointSubset,
    V1EndpointAddress,
    CoreV1EndpointPort,
)

from .discovery import ServiceDiscovery, RankedCache, AbstractServiceCache
from ..options import OPTION


class K8s(ServiceDiscovery):
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
        self._debug = debug
        self._cares = set()
        self._strategy = strategy
        self._ns = OPTION.k8s.namespace

    async def on_close(self):
        self._cares.clear()
        self.server_cache.clear()
        self._instance_lock.clear()

    async def on_startup(self):
        config.load_incluster_config()

    @cached_property
    def _api(self):
        c = client.Configuration.get_default_copy()
        c.verify_ssl = False
        client.Configuration.set_default(c)
        return client.CoreV1Api()

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


    async def _list_all_service_names(self) -> FrozenSet[str]:
        svcs: V1ServiceList = await self._api.list_namespaced_service(self._ns)
        return frozenset(svc.metadata.name for svc in svcs.items)

    async def update_services(self):
        new_services = await self._list_all_service_names()
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
        endpoints: V1Endpoints = await self._api.read_namespaced_endpoints(
            server_name,
            self._ns,
        )

        no_active_nodes = True
        async with self._instance_lock[server_name]:
            cache = self.server_cache[server_name]

            for subset in endpoints.subsets or []:
                subset: V1EndpointSubset
                for addr in subset.addresses or []:
                    addr: V1EndpointAddress
                    no_active_nodes = False
                    for port in subset.ports:
                        port: CoreV1EndpointPort
                        url = f"{port.name}://{addr.ip}:{port.port}"
                        cache.add(url)

            if no_active_nodes:
                logger.warning(f"{server_name} has no active endpoint, clear invalid...")
                for item in list(cache):
                    cache.delete(item)
