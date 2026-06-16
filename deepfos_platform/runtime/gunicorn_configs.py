import multiprocessing
import os
import threading
from typing import Dict, Any

from loguru_corotate import (
    ensure_reopen_inited,
    reset_reopen_state_after_fork,
    managed_rotation_poll_loop,
)

from ..options import OPTION
from ..registry import register_service


def load_gunicorn_configs(
    server_name: str,
    env_prefix: str = None
) -> Dict[str, Any]:
    if env_prefix is not None:
        OPTION.gunicorn.set_env_prefix(env_prefix)
        OPTION.log.set_env_prefix(env_prefix)

    workers_per_core_str = OPTION.gunicorn.workers_per_core
    max_workers_str = OPTION.gunicorn.max_workers
    use_max_workers = None
    if max_workers_str:
        use_max_workers = int(max_workers_str)
    web_concurrency_str = OPTION.gunicorn.web_concurrency

    host = OPTION.gunicorn.python_host
    port = OPTION.gunicorn.python_port
    bind_env = OPTION.gunicorn.python_bind
    use_loglevel = OPTION.log.level
    if bind_env:
        use_bind = bind_env
        host, port = bind_env.split(':')
        OPTION.gunicorn.python_host = host
        OPTION.gunicorn.python_port = port
    else:
        use_bind = f"{host}:{port}"

    cores = multiprocessing.cpu_count()
    workers_per_core = float(workers_per_core_str)
    default_web_concurrency = workers_per_core * cores
    if web_concurrency_str:
        web_concurrency = int(web_concurrency_str)
        assert web_concurrency > 0
    else:
        web_concurrency = max(int(default_web_concurrency), 2)
        if use_max_workers:
            web_concurrency = min(web_concurrency, use_max_workers)
    accesslog_var = OPTION.gunicorn.access_log
    use_accesslog = accesslog_var or None
    errorlog_var = OPTION.gunicorn.error_log
    use_errorlog = errorlog_var or None
    graceful_timeout_str = OPTION.gunicorn.graceful_timeout
    timeout_str = OPTION.gunicorn.timeout
    keepalive_str = OPTION.gunicorn.keep_alive

    # Gunicorn config variables
    loglevel = use_loglevel
    workers = web_concurrency
    bind = use_bind
    errorlog = use_errorlog
    worker_tmp_dir = "/tmp"

    accesslog = use_accesslog
    graceful_timeout = int(graceful_timeout_str)
    timeout = int(timeout_str)
    keepalive = int(keepalive_str)

    worker_class = 'uvicorn.workers.UvicornWorker'
    max_requests = OPTION.gunicorn.max_requests
    max_requests_jitter = 20

    certfile = OPTION.gunicorn.certfile
    keyfile = OPTION.gunicorn.keyfile

    # noinspection PyUnusedLocal
    def on_starting(server):
        from ..log import setup

        setup(OPTION.log.level)
        register_service(server_name)

        file_path = OPTION.log.file
        rotation = OPTION.log.rotation
        if file_path and rotation:
            threading.Thread(
                target=managed_rotation_poll_loop,
                args=(
                    file_path,
                    rotation,
                    OPTION.log.rotation_poll_interval_sec,
                    OPTION.log.retention,
                ),
                daemon=True, name="log-rotation",
            ).start()

    def on_exit(server):
        from py_eureka_client import eureka_client
        eureka_client.stop()

    # noinspection PyProtectedMember
    def post_fork(server, worker):
        import atexit

        from ..registry.nacoscli import maybe_stop_nacos
        atexit.unregister(maybe_stop_nacos)

        from ..discovery import ServiceDiscovery
        ServiceDiscovery.__ins__ = None

    # noinspection PyUnusedLocal
    def post_worker_init(worker):
        # reset + 登记表 + SIGUSR1 链都放在框架 ``init_signals`` 装完 USR1 之后，
        # 避免被 Gunicorn/UvicornWorker 覆盖
        from ..log import logger_config

        reset_reopen_state_after_fork()
        ensure_reopen_inited(OPTION.log.file, **logger_config())

    return {
        "loglevel": loglevel,
        "workers": workers,
        "bind": bind,
        "errorlog": errorlog,
        "worker_tmp_dir": worker_tmp_dir,
        "accesslog": accesslog,
        "graceful_timeout": graceful_timeout,
        "timeout": timeout,
        "keepalive": keepalive,
        "worker_class": worker_class,
        "max_requests": max_requests,
        "max_requests_jitter": max_requests_jitter,
        "certfile": certfile,
        "keyfile": keyfile,
        "on_starting": on_starting,
        "post_fork": post_fork,
        "post_worker_init": post_worker_init,
        "on_exit": on_exit,
    }
