from __future__ import annotations

import asyncio
import gzip
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from deepfos_platform.client.platform import PlatformAPI
from deepfos_platform.lib.httpcli import AioHttpCli
from deep_agent.files.storage import (
    FileStoreError, FileStoreIntegrityError, FileStorePermissionError,
    LocalFileStore, PlatformFileStore,
)


class LocalBoundedDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="TMP_to_delete-bounded-download-")
        self.addCleanup(self.directory.cleanup)
        self.store = LocalFileStore(self.directory.name)
        await self.store.initialize()
        self.headers = {"user": "owner"}
        self.file = await self.store.upload(file_type="DL", file_name="x.txt", mime="text/plain",
                                            data=b"0123456789", request_headers=self.headers)

    async def test_exact_limit_preserves_bytes_and_owner_checks(self):
        self.assertEqual(await self.store.download_bounded(self.file.id, self.headers, max_bytes=10), b"0123456789")
        with self.assertRaises(FileStorePermissionError):
            await self.store.download_bounded(self.file.id, {"user": "other"}, max_bytes=10)

    async def test_metadata_rejects_before_opening_blob(self):
        with patch.object(Path, "open", side_effect=AssertionError("Blob must not be read")):
            with self.assertRaisesRegex(FileStoreError, "size limit"):
                await self.store.download_bounded(self.file.id, self.headers, max_bytes=9)

    async def test_changed_blob_cannot_bypass_limit_or_integrity(self):
        blob = next(self.store.blob_root.rglob("*.blob"))
        blob.write_bytes(b"x" * 100)
        with self.assertRaisesRegex(FileStoreError, "size limit"):
            await self.store.download_bounded(self.file.id, self.headers, max_bytes=10)
        blob.write_bytes(b"abcdefghij")
        with self.assertRaises(FileStoreIntegrityError):
            await self.store.download_bounded(self.file.id, self.headers, max_bytes=10)

    async def test_tampered_metadata_does_not_expand_read(self):
        with sqlite3.connect(self.store.metadata_path) as db:
            db.execute("UPDATE files SET byte_size=1 WHERE file_id=?", (self.file.id,))
        with self.assertRaisesRegex(FileStoreError, "size limit"):
            await self.store.download_bounded(self.file.id, self.headers, max_bytes=5)


class PlatformBoundedDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.seen = []
        self.gate = asyncio.Event()
        async def serve(request):
            mode = request.query["id"]
            self.seen.append((request.path, mode, request.headers.get("user"), request.headers.get("authorization")))
            if mode == "normal":
                return web.Response(body=b"0123456789")
            if mode == "error":
                return web.Response(status=403, body=b"secret-upstream-body")
            if mode == "small-compressed":
                return web.Response(body=gzip.compress(b"0123456789"), headers={"Content-Encoding": "gzip"})
            if mode == "compressed":
                return web.Response(body=gzip.compress(b"a" * 5000), headers={"Content-Encoding": "gzip"})
            if mode in {"oversized-gzip-header", "chunked-gzip-header"}:
                # Valid gzip with a giant FNAME header; decoded data is tiny.
                packed = gzip.compress(b"x")
                packed = packed[:3] + b"\x08" + packed[4:10] + b"n" * 70000 + b"\x00" + packed[10:]
                if mode == "oversized-gzip-header":
                    return web.Response(body=packed, headers={"Content-Encoding": "gzip"})
                response = web.StreamResponse(headers={"Content-Encoding": "gzip"})
                response.enable_chunked_encoding()
                await response.prepare(request)
                await response.write(packed)
                await response.write_eof()
                return response
            if mode == "large-declared":
                response = web.StreamResponse(headers={"Content-Length": "1000000"})
                await response.prepare(request)
                await self.gate.wait()
                return response
            if mode == "broken":
                response = web.StreamResponse(headers={"Content-Length": "10"})
                await response.prepare(request)
                await response.write(b"one")
                request.transport.close()
                return response
            response = web.StreamResponse()
            response.enable_chunked_encoding()
            await response.prepare(request)
            try:
                for _ in range(5):
                    await response.write(b"12345")
                    await asyncio.sleep(0)
                await response.write_eof()
            except ConnectionResetError:
                pass
            return response
        app = web.Application()
        app.router.add_get("/files/download", serve)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        server = web.TCPSite(self.runner, "127.0.0.1", 0)
        await server.start()
        port = server._server.sockets[0].getsockname()[1]
        self.patch = patch.object(PlatformAPI, "get_base_url", new=AsyncMock(return_value=(None, None, f"http://127.0.0.1:{port}")))
        self.patch.start()
        self.discovery_patch = patch("deepfos_platform.client.base.ServiceDiscovery.instantiate", return_value=object())
        self.discovery_patch.start()
        self.headers = {"user": "owner", "authorization": "test-credential"}
        self.store = PlatformFileStore()

    async def asyncTearDown(self):
        self.gate.set()
        self.patch.stop()
        self.discovery_patch.stop()
        await AioHttpCli.close_current()
        await self.runner.cleanup()

    async def test_exact_bytes_and_platform_query_auth_preserved(self):
        self.assertEqual(await self.store.download_bounded("normal", self.headers, max_bytes=10), b"0123456789")
        self.assertEqual(self.seen, [("/files/download", "normal", "owner", "test-credential")])

    async def test_compressed_content_length_is_not_decoded_size(self):
        self.assertEqual(await self.store.download_bounded("small-compressed", self.headers, max_bytes=10), b"0123456789")

    async def test_compressed_wire_header_is_bounded_even_with_tiny_decoded_body(self):
        for mode in ("oversized-gzip-header", "chunked-gzip-header"):
            with self.subTest(mode=mode), self.assertRaisesRegex(FileStoreError, "size limit"):
                await self.store.download_bounded(mode, self.headers, max_bytes=10)

    async def test_content_length_rejects_before_body_wait(self):
        with self.assertRaisesRegex(FileStoreError, "size limit"):
            async with asyncio.timeout(2):
                await self.store.download_bounded("large-declared", self.headers, max_bytes=10)

    async def test_chunked_and_decompressed_bytes_are_bounded(self):
        for mode in ("chunked", "compressed"):
            with self.subTest(mode=mode), self.assertRaisesRegex(FileStoreError, "size limit"):
                await self.store.download_bounded(mode, self.headers, max_bytes=100 if mode == "compressed" else 10)

    async def test_http_and_incomplete_stream_errors_are_safe(self):
        for mode in ("error", "broken"):
            with self.subTest(mode=mode), self.assertRaises(FileStoreError) as error:
                await self.store.download_bounded(mode, self.headers, max_bytes=100)
            self.assertNotIn("secret", str(error.exception))
            self.assertNotIn("http", str(error.exception))


if __name__ == "__main__":
    unittest.main()
