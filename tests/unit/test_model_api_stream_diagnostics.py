"""Real HTTPX/OpenAI consumption and close semantics, with passive logging."""
import asyncio
import json
import unittest
from unittest.mock import patch

import httpx2
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from deep_agent.agent.model_api_diagnostics import observe_model_api
from deep_agent.diagnostics.schema import safe_fields


class Stream(httpx2.AsyncByteStream):
    def __init__(self, chunks=(), error=None, blocked=False):
        self.chunks, self.error, self.blocked = chunks, error, blocked
        self.reads = self.closes = 0
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    async def __aiter__(self):
        self.reads += 1
        for chunk in self.chunks:
            yield chunk
        if self.blocked:
            self.waiting.set()
            await self.release.wait()
        if self.error:
            raise self.error

    async def aclose(self):
        self.closes += 1


class ModelApiStreamDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def open(self, stream, rows, broken=False):
        self.client = httpx2.AsyncClient(transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, stream=stream, headers={"content-type": "text/event-stream"})))
        self.addAsyncCleanup(self.client.aclose)
        self.model = OpenAIChatModel("fixture", provider=OpenAIProvider(api_key="private", http_client=self.client))
        logging = patch("deep_agent.agent.model_api_diagnostics.log_event",
                        side_effect=RuntimeError("private sink") if broken else lambda event, **fields: rows.append((event, fields)))
        logging.start()
        self.addCleanup(logging.stop)
        with observe_model_api(self.model, {"run_id": "run"}, {}):
            return await self.client.send(self.client.build_request("POST", "https://example.test/responses", json={}), stream=True)

    async def test_unconsumed_no_read_and_close_cancels_timer(self):
        rows, stream = [], Stream([b"private"])
        with patch("deep_agent.agent.model_api_diagnostics.STREAM_FIRST_REPORT_SECONDS", .001), patch(
                "deep_agent.agent.model_api_diagnostics.STREAM_REPORT_SECONDS", .001):
            response = await self.open(stream, rows)
            await asyncio.sleep(.005)
            self.assertEqual(stream.reads, 0)
            progress = [r for e, r in rows if e == "model_api_stream_progress"]
            self.assertTrue(progress)
            self.assertEqual(progress[0]["stream_state"], "unconsumed")
            self.assertNotIn("first_byte_ms", progress[0])
            await response.aclose()
            size = len(rows)
            await asyncio.sleep(.005)
            self.assertEqual(len(rows), size)
        self.assertEqual(stream.closes, 1)
        self.assertEqual(rows[-1][1]["stream_state"], "closed_unconsumed")

    async def test_counts_exact_bytes_empty_chunks_and_eof(self):
        chunks = [b"", b"data: private\n", "中文".encode(), b"\n"]
        stream, rows = Stream(chunks), []
        response = await self.open(stream, rows)
        self.assertEqual(await response.aread(), b"".join(chunks))
        final = rows[-1][1]
        self.assertEqual(final["stream_state"], "eof")
        self.assertEqual(final["stream_bytes"], sum(map(len, chunks)))
        self.assertEqual(final["stream_chunks"], len(chunks))
        self.assertGreaterEqual(final["last_byte_age_ms"], 0)
        self.assertEqual(stream.closes, 1)
        self.assertEqual(sum(e == "model_api_stream_first_byte" for e, _ in rows), 1)
        self.assertNotIn("private", repr(rows))
        self.assertNotIn("中文", repr(rows))

    async def test_cancel_and_transport_error_propagate_and_close_once(self):
        for cancel in (True, False):
            with self.subTest(cancel=cancel):
                error = httpx2.ReadError("private transport failure")
                stream, rows = Stream(blocked=cancel, error=None if cancel else error), []
                response = await self.open(stream, rows)
                task = asyncio.create_task(response.aread())
                if cancel:
                    await stream.waiting.wait()
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    with self.assertRaises(httpx2.ReadError) as raised:
                        await task
                    self.assertIs(raised.exception, error)
                await response.aclose()
                self.assertEqual(rows[-1][1]["stream_state"], "cancelled" if cancel else "failed")
                self.assertEqual(stream.closes, 1)
                self.assertNotIn("private transport", repr(rows))

    async def test_absolute_reporting_continues_during_heartbeat_and_consumer_pause(self):
        class Heartbeats(Stream):
            async def __aiter__(self):
                for _ in range(12):
                    await asyncio.sleep(.002)
                    yield b": ping\n\n"
        rows, stream = [], Heartbeats()
        with patch("deep_agent.agent.model_api_diagnostics.STREAM_FIRST_REPORT_SECONDS", .006), patch(
                "deep_agent.agent.model_api_diagnostics.STREAM_REPORT_SECONDS", .006):
            response = await self.open(stream, rows)
            iterator = response.aiter_raw()
            await anext(iterator)
            # Counters also distinguish a downstream consumer pause from read.
            await asyncio.sleep(.009)
            self.assertTrue(any(r["stream_state"] == "consumer_idle" for e, r in rows if e == "model_api_stream_progress"))
            async for _ in iterator:
                pass
        self.assertGreaterEqual(sum(e == "model_api_stream_progress" for e, _ in rows), 3)
        self.assertTrue(any(r["stream_state"] == "reading" for e, r in rows if e == "model_api_stream_progress"))

    async def test_real_sdk_output_close_and_wire_equivalent_with_failed_sink(self):
        wire, outputs = [], []
        body = b'data: {"id":"fixture","choices":[{"index":0,"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
        for mode in ("off", "on", "broken"):
            stream, rows = Stream([body[:17], body[17:]]), []
            async def transport(request):
                wire.append(request.content)
                return httpx2.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
                model = OpenAIChatModel("fixture", provider=OpenAIProvider(api_key="private", http_client=client))
                from contextlib import nullcontext
                with patch("deep_agent.agent.model_api_diagnostics.log_event", side_effect=RuntimeError("sink") if mode == "broken"
                           else lambda e, **f: rows.append((e, f))):
                    with observe_model_api(model, {}, {}) if mode != "off" else nullcontext():
                        result = await model.client.chat.completions.create(model="fixture", messages=[{"role": "user", "content": "private"}], stream=True)
                        outputs.append([item.choices[0].delta.content async for item in result])
                    await result.close()
                self.assertEqual(stream.closes, 1)
        self.assertEqual(outputs, [["ok"]] * 3)
        self.assertEqual(wire, [wire[0]] * 3)

    async def test_close_error_identity_and_sink_failure(self):
        error = RuntimeError("private close")
        class BadClose(Stream):
            async def aclose(self):
                self.closes += 1
                raise error
        for broken in (False, True):
            rows, stream = [], BadClose()
            response = await self.open(stream, rows, broken=broken)
            with self.assertRaises(RuntimeError) as raised:
                await response.aclose()
            self.assertIs(raised.exception, error)
            self.assertEqual(stream.closes, 1)
            self.assertIsNone(response.stream._timer)
            if not broken:
                self.assertEqual(rows[-1][1]["stream_state"], "close_failed")

    async def test_real_sdk_cancel_and_parse_failure_equivalence(self):
        from contextlib import nullcontext
        for scenario in ("cancel", "parse", "read"):
            outcomes = []
            for enabled in (False, True):
                rows = []
                error = httpx2.ReadError("private read")
                stream = Stream([b"data: not-json\n\n"] if scenario == "parse" else [],
                                blocked=scenario == "cancel", error=error if scenario == "read" else None)
                async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(
                        200, stream=stream, headers={"content-type": "text/event-stream"}))) as client:
                    model = OpenAIChatModel("fixture", provider=OpenAIProvider(api_key="private", http_client=client))
                    with patch("deep_agent.agent.model_api_diagnostics.log_event", side_effect=lambda e, **f: rows.append((e, f))):
                        with observe_model_api(model, {}, {}) if enabled else nullcontext():
                            result = await model.client.chat.completions.create(model="fixture", messages=[], stream=True)
                        async def consume():
                            return [item async for item in result]
                        task = asyncio.create_task(consume())
                        if scenario == "cancel":
                            await stream.waiting.wait()
                            task.cancel()
                        try:
                            await task
                            self.fail("expected unchanged SDK failure")
                        except (asyncio.CancelledError, json.JSONDecodeError, httpx2.ReadError) as exc:
                            outcomes.append(type(exc))
                            if scenario == "read":
                                self.assertIs(exc, error)
                        await result.close()
                    self.assertEqual(stream.closes, 1)
                    if enabled:
                        self.assertIsNone(result.response.stream._timer)
                        terminal = next(f for e, f in rows if e == "model_api_stream_finished")
                        self.assertEqual(terminal["stream_state"], {"cancel": "cancelled", "read": "failed", "parse": "closed"}[scenario])
                        self.assertNotIn("not-json", repr(rows))
                self.assertEqual(outcomes[0], outcomes[-1])

    async def test_broken_diagnostic_clock_preserves_body_and_close(self):
        from types import SimpleNamespace
        rows, stream = [], Stream([b"original", b" body"])
        response = await self.open(stream, rows)
        def broken_clock():
            raise RuntimeError("diagnostic clock failed")
        with patch("deep_agent.agent.model_api_diagnostics.time", SimpleNamespace(monotonic=broken_clock)):
            self.assertEqual(await response.aread(), b"original body")
        self.assertEqual(stream.closes, 1)
        self.assertIsNone(response.stream._timer)

    async def test_real_responses_sdk_stream_equivalence(self):
        from contextlib import nullcontext
        wire, outputs = [], []
        body = (b'event: response.created\ndata: {"type":"response.created","response":{"id":"fixture"}}\n\n'
                b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"fixture"}}\n\n')
        for mode in ("off", "on", "broken"):
            stream, rows = Stream([body[:23], body[23:]]), []
            async def transport(request):
                wire.append(request.content)
                return httpx2.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
                model = OpenAIChatModel("fixture", provider=OpenAIProvider(api_key="private", http_client=client))
                with patch("deep_agent.agent.model_api_diagnostics.log_event", side_effect=RuntimeError("sink") if mode == "broken"
                           else lambda e, **f: rows.append((e, f))):
                    with observe_model_api(model, {}, {}) if mode != "off" else nullcontext():
                        result = await model.client.responses.create(model="fixture", input="private", stream=True)
                        outputs.append([item.type async for item in result])
                    await result.close()
                self.assertEqual(stream.closes, 1)
        self.assertEqual(outputs, [["response.created", "response.completed"]] * 3)
        self.assertEqual(wire, [wire[0]] * 3)

    def test_stream_fields_are_closed_allowlist(self):
        self.assertEqual(safe_fields(dict(stream_state="private", stream_bytes=-1, first_byte_ms=float("nan"),
                                          stream_chunks="private", last_byte_age_ms=True, body="private")), {})
        fields = dict(stream_state="reading", stream_bytes=32, stream_chunks=2, first_byte_ms=200, last_byte_age_ms=30)
        self.assertEqual(safe_fields(fields), fields)
