from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from deep_agent import __version__
from deep_agent.main import app, info


class ProductVersionTests(unittest.IsolatedAsyncioTestCase):
    async def test_package_fastapi_and_info_share_one_version(self) -> None:
        ctx = SimpleNamespace(
            stores=object(),
            registry=SimpleNamespace(names=lambda: ["native", "attachment"]),
        )
        with patch(
            "deep_agent.main.load_mcp_capabilities",
            new_callable=AsyncMock,
            return_value=[],
        ):
            payload = await info(ctx)

        self.assertEqual(__version__, "0.2.3")
        self.assertEqual(app.version, __version__)
        self.assertEqual(payload["name"], "DeepAgent")
        self.assertEqual(payload["version"], __version__)
        self.assertEqual(payload["capabilities"], ["native", "attachment"])


if __name__ == "__main__":
    unittest.main()
