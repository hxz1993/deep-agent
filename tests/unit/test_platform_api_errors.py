from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import ClientResponse
from aiohttp.client_reqrep import RequestInfo as HttpRequestInfo
from aiohttp.helpers import TimerNoop
from loguru import logger
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from deepfos_platform.client.base import BaseAPI, ContentType, RequestInfo, Route
from deepfos_platform.errors.classes import (
    APIHttpCodeNotOK,
    APICallError,
    APIResponseParseError,
    APIStatusCodeError,
    APIStatusNotTrue,
)


class PlatformAPIErrorTests(unittest.IsolatedAsyncioTestCase):
    def response(self, status, body, content_type="application/json; charset=utf-8"):
        """Use aiohttp's real decoding/release behavior with an in-memory HTTP body."""
        url = URL("http://platform.invalid/api/file/upload")
        headers = CIMultiDictProxy(CIMultiDict({"Content-Type": content_type}))
        response = ClientResponse(
            "POST", url,
            writer=None,
            continue100=None,
            timer=TimerNoop(),
            request_info=HttpRequestInfo(url, "POST", headers, url),
            traces=[],
            loop=asyncio.get_running_loop(),
            session=None,
            stream_writer=SimpleNamespace(output_size=0),
        )
        response.status = status
        response._headers = headers
        response._body = body.encode() if isinstance(body, str) else body
        return response

    async def check(self, response, **kwargs):
        return await BaseAPI.check_response(
            response, RequestInfo(method="post", url=str(response.url)), **kwargs
        )

    async def call_route(self, response, **route_options):
        @Route("post")("/api/file/upload", retries=0, **route_options)
        def upload(ins):
            return {}

        client = BaseAPI(header={})
        client.get_base_url = AsyncMock(return_value=(None, None, "http://platform.invalid"))
        client.post = AsyncMock(return_value=response)
        with patch("deepfos_platform.client.base.ServiceDiscovery.instantiate"):
            return await upload(client)

    async def test_business_error_keeps_message_code_type_and_actual_http_status(self):
        response = self.response(201, json.dumps({
            "status": False, "message": "不允许上传 HTML 类型文件", "code": "FILE_TYPE_DENIED",
            "type": "UnsupportedFileType", "data": {"private": "not-for-error"},
        }))
        with self.assertRaises(APIStatusNotTrue) as raised:
            await self.call_route(response)
        error = raised.exception
        self.assertEqual(error.upstream_message, "不允许上传 HTML 类型文件")
        self.assertEqual(error.upstream_code, "FILE_TYPE_DENIED")
        self.assertEqual(error.code, "FILE_TYPE_DENIED")
        self.assertEqual(error.upstream_error_type, "UnsupportedFileType")
        self.assertEqual(error.upstream_http_status, 201)
        self.assertEqual(error.status_code, 200)  # Existing local ExpectedError envelope.
        self.assertNotIn("not-for-error", str(error))
        self.assertTrue(response.closed)

    async def test_missing_business_message_does_not_hide_code_with_key_error(self):
        response = self.response(200, '{"status":false,"code":0}')
        with self.assertRaises(APIStatusNotTrue) as raised:
            await self.call_route(response)
        self.assertIsNone(raised.exception.upstream_message)
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(raised.exception.upstream_http_status, 200)

    async def test_non_2xx_errors_are_checked_before_binary_or_text_return(self):
        body = json.dumps({
            "error": {"message": "文件类型不支持", "error_code": "FILE_FORMAT", "error_type": "ValidationError"},
            "data": {"token": "private-data-sentinel"},
            "headers": {"Authorization": "private-header-sentinel"},
        })
        for content_type in ContentType:
            with self.subTest(content_type=content_type):
                response = self.response(415, body)
                with self.assertRaises(APIStatusCodeError) as raised:
                    await self.check(response, content_type=content_type, data_wrapped=False)
                error = raised.exception
                self.assertIsInstance(error, APIHttpCodeNotOK)
                self.assertEqual(error.upstream_http_status, 415)
                self.assertEqual(error.upstream_message, "文件类型不支持")
                self.assertEqual(error.upstream_code, "FILE_FORMAT")
                self.assertEqual(error.upstream_error_type, "ValidationError")
                self.assertIn("文件类型不支持", str(error))
                self.assertNotIn("private-", str(error))
                self.assertTrue(response.closed)

    async def test_non_json_and_empty_http_errors_keep_http_status_without_raw_body(self):
        for body in (b"", b"<html>private-proxy-body</html>", b"\xff", b'["private-list"]'):
            with self.subTest(body=body):
                response = self.response(502, body)
                with self.assertRaises(APIStatusCodeError) as raised:
                    await self.check(response)
                error = raised.exception
                self.assertEqual(error.upstream_http_status, 502)
                self.assertIsNone(error.upstream_message)
                self.assertIsNone(error.upstream_code)
                self.assertIn("502", str(error))
                self.assertNotIn("private-", str(error))

    async def test_error_response_logs_do_not_contain_native_body_or_message(self):
        for status, body in (
            (403, '{"message":"private-native-message","data":"private-body"}'),
            (200, '{"status":false,"message":"private-native-message"}'),
            (200, "private-invalid-json"),
        ):
            with self.subTest(status=status):
                messages = []
                sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
                try:
                    try:
                        await self.check(self.response(status, body))
                    except (APIStatusCodeError, APIResponseParseError):
                        pass
                finally:
                    logger.remove(sink)
                if body == "private-invalid-json":
                    self.assertEqual(len(messages), 1)
                    self.assertIn("error_stack=JSONDecodeError", messages[0])
                    self.assertNotIn("private-", messages[0])
                else:
                    self.assertEqual(messages, [])

    async def test_platform_retry_keeps_request_and_only_logs_safe_attempt_metadata(self):
        route = Route("post")
        route.wait = (0,)

        @route("/api/file/upload", retries=1)
        def upload(ins):
            return {"body": {"data": "NEVER_LOG_BODY"}}

        client = BaseAPI(header={"Authorization": "Bearer NEVER_LOG_TOKEN", "Content-Type": "application/json; charset=utf-8"})
        client.get_base_url = AsyncMock(return_value=(None, None, "http://platform.invalid"))
        messages = []
        sink = logger.add(lambda message: messages.append(str(message)), level="INFO")
        try:
            for succeeds in (True, False):
                messages.clear()
                outcome = self.response(200, '{"status":true,"data":{"ok":true}}') if succeeds else OSError("NEVER_LOG_SECOND_ERROR")
                with patch("deepfos_platform.client.base.ServiceDiscovery.instantiate"), patch(
                    "deepfos_platform.client.base.AioHttpCli.post", AsyncMock(side_effect=[
                        OSError("NEVER_LOG_FIRST_ERROR"), outcome,
                    ])
                ) as transport:
                    if succeeds:
                        self.assertEqual(await upload(client), {"ok": True})
                    else:
                        with self.assertRaises(APICallError):
                            await upload(client)
                    self.assertEqual(transport.await_count, 2)
                    self.assertEqual(transport.await_args.kwargs["json"], {"data": "NEVER_LOG_BODY"})
                    self.assertEqual(transport.await_args.kwargs["headers"]["Authorization"], "Bearer NEVER_LOG_TOKEN")
                self.assertEqual(len(messages), 1)
                self.assertIn("retry 1/1", messages[0])
                self.assertNotIn("NEVER_LOG", messages[0])
                self.assertNotIn("error_stack", messages[0])
        finally:
            logger.remove(sink)

    async def test_plain_text_http_error_keeps_bounded_native_message(self):
        for message in ("Unsupported file type: HTML", "x" * 5000):
            with self.subTest(length=len(message)):
                response = self.response(415, message, "text/plain; charset=utf-8")
                with self.assertRaises(APIStatusCodeError) as raised:
                    await self.check(response, content_type=ContentType.bytes)
                self.assertEqual(raised.exception.upstream_message, message[:4000])
                self.assertEqual(raised.exception.upstream_http_status, 415)

    async def test_html_and_tracebacks_are_not_returned_as_plain_error_messages(self):
        for body, content_type in (
            ("<html>private error page</html>", "text/html"),
            ("<html>private error page</html>", "text/plain"),
            ('Traceback (most recent call last):\n  File "private.py", line 3', "text/plain"),
            ("Exception\n  at private.method(Source.java:3)", "text/plain"),
        ):
            with self.subTest(content_type=content_type, body=body):
                with self.assertRaises(APIStatusCodeError) as raised:
                    await self.check(self.response(500, body, content_type))
                self.assertIsNone(raised.exception.upstream_message)
                self.assertEqual(raised.exception.upstream_http_status, 500)

    async def test_successful_bytes_and_text_are_preserved(self):
        for content_type, body, expected in (
            (ContentType.bytes, b"\x00\xfffile", b"\x00\xfffile"),
            (ContentType.text, "plain text", "plain text"),
        ):
            with self.subTest(content_type=content_type):
                self.assertEqual(
                    await self.check(self.response(200, body), content_type=content_type),
                    (True, expected),
                )

    async def test_route_return_raw_and_raise_false_contracts_are_preserved(self):
        payload = {"status": False, "message": "expected error", "data": {"id": "file"}}
        raw = await self.call_route(self.response(200, json.dumps(payload)), return_raw=True)
        self.assertEqual(raw, payload)
        data = await self.call_route(self.response(200, json.dumps(payload)), raise_false=False)
        self.assertEqual(data, payload["data"])
        result = await self.call_route(self.response(200, '{"status":true,"data":{"id":"file"}}'))
        self.assertEqual(result, {"id": "file"})

    async def test_cancellation_propagates_and_releases_response(self):
        response = self.response(503, b"")
        response.read = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await self.check(response)
        self.assertTrue(response.closed)

    def test_existing_exception_constructors_and_alias_remain_compatible(self):
        error = APIStatusNotTrue("api/file", "类型错误", 42)
        self.assertEqual(error.code, 42)
        self.assertEqual(error.upstream_message, "类型错误")
        self.assertIsNone(error.upstream_http_status)
        self.assertEqual(APIStatusNotTrue("api/file", "类型错误").code, None)
        self.assertIs(APIStatusCodeError, APIHttpCodeNotOK)
        self.assertEqual(APIStatusCodeError("api/file", 503).upstream_http_status, 503)


if __name__ == "__main__":
    unittest.main()
