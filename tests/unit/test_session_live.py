"""Process-local observation never owns or restarts an execution."""
from __future__ import annotations

import asyncio
import json
import unittest

from deep_agent.core.session_live import LiveFrame, SessionLiveHub


KEY = ("agent", "session", "owner")


class SessionLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_observers_receive_identical_ordered_replay_and_future_frames(self):
        hub = SessionLiveHub()
        self.addCleanup(hub.close)
        hub.begin(KEY, "run")
        hub.publish(KEY, "run", "text", {"delta": "first"})
        first, second = hub.subscribe(KEY, "run"), hub.subscribe(KEY, "run")
        self.assertEqual(first.initial, second.initial)
        self.assertEqual([frame.event for frame in first.initial], ["observation_sync", "observation"])
        self.assertEqual(json.loads(first.initial[0].data)["run_id"], "run")
        hub.publish(KEY, "run", "tool_call", {"tool_call_id": "call"})
        hub.publish(KEY, "run", "final", {"output": "done"})
        hub.end(KEY, "run")
        left = [await first.get() for _ in range(3)]
        right = [await second.get() for _ in range(3)]
        self.assertEqual(left, right)
        self.assertEqual([frame.event for frame in left], ["observation", "observation", "observation_end"])
        self.assertEqual([json.loads(frame.data)["event"] for frame in left[:2]], ["tool_call", "final"])
        self.assertEqual(hub._replay_bytes, 0)

    async def test_idle_and_wrong_run_or_identity_cannot_subscribe(self):
        hub = SessionLiveHub()
        self.addCleanup(hub.close)
        with self.assertRaises(LookupError):
            hub.subscribe(KEY, "run")
        hub.begin(KEY, "run")
        for key, run in [(KEY, "other-run"), (("agent", "session", "intruder"), "run"),
                         (("other-agent", "session", "owner"), "run")]:
            with self.assertRaises(LookupError):
                hub.subscribe(key, run)

    async def test_ended_subscription_never_receives_next_run(self):
        hub = SessionLiveHub()
        self.addCleanup(hub.close)
        hub.begin(KEY, "old")
        old = hub.subscribe(KEY, "old")
        hub.end(KEY, "old")
        hub.begin(KEY, "new")
        new = hub.subscribe(KEY, "new")
        hub.publish(KEY, "new", "text", {"delta": "new"})
        self.assertEqual((await old.get()).event, "observation_end")
        self.assertTrue(old.queue.empty())
        self.assertEqual(json.loads((await new.get()).data)["run_id"], "new")
        # Late cleanup from an old HTTP request must not remove the new observer.
        hub.unsubscribe(old)
        hub.publish(KEY, "new", "text", {"delta": "still-active"})
        self.assertEqual(json.loads((await new.get()).data)["data"]["delta"], "still-active")

    async def test_disconnected_observer_does_not_end_run_or_other_observer(self):
        hub = SessionLiveHub()
        self.addCleanup(hub.close)
        hub.begin(KEY, "run")
        gone, alive = hub.subscribe(KEY, "run"), hub.subscribe(KEY, "run")
        hub.unsubscribe(gone)
        hub.publish(KEY, "run", "text", {"delta": "after-disconnect"})
        self.assertTrue(gone.closed)
        self.assertTrue(gone.queue.empty())
        self.assertEqual(json.loads((await alive.get()).data)["data"]["delta"], "after-disconnect")
        self.assertEqual(len(hub.subscribe(KEY, "run").initial), 2)

    async def test_replay_frame_limit_rejects_new_observer_but_existing_keeps_streaming(self):
        hub = SessionLiveHub(max_replay_frames=1)
        self.addCleanup(hub.close)
        hub.begin(KEY, "run")
        existing = hub.subscribe(KEY, "run")
        hub.publish(KEY, "run", "text", {"delta": "one"})
        hub.publish(KEY, "run", "text", {"delta": "two"})
        with self.assertRaises(BufferError):
            hub.subscribe(KEY, "run")
        self.assertEqual(hub._replay_bytes, 0)
        self.assertEqual(hub._runs[KEY].frames, [])
        self.assertEqual([json.loads((await existing.get()).data)["data"]["delta"] for _ in range(2)], ["one", "two"])

    async def test_replay_byte_limit_counts_utf8_not_characters(self):
        payload = {"run_id": "run", "event": "text", "data": {"delta": "中文"}}
        size = LiveFrame.make("observation", payload).size
        hub = SessionLiveHub(max_replay_bytes=size - 1)
        self.addCleanup(hub.close)
        hub.begin(KEY, "run")
        hub.publish(KEY, "run", "text", payload["data"])
        with self.assertRaises(BufferError):
            hub.subscribe(KEY, "run")
        self.assertEqual(hub._replay_bytes, 0)

    async def test_global_replay_budget_never_exceeded_and_released_at_end(self):
        size = LiveFrame.make("observation", {"run_id": "run", "event": "text", "data": {"delta": "x"}}).size
        hub = SessionLiveHub(max_total_replay_bytes=size)
        self.addCleanup(hub.close)
        other = ("agent", "other-session", "owner")
        hub.begin(KEY, "run")
        hub.begin(other, "run")
        hub.publish(KEY, "run", "text", {"delta": "x"})
        hub.publish(other, "run", "text", {"delta": "x"})
        self.assertEqual(hub._replay_bytes, size)
        with self.assertRaises(BufferError):
            hub.subscribe(other, "run")
        hub.end(KEY, "run")
        self.assertEqual(hub._replay_bytes, 0)

    async def test_slow_queue_frame_limit_resets_only_slow_observer(self):
        hub = SessionLiveHub(max_queue_frames=1)
        self.addCleanup(hub.close)
        hub.begin(KEY, "run")
        slow, fast = hub.subscribe(KEY, "run"), hub.subscribe(KEY, "run")
        hub.publish(KEY, "run", "text", {"delta": "one"})
        await fast.get()
        hub.publish(KEY, "run", "text", {"delta": "two"})
        reset = await slow.get()
        self.assertEqual((reset.event, json.loads(reset.data)["reason"]), ("observation_reset", "slow_consumer"))
        self.assertTrue(slow.closed)
        self.assertEqual(slow.queued_bytes, 0)
        self.assertEqual((await fast.get()).event, "observation")
        self.assertFalse(fast.closed)

    async def test_slow_queue_byte_limit_resets_without_retaining_huge_frame(self):
        hub = SessionLiveHub(max_queue_bytes=64)
        self.addCleanup(hub.close)
        hub.begin(KEY, "run")
        sub = hub.subscribe(KEY, "run")
        hub.publish(KEY, "run", "text", {"delta": "x" * 1000})
        self.assertTrue(sub.closed)
        self.assertEqual(sub.queue.qsize(), 1)
        self.assertEqual((await sub.get()).event, "observation_reset")

    async def test_capacity_recovers_after_disconnect(self):
        hub = SessionLiveHub(max_subscribers=1)
        self.addCleanup(hub.close)
        hub.begin(KEY, "run")
        sub = hub.subscribe(KEY, "run")
        with self.assertRaises(RuntimeError):
            hub.subscribe(KEY, "run")
        hub.unsubscribe(sub)
        self.assertFalse(hub.subscribe(KEY, "run").closed)

    async def test_close_wakes_waiting_observer_and_releases_replay(self):
        hub = SessionLiveHub()
        hub.begin(KEY, "run")
        hub.publish(KEY, "run", "text", {"delta": "before-close"})
        sub = hub.subscribe(KEY, "run")
        waiting = asyncio.create_task(sub.get())
        hub.close()
        frame = await asyncio.wait_for(waiting, 1)
        self.assertEqual((frame.event, json.loads(frame.data)["reason"]), ("observation_reset", "server_shutdown"))
        self.assertEqual(hub._runs, {})
        self.assertEqual(hub._subscribers, {})
        self.assertEqual(hub._replay_bytes, 0)
        hub.begin(KEY, "later")
        self.assertEqual(hub._runs, {})

    async def test_stale_publish_end_and_replacement_cannot_corrupt_current_run(self):
        hub = SessionLiveHub()
        self.addCleanup(hub.close)
        hub.begin(KEY, "old")
        old = hub.subscribe(KEY, "old")
        hub.begin(KEY, "new")
        self.assertEqual((await old.get()).event, "observation_end")
        hub.publish(KEY, "old", "text", {"delta": "late"})
        hub.end(KEY, "old")
        fresh = hub.subscribe(KEY, "new")
        self.assertEqual(len(fresh.initial), 1)
        self.assertEqual(json.loads(fresh.initial[0].data)["run_id"], "new")

    async def test_frame_snapshots_payload_without_mutable_aliases(self):
        hub = SessionLiveHub()
        self.addCleanup(hub.close)
        hub.begin(KEY, "run")
        data = {"text": ["original"]}
        hub.publish(KEY, "run", "text", data)
        data["text"].append("mutated")
        sub = hub.subscribe(KEY, "run")
        self.assertEqual(json.loads(sub.initial[1].data)["data"], {"text": ["original"]})
