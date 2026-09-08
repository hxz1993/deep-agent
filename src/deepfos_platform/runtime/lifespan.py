from contextlib import asynccontextmanager

from ..discovery.discovery import ServiceDiscovery
from ..lib.httpcli import AioHttpCli


@asynccontextmanager
async def worker_lifespan():
    """
    Uvicorn worker lifespan: outbound ServiceDiscovery + HTTP client cleanup.
    Does not register with Nacos/Eureka (use gunicorn on_starting for that).
    """
    await ServiceDiscovery.start()
    try:
        yield
    finally:
        await ServiceDiscovery.stop()
        await AioHttpCli.close_current()
