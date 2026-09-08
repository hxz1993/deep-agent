import os
import socket
import typing
from functools import cached_property
from typing import List

from .conf.apollo import Apollo
from .conf.option import (
    GlobalOptions, Category, Option, SKIP,
    EnvResult, EnvGetterT,
)
from .log import setup as _setup_logger

from .conf.reloader import EnvFile, EnvironSource, EnvContainer
from .conf.spring import SpringConfig
from .conf.nacos import NacosConfig

__all__ = ["OPTION"]


def _ensure_port_valid(port):
    if isinstance(port, int):
        return port
    if not port:
        return SKIP
    return port


def _ensure_discovery_server_is_set(value):
    if not value:
        return
    impl = OPTION.srvdis.implementation
    server_opt = {
        "eureka": OPTION.eureka.server,
        "nacos": OPTION.nacos.server,
        "k8s": OPTION.k8s.namespace,
    }[impl]
    if server_opt is None:
        raise RuntimeError(f"Missing server configure for {impl}")


def _resolve_instance_host() -> str:
    """Resolve the local registration address without blocking app startup."""
    try:
        return socket.gethostbyname(socket.gethostname())
    except socket.gaierror:
        return "127.0.0.1"


class Server(Category):
    _ENV_PREFIX = "EXPORT_"

    base = Option(..., val_type=str, bind_env=["WEB_GATEWAY_SERVER", "GATEWAY_SERVER"], reload_opt=True)
    app = Option("app-server", val_type=str, bind_env="APP_SERVER_NAME", reload_opt=True)
    system = Option("system-server", val_type=str, bind_env="SYSTEM_SERVER_NAME", reload_opt=True)
    space = Option("space-server", val_type=str, bind_env="SPACE_SERVER_NAME", reload_opt=True)
    platform_file = Option("platform-file-server", val_type=str, bind_env="PLATFORM_FILE_SERVER_NAME", reload_opt=True)
    os_space_tenant = Option("os-space-tenant", val_type=str, bind_env="OS_SPACE_TENANT_SERVER_NAME", reload_opt=True)
    account = Option("seepln-account", val_type=str, bind_env="SERVER_NAME_USER", reload_opt=True)

    if typing.TYPE_CHECKING:
        def __get__(self, instance, owner) -> "Server": ...


class General(Category):
    system_env = Option("", val_type=str, bind_env="SYSTEM_ENV")
    internal_token = Option(None, val_type=str, bind_env="EXPORT_SERVICE_INTERNAL_TOKEN")
    http_verify_ssl = Option(True, val_type=bool, bind_env="EXPORT_HTTP_VERIFY_SSL")

    if typing.TYPE_CHECKING:
        def __get__(self, instance, owner) -> "General": ...


def _normalize_path(path: str):
    return path.rstrip('\\/').replace('\\', '/')


class Log(Category):
    #: 日志等级, 有效设置为CRITICAL, FATAL, ERROR, WARNING, INFO, DEBUG, NOTSET
    level = Option(
        'INFO', val_type=str,
        bind_env="LOG_LEVEL",
        reload_opt=_setup_logger
    )
    #: 日志内容显示等级, 有效设置为CONCISE，ENRICHED，COMPLETE
    content = Option(
        'CONCISE', val_type=str,
        val_choices=('CONCISE', 'ENRICHED', 'COMPLETE'),  # noqa
        bind_env="LOG_CONTENT_LEVEL"
    )
    #: 日志文件
    file = Option(
        None, val_type=str,
        bind_env="LOG_FILE_PATH",
        convertor=_normalize_path,
    )
    #: 单一日志文件轮转条件
    rotation = Option(None, val_type=str, bind_env="LOG_ROTATION")
    #: 单一日志文件轮转检查间隔
    rotation_poll_interval_sec = Option(60, val_type=int, bind_env="LOG_ROTATION_POLL_INTERVAL_SEC")
    #: 轮转后压缩归档的保留策略
    retention = Option(None, val_type=str, bind_env="LOG_RETENTION")

    def __get__(self, instance, owner) -> 'Log':
        """defined to help ide"""
        return super().__get__(instance, owner)


