import enum
import json
import re
import weakref

from typing import Union, TYPE_CHECKING, Callable, Awaitable, Any
from functools import wraps, cached_property
from urllib.parse import urlencode

from aiohttp import ClientResponse, ClientConnectorError
from ..lib.pydantic_v1 import parse_obj_as, BaseModel as PydanticBaseModel
from starlette_context import context
from cachetools import TTLCache
from loguru import logger

from ..lib.httpcli import AioHttpCli
from ..lib.utils import get_ignore_case, concat_url, retry, Wait
from ..lib.repr import repr
from ..lib.constants import INTERNAL_TOKEN_HEADER
from ..options import OPTION
from ..errors.classes import (
    APICallError, APINotAvailError,
    APIStatusNotTrue, APIStatusCodeError, APIResponseParseError,
    APIResponseModelError
)
from ..discovery.discovery import ServiceDiscovery
from ..discovery.target import normalize_service_target

_RE_TRIM_HOST = re.compile(r"http[s]?://(.*?)/(.*)")

_PUBLIC_MODULE = (
    'SpaceAPI',
    'SystemAPI',
    'PlatformAPI'
)


def _upstream_error_fields(payload):
    """仅提取已知错误字段，不把业务 data、请求 Header 或整个响应正文带入异常。"""
    if not isinstance(payload, dict):
        return {}
    sources = [payload]
    sources.extend(
        payload[key] for key in ('error', 'detail')
        if isinstance(payload.get(key), dict)
    )
    fields = {}
    for target, keys in (
        ('message', ('message', 'msg', 'error_description', 'detail', 'error')),
        ('code', ('code', 'errorCode', 'error_code')),
        ('error_type', ('type', 'errorType', 'error_type')),
    ):
        for source in sources:
            for key in keys:
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    fields[target] = value
                    break
                if target == 'code' and type(value) in (int, float):
                    fields[target] = value
                    break
            if target in fields:
                break
    return fields


def _plain_upstream_error(body, content_type):
    """保留纯文本错误提示；HTML 页面和堆栈不作为模型错误消息返回。"""
    if content_type != 'text/plain':
        return {}
    try:
        message = body.decode('utf-8').strip()
    except UnicodeError:
        return {}
    if not message or re.search(
        r'<(?:!doctype|html|head|body|script|style|div|pre)\b|'
        r'Traceback \(most recent call last\)|'
        r'\n\s*(?:at\s+\S+\(|File "[^"\n]+", line \d+)',
        message, re.IGNORECASE,
    ):
        return {}
    return {'message': message[:4000]}


class RequestInfo:
    """请求参数wrapper，提供参数的展示，合并，构造功能"""

    def __init__(
        self,
        method: str = None,
        url: str = None,
        body: dict = None,
        header: dict = None,
        param: dict = None,
        path: str = None,
    ):
        self._method = method
        self.uri = url
        self._body = body
        self._header = header
        self._param = param
        self._meta = None
        self._path = path

    @cached_property
    def meta(self):
        return self._meta

    @property
    def url(self):
        """把 attr:`_param` 拼接至 attr:`uri` ，形成完整的请求地址"""
        if self._path:
            url = concat_url(self.uri, self._path)
        else:
            url = self.uri
        if param := self._param:
            if isinstance(param, PydanticBaseModel):
                encodable_param = param.dict()
            else:
                encodable_param = []
                for k, v in param.items():
                    if v is not None:
                        if not isinstance(v, list):
                            encodable_param.append((k, v))
                        else:
                            encodable_param.extend((k, item) for item in v)
            return f"{url.rstrip('/')}?{urlencode(encodable_param)}"
        return url

    @cached_property
    def method(self):
        return self._method.upper()

    @cached_property
    def body(self):
        if isinstance(self._body, PydanticBaseModel):
            return self._body.dict(by_alias=True, exclude_none=True)
        return self._body

    @cached_property
    def header(self):
        return self._header

    def setdefault(self, key, value):
        """

        Args:
            key:
            value:

        Returns:

        """
        key = '_' + key
        attr = getattr(self, key)
        if attr is None:
            setattr(self, key, value)

    def __repr__(self):
        args = {
            'body': self.body,
            'header': self.header,
        }
        return f"{self.method.upper()} {self.url} {repr(args)}"

    def update_default(self, request: Union[dict, 'RequestInfo']):
        """
        从其他 :class:`RequestInfo` 或者 :class:`dict` 中更新属性

        Tips:
            只会更新自身未设置的属性（即值为None的属性）

        Args:
            request: 更新数据源
        """
        if not request:
            return
        if isinstance(request, dict):
            kv_pairs = request.items()
        else:
            kv_pairs = request.__dict__.items()

        for k, v in kv_pairs:
            self.setdefault(k, v)

