from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from deep_agent.integrations.catalog import IntegrationCode

MAX_SETTINGS_BYTES = 65536
MAX_SETTINGS_DEPTH = 8
MAX_SETTINGS_NODES = 2048


def validate_integration_settings(value: Any) -> dict:
    """Validate bounded JSON without coercing numbers, booleans or nested values."""
    if type(value) is not dict:
        raise ValueError("配置必须是 JSON 对象")
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_SETTINGS_NODES or depth > MAX_SETTINGS_DEPTH:
            raise ValueError("配置最多 8 层、2048 个值")
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str or not key.strip() or len(key) > 128:
                    raise ValueError("配置键必须是非空字符串，且不超过 128 个字符")
                pending.append((child, depth + 1))
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (str, bool, int, float):
            raise ValueError("配置只允许 JSON 值")
        elif type(item) is float and not math.isfinite(item):
            raise ValueError("配置数字必须是有限值")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (UnicodeError, ValueError, OverflowError) as exc:
        raise ValueError("配置包含无效 JSON 值") from exc
    if len(encoded) > MAX_SETTINGS_BYTES:
        raise ValueError("配置不能超过 64 KiB")
    return value


class IntegrationSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: dict[str, Any] = Field(description="通用 JSON 键值配置；空对象清除已保存配置。")

    @field_validator("settings", mode="before")
    @classmethod
    def validate_settings(cls, value: Any) -> dict:
        return validate_integration_settings(value)


class IntegrationItem(BaseModel):
    code: IntegrationCode
    name: str
    settings: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class IntegrationGroup(BaseModel):
    code: str
    name: str
    description: str
    icon: str
    integrations: list[IntegrationItem]


class IntegrationCatalog(BaseModel):
    groups: list[IntegrationGroup]
