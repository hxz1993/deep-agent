import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from deep_agent.channels.media_metadata import _run, inspect_media
from deep_agent.channels.outbound import DeliveryError


class MetadataTest(unittest.IsolatedAsyncioTestCase):
    async def test_audio_duration_without_running_encoder(self):
        probe = AsyncMock(return_value=json.dumps({"format": {"duration": "2.375"},
            "streams": [{"codec_type": "audio"}]}).encode())
        with patch("deep_agent.channels.media_metadata.shutil.which", return_value="/bin/probe"), patch("deep_agent.channels.media_metadata._run", probe):
            result = await inspect_media(b"audio", video=False)
        self.assertEqual(result.duration_ms, 2375)
        self.assertIsNone(result.cover)
        probe.assert_awaited_once()

    async def test_invalid_duration_and_missing_stream_are_known_failures(self):
        for duration in ("nan", "inf", "0", "-1", None):
            with patch("deep_agent.channels.media_metadata.shutil.which", return_value="probe"), patch("deep_agent.channels.media_metadata._run", AsyncMock(return_value=json.dumps({"format": {"duration": duration}, "streams": [{"codec_type": "audio"}]}).encode())):
                with self.assertRaises(DeliveryError) as caught:
                    await inspect_media(b"audio", video=False)
                self.assertFalse(caught.exception.unknown)

    async def test_missing_dependency_is_not_a_delivery_timeout(self):
        with patch("deep_agent.channels.media_metadata.shutil.which", return_value=None):
            with self.assertRaises(DeliveryError) as caught:
                await inspect_media(b"audio", video=False)
        self.assertEqual(caught.exception.code, "media_dependency_missing")

    async def test_cancel_kills_and_reaps_process(self):
        started = asyncio.Event()
        async def communicate():
            started.set()
            await asyncio.Event().wait()
        from types import SimpleNamespace
        from unittest.mock import Mock
        process = SimpleNamespace(returncode=None, communicate=communicate, kill=Mock(), wait=AsyncMock())
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            task = asyncio.create_task(_run("probe"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        process.kill.assert_called_once()
        process.wait.assert_awaited_once()


    async def test_malformed_probe_shapes_become_safe_metadata_failures(self):
        for payload in ([], {"streams": [None]}, {"format": [], "streams": None},
                        {"format": "bad", "streams": [{"codec_type": "audio"}]}):
            with patch("deep_agent.channels.media_metadata.shutil.which", return_value="probe"), patch("deep_agent.channels.media_metadata._run", AsyncMock(return_value=json.dumps(payload).encode())):
                with self.assertRaises(DeliveryError) as caught:
                    await inspect_media(b"audio", video=False)
                self.assertEqual(caught.exception.code, "media_metadata_failed")
                self.assertFalse(caught.exception.unknown)
