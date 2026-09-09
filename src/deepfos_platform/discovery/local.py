import asyncio

from loguru import logger

from ..lib.utils import concat_url
from ..options import OPTION
from .discovery import ServiceDiscovery

__all__ = ['Local']


def _noop():
    pass


class Local(ServiceDiscovery):
    has_instance_cache = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._closed = asyncio.Event()

    async def on_interval(self):
        pass

    async def on_startup(self):
        pass

    async def update_service_cache(self, server_name: str):
        pass

    async def update_instance_cache(self, server_name: str):
        pass

    async def get_url(self, server_name: str):
        url = concat_url(OPTION.server.base, server_name.lower())
        logger.debug(f'Get url in local discovery for {server_name}: {url}')
        return (_noop, _noop, url)
