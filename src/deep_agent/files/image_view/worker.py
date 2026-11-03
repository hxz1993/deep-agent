"""One source per process: PDFium must never run on concurrent threads."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
import warnings


def _restrict_memory() -> None:
    # Apply before importing Pillow/PDFium. macOS finite RLIMIT_AS is not a
    # supported hard-memory boundary and must not be described as one.
    if sys.platform.startswith("linux"):
        import resource

        maximum = 512 * 1024 * 1024
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            maximum = min(maximum, hard)
        resource.setrlimit(resource.RLIMIT_AS, (maximum, maximum))


_restrict_memory()
# -I deliberately ignores host PYTHONPATH. Use this exact installed source tree,
# without importing the API application or forwarding any environment secrets.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from PIL import Image, UnidentifiedImageError  # noqa: E402

from deep_agent.files.image_view.limits import (  # noqa: E402
    MAX_IMAGE_PIXELS, MAX_IMAGE_SOURCE_BYTES, MAX_OUTPUT_EDGE,
    MAX_OUTPUT_PIXELS, MAX_PDF_SOURCE_BYTES, MAX_PROTOCOL_BYTES,
    MAX_SOURCE_IMAGES, PDF_DPI,
)
from deep_agent.files.image_view.normalization import normalize_image, target_size  # noqa: E402


class _SourceError(ValueError):
    pass


def _save_image(output: Path, index: int, item: dict, page: int | None) -> dict:
    data = item.pop("data")
    name = f"image-{index}.{'png' if item['media_type'] == 'image/png' else 'jpeg'}"
    descriptor = os.open(output / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
    return {"page": page, **item, "path": name, "size": len(data)}


def _render_image(source: Path, output: Path, fmt: str) -> dict:
    if source.stat().st_size > MAX_IMAGE_SOURCE_BYTES:
        raise _SourceError("source_size_limit")
    expected = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP", "gif": "GIF"}[fmt]
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as probe:
                if probe.format != expected:
                    raise _SourceError("invalid_image")
                if probe.width * probe.height > MAX_IMAGE_PIXELS:
                    raise _SourceError("pixel_limit")
                probe.verify()
            with Image.open(source) as image:
                image.seek(0)
                item = normalize_image(image, gif_first_frame=fmt == "gif")
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise _SourceError("pixel_limit") from exc
    except MemoryError as exc:
        raise _SourceError("memory_limit") from exc
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise _SourceError("invalid_image") from exc
    return {"format": fmt, "page_count": None, "images": [_save_image(output, 0, item, None)], "errors": []}


def _render_pdf(source: Path, output: Path, pages: list[int]) -> dict:
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    if source.stat().st_size > MAX_PDF_SOURCE_BYTES:
        raise _SourceError("source_size_limit")
    try:
        document = pdfium.PdfDocument(source)
    except pdfium.PdfiumError as exc:
        raise _SourceError("encrypted_pdf" if exc.err_code == raw.FPDF_ERR_PASSWORD else "invalid_pdf") from exc
    try:
        # An encrypted PDF with an empty user password still opens normally.
        if raw.FPDF_GetSecurityHandlerRevision(document) >= 0:
            raise _SourceError("encrypted_pdf")
        form_type = document.get_formtype()
        if form_type in (raw.FORMTYPE_XFA_FULL, raw.FORMTYPE_XFA_FOREGROUND):
            raise _SourceError("unsupported_pdf_form")
        if form_type == raw.FORMTYPE_ACRO_FORM:
            # Render static AcroForm values without providing JavaScript, XFA,
            # network, UI, or timer callbacks to the form environment.
            document.init_forms(raw.FPDF_FORMFILLINFO(version=2, xfa_disabled=True))
        result = {"format": "pdf", "page_count": len(document), "images": [], "errors": []}
        for index, number in enumerate(pages):
            if number > result["page_count"]:
                result["errors"].append({"page": number, "error": "page_out_of_range"})
                continue
            page = None
            bitmap = None
            image = None
            try:
                page = document[number - 1]
                width, height = page.get_size()
                if not all(math.isfinite(n) and 0 < n <= 1_000_000_000 for n in (width, height)):
                    raise ValueError("invalid_page_dimensions")
                original = (math.ceil(width * PDF_DPI / 72), math.ceil(height * PDF_DPI / 72))
                goal_width, goal_height = target_size(*original)
                # Bound before allocating a bitmap, including ceil() rounding.
                scale = min(PDF_DPI / 72, goal_width / width, goal_height / height)
                while (math.ceil(width * scale) > MAX_OUTPUT_EDGE or math.ceil(height * scale) > MAX_OUTPUT_EDGE
                       or math.ceil(width * scale) * math.ceil(height * scale) > MAX_OUTPUT_PIXELS):
                    scale = math.nextafter(scale, 0.0)
                bitmap = page.render(
                    scale=scale, rev_byteorder=True, fill_color=(255, 255, 255, 255),
                    limit_image_cache=True, may_draw_forms=True,
                )
                image = bitmap.to_pil()
                # The bitmap stays alive through encoding: to_pil can share its
                # underlying buffer. Never close the bitmap before the image.
                item = normalize_image(image, original_size=original)
                result["images"].append(_save_image(output, index, item, number))
            except MemoryError:
                result["errors"].append({"page": number, "error": "memory_limit"})
            except (ValueError, OSError, pdfium.PdfiumError):
                result["errors"].append({"page": number, "error": "page_render_failed"})
            finally:
                if image is not None:
                    image.close()
                if bitmap is not None:
                    bitmap.close()
                if page is not None:
                    page.close()
        return result
    finally:
        document.close()


def main() -> None:
    payload = sys.stdin.buffer.read(MAX_PROTOCOL_BYTES + 1)
    if len(payload) > MAX_PROTOCOL_BYTES:
        raise _SourceError("invalid_request")
    request = json.loads(payload)
    if set(request) != {"format", "pages"}:
        raise _SourceError("invalid_request")
    fmt, pages = request["format"], request["pages"]
    if fmt not in {"pdf", "png", "jpeg", "webp", "gif"}:
        raise _SourceError("invalid_request")
    if fmt == "pdf":
        if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_SOURCE_IMAGES or any(type(n) is not int or n < 1 for n in pages):
            raise _SourceError("invalid_request")
        result = _render_pdf(Path("source.bin"), Path("outputs"), pages)
    else:
        if pages is not None:
            raise _SourceError("invalid_request")
        result = _render_image(Path("source.bin"), Path("outputs"), fmt)
    print(json.dumps(result, ensure_ascii=True, allow_nan=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except _SourceError as exc:
        print(json.dumps({"error": str(exc)}), flush=True)
    except MemoryError:
        print('{"error":"memory_limit"}', flush=True)
    except Exception:
        # Never echo a filename, library error, or source content to stdout.
        print('{"error":"worker_failed"}', flush=True)
