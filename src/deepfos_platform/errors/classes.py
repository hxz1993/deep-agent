import functools
import itertools
from typing import Optional, Union

from fastapi.exceptions import HTTPException


class InternalError(HTTPException):
    def __init__(self, detail=None, headers=None, status_code=500):
        super().__init__(status_code=status_code, detail=detail)
        self.headers = headers


class ExpectedError(HTTPException):
    errno: int = 42
    msg: str = None

    def __init__(self, *args, **kwargs):
        self.msg = self.get_msg().format(*args, **kwargs)
        super().__init__(status_code=200, detail=self.msg)
        self.errno = self.get_errno()

    @classmethod
    @functools.lru_cache(maxsize=None)
    def get_msg(cls):
        if cls.msg is not None:
            return cls.msg
        if cls.__doc__ is not None:
            return cls.__doc__
        return cls.__name__

    @classmethod
    @functools.lru_cache(maxsize=None)
    def get_errno(cls):
        errno = 0
        for idx, clz in enumerate(
            itertools.dropwhile(lambda c: c is not ExpectedError, reversed(cls.__mro__))
        ):
            if idx == 0:
                errno += clz.errno * 1_000_000
            elif idx == 1:
                errno += clz.errno * 10_000
            else:
                errno += clz.errno
        return errno

    def __str__(self):
        return f"[{self.get_errno()}] {self.msg}"


class APIError(ExpectedError):
    errno = 99
    # 上游响应信息与本地 ExpectedError 的 HTTP 200 包装状态分开保存。
    upstream_message = None
    upstream_code = None
    upstream_error_type = None
    upstream_http_status = None


class ServerNotFound(APIError):
    msg = "未找到服务{}的注册信息"
    errno = 1


class ServerInstanceNotFound(APIError):
    msg = "未找到服务{}的实例信息"
    errno = 2


class APINotAvailable(APIError):
    msg = "空间[{}]下，组件[{}]没有可用的服务"
    errno = 3


class APICallError(APIError):
    msg = "调用外部接口IO异常。接口：{}；错误：{}"
    errno = 4


class APIHttpCodeNotOK(APIError):
    msg = "调用外部接口http异常。接口：{}；状态码：{}"
    errno = 5

    def __init__(self, path, status_code, *, message=None, code=None, error_type=None):
        self.upstream_message = message
        self.upstream_code = code
        self.upstream_error_type = error_type
        self.upstream_http_status = status_code
        super().__init__(path, status_code)
        if message:
            self.msg += f"；原生提示：{message}"
            self.detail = self.msg


APIStatusCodeError = APIHttpCodeNotOK
APINotAvailError = APINotAvailable


class APIStatusNotTrue(APIError):
    msg = "调用外部接口返回错误。接口：{}；提示：{}"
    errno = 6

    def __init__(self, path, message, code=None, *, http_status=None, error_type=None):
        self.upstream_message = message
        self.upstream_code = code
        self.upstream_error_type = error_type
        self.upstream_http_status = http_status
        super().__init__(path, message or '上游接口返回 status=false', code)

    @property
    def code(self) -> Optional[Union[int, str]]:
        return self.upstream_code


class APIResponseParseError(APIError):
    msg = "调用外部接口{path}返回的不是标准JSON。"
    errno = 7


class APIResponseModelError(APIError):
    msg = "调用外部接口返回不符合接口定义，详细：{}。"
    errno = 8


class APIResponseError(APIError):
    msg = "调用外部接口失败。详细：{}。"
    errno = 9
