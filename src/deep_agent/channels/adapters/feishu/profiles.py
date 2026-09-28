"""Account-local, bounded profile cache shared by inbound SDK and diagnostics.

Only the worker's owning event loop uses this object. The adapter bridges SDK
callbacks onto that loop; no private SDK cache or process-global data is used.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable


class FeishuProfiles:
    capacity = 2000
    batch_size = 50
    negative_seconds = 15.0

    def __init__(self, lookup: Callable[[list[str]], Awaitable[dict[str, str]]]):
        self.lookup = lookup
        self._names: OrderedDict[str, str] = OrderedDict()
        self._negative: OrderedDict[str, float] = OrderedDict()
        self._lock = asyncio.Lock()

    def cached(self, ids: list[str]) -> dict[str, str]:
        result = {}
        for uid in ids:
            if uid in self._names:
                result[uid] = self._names[uid]
                self._names.move_to_end(uid)
        return result

    async def resolve(self, ids: list[str]) -> dict[str, str]:
        ids = list(dict.fromkeys(ids))
        result = self.cached(ids)
        if len(result) == len(ids):
            return result
        # Serialize upstream batches, rechecking after the lock. This coalesces
        # overlapping requests and bounds concurrent provider calls to one.
        async with self._lock:
            result.update(self.cached(ids))
            now = time.monotonic()
            missing = [uid for uid in ids if uid not in result
                       and self._negative.get(uid, 0) <= now]
            for offset in range(0, len(missing), self.batch_size):
                batch = missing[offset:offset + self.batch_size]
                try:
                    async with asyncio.timeout(3):
                        names = await self.lookup(batch)
                except Exception:
                    names = {}
                for uid in batch:
                    name = names.get(uid)
                    if isinstance(name, str) and name.strip():
                        name = name.strip()
                        self._names[uid] = result[uid] = name
                        self._names.move_to_end(uid)
                        self._negative.pop(uid, None)
                    else:
                        self._negative[uid] = time.monotonic() + self.negative_seconds
                        self._negative.move_to_end(uid)
                while len(self._names) > self.capacity:
                    self._names.popitem(last=False)
                while len(self._negative) > self.capacity:
                    self._negative.popitem(last=False)
        return result

    def clear(self):
        self._names.clear()
        self._negative.clear()
