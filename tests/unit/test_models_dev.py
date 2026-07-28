"""Unit tests for the strict models.dev refresh client."""

from __future__ import annotations

import json
import unittest

import httpx

from deep_agent.agent.models.models_dev import (
    ModelsDevFetchError,
    fetch_models_dev_bundle,
    parse_labs_page,
)


class ModelsDevClientTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_labs_page_reads_only_lab_entries(self) -> None:
        payload = [
            {"type": "lab", "id": "openai", "name": "OpenAI"},
            {"type": "model", "id": "openai/gpt-test"},
        ]
        html = (
            '<script id="search-index" type="application/json">'
            f"{json.dumps(payload)}</script>"
        )
        self.assertEqual(
            parse_labs_page(html),
            [{"type": "lab", "id": "openai", "name": "OpenAI"}],
        )
        self.assertEqual(parse_labs_page("<html></html>"), [])

    async def test_fetches_combined_catalog_and_optional_labs(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/catalog.json":
                return httpx.Response(
                    200,
                    json={
                        "models": {
                            "openai/gpt-test": {"id": "openai/gpt-test"}
                        },
                        "providers": {"openai": {"models": {}}},
                    },
                )
            return httpx.Response(503, text="labs unavailable")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://models.test",
        ) as client:
            bundle = await fetch_models_dev_bundle(
                client=client,
                catalog_url="/catalog.json",
                labs_url="/labs",
            )
        self.assertIn("openai/gpt-test", bundle.models)
        self.assertIn("openai", bundle.providers)
        self.assertEqual(bundle.labs, [])

    async def test_required_catalog_failure_aborts_refresh(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/catalog.json":
                return httpx.Response(502, text="bad gateway")
            return httpx.Response(503, text="labs unavailable")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://models.test",
        ) as client:
            with self.assertRaises(ModelsDevFetchError):
                await fetch_models_dev_bundle(
                    client=client,
                    catalog_url="/catalog.json",
                    labs_url="/labs",
                )

    async def test_catalog_requires_models_and_providers_objects(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/catalog.json":
                return httpx.Response(200, json={"models": {}})
            return httpx.Response(503, text="labs unavailable")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://models.test",
        ) as client:
            with self.assertRaisesRegex(ModelsDevFetchError, "providers"):
                await fetch_models_dev_bundle(
                    client=client,
                    catalog_url="/catalog.json",
                    labs_url="/labs",
                )


if __name__ == "__main__":
    unittest.main()
