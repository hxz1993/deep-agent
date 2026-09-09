import os
from functools import cached_property
from typing import Optional, Dict

from ..lib.constants import BOOLEAN_STATES
from ..lib.utils import retry, Wait
from ..lib.nacos import NacosAPI
from .reloader import EnvironSource


def ensure_env(key: str) -> str:
    if (env := os.environ.get(key)) is None:
        raise EnvironmentError(f"Missing environment variable '{key}'")
    return env


def maybe_unwrap_quote(text: str) -> str:
    if text.startswith("'") or text.startswith('"'):
        quote = text[0]
    else:
        return text
    if text.endswith(quote):
        return text[1:-1]
    else:
        return text


class NacosConfig(EnvironSource):
    @cached_property
    def url(self) -> Optional[str]:
        return ensure_env("EXPORT_NACOS_CONFIG_SERVER_ADDR")

    @cached_property
    def enabled(self):
        env_conf = os.getenv("EXPORT_NACOS_CONFIG_ENABLED", "false")
        return bool(BOOLEAN_STATES.get(env_conf.lower()))

    @retry(retries=5, wait=Wait(base=2, algo='exp_backoff', maximum=30), reraise=True)
    async def get_environ(self):
        api = NacosAPI(
            nacos_server=self.url,
            user=os.getenv("EXPORT_NACOS_CONFIG_USERNAME"),
            password=os.getenv("EXPORT_NACOS_CONFIG_PASSWORD"),
        )
        namespace = os.getenv("EXPORT_NACOS_CONFIG_NAMESPACE", "public")
        group = os.getenv("EXPORT_NACOS_CONFIG_GROUP", "DEFAULT_GROUP")
        await api.maybe_login(schedule_renewal=False)

        async def get_configs(data_id: str) -> Dict[str, str]:
            raw = await api.get_configs(data_id, namespace=namespace, group=group)
            conf = {}
            for line in raw.splitlines():
                left, sep, right = line.strip().partition("=")
                if not sep:
                    continue
                conf[left.strip()] = maybe_unwrap_quote(right.strip())
            return conf

        environ = {}
        if shared_conf_id := os.getenv("EXPORT_NACOS_CONFIG_SHARED_CONFIG"):
            environ.update(await get_configs(shared_conf_id))
        if exclusive_conf_id := os.getenv("EXPORT_NACOS_CONFIG_EXT_CONFIG"):
            environ.update(await get_configs(exclusive_conf_id))
        return environ
