import base64
import os
from functools import cached_property
from typing import Optional, Dict

from ..errors.classes import APIStatusCodeError
from ..lib.httpcli import AioHttpCli
from ..lib.utils import retry, Wait
from .modulecfg import get_module_descriptor
from .reloader import EnvironSource


class SpringConfig(EnvironSource):
    def __init__(self, app: str = ''):
        super().__init__()
        self._app_override = app

    @cached_property
    def _app(self) -> str:
        if self._app_override:
            return self._app_override
        return get_module_descriptor().serverName

    @cached_property
    def enabled(self):
        return self.url is not None

    @cached_property
    def url(self) -> Optional[str]:
        address = os.getenv("EXPORT_CONFIG_URI")
        if address is None:
            return
        profile = os.getenv("EXPORT_CONFIG_PROFILE", "lease")
        branch = os.getenv("EXPORT_CONFIG_BRANCH", "master")
        return f"{address}/{branch}/{self._app}-{profile}.json"

    @cached_property
    def _auth_header(self) -> Dict[str, str]:
        user = os.getenv("EXPORT_CONFIG_USER", "")
        password = os.getenv("EXPORT_CONFIG_PASSWORD", "")
        basic = base64.b64encode(f"{user}:{password}".encode())
        return {"Authorization": f"Basic {basic.decode()}"}

    @retry(retries=5, wait=Wait(base=2, algo='exp_backoff', maximum=30), reraise=True)
    async def get_environ(self):
        r = await AioHttpCli.get(self.url, headers=self._auth_header)
        if 200 <= r.status < 300:
            return await r.json()
        raise APIStatusCodeError(self.url, r.status)
