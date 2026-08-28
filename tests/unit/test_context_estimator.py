from __future__ import annotations

import unittest
from io import BytesIO

from PIL import Image
from pydantic_ai.messages import BinaryContent

from deep_agent.agent.context.estimator import (
    MEDIA_CONTENT_TOKENS,
    TokenEstimator,
    deepseek_image_tokens,
)


class ContextEstimatorImageTests(unittest.TestCase):
    def test_deepseek_v4_image_formula_matches_provider_vectors(self) -> None:
        vectors = {
            (100, 100): 117,
            (384, 384): 117,
            (640, 480): 209,
            (800, 800): 349,
            (1024, 768): 357,
            (1920, 1080): 369,
            (2000, 2000): 349,
            (300, 50): 101,
            (9000, 1): 113,
            (16, 8192): 381,
            (100, 4036): 253,
            (4921, 353): 289,
        }
        for dimensions, expected in vectors.items():
            with self.subTest(dimensions=dimensions):
                self.assertEqual(deepseek_image_tokens(*dimensions), expected)

    def test_model_visible_binary_image_uses_route_aware_dimensions(self) -> None:
        buffer = BytesIO()
        Image.new("RGB", (640, 480), color=(10, 20, 30)).save(buffer, format="PNG")
        content = BinaryContent(data=buffer.getvalue(), media_type="image/png")

        deepseek = TokenEstimator.for_model(
            "deepseek-v4-flash-vision-exp",
            provider_type="deepseek",
        )
        unknown = TokenEstimator.for_model(
            "private-vision-model",
            provider_type="openai_compatible",
        )

        self.assertEqual(deepseek.value(content), 209)
        self.assertEqual(unknown.value(content), MEDIA_CONTENT_TOKENS)

    def test_invalid_image_bytes_fail_closed_to_conservative_media_cost(self) -> None:
        estimator = TokenEstimator.for_model(
            "deepseek-v4-flash-vision-exp",
            provider_type="deepseek",
        )
        content = BinaryContent(data=b"not-an-image", media_type="image/png")
        self.assertEqual(estimator.value(content), MEDIA_CONTENT_TOKENS)


if __name__ == "__main__":
    unittest.main()
