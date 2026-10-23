from __future__ import annotations

import asyncio
import io
import json
import struct
import unittest

from deep_agent.sandbox.framing import (
    MAX_FRAME_BYTES,
    FramingError,
    encode_frame,
    read_frame,
    read_frame_async,
    write_frame,
    write_frame_async,
)


class _MockStreamWriter:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        return None


class SandboxFramingTests(unittest.TestCase):
    def test_json_larger_than_four_mib_round_trips_without_truncation(self) -> None:
        payload = {"inputs": {"payload": "x" * (5 * 1024 * 1024) + "完整末尾"}}
        frame = encode_frame(payload)
        self.assertGreater(len(frame), 4 * 1024 * 1024)
        self.assertEqual(read_frame(io.BytesIO(frame)), payload)

    def test_encode_decode_round_trip(self) -> None:
        payload = {"request_id": "call-1", "code": "print(1)", "inputs": {"x": 1}}
        frame = encode_frame(payload)
        stream = io.BytesIO(frame)
        self.assertEqual(read_frame(stream), payload)

    def test_write_frame_writes_length_prefixed_json(self) -> None:
        payload = {"status": "ok"}
        buffer = io.BytesIO()
        write_frame(buffer, payload)
        raw = buffer.getvalue()
        length = struct.unpack(">I", raw[:4])[0]
        self.assertEqual(length, len(raw[4:]))
        self.assertEqual(json.loads(raw[4:].decode("utf-8")), payload)

    def test_rejects_empty_stream(self) -> None:
        with self.assertRaises(FramingError):
            read_frame(io.BytesIO(b""))

    def test_rejects_zero_length_frame_body(self) -> None:
        with self.assertRaises(FramingError):
            read_frame(io.BytesIO(struct.pack(">I", 0)))

    def test_rejects_declared_length_over_max(self) -> None:
        over = struct.pack(">I", MAX_FRAME_BYTES + 1) + b"{}"
        with self.assertRaises(FramingError):
            read_frame(io.BytesIO(over))

    def test_rejects_encoding_oversized_payload(self) -> None:
        oversized = {"payload": "x" * MAX_FRAME_BYTES}
        with self.assertRaises(FramingError):
            encode_frame(oversized)

    def test_rejects_truncated_header(self) -> None:
        with self.assertRaises(FramingError):
            read_frame(io.BytesIO(b"\x00\x00"))

    def test_rejects_truncated_body(self) -> None:
        truncated = struct.pack(">I", 10) + b'{"a":1'
        with self.assertRaises(FramingError):
            read_frame(io.BytesIO(truncated))

    def test_rejects_non_utf8_body(self) -> None:
        body = b"\xff\xfe\xfd"
        frame = struct.pack(">I", len(body)) + body
        with self.assertRaises(FramingError):
            read_frame(io.BytesIO(frame))

    def test_rejects_non_object_json_array(self) -> None:
        body = b"[]"
        frame = struct.pack(">I", len(body)) + body
        with self.assertRaises(FramingError):
            read_frame(io.BytesIO(frame))

    def test_rejects_non_object_json_string(self) -> None:
        body = b'"hello"'
        frame = struct.pack(">I", len(body)) + body
        with self.assertRaises(FramingError):
            read_frame(io.BytesIO(frame))

    def test_rejects_duplicate_json_keys(self) -> None:
        body = b'{"a":1,"a":2}'
        frame = struct.pack(">I", len(body)) + body
        with self.assertRaises(FramingError):
            read_frame(io.BytesIO(frame))

    def test_rejects_trailing_bytes_after_frame(self) -> None:
        frame = struct.pack(">I", 2) + b"{}" + b"extra"
        with self.assertRaises(FramingError):
            read_frame(io.BytesIO(frame))

    def test_encode_rejects_non_dict(self) -> None:
        with self.assertRaises(FramingError):
            encode_frame([])  # type: ignore[arg-type]


class SandboxFramingAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_json_async_writer_and_reader_preserve_unicode_tail(self) -> None:
        payload = {"inputs": {"payload": "x" * (5 * 1024 * 1024) + "完整末尾"}}
        writer = _MockStreamWriter()
        await write_frame_async(writer, payload)
        reader = asyncio.StreamReader()
        reader.feed_data(bytes(writer.data))
        reader.feed_eof()
        self.assertEqual(await read_frame_async(reader), payload)

    async def test_async_reader_rejects_declared_frame_above_sixty_four_mib(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack(">I", 64 * 1024 * 1024 + 1))
        # No body and no EOF: it must reject the length before waiting for bytes.
        with self.assertRaisesRegex(FramingError, "exceeds maximum"):
            await asyncio.wait_for(read_frame_async(reader), timeout=1)

    async def test_read_frame_async_round_trip(self) -> None:
        payload = {"request_id": "async-1", "code": "1 + 1"}
        reader = asyncio.StreamReader()
        reader.feed_data(encode_frame(payload))
        reader.feed_eof()
        self.assertEqual(await read_frame_async(reader), payload)

    async def test_write_frame_async_writes_frame(self) -> None:
        payload = {"status": "ok"}
        writer = _MockStreamWriter()
        await write_frame_async(writer, payload)
        stream = io.BytesIO(bytes(writer.data))
        self.assertEqual(read_frame(stream), payload)

    async def test_read_frame_async_rejects_empty_stream(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_eof()
        with self.assertRaises(FramingError):
            await read_frame_async(reader)

    async def test_read_frame_async_rejects_trailing_bytes(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack(">I", 2) + b"{}" + b"tail")
        reader.feed_eof()
        with self.assertRaises(FramingError):
            await read_frame_async(reader)


if __name__ == "__main__":
    unittest.main()
