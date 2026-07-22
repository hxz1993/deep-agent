from __future__ import annotations

import unittest

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.mcp.toolset_builder import (
    build_mcp_toolset,
    build_raw_url_mcp_toolset,
    resolve_mcp_init_timeout,
    resolve_mcp_max_retries,
)
from deep_agent.options import OPTION


class ResolveMcpInitTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_timeout = OPTION.agent.mcp_init_timeout

    def tearDown(self) -> None:
        OPTION.agent.mcp_init_timeout = float(self._original_timeout)

    def test_uses_global_default(self) -> None:
        OPTION.agent.mcp_init_timeout = 42.0
        self.assertEqual(resolve_mcp_init_timeout({}), 42.0)

    def test_config_override_wins(self) -> None:
        OPTION.agent.mcp_init_timeout = 42.0
        self.assertEqual(resolve_mcp_init_timeout({"init_timeout": 15}), 15.0)


class ResolveMcpMaxRetriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_retries = OPTION.agent.request_retries

    def tearDown(self) -> None:
        OPTION.agent.request_retries = self._original_retries

    def test_uses_request_retries(self) -> None:
        OPTION.agent.request_retries = 3
        self.assertEqual(resolve_mcp_max_retries(), 3)


class BuildRawUrlMcpToolsetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_timeout = OPTION.agent.mcp_init_timeout
        self._original_retries = OPTION.agent.request_retries

    def tearDown(self) -> None:
        OPTION.agent.mcp_init_timeout = float(self._original_timeout)
        OPTION.agent.request_retries = self._original_retries

    def test_passes_init_timeout_to_mcp_toolset(self) -> None:
        OPTION.agent.mcp_init_timeout = 25.0
        with unittest.mock.patch("deep_agent.capabilities.mcp.toolset_builder.MCPToolset") as mcp_toolset_cls:
            mcp_toolset_cls.return_value = unittest.mock.MagicMock()
            build_raw_url_mcp_toolset("deepcube", "http://127.0.0.1:19000/mcp")
            _, kwargs = mcp_toolset_cls.call_args
            self.assertEqual(kwargs["init_timeout"], 25.0)

    def test_passes_max_retries_to_mcp_toolset(self) -> None:
        OPTION.agent.request_retries = 4
        with unittest.mock.patch("deep_agent.capabilities.mcp.toolset_builder.MCPToolset") as mcp_toolset_cls:
            mcp_toolset_cls.return_value = unittest.mock.MagicMock()
            build_raw_url_mcp_toolset("deepcube", "http://127.0.0.1:19000/mcp")
            _, kwargs = mcp_toolset_cls.call_args
            self.assertEqual(kwargs["max_retries"], 4)

    def test_includes_server_instructions(self) -> None:
        with unittest.mock.patch("deep_agent.capabilities.mcp.toolset_builder.MCPToolset") as mcp_toolset_cls:
            mcp_toolset_cls.return_value = unittest.mock.MagicMock()
            build_raw_url_mcp_toolset("deepcube", "http://127.0.0.1:19000/mcp")
            _, kwargs = mcp_toolset_cls.call_args
            self.assertIs(kwargs["include_instructions"], True)


class BuildUrlMcpToolsetTests(unittest.IsolatedAsyncioTestCase):
    async def test_includes_server_instructions(self) -> None:
        ctx = RunContext(
            deps=AgentDeps(session_id="session-a"),
            model=TestModel(),
            usage=RunUsage(),
        )
        with unittest.mock.patch("deep_agent.capabilities.mcp.toolset_builder.MCPToolset") as mcp_toolset_cls:
            mcp_toolset_cls.return_value.prefixed.return_value = unittest.mock.MagicMock()
            toolset = build_mcp_toolset(
                "biz",
                "url",
                {"url": "http://127.0.0.1:8083/mcp"},
            )

            await toolset.for_run(ctx)

            _, kwargs = mcp_toolset_cls.call_args
            self.assertIs(kwargs["include_instructions"], True)


if __name__ == "__main__":
    unittest.main()
