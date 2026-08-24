from __future__ import annotations

from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, Field, TypeAdapter, field_validator, model_validator


_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


class LlmProviderCreate(BaseModel):
    name: str = Field(..., max_length=191)
    provider_type: str = Field(..., max_length=64)
    use_default_base_url: bool = False
    base_url: str | None = None
    api_key: str | None = None
    enabled: bool = True

    @field_validator("name", "provider_type")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return str(_HTTP_URL_ADAPTER.validate_python(value.strip())).rstrip("/")

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class LlmProviderUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=191)
    provider_type: str | None = Field(default=None, max_length=64)
    use_default_base_url: bool | None = None
    base_url: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    enabled: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_required_fields(cls, value):
        if isinstance(value, dict):
            for field in ("name", "provider_type", "use_default_base_url", "enabled"):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
            if value.get("clear_api_key") and value.get("api_key") is not None:
                raise ValueError("api_key and clear_api_key cannot be used together")
            if "api_key" in value and value["api_key"] is None:
                raise ValueError("api_key cannot be null; use clear_api_key to clear it")
            if (
                "api_key" in value
                and isinstance(value["api_key"], str)
                and not value["api_key"].strip()
            ):
                raise ValueError("api_key cannot be blank; use clear_api_key to clear it")
        return value

    @field_validator("name", "provider_type")
    @classmethod
    def normalize_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return str(_HTTP_URL_ADAPTER.validate_python(value.strip())).rstrip("/")

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: str | None) -> str | None:
        return (value.strip() or None) if value is not None else None


class LlmProviderSummary(BaseModel):
    id: str
    name: str
    provider_type: str
    enabled: bool
    api_key_set: bool

    @classmethod
    def from_row(cls, row) -> "LlmProviderSummary":
        return cls(
            id=row.id,
            name=row.name,
            provider_type=row.provider_type,
            enabled=row.enabled,
            api_key_set=bool(row.api_key),
        )


class LlmProviderResponse(LlmProviderSummary):
    provider_type_label: str
    use_default_base_url: bool
    base_url: str
    api_key_set: bool
    model_count: int = 0
    created_at: datetime
    updated_at: datetime


class LlmProviderTypeItem(BaseModel):
    code: str
    label: str
    order: int
    default_base_url: str | None
    supports_default_base_url: bool
    supports_custom_base_url: bool
    model_protocol: str


class LlmProviderTypesResponse(BaseModel):
    items: list[LlmProviderTypeItem]
