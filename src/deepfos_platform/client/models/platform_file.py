from __future__ import annotations

from typing import Optional

from ...lib.model_base import BaseModel

__all__ = ["FileUplodRes"]


class FileUplodRes(BaseModel):
    """Platform ``POST /files/upload`` response."""

    fileDescription: Optional[str] = None
    fileName: str
    fileSize: int
    fileType: str
    id: str
    url: str