class ContentType(str, enum.Enum):
    json = "json"
    bytes = "bytes"
    text = "text"


class Route:
    """装饰器，用于简化系统api的封装流程"""

    def __init__(self, method):
        self.method = method
        self.wait = Wait(1, algo='exp_backoff')

    def __call__(
        self,
        endpoint,
        resp_model=None,
        retries=1,
        allow_none=False,
        raise_false=True,
        return_raw=False,
        data_wrapped: bool = True,
        content_type: ContentType = ContentType.json
    ):
        """
        装饰器主入口

        Args:
            endpoint: 请求地址末端路径
            resp_model: 接口的返回模型
            retries: 接口调用失败时的重试次数，仅对post请求生效
            allow_none: 接口返回的data是否允许为None，如果允许，在返回None时，
                将不会试图把response.data解析成resp_model。一般来说，当接口
                返回status=True，data=None时使用。（这种情况一般由于接口编写不规范导致）
            raise_false: 当接口返回status为false时，是否抛出异常
            data_wrapped: 响应的数据是否被data字段包装

        函数调用可选参数
            dynamic: 接口是否需动态获取服务名，可覆盖装饰器的同名参数
            resp_model: 接口的返回模型，可覆盖装饰器的同名参数
            element_info: 动态获取服务名时，调用get_server_names的传参

        """
        def execute(func):
            method = self.method

            @wraps(func)
            async def call(ins, *args, **kwargs):
                if 'resp_model' in kwargs:
                    model = kwargs.pop('resp_model')
                else:
                    model = func.__annotations__.get('return', resp_model)

                _return_raw = kwargs.pop('return_raw', return_raw)
                func_retries = max(kwargs.pop('retries', 0), retries)

                # ---------------------------------------------------------------------
                # 组装请求函数，多余kwargs在此前pop
                req = RequestInfo(method=method)
                req.update_default(func(ins, *args, **kwargs))
                request = getattr(ins, method)

                retry_count = 0  # 记录重试次数

                async def do_request():
                    nonlocal retry_count
                    dis = ServiceDiscovery.instantiate()
                    on_success, on_failure, base_url = await ins.get_base_url()
                    req.uri = concat_url(base_url, endpoint)
                    try:
                        r = await request(req.url, req.body, req.header)
                        if r.status == 404:
                            if on_failure is not None:
                                on_failure()
                            if retry_count == 0:
                                await dis.update_instance_cache(ins.server_name)
                                retry_count += 1
                                r = await do_request()
                        elif 200 <= r.status < 300 and on_success is not None:
                            on_success()
                        return r

                    except ClientConnectorError as e:
                        if on_failure is not None:
                            on_failure()
                        if retry_count == 0:
                            await dis.update_instance_cache(ins.server_name)
                            retry_count += 1
                            return await do_request()
                        else:
                            raise APICallError(req.uri, e) from None

                    except OSError as e:
                        if on_failure is not None:
                            on_failure()
                        raise APICallError(req.uri, e) from None

                if func_retries > 0:
                    retry_do_request = retry(
                        func=do_request,
                        retries=func_retries,
                        wait=self.wait,
                        name=func.__qualname__
                    )
                    resp = await retry_do_request()
                else:
                    resp = await do_request()

                # -----------------------------------------------------------------------------
                # 校验请求结果
                flag, obj = await ins.check_response(resp, req, data_wrapped, content_type)

                if _return_raw:
                    return obj

                if data_wrapped:
                    data = obj.get('data')
                else:
                    data = obj

                if flag is not True:
                    if raise_false:
                        error_fields = _upstream_error_fields(obj)
                        raise APIStatusNotTrue(
                            _RE_TRIM_HOST.match(req.uri).group(2),
                            error_fields.get('message'),
                            error_fields.get('code'),
                            http_status=resp.status,
                            error_type=error_fields.get('error_type'),
                        )
                    else:
                        return data

                if model is None or (allow_none and data is None):
                    return data

                try:
                    return parse_obj_as(model, data)
                except Exception:
                    logger.exception(f"Parse model failed.")
                    raise APIResponseModelError(
                        f"Expect model: '{model}', Got '{data}'"
                    ) from None

            return call

        return execute


