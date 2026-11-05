"""Versioned, authenticated developer downloads (not public Console assets)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deep_agent import __version__

ROOT = Path(__file__).resolve().parent
GENERATED_ROOT = ROOT / "generated"
PDF_PATH = ROOT.parents[2] / "console" / "downloads" / "spreadsheet-initialization.pdf"
RESOURCE_CODES = ("initialization-guide", "development-skill")


class ResourceUnavailableError(Exception):
    """A shipped resource is missing, stale or incomplete."""


@dataclass(frozen=True)
class DeveloperResource:
    metadata: dict[str, Any]
    content: bytes


def read_resource(code: str) -> DeveloperResource:
    """Read only the two product-owned files; never interpret code as a path."""
    if code not in RESOURCE_CODES:
        raise KeyError(code)
    try:
        manifest = json.loads((GENERATED_ROOT / "manifest.json").read_text(encoding="utf-8"))
        if manifest["version"] != __version__:
            raise ValueError("Developer resources need rebuilding for this release")
        item = next(item for item in manifest["items"] if item["code"] == code)
        path = PDF_PATH if code == "initialization-guide" else GENERATED_ROOT / "deepagent-agents-dev.zip"
        content = path.read_bytes()
        if len(content) != item["size_bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("Resource digest mismatch")
        return DeveloperResource(metadata=dict(item), content=content)
    except (OSError, ValueError, KeyError, StopIteration, TypeError) as exc:
        # No deployment paths, credentials or filesystem exception details in the API.
        raise ResourceUnavailableError("开发资料暂不可用，请联系管理员检查发行文件") from exc


def list_resources() -> list[dict[str, Any]]:
    return [read_resource(code).metadata for code in RESOURCE_CODES]
