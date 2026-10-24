from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from pydantic_ai.messages import BinaryContent, ImageUrl, ToolReturnPart


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


def web_image_tool_value(value: Any, metadata: Any = None) -> Any:
    """Project only a web_image_view result; never serialize its live bytes."""
    if not isinstance(value, BinaryContent):
        return safe_web_image_value(value)
    metadata = metadata if isinstance(metadata, Mapping) else {}
    audit = metadata.get("web_image_view")
    audit = audit if isinstance(audit, Mapping) else {}
    sources = metadata.get("sources")
    source = sources[0] if isinstance(sources, list) and sources else None
    url = source.get("url") if isinstance(source, Mapping) else None
    reference = {
        "kind": WEB_IMAGE_REFERENCE_KIND,
        "media_type": value.media_type,
        "force_download": bool(audit.get("force_download", False)),
        "identifier": value.identifier,
    }
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        reference["url"] = url
    return reference


def persistent_web_image_part(part: Any) -> Any:
    """Make a persistence-only copy, retaining the existing SDK URL format."""
    if not (isinstance(part, ToolReturnPart) and part.tool_name == "web_image_view"
            and isinstance(part.content, BinaryContent)):
        return part
    reference = web_image_tool_value(part.content, part.metadata)
    url = reference.get("url")
    content = ImageUrl(
        url=url, media_type=part.content.media_type,
        force_download=reference["force_download"], identifier=part.content.identifier,
    ) if url else reference
    return replace(part, content=content)


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
