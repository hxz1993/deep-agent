from __future__ import annotations

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from deep_agent.time import UtcDatetime


_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


def mask_api_key(value: str | None) -> str | None:
    """Return a stable identification hint without exposing the stored secret."""
    secret = str(value or "").strip()
    if not secret:
        return None
    if len(secret) <= 4:
        return "*" * max(4, len(secret))
    if len(secret) <= 8:
        prefix_length, suffix_length = 1, 2
    elif len(secret) <= 16:
        prefix_length, suffix_length = 3, 3
    else:
        prefix_length, suffix_length = 6, 4
    return f"{secret[:prefix_length]}********{secret[-suffix_length:]}"


class LlmProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., max_length=191)
    provider_type: str = Field(..., max_length=64)
    use_default_base_url: bool = False
    base_url: str | None = None
    api_key: str | None = None
    authorization_flow_id: str | None = Field(default=None, exclude=True)

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
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=191)
    provider_type: str | None = Field(default=None, max_length=64)
    use_default_base_url: bool | None = None
    base_url: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    authorization_flow_id: str | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def reject_null_required_fields(cls, value):
        if isinstance(value, dict):
            for field in ("name", "provider_type", "use_default_base_url"):
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
    api_key_set: bool
    auth_method: str = "api_key"
    auth_status: str = "unconfigured"
    authenticated: bool = False

    @classmethod
    def from_row(cls, row) -> "LlmProviderSummary":
        return cls(
            id=row.id,
            name=row.name,
            provider_type=row.provider_type,
            api_key_set=bool(row.api_key),
            auth_method=getattr(row, "auth_method", "api_key"),
            auth_status=getattr(row, "auth_status", "unconfigured"),
            authenticated=(bool(row.credential_json) and row.auth_status == "connected") if getattr(row, "auth_method", "api_key") == "oauth" else bool(row.api_key),
        )


class LlmProviderResponse(LlmProviderSummary):
    provider_type_label: str
    use_default_base_url: bool
    base_url: str
    api_key_set: bool
    api_key_masked: str | None = None
    account_hint: str | None = None
    model_count: int = 0
    created_at: UtcDatetime
    updated_at: UtcDatetime


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
