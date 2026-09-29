"""Validated structured channel content; never includes routing or credentials."""
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", check_fields=False)
    @classmethod
    def validate_string(cls, value, info):
        if not isinstance(value, str):
            return value
        limits = {"title": 128, "description": 512, "url": 2048, "picurl": 2048, "btntxt": 16}
        if len(value.encode("utf-8")) > limits.get(info.field_name, 2048):
            raise ValueError("内容超过字段字节限制")
        if info.field_name in {"url", "picurl"}:
            parsed = urlsplit(value)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or any(c.isspace() or ord(c) < 32 for c in value)):
                raise ValueError("链接须为不含凭据的http(s)地址")
            try:
                parsed.port
            except ValueError:
                raise ValueError("链接端口无效") from None
        elif info.field_name in {"title", "btntxt"} and not value.strip():
            raise ValueError("标题和按钮文字不能为空")
        return value


class TextCardPayload(_Payload):
    title: str
    description: str = Field(min_length=1)
    url: str
    btntxt: str | None = None

    @field_validator("description")
    @classmethod
    def description_limit(cls, value):
        # Shared 512-byte bound is intentionally conservative across card types.
        if not value.strip():
            raise ValueError("描述不能为空")
        return value


class NewsArticle(_Payload):
    title: str
    description: str = ""
    url: str
    picurl: str | None = None


class NewsPayload(_Payload):
    articles: list[NewsArticle] = Field(min_length=1, max_length=8)


def validate_message_payload(kind: str, payload: dict | BaseModel | None) -> dict:
    model = {"textcard": TextCardPayload, "news": NewsPayload}.get(kind)
    if model is None:
        raise ValueError("此消息类型不支持结构化内容")
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(exclude_none=True)
    return model.model_validate(payload).model_dump(exclude_none=True)
