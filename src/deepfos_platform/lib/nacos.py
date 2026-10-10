import asyncio
import functools
import json
from typing import Any, Tuple, Type, Optional

from loguru import logger
from aiohttp import ClientError

from ..errors.classes import APIResponseParseError, APIStatusNotTrue, APIStatusCodeError
from .httpcli import AioHttpCli
from .utils import concat_url
from .nacos_auth import PasswordAuthProvider, create_auth_provider
from .cache import RankedCache


class ResponseChecker:
    @classmethod
    def expect(cls, response: Any) -> bool:
        return True

    @classmethod
    def cast(cls, response: str, endpoint: str):
        return response

    @classmethod
    def validate(cls, response: str, endpoint: str) -> Tuple[bool, Any]:
        casted = cls.cast(response, endpoint)
        return cls.expect(casted), casted


class TextResponse(ResponseChecker):
    @classmethod
    def expect(cls, response: str) -> bool:
        return response == 'ok'


class JsonResponse(ResponseChecker):
    @classmethod
    def cast(cls, response: str, endpoint: str):
        try:
            return json.loads(response)
        except (TypeError, ValueError):
            logger.exception(f'Call Nacos api {endpoint} returned invalid JSON.')
            raise APIResponseParseError(endpoint) from None


class Route:
    def __init__(self, method: str):
        self.method = method

    def __call__(
        self,
        endpoint,
        response_checker: Type[ResponseChecker] = None,
    ):
        def execute(func):
            method = self.method.upper()
            is_auth = endpoint.lstrip('/').startswith('auth/')

            @functools.wraps(func)
            async def call(ins: "NacosAPI", *args, **kwargs):
                request = getattr(AioHttpCli, self.method)
                url_cache = ins.base_urls
                bases = url_cache.sort()
                for index, base_url in enumerate(bases):
                    is_last_url = index == len(bases) - 1
                    try:
                        url = concat_url(base_url, endpoint)
                        # Rebuild the business payload after refresh; never reuse old auth fields.
                        for attempt in range(2):
                            body = func(ins, *args, **kwargs)
                            if not is_auth:
                                await ins.auth_provider.prepare_request()
                                body = ins.auth_provider.enrich(endpoint, body)
                            ins.log(f'[nacos] method={method} route={func.__name__}')
                            response = await request(
                                url, headers=ins.header,
                                **{'params' if method == 'GET' else 'data': body},
                            )
                            if response.status == 403 and not is_auth and attempt == 0:
                                response.release()
                                if await ins.auth_provider.refresh(schedule_renewal=False):
                                    continue
                                raise APIStatusCodeError(base_url, response.status)
                            break
                        if not 200 <= response.status < 300:
                            response.release()
                            raise APIStatusCodeError(base_url, response.status)
                        try:
                            text = await response.text()
                        finally:
                            response.release()
                        url_cache.reward(base_url)
                        break
                    except ClientError:
                        url_cache.punish(base_url)
                        if is_last_url:
                            logger.warning('Request of all Nacos urls failed.')
                            raise
                        logger.warning(f'Nacos connection failed, trying next url.')

                if response_checker is not None:
                    ok, result = response_checker.validate(text, endpoint)
                    if not ok:
                        raise APIStatusNotTrue(endpoint, text)
                    return result
                return text

            return call

        return execute


get = Route(method='get')
post = Route(method='post')
put = Route(method='put')
delete = Route(method='delete')


