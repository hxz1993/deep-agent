from __future__ import annotations

from pydantic import BaseModel


class ConsoleAdminAccessResponse(BaseModel):
    allowed: bool
    roles: list[str]
