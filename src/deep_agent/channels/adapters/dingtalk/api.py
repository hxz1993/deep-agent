"""Explicit DingTalk HTTP operations with checked platform acknowledgements."""
import asyncio
import time
import logging
import httpx
from deep_agent.channels.outbound import DeliveryError

API = 'https://api.dingtalk.com'


class _DingTalkRequestLogFilter(logging.Filter):
    def filter(self, record):
        # httpx INFO logs complete upload-token and signed download URLs.
        # Preserve unrelated HTTP diagnostics while withholding these URLs.
        if any(domain in record.getMessage() for domain in
               ("dingtalk.com", "alicdn.com", "aliyuncs.com", "alibaba.com")):
            record.msg = "DingTalk HTTP request completed (URL withheld)"
            record.args = ()
            record.exc_info = record.exc_text = record.stack_info = None
        return True


def _configure_request_logging():
    http_logger = logging.getLogger("httpx")
    if not any(isinstance(item, _DingTalkRequestLogFilter) for item in http_logger.filters):
        http_logger.addFilter(_DingTalkRequestLogFilter())


class DingTalkAPI:
    def __init__(self, client_id, secret):
        _configure_request_logging()
        self.client_id, self.secret = client_id, secret
        self.http = httpx.AsyncClient(timeout=30, follow_redirects=False)
        self._token = ''
        self._expires = 0
        self._lock = asyncio.Lock()

    async def close(self):
        await self.http.aclose()

    async def token(self):
        async with self._lock:
            if self._token and self._expires > time.monotonic():
                return self._token
            data = await self.request('POST', '/v1.0/oauth2/accessToken', authenticated=False,
                json={'appKey': self.client_id, 'appSecret': self.secret})
            if not data.get('accessToken'):
                raise DeliveryError('authentication_failed', '钉钉应用认证失败')
            self._token = data['accessToken']
            self._expires = time.monotonic() + max(0, int(data.get('expireIn', 7200)) - 120)
            return self._token

    async def request(self, method, path, *, authenticated=True, **kwargs):
        headers = {'x-acs-dingtalk-access-token': await self.token()} if authenticated else {}
        try:
            response = await self.http.request(method, API + path, headers=headers, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError):
            raise DeliveryError('request_unconfirmed', '钉钉请求中断，结果未知', unknown=True) from None
        return self.checked(response)

    @staticmethod
    def checked(response):
        # A server/proxy failure cannot prove that a mutating request was rejected.
        # Never tell the caller it is safe to resend a potentially delivered item.
        if response.status_code >= 500 or response.status_code == 408:
            raise DeliveryError('request_unconfirmed', '钉钉响应异常，结果未知，请勿重复发送', unknown=True)
        if not response.is_success:
            if response.status_code not in (403, 429):
                raise DeliveryError('platform_rejected', '钉钉拒绝请求，请检查应用权限、可见范围及消息格式')
        try:
            data = response.json()
        except ValueError:
            raise DeliveryError('invalid_response', '钉钉返回了无法识别的响应', unknown=True) from None
        if not isinstance(data, dict):
            raise DeliveryError('invalid_response', '钉钉返回了无法识别的响应', unknown=True)
        if response.status_code in (403, 429) and 'QpsLimit' in str(data.get('code', '')):
            raise DeliveryError('card_rate_limited', '钉钉请求限流')
        if response.is_error or data.get('errcode', 0) not in (0, '0') or data.get('code') not in (None, '', 0, '0') or data.get('success') is False:
            raise DeliveryError('platform_rejected', '钉钉拒绝请求，请检查应用权限、可见范围及消息格式')
        return data

    async def upload(self, component):
        token = await self.token()
        kind = {'image': 'image', 'audio': 'voice', 'video': 'file'}.get(component.kind, 'file')
        try:
            response = await self.http.post('https://oapi.dingtalk.com/media/upload',
                params={'access_token': token, 'type': kind},
                files={'media': (component.filename, component.data, component.mime)})
        except (httpx.TimeoutException, httpx.TransportError):
            raise DeliveryError('upload_failed', '钉钉媒体上传失败，未发送消息') from None
        try:
            data = self.checked(response)
        except DeliveryError as exc:
            if exc.unknown:
                raise DeliveryError('upload_failed', '钉钉媒体上传失败，未发送消息') from None
            raise
        if not data.get('media_id'):
            raise DeliveryError('upload_failed', '钉钉未返回媒体标识，未发送消息')
        return data['media_id']
