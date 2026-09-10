"""Native failure details survive builtin wrappers without leaking credentials."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx2
from pydantic_ai import ModelRetry, RunContext, ToolFailed
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.files.toolset import _load_cached, _parse_cached
from deep_agent.files.tool_result_reader import read_stored_tool_result
from deep_agent.ptc.file_payload import _load_authorized_file, _load_public_url
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.web.runtime import _web_fetch_tool, execute_provider_search
from deep_agent.web.settings import WebSearchRuntimeConfig


class BuiltinNativeErrorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sink = patch("deep_agent.agent.tool_errors.log_event")
        sink.start()
        self.addCleanup(sink.stop)

    def context(self):
        return RunContext(
            deps=AgentDeps(session_id="native-errors", agent_id="agent", run_id="run",
                           request_headers={"authorization": "Bearer fixture-secret"},
                           attachment_by_id={"file": AttachmentRef(id="file", name="a.txt", mime="text/plain")}),
            model=TestModel(), usage=RunUsage(),
        )

    async def test_file_load_retains_reason_redacts_credentials_and_logs_only_facts(self):
        ctx = self.context()
        loader = AsyncMock(side_effect=PermissionError("file access denied Bearer fixture-secret"))
        rows = []
        with patch("deep_agent.agent.tool_errors.log_event", side_effect=lambda name, **fields: rows.append((name, fields))):
            with self.assertRaises(ToolFailed) as raised:
                await _load_cached(ctx, key="fixture", loader=loader, failure_label="附件")
        self.assertIn("file access denied", str(raised.exception))
        self.assertIn("PermissionError", str(raised.exception))
        self.assertNotIn("fixture-secret", str(raised.exception))
        self.assertEqual([r[0] for r in rows], ["builtin_operation_started", "builtin_operation_failed"])
        self.assertEqual(rows[-1][1]["phase"], "file_read")
        self.assertNotIn("file access denied", repr(rows))
        self.assertNotIn("fixture-secret", repr(rows))
        self.assertFalse(ctx.deps.file_bytes)
        self.assertFalse(ctx.deps.file_load_tasks)

    async def test_parse_native_error_is_redacted_and_sink_failure_is_isolated(self):
        def parser():
            raise ValueError("invalid workbook header Bearer fixture-secret")
        with patch("deep_agent.agent.tool_errors.log_event", side_effect=RuntimeError("sink unavailable")):
            with self.assertRaises(ToolFailed) as raised:
                await _parse_cached(self.context(), key="parse", parser=parser, failure_label="附件")
        self.assertIn("invalid workbook header", str(raised.exception))
        self.assertNotIn("fixture-secret", str(raised.exception))

    async def test_file_cancellation_remains_cancellation(self):
        with self.assertRaises(asyncio.CancelledError):
            await _load_cached(self.context(), key="cancel", loader=AsyncMock(side_effect=asyncio.CancelledError()), failure_label="附件")

    async def test_overflow_failure_preserves_error_shape_without_replay_advice(self):
        result = await read_stored_tool_result(SimpleNamespace(read=AsyncMock(side_effect=OSError("storage volume unavailable"))), "handle")
        self.assertEqual(list(result), ["error"])
        self.assertIn("storage volume unavailable", result["error"])
        self.assertNotIn("rerun", result["error"])

    async def test_ptc_authorized_download_preserves_reason_and_credentials(self):
        with patch("deep_agent.ptc.file_payload._download_file", new=AsyncMock(side_effect=OSError("download denied Bearer fixture-secret"))):
            with self.assertRaises(ToolFailed) as raised:
                await _load_authorized_file(self.context(), "file")
        self.assertIn("download denied", str(raised.exception))
        self.assertNotIn("fixture-secret", str(raised.exception))

    async def test_public_file_rejection_keeps_retry_semantics_and_native_detail(self):
        with patch("deep_agent.ptc.file_payload.safe_download", new=AsyncMock(side_effect=ValueError("private/internal IP address"))):
            with self.assertRaises(ModelRetry) as raised:
                await _load_public_url("https://example.com/file")
        self.assertIn("private/internal IP address", str(raised.exception))

    async def test_provider_error_includes_native_code_and_message_without_key_or_fallback(self):
        class ProviderError(Exception):
            status_code = 403
            body = {"error": {"message": "search not enabled for fixture-secret", "code": "search_disabled"}}
        config = WebSearchRuntimeConfig(mode="provider", provider="tavily", api_key="fixture-secret")
        with patch("deep_agent.web.runtime._search_tavily", new=AsyncMock(side_effect=ProviderError("provider rejected"))) as search, patch("deep_agent.web.runtime._search_duckduckgo", new=AsyncMock()) as fallback:
            with self.assertRaises(ToolFailed) as raised:
                await execute_provider_search(config, "fixture query")
        text = str(raised.exception)
        self.assertIn("search not enabled", text)
        self.assertIn("search_disabled", text)
        self.assertIn("HTTP=403", text)
        self.assertNotIn("fixture-secret", text)
        search.assert_awaited_once()
        fallback.assert_not_awaited()

    async def test_web_fetch_keeps_http_failure_and_native_reason(self):
        response = httpx2.Response(403, request=httpx2.Request("GET", "https://example.com/file?token=fixture-secret"), json={"message": "download type is forbidden", "code": "file_type_denied"})
        error = httpx2.HTTPStatusError("forbidden", request=response.request, response=response)
        with patch("deep_agent.web.runtime.safe_download", new=AsyncMock(side_effect=error)) as download:
            with self.assertRaises(ToolFailed) as raised:
                await _web_fetch_tool().function("https://example.com/file")
        self.assertIn("HTTP 403", str(raised.exception))
        self.assertIn("download type is forbidden", str(raised.exception))
        self.assertNotIn("fixture-secret", str(raised.exception))
        download.assert_awaited_once()
