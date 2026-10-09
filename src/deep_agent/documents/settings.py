from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal, Mapping

from .models import DocumentProvider

DOCUMENT_PARSING_SETTING_KEY = "document_parsing"
DOCUMENT_PARSING_SCHEMA_VERSION = 1
DOCUMENT_PACKAGE_CODE = "builtin:document"


class DocumentParsingConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class DocumentParsingRuntimeConfig:
    mode: Literal["unconfigured", "provider"]
    provider: DocumentProvider | None = None
    api_key: str | None = field(default=None, repr=False)
    configuration_error: str | None = None

    @property
    def model(self) -> str | None:
        from .providers import PROVIDERS
        return PROVIDERS[self.provider].model if self.provider in PROVIDERS else None

    def to_stored_value(self) -> dict[str, Any]:
        if self.mode == "unconfigured":
            return {}
        return {"mode": self.mode, "provider": self.provider, "api_key": self.api_key}


def resolve_document_parsing_config(value: Mapping[str, Any] | None, *, existing: DocumentParsingRuntimeConfig | None = None,
                                    require_credentials: bool = True) -> DocumentParsingRuntimeConfig:
    if value is not None and not isinstance(value, Mapping):
        raise DocumentParsingConfigurationError("文档解析配置必须为对象")
    raw = dict(value or {})
    if not raw or raw.get("mode") == "unconfigured":
        if raw.get("provider") or raw.get("api_key"):
            raise DocumentParsingConfigurationError("清空配置时不能携带供应商或凭据")
        return DocumentParsingRuntimeConfig("unconfigured")
    if raw.get("mode") != "provider" or raw.get("provider") not in {"paddle", "glm"}:
        raise DocumentParsingConfigurationError("请选择有效的文档解析供应商")
    provider = raw["provider"]
    key = str(raw.get("api_key") or "").strip()
    if key and ("*" in key or "•" in key or "…" in key):
        raise DocumentParsingConfigurationError("请填写真实 API Key，不能提交脱敏占位值")
    if not key and existing is not None and existing.provider == provider and existing.mode == "provider":
        key = existing.api_key or ""
    if require_credentials and not key:
        raise DocumentParsingConfigurationError("该供应商的 API Key 为必填项")
    return DocumentParsingRuntimeConfig("provider", provider, key or None)


async def load_document_parsing_config(store: Any) -> DocumentParsingRuntimeConfig:
    if store is None:
        return DocumentParsingRuntimeConfig("unconfigured")
    return read_document_parsing_config(await store.get(DOCUMENT_PARSING_SETTING_KEY))


def read_document_parsing_config(row: Any) -> DocumentParsingRuntimeConfig:
    config = DocumentParsingRuntimeConfig("unconfigured")
    if row is None:
        return config
    try:
        if row.version != DOCUMENT_PARSING_SCHEMA_VERSION:
            raise DocumentParsingConfigurationError("不支持的文档解析配置版本")
        config = resolve_document_parsing_config(row.value, require_credentials=False)
        return resolve_document_parsing_config(row.value)
    except DocumentParsingConfigurationError as exc:
        return replace(config, configuration_error=str(exc))
