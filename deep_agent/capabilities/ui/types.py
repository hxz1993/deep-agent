from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from deep_agent.db.models import UiToolDefinition


@dataclass(frozen=True)
class UIToolRecord:
    name: str
    package_code: str
    description: str
    parameters_json_schema: dict[str, Any]
    widget: str | None = None
    result_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    source: str = "builtin"

    @classmethod
    def from_row(cls, row: UiToolDefinition) -> UIToolRecord:
        return cls(
            name=row.name,
            package_code=row.package_code,
            description=row.description,
            parameters_json_schema=row.parameters_json_schema,
            widget=row.widget,
            result_schema=row.result_schema,
            metadata=row.metadata_,
            source=row.source,
        )
