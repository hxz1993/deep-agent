from __future__ import annotations

import datetime
import decimal
import enum
import fractions
import functools
import json
import math
import uuid
from pathlib import Path
from typing import Any

from deep_agent.sandbox.types import MAX_RESULT_BYTES

MAX_PREVIEW_ROWS = 10
_MAX_REPR_CHARS = 4_096
_MAX_BYTES_REPR = 256
RESULT_PREVIEW_GUIDANCE = (
    "This is only a result preview, not the full data. For complete output, write "
    "the data to publish_path(file_name, mime=...) in the same execution and use "
    "the returned files/file_id for subsequent reading. Do not use print for full "
    "data or repeat an execution with side effects merely to recover a preview."
)


def truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    truncated = encoded[:max_bytes]
    while truncated:
        try:
            return truncated.decode("utf-8"), True
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return "", True


def repr_value(value: Any) -> str:
    try:
        text = repr(value)
    except Exception:
        text = f"<{type(value).__name__}>"
    if len(text) > _MAX_REPR_CHARS:
        text = text[:_MAX_REPR_CHARS] + "..."
    return text


def _json_byte_length(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _contains_circular_marker(value: Any) -> bool:
    if value == "[...]":
        return True
    if isinstance(value, list):
        return any(_contains_circular_marker(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_circular_marker(item) for item in value.values())
    return False


@functools.singledispatch
def to_json_value(value: Any, *, _seen: set[int] | None = None) -> Any:
    return repr_value(value)


@to_json_value.register(type(None))
def _to_json_none(value: None, *, _seen: set[int] | None = None) -> Any:
    return None


@to_json_value.register(bool)
def _to_json_bool(value: bool, *, _seen: set[int] | None = None) -> Any:
    return value


@to_json_value.register(str)
def _to_json_str(value: str, *, _seen: set[int] | None = None) -> Any:
    return value


@to_json_value.register(int)
def _to_json_int(value: int, *, _seen: set[int] | None = None) -> Any:
    if isinstance(value, bool):
        return value
    return value


@to_json_value.register(float)
def _to_json_float(value: float, *, _seen: set[int] | None = None) -> Any:
    if math.isnan(value) or math.isinf(value):
        return str(value)
    return value


@to_json_value.register(complex)
def _to_json_complex(value: complex, *, _seen: set[int] | None = None) -> Any:
    return str(value)


@to_json_value.register(dict)
def _to_json_dict(value: dict, *, _seen: set[int] | None = None) -> Any:
    if _seen is None:
        _seen = set()
    obj_id = id(value)
    if obj_id in _seen:
        return "[...]"
    _seen.add(obj_id)
    return {
        str(key): to_json_value(item, _seen=_seen)
        for key, item in value.items()
    }


@to_json_value.register(list)
def _to_json_list(value: list, *, _seen: set[int] | None = None) -> Any:
    if _seen is None:
        _seen = set()
    obj_id = id(value)
    if obj_id in _seen:
        return "[...]"
    _seen.add(obj_id)
    return [to_json_value(item, _seen=_seen) for item in value]


@to_json_value.register(tuple)
def _to_json_tuple(value: tuple, *, _seen: set[int] | None = None) -> Any:
    if _seen is None:
        _seen = set()
    obj_id = id(value)
    if obj_id in _seen:
        return "[...]"
    _seen.add(obj_id)
    return [to_json_value(item, _seen=_seen) for item in value]


@to_json_value.register(set)
def _to_json_set(value: set, *, _seen: set[int] | None = None) -> Any:
    if _seen is None:
        _seen = set()
    obj_id = id(value)
    if obj_id in _seen:
        return "[...]"
    _seen.add(obj_id)
    return [to_json_value(item, _seen=_seen) for item in value]


@to_json_value.register(frozenset)
def _to_json_frozenset(value: frozenset, *, _seen: set[int] | None = None) -> Any:
    if _seen is None:
        _seen = set()
    obj_id = id(value)
    if obj_id in _seen:
        return "[...]"
    _seen.add(obj_id)
    return [to_json_value(item, _seen=_seen) for item in value]


@to_json_value.register(range)
def _to_json_range(value: range, *, _seen: set[int] | None = None) -> Any:
    if len(value) <= MAX_PREVIEW_ROWS:
        return list(value)
    return str(value)


@to_json_value.register(decimal.Decimal)
def _to_json_decimal(value: decimal.Decimal, *, _seen: set[int] | None = None) -> Any:
    return str(value)


@to_json_value.register(datetime.date)
def _to_json_date(value: datetime.date, *, _seen: set[int] | None = None) -> Any:
    return value.isoformat()


@to_json_value.register(datetime.datetime)
def _to_json_datetime(value: datetime.datetime, *, _seen: set[int] | None = None) -> Any:
    return value.isoformat()


@to_json_value.register(datetime.time)
def _to_json_time(value: datetime.time, *, _seen: set[int] | None = None) -> Any:
    return value.isoformat()


@to_json_value.register(datetime.timedelta)
def _to_json_timedelta(value: datetime.timedelta, *, _seen: set[int] | None = None) -> Any:
    return str(value)


@to_json_value.register(uuid.UUID)
def _to_json_uuid(value: uuid.UUID, *, _seen: set[int] | None = None) -> Any:
    return str(value)


@to_json_value.register(enum.Enum)
def _to_json_enum(value: enum.Enum, *, _seen: set[int] | None = None) -> Any:
    return to_json_value(value.value, _seen=_seen)


@to_json_value.register(Path)
def _to_json_path(value: Path, *, _seen: set[int] | None = None) -> Any:
    return str(value)


@to_json_value.register(fractions.Fraction)
def _to_json_fraction(value: fractions.Fraction, *, _seen: set[int] | None = None) -> Any:
    return str(value)


@to_json_value.register(bytes)
def _to_json_bytes(value: bytes, *, _seen: set[int] | None = None) -> Any:
    if len(value) <= _MAX_BYTES_REPR:
        return repr(value)
    return repr(value[:_MAX_BYTES_REPR])[:-1] + "...'"


@to_json_value.register(bytearray)
def _to_json_bytearray(value: bytearray, *, _seen: set[int] | None = None) -> Any:
    if len(value) <= _MAX_BYTES_REPR:
        return repr(value)
    return repr(bytes(value[:_MAX_BYTES_REPR]))[:-1] + "...'"


def _preview_dataframe(df: Any) -> dict[str, Any]:
    records = [
        {str(key): to_json_value(item) for key, item in row.items()}
        for row in df.head(MAX_PREVIEW_ROWS).to_dict(orient="records")
    ]
    return {
        "kind": "dataframe",
        "shape": [int(df.shape[0]), int(df.shape[1])],
        "columns": [str(column) for column in df.columns],
        "dtypes": {str(column): str(dtype) for column, dtype in df.dtypes.items()},
        "records": records,
        **_preview_metadata(len(df), len(records)),
    }


def _preview_series(series: Any) -> dict[str, Any]:
    head = series.head(MAX_PREVIEW_ROWS)
    records = [
        {"index": to_json_value(index), "value": to_json_value(value)}
        for index, value in head.items()
    ]
    return {
        "kind": "series",
        "name": series.name if series.name is not None else None,
        "length": int(len(series)),
        "dtype": str(series.dtype),
        "records": records,
        **_preview_metadata(len(series), len(records)),
    }


def _preview_ndarray(arr: Any) -> dict[str, Any]:
    flat = arr.flat
    head_count = min(len(flat), MAX_PREVIEW_ROWS)
    values = [to_json_value(flat[i]) for i in range(head_count)]
    return {
        "kind": "ndarray",
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "values": values,
        **_preview_metadata(int(arr.size), head_count),
    }


def _preview_metadata(total: int, returned: int) -> dict[str, Any]:
    return {
        "is_preview": returned < total,
        "returned_count": returned,
        "total_count": total,
        **({"guidance": RESULT_PREVIEW_GUIDANCE} if returned < total else {}),
    }


def _preview_pandas_value(value: Any) -> dict[str, Any] | None:
    try:
        import pandas as pd
    except ImportError:
        return None

    if isinstance(value, pd.DataFrame):
        return _preview_dataframe(value)
    if isinstance(value, pd.Series):
        return _preview_series(value)
    return None


def _preview_numpy_value(value: Any) -> dict[str, Any] | None:
    try:
        import numpy as np
    except ImportError:
        return None

    if isinstance(value, np.ndarray):
        return _preview_ndarray(value)
    return None


def _register_optional_serializers() -> None:
    try:
        import numpy as np
    except ImportError:
        return

    @to_json_value.register(np.integer)
    def _to_json_np_integer(value: Any, *, _seen: set[int] | None = None) -> Any:
        return int(value)

    @to_json_value.register(np.floating)
    def _to_json_np_floating(value: Any, *, _seen: set[int] | None = None) -> Any:
        fval = float(value)
        if math.isnan(fval) or math.isinf(fval):
            return str(fval)
        return fval

    @to_json_value.register(np.bool_)
    def _to_json_np_bool(value: Any, *, _seen: set[int] | None = None) -> Any:
        return bool(value)

    @to_json_value.register(np.str_)
    def _to_json_np_str(value: Any, *, _seen: set[int] | None = None) -> Any:
        return str(value)

    @to_json_value.register(np.bytes_)
    def _to_json_np_bytes(value: Any, *, _seen: set[int] | None = None) -> Any:
        return _to_json_bytes(bytes(value), _seen=_seen)

    try:
        import pandas as pd
    except ImportError:
        return

    @to_json_value.register(pd.Timestamp)
    def _to_json_pd_timestamp(value: Any, *, _seen: set[int] | None = None) -> Any:
        if pd.isna(value):
            return None
        return value.isoformat()

    @to_json_value.register(type(pd.NA))
    def _to_json_pd_na(value: Any, *, _seen: set[int] | None = None) -> Any:
        return None

    @to_json_value.register(type(pd.NaT))
    def _to_json_pd_nat(value: Any, *, _seen: set[int] | None = None) -> Any:
        return None


_register_optional_serializers()


def serialize_result(value: Any) -> tuple[Any, bool]:
    preview = _preview_pandas_value(value)
    if preview is None:
        preview = _preview_numpy_value(value)

    if preview is not None:
        serialized = preview
    elif isinstance(value, (dict, list, tuple, set, frozenset)):
        serialized = to_json_value(value)
        if _contains_circular_marker(serialized):
            serialized = repr_value(value)
    elif isinstance(value, (str, int, float, bool)) or value is None:
        serialized = to_json_value(value)
    else:
        serialized = to_json_value(value)

    if _json_byte_length(serialized) <= MAX_RESULT_BYTES:
        return serialized, bool(preview is not None and preview["is_preview"])

    # The transport stays bounded. Never present the lossy repr as a complete
    # result; explicit publish_path outputs are collected separately by the host.
    return {
        "kind": "result_preview",
        "is_preview": True,
        "reason": "byte_limit",
        "full_result_saved_automatically": False,
        "preview": repr_value(value),
        "guidance": RESULT_PREVIEW_GUIDANCE,
    }, True
