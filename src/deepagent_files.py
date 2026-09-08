"""File helpers exposed inside one DeepAgent Python sandbox execution.

The host creates the manifest and collects explicitly published files after the
worker succeeds. This module never contains platform credentials and cannot
upload or download files by itself.
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any

_MANIFEST_NAME = ".deepagent-files.json"
_OUTPUT_DIR = "outputs"
_published: dict[str, dict[str, str | None]] = {}


def _root() -> Path:
    return Path.cwd().resolve()


def _manifest() -> dict[str, Any]:
    path = _root() / _MANIFEST_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("sandbox file manifest is unavailable") from exc
    if not isinstance(value, dict):
        raise RuntimeError("sandbox file manifest is invalid")
    return value


def input_path(file_id: str) -> str:
    """Return the temporary path for an authorized input ``file_id``."""
    normalized = str(file_id).strip()
    inputs = _manifest().get("inputs")
    relative = inputs.get(normalized) if isinstance(inputs, dict) else None
    if not isinstance(relative, str) or not relative:
        raise KeyError(f"file_id is not available in this execution: {normalized}")
    target = (_root() / relative).resolve()
    root = _root()
    if root not in target.parents or not target.is_file():
        raise RuntimeError("sandbox input path is unavailable")
    return str(Path(relative))


def publish_path(file_name: str, mime: str | None = None) -> str:
    """Register one output and return its writable temporary path.

    Only files registered here are collected and uploaded by the host after a
    successful execution. Calling this function does not upload immediately.
    """
    normalized = str(file_name).replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or path.is_absolute()
        or ".." in path.parts
        or len(path.parts) != 1
        or path.name in {".", ".."}
    ):
        raise ValueError("published file name must be a safe base name")
    target = (_root() / _OUTPUT_DIR / path.name).resolve()
    output_root = (_root() / _OUTPUT_DIR).resolve()
    if output_root not in target.parents:
        raise ValueError("published file escaped the output directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized_mime = str(mime).strip() if mime is not None else None
    if not normalized_mime:
        normalized_mime = mimetypes.guess_type(path.name)[0]
    _published[path.name] = {
        "file_name": path.name,
        "mime": normalized_mime,
        "path": f"{_OUTPUT_DIR}/{path.name}",
    }
    return f"{_OUTPUT_DIR}/{path.name}"


def collect_published_files() -> list[dict[str, str | None]]:
    """Worker-only manifest collection with final existence checks."""
    root = _root()
    output_root = (root / _OUTPUT_DIR).resolve()
    collected: list[dict[str, str | None]] = []
    for item in _published.values():
        relative = item["path"]
        assert isinstance(relative, str)
        target = (root / relative).resolve()
        if output_root not in target.parents or not target.is_file():
            raise FileNotFoundError(
                f"published output was not created: {item['file_name']}"
            )
        collected.append(dict(item))
    return collected
