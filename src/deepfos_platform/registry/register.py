import asyncio
import socket

from loguru import logger
import py_eureka_client.eureka_client as eurekacli

from ..client.system import SystemAPI
from ..conf.modulecfg import ModuleDescriptor, get_module_descriptor, set_module_descriptor
from ..discovery.discovery import ServiceDiscovery
from ..lib.libasync import run_background, run_sync
from ..options import OPTION
from . import nacoscli


def module_to_dict(module: ModuleDescriptor) -> dict:
    return module.to_register_dict(OPTION.general.system_env)


def register_service(server_name: str):
    inst_port = int(OPTION.gunicorn.inst_port)
    full_server_name = f'{OPTION.general.system_env}{server_name}'

    if OPTION.srvdis.implementation == 'eureka':
        instance_id = f"{socket.gethostname()}:{server_name}:{inst_port}"

        eurekacli.init(
            eureka_server=OPTION.eureka.server,
            app_name=full_server_name,
            instance_host=OPTION.gunicorn.inst_host,
            instance_port=inst_port,
            instance_id=instance_id,
            health_check_url='/git-version',
            secure_health_check_url='/git-version',
            duration_in_secs=30,
            renewal_interval_in_secs=10
        )
    elif OPTION.srvdis.implementation == 'nacos':
        run_sync(nacoscli.register_once(
            server_name=full_server_name,
            instance_host=OPTION.gunicorn.inst_host,
            instance_port=inst_port,
            heartbeat_interval=10,
            namespace=OPTION.nacos.namespace,
            group=OPTION.nacos.group,
            cluster=OPTION.nacos.cluster,
        ))
        run_background(
            nacoscli.heartbeat_loop(),
            name='nacos-heartbeat',
        )


async def _install_module(module: ModuleDescriptor):
    await ServiceDiscovery.instantiate().on_startup()
    api = SystemAPI({})
    config = module_to_dict(module)

    try:
        await api.module.install_module(config)
    except Exception:
        logger.error("注册失败")
        raise
    finally:
        await ServiceDiscovery.stop()

    logger.info(f"项目注册完成: {config}")


async def register_module(module: ModuleDescriptor):
    set_module_descriptor(module)
    await _install_module(module)
    register_service(module.serverName)


def sync_register_module(port, module: ModuleDescriptor = None):
    module = module or get_module_descriptor()
    set_module_descriptor(module)

    if port is not None:
        OPTION.gunicorn.inst_port = str(port)

    asyncio.run(_install_module(module))
    register_service(module.serverName)
