from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx2
from pydantic_ai import ModelRetry

from deep_agent.files.public_download import MAX_PUBLIC_FILE_BYTES, load_public_file
from deep_agent.web.proxy_compat import install_proxy_compatibility


_PUBLIC_URL = "https://93.184.216.34/files/report.csv"


def _response(
    content: bytes = b"a,b\n1,2\n", *,
    url: str = _PUBLIC_URL,
    headers: dict[str, str] | None = None,
) -> httpx2.Response:
    return httpx2.Response(
        200, content=content,
        headers=headers or {"content-type": "text/csv; charset=utf-8"},
        request=httpx2.Request("GET", url),
    )


class _Chunks(httpx2.AsyncByteStream):
    def __init__(self, size: int):
        self.size = size
        self.closed = False

    async def __aiter__(self):
        remaining = self.size
        while remaining:
            size = min(remaining, 64 * 1024)
            yield b"x" * size
            remaining -= size

    async def aclose(self) -> None:
        self.closed = True


class PublicFileDownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Install once before patching the SDK transport; otherwise the scoped
        # proxy hook's first installation would replace the transport double.
        install_proxy_compatibility()

    async def test_safe_download_receives_fixed_default_limit_and_no_identity(self):
        with patch(
            "deep_agent.files.public_download.safe_download",
            AsyncMock(return_value=_response()),
        ) as download:
            data, name, mime, final_url = await load_public_file(_PUBLIC_URL)

        self.assertEqual((data, name, mime, final_url), (
            b"a,b\n1,2\n", "report.csv", "text/csv", _PUBLIC_URL,
        ))
        download.assert_awaited_once()
        self.assertEqual(download.await_args.args, (_PUBLIC_URL,))
        self.assertFalse(download.await_args.kwargs["allow_local"])
        self.assertEqual(download.await_args.kwargs["headers"], {"Accept": "*/*"})
        self.assertEqual(download.await_args.kwargs["max_bytes"], 20 * 1024 * 1024)

    async def test_caller_can_lower_but_never_raise_the_limit(self):
        for requested, effective in ((4, 4), (MAX_PUBLIC_FILE_BYTES * 2, MAX_PUBLIC_FILE_BYTES)):
            with self.subTest(requested=requested), patch(
                "deep_agent.files.public_download.safe_download",
                AsyncMock(return_value=_response(b"1234")),
            ) as download:
                result = await load_public_file(_PUBLIC_URL, max_bytes=requested)
                self.assertEqual(result[0], b"1234")
                self.assertEqual(download.await_args.kwargs["max_bytes"], effective)

    async def test_nonpositive_limit_fails_before_any_request(self):
        for limit in (0, -1):
            with self.subTest(limit=limit), patch(
                "deep_agent.files.public_download.safe_download", AsyncMock(),
            ) as download:
                with self.assertRaisesRegex(ModelRetry, "无可用发送额度"):
                    await load_public_file(_PUBLIC_URL, max_bytes=limit)
                download.assert_not_awaited()

    async def test_relative_and_non_http_urls_fail_before_any_request(self):
        for url in ("/report.csv", "file:///etc/passwd", "ftp://93.184.216.34/report", "https://"):
            with self.subTest(url=url), patch(
                "deep_agent.files.public_download.safe_download", AsyncMock(),
            ) as download:
                with self.assertRaisesRegex(ModelRetry, "HTTP"):
                    await load_public_file(url)
                download.assert_not_awaited()

    async def test_url_credentials_fail_before_any_request(self):
        for url in (
            "https://user:password@93.184.216.34/report.csv",
            "https://user@93.184.216.34/report.csv",
            "https://:password@93.184.216.34/report.csv",
        ):
            with self.subTest(url=url), patch(
                "deep_agent.files.public_download.safe_download", AsyncMock(),
            ) as download:
                with self.assertRaisesRegex(ModelRetry, "用户名或密码"):
                    await load_public_file(url)
                download.assert_not_awaited()

    async def test_malformed_url_is_a_correctable_preflight_error(self):
        for url in ("https://[invalid]/report.csv", "https://[::1/report.csv"):
            with self.subTest(url=url), patch(
                "deep_agent.files.public_download.safe_download", AsyncMock(),
            ) as download:
                with self.assertRaisesRegex(ModelRetry, "有效的绝对 HTTP"):
                    await load_public_file(url)
                download.assert_not_awaited()

    async def test_real_sdk_rejects_private_local_and_metadata_addresses(self):
        for host in (
            "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.2",
            "169.254.169.254", "[::1]", "[fd00::1]", "[::ffff:127.0.0.1]",
        ):
            with self.subTest(host=host), patch(
                "pydantic_ai._ssrf._send_request", AsyncMock(),
            ) as send:
                with self.assertRaisesRegex(ModelRetry, "不安全"):
                    await load_public_file(f"https://{host}/report.csv")
                send.assert_not_awaited()

    async def test_real_sdk_rejects_hostname_resolving_to_private_ip(self):
        with patch(
            "pydantic_ai._ssrf.resolve_hostname", AsyncMock(return_value=["127.0.0.1"]),
        ) as resolve, patch("pydantic_ai._ssrf._send_request", AsyncMock()) as send:
            with self.assertRaisesRegex(ModelRetry, "不安全"):
                await load_public_file("https://localhost/report.csv")
        resolve.assert_awaited_once_with("localhost")
        send.assert_not_awaited()

    async def test_real_sdk_rechecks_every_redirect_for_private_destinations(self):
        for destination in ("http://127.0.0.1/private", "https://10.0.0.1/private", "http://[::1]/private"):
            async def redirect(_client, request):
                return httpx2.Response(302, headers={"location": destination}, request=request)

            with self.subTest(destination=destination), patch(
                "pydantic_ai._ssrf._send_request", AsyncMock(side_effect=redirect),
            ) as send:
                with self.assertRaisesRegex(ModelRetry, "不安全"):
                    await load_public_file(_PUBLIC_URL)
                self.assertEqual(send.await_count, 1)

    async def test_real_sdk_strips_credentials_introduced_by_redirect(self):
        requests = []

        async def redirect(_client, request):
            requests.append(request)
            if len(requests) == 1:
                return httpx2.Response(
                    302, headers={"location": "https://user:password@93.184.216.34/final.csv"},
                    request=request,
                )
            return httpx2.Response(200, content=b"ok", request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=redirect)) as send:
            result = await load_public_file(_PUBLIC_URL)
        self.assertEqual(send.await_count, 2)
        self.assertNotIn("authorization", requests[1].headers)
        self.assertNotIn("proxy-authorization", requests[1].headers)
        self.assertEqual(str(requests[1].url), "https://93.184.216.34/final.csv")
        self.assertEqual(result[3], "https://93.184.216.34/final.csv")

    async def test_real_sdk_sends_only_public_get_without_identity_headers(self):
        async def send_response(_client, request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.headers["accept"], "*/*")
            self.assertEqual(request.headers["host"], "93.184.216.34")
            for forbidden in (
                "authorization", "cookie", "proxy-authorization", "x-access-token",
                "x-app-id", "x-space-id", "x-user-id",
            ):
                self.assertNotIn(forbidden, request.headers)
            return httpx2.Response(200, content=b"data", request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send_response)) as send:
            self.assertEqual((await load_public_file(_PUBLIC_URL))[0], b"data")
        send.assert_awaited_once()

    async def test_real_sdk_preserves_final_redirect_url_filename_and_mime(self):
        final_url = "https://93.184.216.34/exports/final.csv?download=1"
        requests = []

        async def send_response(_client, request):
            requests.append(request)
            if len(requests) == 1:
                return httpx2.Response(302, headers={"location": final_url}, request=request)
            return httpx2.Response(
                200, content=b"ok", headers={
                    "content-type": "Text/CSV; charset=utf-8",
                    "content-disposition": "attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A.csv",
                }, request=request,
            )

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send_response)):
            result = await load_public_file(_PUBLIC_URL)
        self.assertEqual(result, (b"ok", "报告.csv", "text/csv", final_url))
        self.assertEqual(len(requests), 2)

    async def test_original_public_host_is_restored_from_pinned_response(self):
        response = httpx2.Response(
            200, content=b"png", headers={"content-type": "image/png"},
            request=httpx2.Request(
                "GET", "https://93.184.216.34/assets/chart.png?view=1",
                headers={"host": "files.example.com:8443"},
            ),
        )
        with patch(
            "deep_agent.files.public_download.safe_download", AsyncMock(return_value=response),
        ):
            result = await load_public_file("https://files.example.com:8443/assets/chart.png?view=1")
        self.assertEqual(result, (
            b"png", "chart.png", "image/png",
            "https://files.example.com:8443/assets/chart.png?view=1",
        ))

    async def test_real_sdk_stream_accepts_exact_limit_and_rejects_one_extra_byte(self):
        for size in (4, 5):
            stream = _Chunks(size)

            async def send_response(_client, request):
                return httpx2.Response(200, stream=stream, request=request)

            with self.subTest(size=size), patch(
                "pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send_response),
            ) as send:
                if size == 4:
                    self.assertEqual((await load_public_file(_PUBLIC_URL, max_bytes=4))[0], b"xxxx")
                else:
                    with self.assertRaisesRegex(ModelRetry, "maximum size of 4 bytes"):
                        await load_public_file(_PUBLIC_URL, max_bytes=4)
                send.assert_awaited_once()
                self.assertTrue(stream.closed)

    async def test_real_sdk_never_downloads_above_twenty_mib_when_caller_raises_limit(self):
        stream = _Chunks(MAX_PUBLIC_FILE_BYTES + 1)

        async def send_response(_client, request):
            return httpx2.Response(200, stream=stream, request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send_response)) as send:
            with self.assertRaisesRegex(ModelRetry, f"maximum size of {MAX_PUBLIC_FILE_BYTES} bytes"):
                await load_public_file(_PUBLIC_URL, max_bytes=MAX_PUBLIC_FILE_BYTES * 2)
        send.assert_awaited_once()
        self.assertTrue(stream.closed)

    async def test_response_byte_fallback_rejects_oversize_even_if_sdk_did_not(self):
        with patch(
            "deep_agent.files.public_download.safe_download",
            AsyncMock(return_value=_response(b"12345")),
        ) as download:
            with self.assertRaisesRegex(ModelRetry, "4 字节上限"):
                await load_public_file(_PUBLIC_URL, max_bytes=4)
        download.assert_awaited_once()

    async def test_filename_removes_directories_and_mime_falls_back_safely(self):
        response = _response(b"data", headers={
            "content-type": "not a media type",
            "content-disposition": 'attachment; filename="../../report.csv"',
        })
        with patch(
            "deep_agent.files.public_download.safe_download", AsyncMock(return_value=response),
        ):
            result = await load_public_file(_PUBLIC_URL)
        self.assertEqual(result[1:3], ("report.csv", "application/octet-stream"))

    async def test_empty_url_filename_uses_mime_extension(self):
        url = "https://93.184.216.34/"
        with patch(
            "deep_agent.files.public_download.safe_download",
            AsyncMock(return_value=_response(b"png", url=url, headers={"content-type": "image/png"})),
        ):
            result = await load_public_file(url)
        self.assertEqual(result[1:3], ("download.png", "image/png"))

    async def test_cancel_propagates_without_retry_and_joins_download(self):
        entered = asyncio.Event()
        finished = asyncio.Event()

        async def wait_for_cancel(*_args, **_kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

        with patch(
            "deep_agent.files.public_download.safe_download", AsyncMock(side_effect=wait_for_cancel),
        ) as download:
            task = asyncio.create_task(load_public_file(_PUBLIC_URL))
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(finished.is_set())
                download.assert_awaited_once()
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_timeout_read_retries_remain_bounded_by_attempt_count(self):
        for error in (TimeoutError("read timed out"), httpx2.ReadTimeout("read timed out")):
            with self.subTest(error=type(error).__name__), patch(
                "deep_agent.files.public_download.safe_download", AsyncMock(side_effect=error),
            ) as download, patch(
                "deep_agent.web.runtime._WEB_FETCH_MAX_ATTEMPTS", 3,
            ), patch("deep_agent.web.runtime._WEB_FETCH_RETRY_DELAY_SECONDS", 0):
                with self.assertRaisesRegex(ModelRetry, "下载失败"):
                    await load_public_file(_PUBLIC_URL)
                self.assertEqual(download.await_count, 3)

    async def test_total_deadline_cancels_inflight_read_and_prevents_retry(self):
        finished = asyncio.Event()

        async def slow_download(*_args, **_kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

        with patch(
            "deep_agent.files.public_download.safe_download", AsyncMock(side_effect=slow_download),
        ) as download, patch(
            "deep_agent.web.runtime._WEB_FETCH_ATTEMPT_TIMEOUT_SECONDS", 1,
        ), patch("deep_agent.web.runtime._WEB_FETCH_TOTAL_TIMEOUT_SECONDS", 0.02):
            with self.assertRaisesRegex(ModelRetry, "下载失败"):
                await asyncio.wait_for(load_public_file(_PUBLIC_URL), timeout=1)
            download.assert_awaited_once()
            self.assertTrue(finished.is_set())


if __name__ == "__main__":
    unittest.main()
