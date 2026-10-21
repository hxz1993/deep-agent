"""One-shot validator subprocess. No business payload is written to logs/files."""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry
from referencing.exceptions import NoSuchResource


def _deny_retrieval(uri: str):
    raise NoSuchResource(ref=uri)


_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[Tt](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?([Zz]|[+-]\d{2}:\d{2})\Z", re.ASCII)
_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?([Zz]|[+-]\d{2}:\d{2})\Z", re.ASCII)
_FORMATS = FormatChecker(formats=[])


def _rfc3339(value: str, *, date_time: bool) -> bool:
    match = (_DATE_TIME if date_time else _TIME).fullmatch(value)
    if not match:
        return False
    hour, minute, second = map(int, match.groups()[:3])
    if hour > 23 or minute > 59 or second > 60:
        return False
    zone = match.group(4)
    if len(zone) > 1 and (int(zone[1:3]) > 23 or int(zone[4:6]) > 59):
        return False
    if date_time:
        date.fromisoformat(value[:10])
    return True


@_FORMATS.checks("date", raises=ValueError)
def _date(value):
    return not isinstance(value, str) or (bool(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)) and bool(date.fromisoformat(value)))


@_FORMATS.checks("date-time", raises=ValueError)
def _date_time(value):
    return not isinstance(value, str) or _rfc3339(value, date_time=True)


@_FORMATS.checks("time", raises=ValueError)
def _time(value):
    return not isinstance(value, str) or _rfc3339(value, date_time=False)


@_FORMATS.checks("uuid", raises=ValueError)
def _uuid(value):
    return not isinstance(value, str) or (bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value)) and bool(UUID(value)))


def validate(payload: dict) -> list[dict]:
    validator = Draft202012Validator(payload["schema"], format_checker=_FORMATS, registry=Registry(retrieve=_deny_retrieval))
    errors = []
    for error in validator.iter_errors(payload["value"]):
        pointer = "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in error.absolute_path)
        constraint = str(error.validator or "schema")[:64]
        # Library error.message contains candidate values, so expose only a
        # bounded path and constraint, never its original message/context.
        errors.append({"path": pointer[:160], "constraint": constraint,
                       "message": f"{pointer}: does not satisfy {constraint}"[:256]})
        if len(errors) >= 20:
            break
    return errors


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(64 * 1024 + 1024 * 1024 + 256)
        payload = json.loads(raw)
        errors = validate(payload)
        sys.stdout.write(json.dumps({"errors": errors}, ensure_ascii=True, separators=(",", ":")))
        return 0
    except Exception:
        # A protocol/validator fault is not a candidate validation error.
        sys.stdout.write('{"error":"worker_failed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
