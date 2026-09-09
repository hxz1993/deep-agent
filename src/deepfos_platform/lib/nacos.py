import asyncio
import functools
import json
from typing import Any, Tuple, Type, Optional

from loguru import logger
from aiohttp import ClientError

from ..errors.classes import APIResponseParseError, APIStatusNotTrue, APIStatusCodeError
from .httpcli import AioHttpCli
from .utils import concat_url, retry
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
            logger.exception(
                f'Call api: {endpoint} failed. '
                f'Response << {response} >> cannot be decoded as json.'
            )
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
            request = getattr(AioHttpCli, self.method)
            method = self.method.upper()
            is_get = method == 'GET'

            @functools.wraps(func)
            async def call(ins: "NacosAPI", *args, **kwargs):
                body = func(ins, *args, **kwargs)
                req_args = {
                    'headers': ins.header,
                    'params' if is_get else 'data': body
                }
                url_cache = ins.base_urls
                for i, base_url in zip(reversed(range(len(url_cache))), url_cache.sort()):
                    is_last_url = i == 0
                    try:
                        url = concat_url(base_url, endpoint)
                        ins.log(
                            f"Sending request: {method} {url} "
                            f"body: {repr(body)}"
                        )
                        response = await request(url, **req_args)
                        if (
                            response.status == 403
                            and func.__qualname__ != 'NacosAPI.login'
                        ):
                            # retry if token expired
                            if await ins.maybe_login(schedule_renewal=False):
                                body.update(func(ins, *args, **kwargs))
                                response = await request(url, **req_args)

                        if not (200 <= response.status < 300):
                            raise APIStatusCodeError(base_url, response.status)
                        url_cache.reward(base_url)
                        break
                    except ClientError as e:
                        url_cache.punish(base_url)
                        if is_last_url:
                            logger.warning(f"Request of all urls failed.")
                            raise
                        else:
                            logger.warning(f"Request failed with {e}, try next url...")

                text = await response.text()  # noqa
                if response_checker is not None:
                    ok, result = response_checker.validate(text, endpoint)
                    if not ok:
                        raise APIStatusNotTrue(endpoint, text)
                    else:
                        return result
                else:
                    return text

            return call

        return execute


get = Route(method='get')
post = Route(method='post')
put = Route(method='put')
delete = Route(method='delete')


def auth(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        resp = func(self, *args, **kwargs)
        if token := getattr(self, '_token', None):
            resp['accessToken'] = token
        return resp

    return wrapper


class NacosAPI:
    def __init__(
        self,
        nacos_server: str,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ):  # noqa
        self._base_urls = self._resolve_base_urls(nacos_server)
        self.log = logger.debug
        self.header = {"Content-Type": "application/x-www-form-urlencoded"}
        self._token = None
        self._user = user
        self._password = password

    @staticmethod
    def _resolve_base_urls(url: str) -> RankedCache:
        urls = RankedCache()
        for nacos_server in url.split(','):
            if nacos_server.rstrip('/').endswith('nacos'):
                base_url = nacos_server
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
        if self._user is not None and self._password is not None:
            if delay:
                await asyncio.sleep(delay)

            resp = await retry(self.login, wait=1)(self._user, self._password)
            self._token = resp['accessToken']

            ttl = resp['tokenTtl']
            if schedule_renewal:
                asyncio.create_task(self.maybe_login(
                    schedule_renewal=True,
                    delay=min((ttl * 2 / 3), ttl - 5)
                ))
            return True
        return False

    @post('auth/login', JsonResponse)
    def login(self, user: str, password: str):
        return {
            "username": user,
            "password": password
        }

    @put('/ns/instance/beat')
    @auth
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
    @auth
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
    @auth
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
    @auth
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
    @auth
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
    @auth
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
    @auth
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

