from __future__ import annotations

from typing import Any, Mapping

from pydantic_ai.messages import ImageUrl


WEB_IMAGE_REFERENCE_KIND = "web-image-reference"


def web_image_reference(value: ImageUrl) -> dict[str, Any]:
    """Return the safe, persistence-friendly projection of a web image input."""
    return {
        "kind": WEB_IMAGE_REFERENCE_KIND,
        "url": value.url,
        "media_type": value.media_type,
        "force_download": value.force_download,
        "identifier": value.identifier,
    }


def safe_web_image_value(value: Any) -> Any:
    """Project ImageUrl objects without downloading or embedding image bytes."""
    if isinstance(value, ImageUrl):
        return web_image_reference(value)
    if isinstance(value, Mapping):
        if value.get("kind") == "image-url" and isinstance(value.get("url"), str):
            url = str(value["url"])
            if url.startswith(("http://", "https://")):
                return {
                    "kind": WEB_IMAGE_REFERENCE_KIND,
                    "url": url,
                    "media_type": value.get("media_type"),
                    "force_download": value.get("force_download", False),
                    "identifier": value.get("identifier"),
                }
        return {
            str(key): safe_web_image_value(child)
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [safe_web_image_value(child) for child in value]
    return value


def prior_run_web_image_value(value: Any) -> Any:
    """Replace prior-run visual content with a non-multimodal URL reference."""
    projected = safe_web_image_value(value)
    if isinstance(value, ImageUrl) and isinstance(projected, dict):
        return {
            **projected,
            "note": "Web image viewed in a previous run; call web_image_view to inspect it again.",
        }
    return projected
