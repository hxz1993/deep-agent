import asyncio
import atexit

from typing import Optional

from loguru import logger

from ..lib.utils import retry, Wait
from ..lib.nacos import NacosAPI
from ..discovery.discovery import poll_event
from ..options import OPTION

_NacosIns: Optional['NacosInstance'] = None


class NacosInstance:
    def __init__(
        self,
        server_name: str,
        ip: str,
        port: int,
        api: NacosAPI,
        heartbeat_interval: int = 2,
        group: str = 'DEFAULT_GROUP',
        namespace: str = 'public',
        cluster: str = 'DEFAULT',
    ):
        self.server_name = server_name
        self.ip = ip
        self.port = port
        self.api = api
        self.heartbeat_interval = heartbeat_interval
        self.group = group
        self.namespace = namespace
        self.cluster = cluster
        self._stop = asyncio.Event()
        self._wait = Wait(
            max(heartbeat_interval // 2, 1),
            'exp_backoff', heartbeat_interval
        )

    async def initialize(self):
        await self.api.maybe_login()

    @property
    def running(self):
        return not self._stop.is_set()

    async def register_once(self):
        await self.api.register_instance(
            self.server_name,
            self.ip,
            self.port,
            group=self.group,
            namespace=self.namespace,
            cluster=self.cluster,
        )

    async def run_heartbeat_loop(self):
        send_hearbeat = retry(
            self.send_hearbeat,
            retries=3,
            wait=self._wait
        )

        has_exception = False
        async for _ in poll_event(self._stop, self.heartbeat_interval):
            try:
                if has_exception:
                    await self.register_once()
                await send_hearbeat()
                has_exception = False
            except Exception:  # noqa
                has_exception = True
                logger.exception("exception occurred on send heartbeat")

        logger.debug('finish sending hearbeat')

    async def register(self):
        """Register then block in heartbeat loop (legacy init path)."""
        await self.register_once()
        await self.run_heartbeat_loop()

    async def send_hearbeat(self):
        await self.api.send_heartbeat(
            self.server_name,
            self.ip,
            self.port,
            group=self.group,
            namespace=self.namespace,
            cluster=self.cluster,
        )
        logger.debug('hearbeat sent')

    async def unregister(self):
        if not self._stop.is_set():
            self._stop.set()
            logger.debug('stop is set')
        await self.api.unregister_instance(
            self.server_name,
            self.ip,
            self.port,
            group=self.group,
            namespace=self.namespace,
            cluster=self.cluster,
        )


def _build_instance(
    server_name: str,
    instance_host: str,
    instance_port: int,
    heartbeat_interval: int,
    group: str,
    namespace: str,
    cluster: str,
) -> NacosInstance:
    return NacosInstance(
        server_name,
        ip=instance_host,
        port=instance_port,
        api=NacosAPI(
            nacos_server=OPTION.nacos.server,
            user=OPTION.nacos.user,
            password=OPTION.nacos.password,
        ),
        heartbeat_interval=heartbeat_interval,
        group=group,
        namespace=namespace,
        cluster=cluster,
    )


async def register_once(
    server_name: str,
    instance_host: str,
    instance_port: int,
    heartbeat_interval: int = 2,
    group: str = 'DEFAULT_GROUP',
    namespace: str = 'public',
    cluster: str = 'DEFAULT',
):
    global _NacosIns

    if _NacosIns and _NacosIns.running:
        return

    _NacosIns = _build_instance(
        server_name, instance_host, instance_port,
        heartbeat_interval, group, namespace, cluster,
    )
    await _NacosIns.initialize()
    try:
        await _NacosIns.register_once()
    except Exception:
        logger.error(
            f"Nacos register_once failed for {server_name} "
            f"{instance_host}:{instance_port}"
        )
        raise


async def heartbeat_loop():
    global _NacosIns

    if _NacosIns is None:
        logger.error("heartbeat_loop called before register_once")
        return
    await _NacosIns.run_heartbeat_loop()


async def init(
    server_name: str,
    instance_host: str,
    instance_port: int,
    heartbeat_interval: int = 2,
    group: str = 'DEFAULT_GROUP',
    namespace: str = 'public',
    cluster: str = 'DEFAULT',
):
    """Register and run heartbeat loop in the current event loop (blocking)."""
    await register_once(
        server_name, instance_host, instance_port,
        heartbeat_interval, group, namespace, cluster,
    )
    await heartbeat_loop()


async def stop():
    global _NacosIns

    if _NacosIns is None:
        return

    ins = _NacosIns
    _NacosIns = None
    try:
        await ins.unregister()
    except Exception:
        logger.exception("Nacos unregister failed")


@atexit.register
def maybe_stop_nacos():
    global _NacosIns

    if _NacosIns is None:
        return

    from ..lib.libasync import run_sync

    run_sync(stop())
