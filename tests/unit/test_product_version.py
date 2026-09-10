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
            registry=SimpleNamespace(codes=lambda: ["builtin:general"]),
        )
        with patch(
            "deep_agent.main.selectable_tool_package_codes",
            new_callable=AsyncMock,
            return_value={"builtin:general", "ui:builtin"},
        ):
            payload = await info(ctx)

        self.assertEqual(__version__, "0.9.5")
        self.assertEqual(app.version, __version__)
        self.assertEqual(payload["name"], "DeepAgent")
        self.assertEqual(payload["version"], __version__)
        self.assertEqual(
            payload["capabilities"],
            ["builtin:general", "ui:builtin"],
        )


if __name__ == "__main__":
    unittest.main()
