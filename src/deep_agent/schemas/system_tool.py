from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SystemToolDescription(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    display_name: str
    source: str
    injection_condition: str
    description: str


class SystemToolDomain(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    label: str
    description: str
    tools: tuple[SystemToolDescription, ...]
