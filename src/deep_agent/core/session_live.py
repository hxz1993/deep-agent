"""Bounded, process-local fan-out of the existing chat SSE, never a fact store.

Only the API producer owns execution. Observers have independent queues and may
leave at any time. All methods run on the API event loop, with no await between
subscription and replay capture, so joining cannot miss the next frame.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

SessionKey = tuple[str | None, str, str | None]


@dataclass(frozen=True)
class LiveFrame:
    event: str
    data: str
    size: int

    @classmethod
    def make(cls, event: str, data: dict[str, Any]) -> LiveFrame:
        serialized = json.dumps(data, ensure_ascii=False)
        return cls(event, serialized, len(serialized.encode("utf-8")))

    def sse(self) -> dict[str, str]:
        return {"event": self.event, "data": self.data}


@dataclass(eq=False)
class LiveSubscription:
    key: SessionKey
    initial: tuple[LiveFrame, ...]
    queue: asyncio.Queue[LiveFrame] = field(default_factory=asyncio.Queue)
    queued_bytes: int = 0
    closed: bool = False

    async def get(self) -> LiveFrame:
        frame = await self.queue.get()
        self.queued_bytes -= frame.size
        return frame


@dataclass
class _LiveRun:
    run_id: str
    baseline_sequence: int = 0
    frames: list[LiveFrame] = field(default_factory=list)
    size: int = 0
    replay_available: bool = True


class SessionLiveHub:
    def __init__(self, *, max_replay_bytes: int = 4 * 1024 * 1024,
                 max_replay_frames: int = 4096, max_queue_bytes: int = 1024 * 1024,
                 max_queue_frames: int = 512, max_total_replay_bytes: int = 32 * 1024 * 1024,
                 max_subscribers: int = 128) -> None:
        self.max_replay_bytes = max_replay_bytes
        self.max_replay_frames = max_replay_frames
        self.max_queue_bytes = max_queue_bytes
        self.max_queue_frames = max_queue_frames
        self.max_total_replay_bytes = max_total_replay_bytes
        self.max_subscribers = max_subscribers
        self._runs: dict[SessionKey, _LiveRun] = {}
        self._subscribers: dict[SessionKey, set[LiveSubscription]] = {}
        self._replay_bytes = 0
        self._closed = False

    def begin(self, key: SessionKey, run_id: str, *, baseline_sequence: int = 0) -> None:
        if self._closed:
            return
        old = self._runs.get(key)
        if old is not None:
            self.end(key, old.run_id)
        self._runs[key] = _LiveRun(run_id, baseline_sequence=baseline_sequence)

    def publish(self, key: SessionKey, run_id: str, event: str, data: dict[str, Any]) -> None:
        run = self._runs.get(key)
        if self._closed or run is None or run.run_id != run_id:
            return
        frame = LiveFrame.make("observation", {"run_id": run_id, "event": event, "data": data})
        if run.replay_available:
            if (run.size + frame.size > self.max_replay_bytes
                    or len(run.frames) >= self.max_replay_frames
                    or self._replay_bytes + frame.size > self.max_total_replay_bytes):
                self._replay_bytes -= run.size
                run.frames.clear()
                run.size = 0
                run.replay_available = False
            else:
                run.frames.append(frame)
                run.size += frame.size
                self._replay_bytes += frame.size
        self._broadcast(key, frame)

    def end(self, key: SessionKey, run_id: str) -> None:
        run = self._runs.get(key)
        if run is None or run.run_id != run_id:
            return
        self._runs.pop(key)
        self._replay_bytes -= run.size
        self._broadcast(key, LiveFrame.make("observation_end", {"run_id": run_id}))
        # A subscription belongs to this Run only, even if the next request
        # begins before its client consumes the terminal queue entry.
        self._subscribers.pop(key, None)

    def subscribe(self, key: SessionKey, run_id: str) -> LiveSubscription:
        run = self._runs.get(key)
        if run is None or run.run_id != run_id:
            raise LookupError("Requested execution is no longer active")
        if not run.replay_available:
            raise BufferError("Live replay unavailable; read persisted history")
        if self._closed or sum(map(len, self._subscribers.values())) >= self.max_subscribers:
            raise RuntimeError("Live observation capacity unavailable")
        initial = [LiveFrame.make("observation_sync", {
            "session_id": key[1], "agent_id": key[0],
            "run_id": run.run_id,
            "replay_available": True,
            "baseline_sequence": run.baseline_sequence,
        })]
        initial.extend(run.frames)
        subscription = LiveSubscription(key=key, initial=tuple(initial))
        self._subscribers.setdefault(key, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription: LiveSubscription) -> None:
        subscribers = self._subscribers.get(subscription.key)
        if subscribers is not None:
            subscribers.discard(subscription)
        subscription.closed = True
        subscription.initial = ()
        while not subscription.queue.empty():
            subscription.queue.get_nowait()
        subscription.queued_bytes = 0
        self._prune(subscription.key)

    def _prune(self, key: SessionKey) -> None:
        if not self._subscribers.get(key):
            self._subscribers.pop(key, None)

    def _broadcast(self, key: SessionKey, frame: LiveFrame) -> None:
        for subscription in tuple(self._subscribers.get(key, ())):
            if subscription.closed:
                continue
            if (subscription.queue.qsize() >= self.max_queue_frames
                    or subscription.queued_bytes + frame.size > self.max_queue_bytes):
                self._reset(subscription, "slow_consumer")
            else:
                subscription.queue.put_nowait(frame)
                subscription.queued_bytes += frame.size

    def _reset(self, subscription: LiveSubscription, reason: str) -> None:
        self.unsubscribe(subscription)
        frame = LiveFrame.make("observation_reset", {"reason": reason})
        subscription.queue.put_nowait(frame)
        subscription.queued_bytes = frame.size

    def close(self) -> None:
        self._closed = True
        for subscriptions in tuple(self._subscribers.values()):
            for subscription in tuple(subscriptions):
                self._reset(subscription, "server_shutdown")
        self._runs.clear()
        self._replay_bytes = 0
