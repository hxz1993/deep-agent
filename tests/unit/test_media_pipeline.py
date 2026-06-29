from __future__ import annotations

import io
import unittest

from PIL import Image

from deep_agent.agent.media_pipeline import MediaPipelineError, process_upload


class TestMediaPipeline(unittest.TestCase):
    def _png_bytes(self, size: tuple[int, int]) -> bytes:
        img = Image.new("RGB", size, color="red")
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()

    def test_process_image_resizes_large_png(self) -> None:
        processed = process_upload(
            data=self._png_bytes((3000, 2000)),
            mime="image/png",
            filename="big.png",
        )
        self.assertIsNone(processed.extracted_text)
        with Image.open(io.BytesIO(processed.data)) as img:
            self.assertLessEqual(max(img.size), 1568)

    def test_rejects_unsupported_mime(self) -> None:
        with self.assertRaises(MediaPipelineError):
            process_upload(data=b"hello", mime="application/zip", filename="a.zip")

    def test_extracts_plain_text(self) -> None:
        processed = process_upload(
            data=b"hello world",
            mime="text/plain",
            filename="note.txt",
        )
        self.assertIn("hello world", processed.extracted_text or "")
