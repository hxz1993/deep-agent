from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from pydantic_ai import ToolFailed, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_errors import native_error_facts, observe_tool_operation, tool_error_message
from deep_agent.diagnostics.schema import safe_fields
from deepfos_platform.errors.classes import APIStatusNotTrue


def _context():
    return RunContext(deps=AgentDeps(session_id="file-export-test", request_headers={}),
                      model=TestModel(), usage=RunUsage())


class NativeToolErrorTests(unittest.TestCase):
    def test_platform_business_error_survives_wrapping(self):
        native = APIStatusNotTrue("files/upload", "不允许上传 HTML 类型文件", "FILE_TYPE_DENIED")
        wrapper = ToolFailed("上传失败")
        wrapper.__cause__ = native
        message = tool_error_message("发布失败", wrapper)
        self.assertIn("发布失败", message)
        self.assertIn("不允许上传 HTML 类型文件", message)
        self.assertIn("错误码=FILE_TYPE_DENIED", message)
        self.assertIn("APIStatusNotTrue", message)

    def test_http_json_keeps_only_error_fields_and_scrubs_credentials(self):
        response = httpx.Response(422, request=httpx.Request("POST", "https://host/upload"), json={
            "error": {"message": "HTML forbidden; token=abc-secret", "code": "FORMAT_DENIED", "type": "ValidationError"},
            "data": {"body": "PRIVATE_DOCUMENT"}, "headers": {"cookie": "PRIVATE_COOKIE"},
        })
        error = httpx.HTTPStatusError("HTTP failed", request=response.request, response=response)
        message = tool_error_message("上传失败", error)
        for expected in ("HTTP=422", "FORMAT_DENIED", "ValidationError", "HTML forbidden"):
            self.assertIn(expected, message)
        for secret in ("abc-secret", "PRIVATE_DOCUMENT", "PRIVATE_COOKIE"):
            self.assertNotIn(secret, message)

    def test_known_credentials_url_query_paths_binary_and_limits(self):
        error = RuntimeError('Native denied secret-fragment Bearer abc123 '
                             'https://user:pass@example.com/file?token=query-secret '
                             '/Users/test/private/key.txt password="hidden value" ' + 'A' * 300)
        message = tool_error_message("读取失败", error, {"apptoken": "secret-fragment"})
        for secret in ("secret-fragment", "abc123", "user:pass", "query-secret", "/Users/test", "hidden value", "A" * 300):
            self.assertNotIn(secret, message)
        self.assertIn("Native denied", message)
        self.assertLess(len(tool_error_message("失败", RuntimeError("错误" * 10000))), 1800)

    def test_empty_timeout_and_exception_cycle_are_bounded(self):
        error = TimeoutError()
        error.__cause__ = error
        self.assertIn("TimeoutError", tool_error_message("下载失败", error))

    def test_local_os_error_keeps_errno_without_host_path(self):
        message = tool_error_message("读取失败", PermissionError(13, "Permission denied", "/opt/service/private-file"))
        self.assertIn("Permission denied", message)
        self.assertIn("错误码=13", message)
        self.assertNotIn("/opt/service/private-file", message)

    def test_sdk_without_error_message_does_not_fallback_to_full_body(self):
        from openai import BadRequestError
        body = {"data": {"private": "PRIVATE_DOCUMENT_SENTINEL"}}
        response = httpx.Response(400, request=httpx.Request("POST", "https://example.com"), json=body)
        exc = BadRequestError("Error code: 400 - " + json.dumps(body), response=response, body=body)
        message = tool_error_message("搜索失败", exc)
        self.assertIn("HTTP=400", message)
        self.assertIn("BadRequestError", message)
        self.assertNotIn("PRIVATE_DOCUMENT_SENTINEL", message)

    def test_implicit_cleanup_context_is_not_the_native_cause(self):
        exc = OSError("cleanup unavailable")
        exc.__context__ = APIStatusNotTrue("files/upload", "format not allowed", "FORMAT")
        message = tool_error_message("清理失败", exc)
        self.assertIn("cleanup unavailable", message)
        self.assertNotIn("FORMAT", message)

    def test_native_code_cannot_echo_known_credentials_into_logs(self):
        exc = APIStatusNotTrue("files/upload", "rejected", "prefix-private-key-suffix")
        facts = native_error_facts(exc, {"apptoken": "private-key"})
        self.assertNotIn("error_code", facts)


class ToolOperationDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_failure_is_correlated_without_logging_text(self):
        ctx = _context()
        ctx.deps.run_id = "run-1"
        ctx.tool_call_id = "call-1"
        native = APIStatusNotTrue("files/upload", "客户私有错误内容", "FILE_TYPE_DENIED")
        rows = []
        with patch("deep_agent.agent.tool_errors.log_event", side_effect=lambda e, **f: rows.append((e, safe_fields(f)))):
            with self.assertRaises(APIStatusNotTrue) as raised:
                async with observe_tool_operation(ctx, "upload", tool="export_file_to_download_center", byte_size=123):
                    raise native
        self.assertIs(raised.exception, native)
        self.assertEqual([r[0] for r in rows], ["builtin_operation_started", "builtin_operation_failed"])
        fields = rows[-1][1]
        self.assertEqual(fields["session_id"], "file-export-test")
        self.assertEqual(fields["run_id"], "run-1")
        self.assertEqual(fields["tool_call_id"], "call-1")
        self.assertEqual(fields["phase"], "upload")
        self.assertEqual(fields["error_code"], "FILE_TYPE_DENIED")
        self.assertEqual(fields["byte_size"], 123)
        self.assertNotIn("客户私有", json.dumps(rows, ensure_ascii=False))

    async def test_broken_sink_and_cancellation_do_not_change_execution(self):
        ctx = _context()
        with patch("deep_agent.agent.tool_errors.log_event", side_effect=RuntimeError("broken sink")):
            async with observe_tool_operation(ctx, "upload", tool="execute_python"):
                pass
            with self.assertRaises(asyncio.CancelledError):
                async with observe_tool_operation(ctx, "upload", tool="execute_python"):
                    raise asyncio.CancelledError()
