from __future__ import annotations

import asyncio
import io
import json
import struct
from typing import Any

MAX_FRAME_BYTES = 4 * 1024 * 1024
_HEADER_SIZE = 4


class FramingError(Exception):
    """Raised when a sandbox frame cannot be encoded or decoded."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise FramingError(f"duplicate JSON key: {key!r}")
        seen.add(key)
        result[key] = value
    return result


def _decode_payload(payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FramingError("invalid UTF-8 in frame body") from exc

    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise FramingError("invalid JSON in frame body") from exc
    except FramingError:
        raise
    except Exception as exc:
        raise FramingError("invalid JSON in frame body") from exc

    if not isinstance(value, dict):
        raise FramingError("frame body must be a JSON object")
    return value


def _read_exact(stream: io.BufferedIOBase, size: int) -> bytes:
    data = stream.read(size)
    if data == b"" and size > 0:
        raise FramingError("unexpected end of stream while reading frame")
    if len(data) < size:
        if size == _HEADER_SIZE:
            raise FramingError("truncated frame header")
        raise FramingError("truncated frame body")
    return data


def _parse_length(header: bytes) -> int:
    (length,) = struct.unpack(">I", header)
    if length == 0:
        raise FramingError("empty frame body")
    if length > MAX_FRAME_BYTES:
        raise FramingError(f"frame length {length} exceeds maximum {MAX_FRAME_BYTES}")
    return length


def _ensure_no_trailing_bytes(stream: io.BufferedIOBase) -> None:
    extra = stream.read(1)
    if extra:
        raise FramingError("unexpected trailing bytes after frame")


def encode_frame(obj: dict[str, Any]) -> bytes:
    if not isinstance(obj, dict):
        raise FramingError("frame body must be a JSON object")

    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise FramingError(f"frame body exceeds {MAX_FRAME_BYTES} bytes")
    return struct.pack(">I", len(payload)) + payload


def read_frame(stream: io.BufferedIOBase) -> dict[str, Any]:
    header = _read_exact(stream, _HEADER_SIZE)
    length = _parse_length(header)
    payload = _read_exact(stream, length)
    obj = _decode_payload(payload)
    _ensure_no_trailing_bytes(stream)
    return obj


def write_frame(stream: io.BufferedIOBase, obj: dict[str, Any]) -> None:
    stream.write(encode_frame(obj))
    stream.flush()


async def read_frame_async(reader: asyncio.StreamReader) -> dict[str, Any]:
    try:
        header = await reader.readexactly(_HEADER_SIZE)
    except asyncio.IncompleteReadError as exc:
        if exc.partial == b"":
            raise FramingError("unexpected end of stream while reading frame") from exc
        raise FramingError("truncated frame header") from exc

    length = _parse_length(header)
    try:
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise FramingError("truncated frame body") from exc

    obj = _decode_payload(payload)
    if not reader.at_eof():
        extra = await reader.read(1)
        if extra:
            raise FramingError("unexpected trailing bytes after frame")
    return obj


async def write_frame_async(writer: asyncio.StreamWriter, obj: dict[str, Any]) -> None:
    writer.write(encode_frame(obj))
    await writer.drain()
