import base64
import hashlib
import hmac
import json
import os
import socket
import time
from functools import cached_property
from typing import Optional

from ..errors.classes import APIStatusCodeError
from ..lib.httpcli import AioHttpCli
from ..lib.utils import retry, Wait
from .reloader import EnvironSource

APOLLO_CONFIG_URL = 'APOLLO_CONFIG_URL'
APOLLO_CONFIG_CLUSTER = 'APOLLO_CONFIG_CLUSTER'
APOLLO_APP_ID = 'EXPORT_APOLLO_APP_ID'
APOLLO_NAMESPACE = 'EXPORT_APOLLO_NAMESPACE'
APOLLO_ACCESS_KEY_SECRET = 'EXPORT_APOLLO_ACCESS_KEY_SECRET'
NO_APOLLO = 'NO APOLLO'


def _ensure_valid(val: Optional[str]) -> Optional[str]:
    if val is not None and val.upper() != NO_APOLLO:
        return val


def _get_ip() -> str:
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(('8.8.8.8', 53))
        ip = sock.getsockname()[0]
    except Exception:  # noqa
        ip = '127.0.0.1'
    finally:
        if sock is not None:
            sock.close()
    return ip



class Apollo(EnvironSource):
    @cached_property
    def url(self) -> Optional[str]:
        addr = _ensure_valid(os.getenv(APOLLO_CONFIG_URL))
        return f'{addr}{self._path}'

    @cached_property
    def enabled(self):
        addr = _ensure_valid(os.getenv(APOLLO_CONFIG_URL))
        cluster = _ensure_valid(os.getenv(APOLLO_CONFIG_CLUSTER))
        return addr is not None and cluster is not None

    @cached_property
    def _need_auth(self) -> bool:
        return APOLLO_ACCESS_KEY_SECRET in os.environ

    @cached_property
    def _path(self) -> str:
        if not (namespace := _ensure_valid(os.getenv(APOLLO_NAMESPACE))):
            raise RuntimeError(f'environment {APOLLO_NAMESPACE} not set')

        if not (app_id := _ensure_valid(os.getenv(APOLLO_APP_ID))):
            raise RuntimeError(f'environment {APOLLO_APP_ID} not set')

        cluster = _ensure_valid(os.getenv(APOLLO_CONFIG_CLUSTER))
        return f'/configs/{app_id}/{cluster}/{namespace}?ip={_get_ip()}'

    def _build_auth_header(self) -> Optional[dict]:
        if not self._need_auth:
            return None

        timestamp = str(int(time.time() * 1000))
        secret = os.getenv(APOLLO_ACCESS_KEY_SECRET)
        app_id = os.getenv(APOLLO_APP_ID)
        signing_string = f"{timestamp}\n{self._path}"
        signature = base64.b64encode(
            hmac.new(secret.encode(), signing_string.encode(), hashlib.sha1).digest()
        ).decode()
        return {
            "Authorization": f"Apollo {app_id}:{signature}",
            "Timestamp": timestamp,
        }

    @retry(retries=5, wait=Wait(base=2, algo='exp_backoff', maximum=30), reraise=True)
    async def get_environ(self):
        r = await AioHttpCli.get(self.url, headers=self._build_auth_header())
        try:
            if 200 <= r.status < 300:
                text = await r.text()
                return json.loads(text)['configurations']
            raise APIStatusCodeError(self.url, r.status)
        finally:
            r.release()
