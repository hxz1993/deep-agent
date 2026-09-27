from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from deep_agent.time import UtcDatetime
from deep_agent.channels.app_context import channel_deepfos_app, validate_channel_settings


ChannelRuntimeState = Literal[
    "not_deployed",
    "unreachable",
    "closed",
    "connecting",
    "connected",
    "reconnecting",
    "conflict",
    "failed",
]


class FeishuRegistrationCreate(BaseModel):
    app_name: str = Field(default="", max_length=191)

    @field_validator("app_name", mode="before")
    @classmethod
    def trim_app_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class WecomRegistrationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChannelAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., max_length=191)
    channel_type: Literal["wecom", "feishu"] = "wecom"
    transport: Literal["websocket"] = "websocket"
    bot_id: str = Field(default="", max_length=191)
    secret: str = ""
    registration_id: str | None = Field(default=None, exclude=True, max_length=128)
    agent_id: str
    session_user_id: str = Field(..., max_length=128)
    enabled: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_credentials(self):
        validate_channel_settings(self.channel_type, self.settings)
        if self.registration_id:
            if self.bot_id or self.secret:
                raise ValueError("扫码授权不能同时提交机器人凭据")
        elif not self.bot_id or not self.secret:
            raise ValueError("bot_id and secret are required")
        return self

    @field_validator("settings")
    @classmethod
    def validate_deepfos_settings(cls, value: dict[str, Any]) -> dict[str, Any]:
        channel_deepfos_app(value)
        return value

    @field_validator("name", "bot_id", "secret", "agent_id", "session_user_id")
    @classmethod
    def normalize_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="before")
    @classmethod
    def reject_owner_fields(cls, value):
        if isinstance(value, dict):
            forbidden = {"owner_user_id", "platform_token", "cookie"} & set(value)
            if forbidden:
                raise ValueError(
                    "identity and platform credentials come from trusted runtime configuration"
                )
        return value


class ChannelAccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=191)
    bot_id: str | None = Field(default=None, max_length=191)
    secret: str | None = None
    agent_id: str | None = None
    session_user_id: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    settings: dict[str, Any] | None = None

    @field_validator("settings")
    @classmethod
    def validate_deepfos_settings(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        channel_deepfos_app(value)
        return value

    @model_validator(mode="before")
    @classmethod
    def reject_immutable_fields(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            forbidden = {
                "owner_user_id",
                "channel_type",
                "transport",
                "platform_token",
                "cookie",
            } & set(value)
            if forbidden:
                raise ValueError("immutable or trusted fields cannot be updated")
            # Match the Provider API-key contract: an empty editor means
            # "keep the stored secret", not "replace it with an empty value".
            if isinstance(value.get("secret"), str) and not value["secret"].strip():
                value.pop("secret")
            required = {
                "name",
                "bot_id",
                "secret",
                "agent_id",
                "session_user_id",
                "enabled",
                "settings",
            }
            if any(key in value and value[key] is None for key in required):
                raise ValueError("required channel fields cannot be null")
        return value

    @field_validator("name", "bot_id", "secret", "agent_id", "session_user_id")
    @classmethod
    def normalize_required(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class ChannelAccountResponse(BaseModel):
    id: str
    name: str
    channel_type: str
    transport: str
    bot_id: str
    secret_set: bool
    secret_masked: str | None = None
    agent_id: str
    agent_name: str | None = None
    session_user_id: str
    enabled: bool
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ChannelAccountRuntimeStatus(BaseModel):
    account_id: str
    state: ChannelRuntimeState
    message: str = ""
    authenticated: bool = False
    updated_at: UtcDatetime | None = None


class ChannelRuntimeResponse(BaseModel):
    deployed: bool
    reachable: bool
    state: ChannelRuntimeState
    message: str
    accounts: list[ChannelAccountRuntimeStatus] = Field(default_factory=list)


class ChannelConnectionTestResponse(BaseModel):
    account_id: str
    success: bool
    state: ChannelRuntimeState
    message: str
    warning: str | None = None
