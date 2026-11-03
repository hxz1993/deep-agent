"""Real isolated image/PDF rendering and lifecycle regression tests."""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
from pathlib import Path
import random
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from deep_agent.files.image_view import rendering


def image_bytes(fmt="PNG", size=(80, 50), *, exif=False):
    stream = io.BytesIO()
    with Image.new("RGB", size, "#2780bc") as image:
        kwargs = {}
        if exif:
            tags = image.getexif()
            tags[274] = 6
            kwargs["exif"] = tags
        if fmt == "GIF":
            with Image.new("RGB", size, "red") as second:
                image.save(stream, fmt, save_all=True, append_images=[second], duration=100)
        else:
            image.save(stream, fmt, **kwargs)
    return stream.getvalue()


def pdf_bytes(pages=2, *, password=None, rotate=0, size=(300, 600)):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=size[0], height=size[1])
        if rotate:
            page.rotate(rotate)
    if password is not None:
        writer.encrypt(password, owner_password="owner")
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


class ViewImageRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_16bit_grayscale_png_preserves_absolute_tones(self):
        # No black or white in this source: stretching to local min/max would
        # change the absolute tones just as clipping everything to 255 would.
        for mode, order in (("I;16", "<"), ("I;16B", ">")):
            with self.subTest(mode=mode):
                stream = io.BytesIO()
                with Image.frombytes(mode, (3, 1), struct.pack(f"{order}3H", 16384, 32768, 49152)) as source:
                    source.save(stream, "PNG")
                result = await rendering.render_source(stream.getvalue())
                with Image.open(io.BytesIO(result["images"][0]["data"])) as image:
                    self.assertEqual([image.getpixel((i, 0)) for i in range(3)], [(64,) * 3, (128,) * 3, (191,) * 3])

    async def test_16bit_grayscale_png_keeps_exact_transparency(self):
        stream = io.BytesIO()
        with Image.frombytes("I;16", (3, 1), struct.pack("<3H", 16384, 16385, 32768)) as source:
            source.save(stream, "PNG", transparency=16384)
        result = await rendering.render_source(stream.getvalue())
        with Image.open(io.BytesIO(result["images"][0]["data"])) as image:
            self.assertEqual([image.getpixel((i, 0)) for i in range(3)],
                             [(64, 64, 64, 0), (64, 64, 64, 255), (128, 128, 128, 255)])

    async def test_real_image_formats_and_exif(self):
        for fmt, expected in (("PNG", "png"), ("JPEG", "jpeg"), ("WEBP", "webp"), ("GIF", "gif")):
            with self.subTest(fmt=fmt):
                result = await rendering.render_source(image_bytes(fmt, exif=fmt == "JPEG"))
                self.assertEqual(result["format"], expected)
                self.assertIsNone(result["page_count"])
                self.assertEqual(result["errors"], [])
                item = result["images"][0]
                self.assertEqual((item["width"], item["height"]), (50, 80) if fmt == "JPEG" else (80, 50))
                self.assertEqual(item["gif_first_frame"], fmt == "GIF")
                self.assertFalse(item["scaled"])
                self.assertEqual(item["sha256"], hashlib.sha256(item["data"]).hexdigest())
                with Image.open(io.BytesIO(item["data"])) as image:
                    image.load()
                    self.assertNotIn(274, image.getexif())

    async def test_real_pdf_defaults_selection_dedup_rotation_and_partial_failure(self):
        data = pdf_bytes(rotate=90)
        default = await rendering.render_source(data)
        self.assertEqual(default["page_count"], 2)
        self.assertEqual([x["page"] for x in default["images"]], [1])
        selected = await rendering.render_source(data, [2, 2, 99, 1])
        self.assertEqual([x["page"] for x in selected["images"]], [2, 1])
        self.assertEqual(selected["errors"], [{"page": 99, "error": "page_out_of_range"}])
        self.assertGreater(selected["images"][0]["width"], selected["images"][0]["height"])

    async def test_huge_pdf_page_is_scaled_before_bitmap_allocation(self):
        result = await rendering.render_source(pdf_bytes(1, size=(100000, 100000)))
        item = result["images"][0]
        self.assertTrue(item["scaled"])
        self.assertLessEqual(max(item["width"], item["height"]), 3072)
        self.assertLessEqual(item["width"] * item["height"], 6_000_000)
        self.assertLessEqual(len(item["data"]), 4 * 1024 * 1024)

    async def test_pdf_renders_vector_and_bitmap_pages_without_text_layer(self):
        writer = PdfWriter()
        vector = writer.add_blank_page(width=100, height=100)
        content = DecodedStreamObject()
        content.set_data(b"0 0 0 RG 4 w 10 10 m 70 70 l 55 70 m 70 70 l 70 55 l S")
        vector[NameObject("/Contents")] = writer._add_object(content)
        bitmap_page = writer.add_blank_page(width=100, height=100)
        xobject = DecodedStreamObject()
        xobject.set_data(bytes([255, 0, 0]) * 4)
        xobject.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                        NameObject("/Width"): NumberObject(2), NameObject("/Height"): NumberObject(2),
                        NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8)})
        bitmap_page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Picture"): writer._add_object(xobject)})})
        bitmap_content = DecodedStreamObject()
        bitmap_content.set_data(b"q 60 0 0 60 20 20 cm /Picture Do Q")
        bitmap_page[NameObject("/Contents")] = writer._add_object(bitmap_content)
        stream = io.BytesIO()
        writer.write(stream)
        result = await rendering.render_source(stream.getvalue(), [1, 2])
        self.assertEqual(result["errors"], [])
        for item in result["images"]:
            with Image.open(io.BytesIO(item["data"])) as image:
                self.assertTrue(any(low < 100 for low, _ in image.getextrema()))

    async def test_noisy_image_prefers_jpeg_when_png_exceeds_four_mb(self):
        pixels = random.Random(0).randbytes(1600 * 1200 * 3)
        with Image.frombytes("RGB", (1600, 1200), pixels) as source:
            stream = io.BytesIO()
            source.save(stream, "PNG")
        result = await rendering.render_source(stream.getvalue())
        item = result["images"][0]
        self.assertEqual(item["media_type"], "image/jpeg")
        self.assertLessEqual(len(item["data"]), 4 * 1024 * 1024)
        self.assertEqual((item["width"], item["height"]), (1600, 1200))

    async def test_static_acroform_value_is_not_silently_missing(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=100, height=100)
        font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                                    NameObject("/Subtype"): NameObject("/Type1"),
                                                    NameObject("/BaseFont"): NameObject("/Helvetica")}))
        widget = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject("example"),
            NameObject("/V"): TextStringObject("VISIBLE"), NameObject("/DA"): TextStringObject("/Helv 12 Tf 0 g"),
            NameObject("/Rect"): ArrayObject([NumberObject(n) for n in (10, 10, 90, 40)]),
            NameObject("/P"): page.indirect_reference,
        })
        widget_ref = writer._add_object(widget)
        page[NameObject("/Annots")] = ArrayObject([widget_ref])
        writer._root_object[NameObject("/AcroForm")] = writer._add_object(DictionaryObject({
            NameObject("/Fields"): ArrayObject([widget_ref]),
            NameObject("/DR"): DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): font})}),
            NameObject("/DA"): TextStringObject("/Helv 12 Tf 0 g"),
        }))
        stream = io.BytesIO()
        writer.write(stream)
        result = await rendering.render_source(stream.getvalue())
        self.assertEqual(result["errors"], [])
        with Image.open(io.BytesIO(result["images"][0]["data"])) as image:
            self.assertLess(image.getextrema()[0][0], 100)

    def test_encoder_scales_after_jpeg_overflow_and_preserves_transparency_when_small(self):
        from deep_agent.files.image_view import normalization

        with Image.frombytes("RGB", (320, 240), random.Random(1).randbytes(320 * 240 * 3)) as source:
            with patch.object(normalization, "MAX_OUTPUT_BYTES", 9000):
                item = normalization.normalize_image(source)
            self.assertLessEqual(len(item["data"]), 9000)
            self.assertEqual(item["media_type"], "image/jpeg")
            self.assertTrue(item["scaled"])
        with Image.new("RGBA", (20, 20), (0, 100, 200, 80)) as source:
            item = normalization.normalize_image(source)
        self.assertEqual(item["media_type"], "image/png")
        with Image.open(io.BytesIO(item["data"])) as restored:
            self.assertEqual(restored.getpixel((0, 0)), (0, 100, 200, 80))

    async def test_image_pixel_and_output_dimension_limits(self):
        result = await rendering.render_source(image_bytes(size=(3400, 2400)))
        item = result["images"][0]
        self.assertTrue(item["scaled"])
        self.assertLessEqual(max(item["width"], item["height"]), 3072)
        self.assertLessEqual(item["width"] * item["height"], 6_000_000)
        large = await asyncio.to_thread(image_bytes, size=(8001, 8000))
        with self.assertRaisesRegex(ValueError, "pixel_limit"):
            await rendering.render_source(large)

    async def test_encryption_with_nonempty_and_empty_password_rejected(self):
        for password in ("secret", ""):
            with self.subTest(password_is_empty=not password), self.assertRaisesRegex(ValueError, "encrypted_pdf"):
                await rendering.render_source(pdf_bytes(1, password=password))

    async def test_invalid_sources_and_pages_fail_explicitly(self):
        for data in (b"", b"<html>fake.png</html>", image_bytes("JPEG")[:-2], b"%PDF-1.7\nbroken"):
            with self.subTest(data=data[:12]), self.assertRaises(ValueError):
                await rendering.render_source(data)
        for pages in ([True], [0], [-1], [1.0], [], "1", list(range(1, 10))):
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                await rendering.render_source(pdf_bytes(), pages)
        with self.assertRaisesRegex(ValueError, "pages_for_image"):
            await rendering.render_source(image_bytes(), [1])
        with patch.object(rendering, "MAX_IMAGE_SOURCE_BYTES", 10), self.assertRaisesRegex(ValueError, "source_size_limit"):
            await rendering.render_source(image_bytes())
        with patch.object(rendering, "MAX_PDF_SOURCE_BYTES", 10), self.assertRaisesRegex(ValueError, "source_size_limit"):
            await rendering.render_source(pdf_bytes())

    async def test_minimal_environment_and_temp_permissions_cleanup(self):
        real_spawn = asyncio.create_subprocess_exec
        observed = []

        async def spawn(*args, **kwargs):
            root = Path(kwargs["cwd"])
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((root / "source.bin").stat().st_mode), 0o600)
            self.assertNotIn("VIEW_IMAGE_TEST_SECRET", kwargs["env"])
            self.assertNotIn("HOME", kwargs["env"])
            self.assertIn("-I", args)
            observed.append(root)
            return await real_spawn(*args, **kwargs)

        with patch.dict(os.environ, {"VIEW_IMAGE_TEST_SECRET": "not-for-child"}), patch.object(
            rendering.asyncio, "create_subprocess_exec", side_effect=spawn
        ):
            await rendering.render_source(image_bytes())
        self.assertTrue(observed)
        self.assertTrue(all(not root.exists() for root in observed))

    async def test_timeout_and_cancel_reap_real_workers_and_allow_next_run(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-view-image-worker-") as folder:
            worker = Path(folder) / "worker.py"
            worker.write_text("import time\ntime.sleep(30)\n")
            real_spawn = asyncio.create_subprocess_exec
            processes, roots = [], []
            started = asyncio.Event()

            async def spawn(*args, **kwargs):
                process = await real_spawn(*args, **kwargs)
                processes.append(process)
                roots.append(Path(kwargs["cwd"]))
                started.set()
                return process

            with patch.object(rendering, "_WORKER", worker), patch.object(rendering, "RENDER_TIMEOUT", 0.15), patch.object(
                rendering.asyncio, "create_subprocess_exec", side_effect=spawn
            ), self.assertRaisesRegex(ValueError, "render_timeout"):
                await rendering.render_source(image_bytes())
            self.assertIsNotNone(processes[-1].returncode)
            self.assertFalse(roots[-1].exists())
            started.clear()
            with patch.object(rendering, "_WORKER", worker), patch.object(
                rendering.asyncio, "create_subprocess_exec", side_effect=spawn
            ):
                task = asyncio.create_task(rendering.render_source(image_bytes()))
                await started.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertIsNotNone(processes[-1].returncode)
            self.assertFalse(roots[-1].exists())
        self.assertEqual(len((await rendering.render_source(image_bytes()))["images"]), 1)

    async def test_spawn_race_cancellation_reaps_child(self):
        real_spawn = asyncio.create_subprocess_exec
        started, release = asyncio.Event(), asyncio.Event()
        processes, roots = [], []

        async def delayed_spawn(*args, **kwargs):
            process = await real_spawn(*args, **kwargs)
            processes.append(process)
            roots.append(Path(kwargs["cwd"]))
            started.set()
            await release.wait()
            return process

        with patch.object(rendering.asyncio, "create_subprocess_exec", side_effect=delayed_spawn):
            task = asyncio.create_task(rendering.render_source(image_bytes()))
            await started.wait()
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(processes[0].returncode)
        self.assertFalse(roots[0].exists())

    async def test_failed_spawn_preserves_cancellation_and_releases_capacity(self):
        started, release = asyncio.Event(), asyncio.Event()
        roots = []

        async def failed_spawn(*_args, **kwargs):
            roots.append(Path(kwargs["cwd"]))
            started.set()
            await release.wait()
            raise OSError("simulated process creation failure")

        with patch.object(rendering.asyncio, "create_subprocess_exec", side_effect=failed_spawn):
            task = asyncio.create_task(rendering.render_source(image_bytes()))
            await started.wait()
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(all(not root.exists() for root in roots))
        self.assertEqual(len((await rendering.render_source(image_bytes()))["images"]), 1)

    async def test_two_worker_concurrency_and_waiting_cancellation(self):
        real_spawn = asyncio.create_subprocess_exec
        two_started, release = asyncio.Event(), asyncio.Event()
        processes = []

        async def held_spawn(*args, **kwargs):
            process = await real_spawn(*args, **kwargs)
            processes.append(process)
            if len(processes) == 2:
                two_started.set()
            await release.wait()
            return process

        with patch.object(rendering.asyncio, "create_subprocess_exec", side_effect=held_spawn):
            tasks = [asyncio.create_task(rendering.render_source(image_bytes())) for _ in range(3)]
            try:
                await asyncio.wait_for(two_started.wait(), 2)
                await asyncio.sleep(0.02)
                self.assertEqual(len(processes), 2)
                tasks[2].cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await tasks[2]
                release.set()
                await asyncio.gather(*tasks[:2])
            finally:
                release.set()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        self.assertTrue(all(process.returncode == 0 for process in processes))
        self.assertEqual(len((await rendering.render_source(image_bytes()))["images"]), 1)

    async def test_queue_is_bounded_and_timeout_does_not_release_an_unowned_slot(self):
        capacity = rendering._Capacity()
        await capacity.acquire()
        await capacity.acquire()
        with patch.object(rendering, "_capacity", return_value=capacity), patch.object(rendering, "RENDER_TIMEOUT", 0.03):
            with self.assertRaisesRegex(ValueError, "render_timeout"):
                await rendering.render_source(image_bytes())
            self.assertEqual(capacity.waiting, 0)
            self.assertTrue(capacity.slots.locked())
            capacity.waiting = rendering.MAX_WAITING_RENDERS
            with self.assertRaisesRegex(ValueError, "render_queue_full"):
                await rendering.render_source(image_bytes())
        capacity.slots.release()
        capacity.slots.release()

    async def test_worker_protocol_rejects_escaped_path_and_oversized_stdout(self):
        bodies = [
            'import json\nprint(json.dumps({"format":"png","page_count":None,"images":[{"path":"../outside.png"}],"errors":[]}))\n',
            'print("x" * 100000)\n',
            'print("[" * 2000 + "0" + "]" * 2000)\n',
        ]
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-view-image-protocol-") as folder:
            worker = Path(folder) / "worker.py"
            for body in bodies:
                worker.write_text(body)
                with self.subTest(body=body[:20]), patch.object(rendering, "_WORKER", worker), self.assertRaisesRegex(ValueError, "worker_protocol_error"):
                    await rendering.render_source(image_bytes())

    async def test_worker_cannot_return_symlinked_files_or_false_digest(self):
        data = image_bytes()
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-view-image-protocol-") as folder:
            outside = Path(folder) / "outside.png"
            outside.write_bytes(data)
            outside.chmod(0o600)
            worker = Path(folder) / "worker.py"
            item = {"page": None, "original_width": 80, "original_height": 50, "width": 80, "height": 50,
                    "media_type": "image/png", "sha256": hashlib.sha256(data).hexdigest(),
                    "scaled": False, "gif_first_frame": False, "path": "image-0.png", "size": len(data)}
            for kind in ("symlink", "digest", "traversal"):
                manifest = {"format": "png", "page_count": None, "images": [{**item}], "errors": []}
                if kind == "symlink":
                    write = f"os.symlink({str(outside)!r}, 'outputs/image-0.png')"
                else:
                    write = f"from pathlib import Path\np = Path('outputs/image-0.png'); p.write_bytes({data!r}); p.chmod(0o600)"
                    if kind == "digest":
                        manifest["images"][0]["sha256"] = "0" * 64
                    else:
                        manifest["images"][0]["path"] = "../outside.png"
                worker.write_text(f"import os,json\n{write}\nprint(json.dumps({manifest!r}))\n")
                with self.subTest(kind=kind), patch.object(rendering, "_WORKER", worker), self.assertRaisesRegex(ValueError, "worker_protocol_error"):
                    await rendering.render_source(data)

    async def test_worker_fifo_is_rejected_without_blocking_the_event_loop(self):
        item = {"page": None, "original_width": 1, "original_height": 1, "width": 1, "height": 1,
                "media_type": "image/png", "sha256": "0" * 64, "scaled": False, "gif_first_frame": False,
                "path": "image-0.png", "size": 8}
        manifest = {"format": "png", "page_count": None, "images": [item], "errors": []}
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-view-image-fifo-") as folder:
            worker = Path(folder) / "worker.py"
            worker.write_text(f"import json,os\nos.mkfifo('outputs/image-0.png', 0o600)\nprint(json.dumps({manifest!r}))\n")
            probe = (
                "import asyncio,sys\n"
                "from pathlib import Path\n"
                "from deep_agent.files.image_view import rendering\n"
                "rendering._WORKER = Path(sys.argv[1])\n"
                "try:\n"
                "    asyncio.run(rendering.render_source(b'\\x89PNG\\r\\n\\x1a\\n'))\n"
                "except rendering.ImageRenderError as exc:\n"
                "    print(exc.code)\n"
            )
            # A regression blocks a synchronous open, so asyncio.timeout alone
            # cannot protect the test. The external watchdog kills and reaps it.
            completed = await asyncio.to_thread(
                subprocess.run, [sys.executable, "-c", probe, str(worker)],
                env={"PYTHONPATH": str(Path(rendering.__file__).resolve().parents[3]), "TMPDIR": folder},
                capture_output=True, text=True, timeout=5, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "worker_protocol_error")
            self.assertFalse(list(Path(folder).glob("TMP_to_delete-view-image-*")))

    async def test_all_requested_invalid_pages_are_returned_as_explicit_errors(self):
        result = await rendering.render_source(pdf_bytes(1), [2, 3])
        self.assertEqual(result["images"], [])
        self.assertEqual(result["errors"], [{"page": 2, "error": "page_out_of_range"}, {"page": 3, "error": "page_out_of_range"}])


if __name__ == "__main__":
    unittest.main()
