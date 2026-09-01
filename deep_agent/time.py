from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, PlainSerializer


def ensure_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime, treating legacy naive values as UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utc_rfc3339(value: datetime) -> str:
    """Serialize a datetime as RFC 3339 with an explicit ``+00:00`` offset."""
    return ensure_utc(value).isoformat()


UtcDatetime = Annotated[
    datetime,
    AfterValidator(ensure_utc),
    PlainSerializer(utc_rfc3339, return_type=str, when_used="json"),
]
