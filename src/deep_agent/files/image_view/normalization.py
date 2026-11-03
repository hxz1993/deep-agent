"""Image decoding and bounded encoding, imported only in render workers."""
from __future__ import annotations

import hashlib
import io
import math

from PIL import Image, ImageOps

from deep_agent.files.image_view.limits import (
    MAX_OUTPUT_BYTES, MAX_OUTPUT_EDGE, MAX_OUTPUT_PIXELS,
)


class _EncodingTooLarge(ValueError):
    pass


class _BoundedBuffer(io.BytesIO):
    def write(self, value):
        if self.tell() + len(value) > MAX_OUTPUT_BYTES:
            raise _EncodingTooLarge("output_size_limit")
        return super().write(value)


def target_size(width: int, height: int) -> tuple[int, int]:
    scale = min(1.0, MAX_OUTPUT_EDGE / max(width, height), math.sqrt(MAX_OUTPUT_PIXELS / (width * height)))
    return max(1, math.floor(width * scale)), max(1, math.floor(height * scale))


def _encode(image: Image.Image, fmt: str) -> bytes:
    with _BoundedBuffer() as stream:
        image.save(stream, format=fmt, **({"quality": 90} if fmt == "JPEG" else {}))
        return stream.getvalue()


def _png_gray16_to_8(image: Image.Image) -> Image.Image:
    # Pillow's plain RGB conversion clamps 16-bit samples at 255. Use the
    # full fixed input range, not the darkest/lightest values in this image.
    gray_lut = [(sample + 128) // 257 for sample in range(65536)]
    transparent = image.info.get("transparency")
    alpha_lut = None
    if type(transparent) is int and 0 <= transparent < 65536:
        alpha_lut = [255] * 65536
        alpha_lut[transparent] = 0
    result = Image.new("L" if alpha_lut is None else "LA", image.size)
    tile_width = min(image.width, 262144)
    tile_height = 262144 // tile_width
    try:
        # Mode I handles either byte order. Convert one tile at a time so a
        # full-sized 32-bit intermediate cannot exhaust the render worker.
        for y in range(0, image.height, tile_height):
            for x in range(0, image.width, tile_width):
                box = (x, y, min(image.width, x + tile_width), min(image.height, y + tile_height))
                with image.crop(box) as tile, tile.convert("I") as samples, samples.point(gray_lut, "L") as gray:
                    if alpha_lut is not None:
                        with samples.point(alpha_lut, "L") as alpha:
                            gray.putalpha(alpha)
                    result.paste(gray, (x, y))
        result.info = image.info.copy()
        result.info.pop("transparency", None)  # already resolved against 16-bit samples
        return result
    except BaseException:
        result.close()
        raise


def normalize_image(
    image: Image.Image, *, original_size: tuple[int, int] | None = None,
    gif_first_frame: bool = False,
) -> dict:
    """Normalize orientation, strip metadata, prefer PNG, and bound encoded bytes.

    The caller owns ``image``. Working copies are all closed here. Resizing
    before EXIF transpose avoids duplicating a full 64-megapixel decoded image.
    Orientation does not affect either the long edge or the pixel budget.
    """
    original_width, original_height = original_size or image.size
    orientation = image.getexif().get(274, 1)
    oriented_size = (original_height, original_width) if orientation in (5, 6, 7, 8) else (original_width, original_height)
    size = target_size(*image.size)
    source = (_png_gray16_to_8(image) if image.format == "PNG"
              and image.mode in {"I", "I;16", "I;16L", "I;16B", "I;16N"} else image)
    try:
        # Pillow's thumbnail uses decoder-level reduction for JPEG when available.
        source.thumbnail(size, Image.Resampling.LANCZOS, reducing_gap=3.0)
        working = ImageOps.exif_transpose(source)
    finally:
        if source is not image:
            source.close()
    try:
        mode = "RGBA" if working.mode in ("RGBA", "LA") or "transparency" in working.info else "RGB"
        converted = working.convert(mode)
        working.close()
        working = converted
        # Do not carry EXIF, ICC, text chunks, or source metadata into output.
        working.info.clear()
        try:
            data = _encode(working, "PNG")
            media_type = "image/png"
        except _EncodingTooLarge:
            if working.mode == "RGBA":
                flattened = Image.new("RGB", working.size, "white")
                mask = working.getchannel("A")
                try:
                    flattened.paste(working, mask=mask)
                finally:
                    mask.close()
                working.close()
                working = flattened
            for _ in range(32):
                try:
                    data = _encode(working, "JPEG")
                    media_type = "image/jpeg"
                    break
                except _EncodingTooLarge:
                    if working.size == (1, 1):
                        raise
                    resized = working.resize(
                        (max(1, math.floor(working.width * 0.8)), max(1, math.floor(working.height * 0.8))),
                        Image.Resampling.LANCZOS,
                    )
                    working.close()
                    working = resized
            else:
                raise ValueError("output_size_limit")
        return {
            "original_width": original_width,
            "original_height": original_height,
            "width": working.width,
            "height": working.height,
            "media_type": media_type,
            "sha256": hashlib.sha256(data).hexdigest(),
            "data": data,
            "scaled": working.size != oriented_size,
            "gif_first_frame": gif_first_frame,
        }
    finally:
        working.close()