class NacosAPI:
    def __init__(self, options=None):
        if options is None:
            # Options also imports this client through the config source.
            from ..options import OPTION
            options = OPTION.nacos
        self.log = logger.debug
        self.header = {"Content-Type": "application/x-www-form-urlencoded"}
        self._token = None
        self._base_urls = self._resolve_base_urls(options.server)
        self.auth_provider = create_auth_provider(
            options.auth_mode, user=options.user, password=options.password,
            access_key=options.access_key, secret_key=options.secret_key,
            ram_role_name=options.ram_role_name,
        )
        self.auth_provider.bind(self)

    @staticmethod
    def _resolve_base_urls(url: str) -> RankedCache:
        urls = RankedCache()
        for nacos_server in url.split(','):
            nacos_server = nacos_server.rstrip('/')
            if nacos_server.lower().endswith('nacos'):
                base_url = nacos_server[:-5] + 'nacos'
            else:
                base_url = concat_url(nacos_server, 'nacos')
            urls.add(concat_url(base_url, 'v1'))
        return urls

    @property
    def base_urls(self) -> RankedCache:
        return self._base_urls

    async def maybe_login(
        self,
        schedule_renewal: bool = True,
        delay: Optional[float] = None,
    ) -> bool:
        if delay:
            await asyncio.sleep(delay)
        if isinstance(self.auth_provider, PasswordAuthProvider):
            return await self.auth_provider.refresh(schedule_renewal=schedule_renewal)
        await self.auth_provider.initialize()
        return True

    async def close(self):
        await self.auth_provider.close()

    @post('auth/login', JsonResponse)
    def login(self, user: str, password: str):
        return {
            "username": user,
            "password": password
        }

    @put('/ns/instance/beat')
    def send_heartbeat(
        self,
        server_name: str,
        ip: str,
        port: int,
        group: str = 'DEFAULT_GROUP',
        namespace: str = 'public',
        cluster: str = 'DEFAULT',
    ):
        beat = json.dumps({
            'ip': ip,
            'port': port,
            'serviceName': server_name,
            'weight': 1.0,
            'groupName': group,
            'namespaceId': namespace,
            'clusterName': cluster,
        })
        return {
            'serviceName': server_name,
            'beat': beat,
            'groupName': group,
            'namespaceId': namespace,
            'clusterName': cluster,
        }

    @delete('ns/instance', TextResponse)
    def unregister_instance(
        self,
        server_name: str,
        ip: str,
        port: int,
        group: str = 'DEFAULT_GROUP',
        namespace: str = 'public',
        cluster: str = 'DEFAULT',
    ):
        return {
            'serviceName': server_name,
            'ip': ip,
            'port': port,
            'groupName': group,
            'namespaceId': namespace,
            'clusterName': cluster,
        }

    @post('ns/instance', TextResponse)
    def register_instance(
        self,
        server_name: str,
        ip: str,
        port: int,
        group: str = 'DEFAULT_GROUP',
        namespace: str = 'public',
        cluster: str = 'DEFAULT',
    ):
        return {
            'serviceName': server_name,
            'ip': ip,
            'port': port,
            'enabled': True,
            'healthy': True,
            'weight': 1.0,
            'groupName': group,
            'namespaceId': namespace,
            'clusterName': cluster,
        }

    @put('ns/instance', TextResponse)
    def modify_instance(
        self,
        server_name: str,
        ip: str,
        port: int
    ):
        return {
            'serviceName': server_name,
            'ip': ip,
            'port': port,
            'enabled': True,
            'healthy': True,
            'weight': 1.0,
        }

    @get('ns/service/list', JsonResponse)
    def list_service(
        self,
        group: str = 'DEFAULT_GROUP',
        namespace: str = 'public',
    ):
        return {
            'pageNo': 1,
            'pageSize': 1000000,
            'namespaceId': namespace,
            'groupName': group,
        }

    @get('ns/instance/list', JsonResponse)
    def list_instance(
        self,
        service_name: str,
        group: str = 'DEFAULT_GROUP',
        namespace: str = 'public',
        cluster: str = 'DEFAULT',
    ):
        return {
            'serviceName': service_name,
            'groupName': group,
            'namespaceId': namespace,
            'clusters': cluster,
        }

    @get('cs/configs')
    def get_configs(
        self,
        data_id: str,
        group: str = 'DEFAULT_GROUP',
        namespace: str = 'public',
    ):
        return {
            'dataId': data_id,
            'group': group,
            'tenant': namespace,
        }
