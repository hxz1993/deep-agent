"""Upload-time policy. No startup cache, event snapshots or automatic writes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

UPLOAD_SETTING_KEY = "attachment_upload"
UPLOAD_SCHEMA_VERSION = 1
MAX_ATTACHMENT_BYTES = 200 * 1024 * 1024


class UploadSettingsError(ValueError):
    pass


@dataclass(frozen=True)
class UploadSettings:
    max_size_mb: int = 20
    max_count: int = 10

    @property
    def max_bytes(self) -> int:
        return self.max_size_mb * 1024 * 1024

    def to_stored_value(self) -> dict[str, int]:
        return {"max_size_mb": self.max_size_mb, "max_count": self.max_count}


def read_upload_settings(row: Any) -> UploadSettings:
    if row is None:
        return UploadSettings()
    if row.version != UPLOAD_SCHEMA_VERSION or not isinstance(row.value, dict):
        raise UploadSettingsError("附件上传配置格式无效")
    values = {}
    for name, default, minimum, maximum in (("max_size_mb", 20, 1, 200), ("max_count", 10, 1, 20)):
        value = row.value.get(name, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise UploadSettingsError(f"附件上传配置 {name} 无效")
        values[name] = value
    return UploadSettings(**values)


async def load_upload_settings(store: Any) -> UploadSettings:
    if store is None:
        raise UploadSettingsError("附件上传配置存储不可用")
    return read_upload_settings(await store.get(UPLOAD_SETTING_KEY))


class DefaultUploadSettingsStore:
    """Empty configuration for tools embedded without an application database.

    HTTP entry points explicitly replace this with their real store (including
    None when unavailable), so a database failure can never select this fallback.
    """

    async def get(self, key: str):
        return None


async def load_upload_settings_for_tool(ctx: Any) -> UploadSettings:
    deps = getattr(ctx, "deps", ctx)
    return await load_upload_settings(deps.upload_settings_store)
