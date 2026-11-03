"""Sources for visual viewing only; read_file keeps its original source union."""
from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from deep_agent.files.schema import AttachmentFileRef, FileResourceRef, SkillFileRef

from .limits import MAX_CALL_IMAGES, MAX_ITEMS


class PublicUrlRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["url"]
    url: str = Field(min_length=1, max_length=8192)

    @field_validator("url")
    @classmethod
    def absolute_public_candidate(cls, value: str) -> str:
        # DNS and every redirect are checked by the public downloader, never
        # by the FileStore path or with the current user's authentication.
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url 必须是绝对 HTTP(S) 地址")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("url 不能包含用户名或密码")
        return value


VisualResourceRef = Annotated[AttachmentFileRef | SkillFileRef | PublicUrlRef,
                              Field(discriminator="type")]
PageNumber = Annotated[StrictInt, Field(ge=1)]
PageSelection = Annotated[list[PageNumber], Field(min_length=1, max_length=MAX_CALL_IMAGES)]


class ViewImageItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource: VisualResourceRef
    pages: PageSelection | None = None


class LocalViewImageItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource: FileResourceRef
    pages: PageSelection | None = None


ViewImageItems = Annotated[list[ViewImageItem], Field(min_length=1, max_length=MAX_ITEMS)]
LocalViewImageItems = Annotated[list[LocalViewImageItem], Field(min_length=1, max_length=MAX_ITEMS)]
