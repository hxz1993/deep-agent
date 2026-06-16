import re
from abc import ABC
from typing import (
    Iterable, Dict, List, Optional, Any, Tuple, Coroutine, Callable, Protocol
)

from ..lib.libasync import periodic_task, run_sync

_RE_ENV = re.compile(r'export\s+(\w+)\s*=\s*[\'\"]?([^\"\'\r\n]*)[\'\"]?')
OnChangeT = Callable[[str], Coroutine[Any, Any, None]]


class EnvContainer(Protocol):
    def get(self, key: str) -> Optional[str]: ...
    def __contains__(self, item: str) -> bool: ...


class EnvironSource(ABC):
    def __init__(self):
        self._env: Optional[Dict[str, str]] = None

    async def get_environ(self) -> Dict[str, str]:
        raise NotImplementedError

    async def diff(self, keys: Iterable[str]) -> Dict[str, Tuple[str, str]]:
        if self._env is None:
            self._env = await self.get_environ()
            return {}

        curr = await self.get_environ()
        diff = {}
        get_old = self._env.get
        get_new = curr.get

        for key in keys:
            if (old := get_old(key)) != (new := get_new(key)):
                diff[key] = (old, new)

        if diff:
            self._env = curr
        return diff

    def _ensure_env(self) -> Dict[str, str]:
        if self._env is None:
            self._env = run_sync(self.get_environ())
        return self._env

    def get(self, key: str) -> str:
        return self._ensure_env()[key]

    def __contains__(self, item):
        return item in self._ensure_env()


class EnvFile(EnvironSource):
    def __init__(self, path: str):
        super().__init__()
        self._path = path

    @staticmethod
    def convert(content: List[str]):
        config_map = {}
        for line in content:
            if line.startswith('#'):
                continue

            if match := _RE_ENV.match(line):
                key, value = match.groups()
                config_map[key] = value

        return config_map

    async def get_environ(self) -> Dict[str, str]:
        with open(self._path, 'r') as fp:
            content = fp.readlines()

        return self.convert(content)


class EnvironMonitor:
    def __init__(
        self,
        sources: List[EnvironSource],
        callbacks: Dict[str, OnChangeT],
    ):
        self._sources = sources
        self._callbacks = callbacks

    @periodic_task(interval=5)
    async def start(self):
        for source in self._sources:
            diff = await source.diff(self._callbacks.keys())
            if not diff:
                continue

            for env, (old, new) in diff.items():
                await self._callbacks[env](new)
