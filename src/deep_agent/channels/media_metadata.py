"""On-demand local media inspection for channel adapters, without transcoding."""
from __future__ import annotations

import asyncio
import json
import math
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .outbound import DeliveryError

_slots = asyncio.Semaphore(2)


@dataclass(frozen=True)
class MediaMetadata:
    duration_ms: int
    width: int | None = None
    height: int | None = None
    cover: bytes | None = None


async def _run(*args: str) -> bytes:
    process = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(20):
            output, _ = await process.communicate()
        if process.returncode:
            raise DeliveryError("media_metadata_failed", "无法读取音视频信息，尚未发送")
        return output
    except TimeoutError:
        raise DeliveryError("media_metadata_timeout", "读取音视频信息超时，尚未发送") from None
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def inspect_media(data: bytes, *, video: bool) -> MediaMetadata:
    async with _slots:
        return await _inspect_media(data, video=video)


async def _inspect_media(data: bytes, *, video: bool) -> MediaMetadata:
    """Read real duration and optionally a bounded JPEG of the first video frame."""
    probe = shutil.which("ffprobe")
    encoder = shutil.which("ffmpeg") if video else None
    if not probe or (video and not encoder):
        raise DeliveryError("media_dependency_missing", "渠道网关未安装 FFmpeg/ffprobe，无法补齐媒体信息")
    with tempfile.TemporaryDirectory(prefix="TMP_to_delete_channel_media_") as directory:
        source = Path(directory) / "source.media"
        await asyncio.to_thread(source.write_bytes, data)
        # No playlists or network protocols: inputs are already authorized file bytes.
        input_args = ("-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,ogg,amr,mp3,wav")
        raw = await _run(probe, "-v", "error", "-threads", "1", *input_args,
                         "-show_entries", "format=duration:stream=codec_type,duration,width,height",
                         "-of", "json", str(source))
        try:
            info = json.loads(raw)
            if not isinstance(info, dict):
                raise ValueError
            streams = info.get("streams", [])
            container = info.get("format") or {}
            if not isinstance(streams, list) or not isinstance(container, dict):
                raise ValueError
            stream = next(s for s in streams if isinstance(s, dict)
                          and s.get("codec_type") == ("video" if video else "audio"))
            duration = float(container.get("duration") or stream.get("duration"))
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError
            width, height = (int(stream["width"]), int(stream["height"])) if video else (None, None)
            if video and (width <= 0 or height <= 0):
                raise ValueError
        except (ValueError, TypeError, KeyError, StopIteration, OverflowError):
            raise DeliveryError("media_metadata_failed", "音视频缺少有效时长或画面信息，尚未发送") from None
        cover = None
        if video:
            destination = Path(directory) / "cover.jpg"
            await _run(encoder, "-nostdin", "-v", "error", "-threads", "1", *input_args,
                       "-i", str(source), "-map", "0:v:0", "-frames:v", "1",
                       "-vf", "scale=640:640:force_original_aspect_ratio=decrease",
                       "-filter_threads", "1", "-threads", "1", "-q:v", "3", str(destination))
            cover = await asyncio.to_thread(destination.read_bytes)
            if not cover:
                raise DeliveryError("media_metadata_failed", "无法提取视频封面，尚未发送")
        return MediaMetadata(max(1, round(duration * 1000)), width, height, cover)
