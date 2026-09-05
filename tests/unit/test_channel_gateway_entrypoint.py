from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch

import uvicorn

from deep_agent.channel_gateway import __main__ as entrypoint


async def _fixture_app(scope, receive, send):
    raise AssertionError("Entrypoint tests must not start an application")


class ChannelGatewayEntrypointTest(unittest.TestCase):
    def run_entrypoint(self, environment=None, arguments=()):
        # Keep Uvicorn's real run/Config path: that is where WEB_CONCURRENCY
        # caused the deployment failure. Replace only the application and the
        # final server loop, so this test never opens a listener or database.
        app_module = types.ModuleType("deep_agent.channel_gateway.app")
        app_module.app = _fixture_app
        configs = []

        def server_run(server, *args, **kwargs):
            configs.append(server.config)
            server.started = True

        with (
            patch.dict(os.environ, {
                "DEEP_AGENT_LOAD_DOTENV": "false",
                "DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "entrypoint-test-only",
                **(environment or {}),
            }, clear=True),
            patch.dict(sys.modules, {"deep_agent.channel_gateway.app": app_module}),
            patch.object(sys, "argv", ["channel_gateway", *arguments]),
            patch.object(uvicorn.Server, "run", server_run),
        ):
            entrypoint.main()
        self.assertEqual(len(configs), 1)
        config = configs[0]
        self.assertEqual(config.workers, 1)
        self.assertFalse(config.reload)
        self.assertIs(config.app, _fixture_app)
        return config

    def test_default_entrypoint_is_single_process(self):
        config = self.run_entrypoint()
        self.assertEqual((config.host, config.port), ("127.0.0.1", 8010))

    def test_inherited_web_concurrency_cannot_start_extra_gateways(self):
        for value in ("1", "2", "8", "not-a-number"):
            with self.subTest(WEB_CONCURRENCY=value):
                self.run_entrypoint({
                    "WEB_CONCURRENCY": value,
                    "UVICORN_WORKERS": "8",
                    "UVICORN_RELOAD": "true",
                })

    def test_host_and_port_overrides_are_preserved(self):
        config = self.run_entrypoint(
            {"WEB_CONCURRENCY": "4"},
            ("--host", "127.0.0.2", "--port", "8110"),
        )
        self.assertEqual((config.host, config.port), ("127.0.0.2", 8110))


if __name__ == "__main__":
    unittest.main()