get = Route(method='get')
post = Route(method='post')
put = Route(method='put')
delete = Route(method='delete')


class APIMeta(type):
    def __new__(mcs, name, bases, namespace):
        if not bases:
            async def get_base_url(self):
                sd_ins = ServiceDiscovery.instantiate()
                if sd_ins.closed:
                    await sd_ins.start()
                if type(self).__name__ not in _PUBLIC_MODULE:
                    server_name = f"{OPTION.general.system_env}{self.server_name}"
                else:
                    server_name = self.server_name
                *callbacks, url = await sd_ins.get_url(server_name)
                return *callbacks, concat_url(url, self.endpoint)

            namespace.setdefault('get_base_url', get_base_url)
        return super().__new__(mcs, name, bases, namespace)


class BaseAPI(metaclass=APIMeta):
    _RE_CONTENT_TYPE = re.compile(r'^\s*application/(?P<ctype>.*);.*')
    endpoint = '/'
    if TYPE_CHECKING:
        get_base_url: Callable[[Any], Awaitable[Callable, Callable, str]]

    def __init__(
        self,
        header: Union[PydanticBaseModel, dict] = None,
        server_name: str = '',
    ):
        if isinstance(header, PydanticBaseModel):
            self.header = header.dict(by_alias=True)
        elif header is None:
            self.header = context.data
        else:
            self.header = header.copy()

        self.server_name = normalize_service_target(server_name)
        self.log = logger.info

    def _parse_content_type(self, header):
        content_type = get_ignore_case(header, 'Content-Type', None)
        if content_type is None:
            logger.warning('Missing content-type in request header.')
            return

        matched = self._RE_CONTENT_TYPE.match(content_type)
        if matched:
            return matched.group('ctype').lower()

    def _merge_header(self, header):
        if header is not None:
            _header = self.header.copy()
            _header.update(header)

            if internal_token := OPTION.general.internal_token:
                _header[INTERNAL_TOKEN_HEADER] = internal_token

            return _header
        return self.header

    async def post(self, url, body, header=None):
        header = self._merge_header(header)
        content_type = self._parse_content_type(header)

        self.log(
            f"Sending request: POST {url} "
            f"body: {repr(body)} header: {repr(header)}")

        if content_type == 'json':
            return await AioHttpCli.post(url, json=body, headers=header)
        else:
            return await AioHttpCli.post(url, data=body, headers=header)

    async def get(self, url, params=None, header=None):
        header = self._merge_header(header)

        self.log(
            f"Sending request: GET {url} "
            f"params: {repr(params)} header: {repr(header)}")

        return await AioHttpCli.get(url, params=params, headers=header)

    async def put(self, url, body, header=None):
        header = self._merge_header(header)

        self.log(
            f"Sending request: PUT {url} "
            f"body: {repr(body)} header: {repr(header)}")

        return await AioHttpCli.put(url, data=body, headers=header)

    async def delete(self, url, body, header=None):
        header = self._merge_header(header)

        self.log(
            f"Sending request: DELETE {url} "
            f"body: {repr(body)} header: {repr(header)}")

        return await AioHttpCli.delete(url, data=body, headers=header)

    @staticmethod
    async def check_response(
        response: ClientResponse,
        request: RequestInfo,
        data_wrapped: bool = True,
        content_type: ContentType = ContentType.json
    ):
        try:
            status_code = response.status
            if not (200 <= status_code < 300):
                body = await response.read()
                try:
                    error_fields = _upstream_error_fields(json.loads(body))
                except (ValueError, UnicodeError):
                    error_fields = _plain_upstream_error(body, response.content_type)
                logger.warning(
                    "Platform API returned HTTP error: status={}, exception_type={}",
                    status_code, APIStatusCodeError.__name__,
                )
                raise APIStatusCodeError(
                    _RE_TRIM_HOST.match(request.uri).group(2),
                    status_code, **error_fields)

            if content_type is ContentType.bytes:
                return True, (await response.read())

            text = await response.text()

            if content_type is ContentType.text:
                return True, text

            try:
                resp = json.loads(text)

                if not data_wrapped and 'status' not in resp:
                    return True, resp

                if resp['status'] is False:
                    logger.warning(
                        "Platform API returned business error: status={}, exception_type={}",
                        status_code, APIStatusNotTrue.__name__,
                    )
                    return False, resp
            except (TypeError, ValueError):
                if not data_wrapped and not text:
                    return True, None

                logger.warning(
                    "Platform API returned invalid JSON: status={}, exception_type={}",
                    status_code, APIResponseParseError.__name__,
                )
                raise APIResponseParseError(
                    path=_RE_TRIM_HOST.match(request.uri).group(2)
                ) from None
            else:
                return True, resp
        finally:
            response.release()


