from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExistingPlatformFileSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["platform_file"]
    file_id: str = Field(
        min_length=1,
        description="当前会话附件或已外置大工具结果的平台File ID",
    )


class GeneratedTextFileSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["content"]
    content: str = Field(
        min_length=1,
        description="要新建并上传的完整UTF-8文本内容",
    )


ExportFileSource = Annotated[
    ExistingPlatformFileSource | GeneratedTextFileSource,
    Field(discriminator="type"),
]


class ExportFileResult(BaseModel):
    status: Literal["published"] = "published"
    destination: Literal["download_center"] = "download_center"
    source_type: Literal["platform_file", "content"]
    download_record_id: str
    file_id: str
    file_name: str
    byte_size: int = Field(ge=0)