class Gunicorn(Category):
    #: gunicorn启动时每个worker的核数
    workers_per_core = Option("1", val_type=str, bind_env="WORKERS_PER_CORE")
    #: gunicorn启动时最大worker数
    max_workers = Option('', val_type=str, bind_env="MAX_WORKERS")
    #: python服务进程数
    web_concurrency = Option('', val_type=str, bind_env="WEB_CONCURRENCY")
    #: socket绑定IP
    python_host = Option("0.0.0.0", val_type=str, bind_env="PYTHON_HOST")
    #: socket绑定端口
    python_port = Option("30098", val_type=str, bind_env="PYTHON_PORT")
    #: socket绑定的host+port地址
    python_bind = Option('', val_type=str, bind_env="PYTHON_BIND")
    #: 错误日志的目录
    error_log = Option("-", val_type=str, bind_env="ERROR_LOG")
    #: 访问日志的目录
    access_log = Option("-", val_type=str, bind_env="ACCESS_LOG")
    #: 重启时杀死worker的延迟时间，在此之前，worker可以结束服务请求
    graceful_timeout = Option("120", val_type=str, bind_env="GRACEFUL_TIMEOUT")
    #: 超时时间，超过该时长无响应的worker会被杀掉重启
    timeout = Option("120", val_type=str, bind_env="TIMEOUT")
    #: 在keep-alive连接上等待请求的秒数
    keep_alive = Option("5", val_type=str, bind_env="KEEP_ALIVE")
    #: 单个工作进程可处理的最大请求数，超过此值时，工作进程将被关闭，并且启动新的工作进程
    max_requests = Option(2000, val_type=int, bind_env="MAX_REQUESTS_BEFORE_RESTART")
    #: Eureka注册IP
    inst_host = Option(
        _resolve_instance_host(),
        val_type=str, bind_env="LOAD_BALANCER_HOST")
    #: Eureka注册PORT
    inst_port = Option(
        python_port, val_type=str, convertor=_ensure_port_valid,
        bind_env="LOAD_BALANCER_PORT")
    certfile = Option(None, val_type=str, bind_env="GUNICORN_CERTFILE", respect_env_prefix=False)
    keyfile = Option(None, val_type=str, bind_env="GUNICORN_KEYFILE", respect_env_prefix=False)


    def __get__(self, instance, owner) -> 'Gunicorn':
        """defined to help ide"""
        return super().__get__(instance, owner)


class ServiceDiscovery(Category):
    _ENV_PREFIX = "EXPORT_SERVICE_DISCOVERY_"

    enabled = Option(True, val_type=bool, bind_env="ENABLED", extra_check=_ensure_discovery_server_is_set)
    implementation = Option(
        "eureka", val_type=str,
        val_choices=("eureka", "nacos", "k8s", "local"), bind_env="IMPLEMENTATION"
    )
    cache_strategy = Option(
        "ranked", val_type=str,
        val_choices=("ranked", "roundrobin", "random"), bind_env="CACHE_STRATEGY"
    )
    heartbeat = Option(5, val_type=int, bind_env="HEARTBEAT_INTERVAL")

    if typing.TYPE_CHECKING:
        def __get__(self, instance, owner) -> "ServiceDiscovery": ...


class Eureka(Category):
    _ENV_PREFIX = "EXPORT_EUREKA_"

    server = Option(..., val_type=str, bind_env=["DEFAULTZONE", "SERVER_ADDR"])
    prefer_https = Option(False, val_type=bool, bind_env="PREFER_HTTPS")

    if typing.TYPE_CHECKING:
        def __get__(self, instance, owner) -> "Eureka": ...


class Nacos(Category):
    _ENV_PREFIX = "EXPORT_NACOS_"

    server = Option(None, val_type=str, bind_env="SERVER_ADDR")
    cluster = Option("DEFAULT", val_type=str, bind_env="CLUSTER_NAME")
    namespace = Option("public", val_type=str, bind_env="NAMESPACE")
    group = Option("DEFAULT_GROUP", val_type=str, bind_env="GROUP")
    user = Option(None, val_type=str, bind_env="USER_NAME")
    password = Option(None, val_type=str, bind_env="PASSWORD")

    if typing.TYPE_CHECKING:
        def __get__(self, instance, owner) -> "Nacos": ...


class Kubernets(Category):
    namespace = Option(None, val_type=str, bind_env="EXPORT_K8S_DISCOVERY_NAMESPACE")

    if typing.TYPE_CHECKING:
        def __get__(self, instance, owner) -> "Kubernets": ...


class PlatformOptions(GlobalOptions):
    general = General()
    server = Server()
    gunicorn = Gunicorn()
    srvdis = ServiceDiscovery()
    nacos = Nacos()
    k8s = Kubernets()
    eureka = Eureka()
    log = Log()


OPTION = PlatformOptions()