class DynamicBaseAPI(BaseAPI):
    server_cache = TTLCache(maxsize=128, ttl=3600)
    module_type: str = ""

    def __init__(self, version: str = None, header=None):
        super().__init__(header)
        if version is not None:
            self.module_id = f"{self.module_type}{version.replace('.', '_')}"
        else:
            self.module_id = None

    def __await__(self):
        return self.init().__await__()

    def _add_to_memo(self, server_meta):
        if not server_meta:
            raise APINotAvailError(self.header['space'], self.module_type)
        server_name = server_meta.serverName
        self.server_cache[self.module_id] = server_name
        return server_name

    async def _get_server_name(self):
        module_id = self.module_id
        if module_id in self.server_cache:
            return self.server_cache[module_id]
        from .space import SpaceAPI
        api = SpaceAPI(self.header)
        server_meta = await api.module.get_module_detail(module_id)
        return self._add_to_memo(server_meta)

    async def init(self):
        if self.module_id is not None:
            server_name = await self._get_server_name()
        else:
            server_name = await self._find_valid_server()
        self.server_name = server_name
        return self

    async def _find_valid_server(self):
        space = self.header['space']

        if (key := (space, self.module_type)) in self.server_cache:
            return self.server_cache[key]

        from .space import SpaceAPI
        api = SpaceAPI(self.header)
        modules = await api.module.get_usable_module()
        target_module = self.module_type

        for mdl in modules:
            if mdl.moduleType == target_module and mdl.status == 1:
                self.server_cache[key] = mdl.serverName
                return mdl.serverName

        raise APINotAvailError(space, self.module_type)


class ComponentAPI(BaseAPI):
    def __init__(self, root: BaseAPI):
        super().__init__(header=root.header)
        self.__root = root
        self.server_name = root.server_name

    @property
    def root(self):
        return self.__root

    async def get_base_url(self):
        *callbacks, base = await self.__root.get_base_url()
        return *callbacks, concat_url(base, self.endpoint)


class V3ComponentAPI(ComponentAPI):
    async def get_base_url(self):
        *callbacks, base = await self.root.get_base_url()
        return *callbacks, concat_url(base, self.endpoint.format(**self.header))
