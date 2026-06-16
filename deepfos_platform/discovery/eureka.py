from xml.etree import ElementTree
from loguru import logger
from collections import defaultdict
from typing import List, Dict, Type

from ..lib.httpcli import AioHttpCli
from ..lib.utils import concat_url
from ..options import OPTION
from .discovery import ServiceDiscovery
from ..lib.cache import RankedCache, AbstractServiceCache


__all__ = ['Eureka']

XML_KEY = "__xml__"


def http(ins):
    port = ins.find('port')
    enabled = port.get('enabled').lower() == "true"
    if enabled:
        ip = ins.find('ipAddr').text
        return f"http://{ip}:{port.text}"


def https(ins):
    port = ins.find('securePort')
    enabled = port.get('enabled').lower() == "true"
    if enabled:
        ip = ins.find('ipAddr').text
        return f"https://{ip}:{port.text}"


class Eureka(ServiceDiscovery):
    has_instance_cache = False

    def __init__(
        self,
        cache_class: Type[AbstractServiceCache] = RankedCache,
        interval: int = 5,
        prefer_https: bool = OPTION.eureka.prefer_https,
        cares: List[str] = None,
    ):
        super().__init__(cache_class, interval)
        self.prefer_https = prefer_https
        self.cares = cares
        self.versions_delta = ''  # 增量更新的versions_delta
        self.apps_hashcode = ''  # 增量更新的apps_hashcode
        self.setup()

    # noinspection PyAttributeOutsideInit
    def setup(self):
        from .discovery import CACHE_STRATEGY
        base_url: AbstractServiceCache = CACHE_STRATEGY[OPTION.srvdis.cache_strategy]()
        for url in OPTION.eureka.server.split(','):
            base_url.add(url)
        self.base_url = base_url

    async def on_interval(self):
        if self.server_cache:  # 有全量数据时-保持增量更新，否则-全量更新
            await self.get_delta_apps()
        else:
            await self._get_full_apps()

    async def on_startup(self):
        await self._get_full_apps()

    async def get_url(self, server_name: str):
        return await super().get_url(server_name.upper())

    async def update_service_cache(self, server_name: str):
        await self.get_delta_apps()

    async def update_instance_cache(self, server_name: str):
        await self.get_delta_apps()
        
    async def _get_xml(self, end_point: str):
        if not self.base_url:
            # Maybe all deleted by punish
            self.setup()

        logger.debug(f'Current base urls: {self.base_url}')
        url = self.base_url.pick_best()
        try:
            resp = await AioHttpCli.get(concat_url(url, end_point))
            raw_xml = await resp.text()
            xml = ElementTree.fromstring(raw_xml)
        except Exception:
            self.base_url.punish(url)
            raise
        else:
            self.base_url.reward(url)
        return xml

    async def _get_full_apps(self):
        """
        全量获取eureka服务列表，并更新到缓存中
        """
        xml = await self._get_xml('apps')
        # 全量更新：确保拿到数据后，先清缓存
        if xml.find('apps__hashcode').text:
            self.server_cache.clear()
        server_memo = self.server_cache
        prefer_https = self.prefer_https
        cares = self.cares

        for app in xml.iter('application'):
            app_name = app.find('name').text.upper()
            if cares and app_name not in cares:
                continue

            for instance in app.iter('instance'):
                status = instance.find('status')
                if status.text.upper() != "UP":
                    continue

                if prefer_https:
                    addr = https(instance) or http(instance)
                else:
                    addr = http(instance)

                if addr is not None:
                    server_memo[app_name].add(addr)  # 全量更新缓存

        logger.debug("FULL-XML cache updated.")
        return server_memo

    async def get_delta_apps(self):
        """
        增量获取eureka服务列表，并更新到缓存中
        """
        xml = await self._get_xml('apps/delta')
        server_memo = self.server_cache
        prefer_https = self.prefer_https
        cares = self.cares

        if xml.find('versions__delta').text == self.versions_delta and \
                xml.find('apps__hashcode').text == self.apps_hashcode:
            return server_memo  # 如果versions__delta和apps__hashcode完全一样，则不必更新

        # versions__delta和apps__hashcode赋值为最新
        self.versions_delta = xml.find('versions__delta').text
        self.apps_hashcode = xml.find('apps__hashcode').text

        for app in xml.iter('application'):
            app_name = app.find('name').text.upper()
            if cares and app_name not in cares:
                continue

            for instance in app.iter('instance'):
                addr = https(instance) or http(instance) if prefer_https else http(instance)
                if not addr:
                    continue
                # instance的操作类型：ADDED，MODIFIED，DELETED；instacce的状态：UP,...
                # 处理逻辑：只有操作类型在ADDED，MODIFIED中，并且状态是UP的才更新缓存，其他情况都删除
                if instance.find('actionType').text in ('ADDED', 'MODIFIED') and \
                        instance.find('status').text.upper() == "UP":

                    server_memo[app_name].add(addr)  # 增量更新缓存
                else:
                    server_memo[app_name].delete(addr)

        # 增量更新后 - 计算此时全量 apps__hashcode与本次增量请求获取到的的apps__hashcode是否相等
        up_count = 0
        for server_obj in list(server_memo.values()):
            up_count += len(server_obj)
        now_apps_hashcode = f'UP_{up_count}_'
        if now_apps_hashcode != self.apps_hashcode:  # 如果不相等，做全量更新
            self.server_cache = await self._get_full_apps()
        logger.debug("DELTA-XML cache updated.")
        return server_memo
