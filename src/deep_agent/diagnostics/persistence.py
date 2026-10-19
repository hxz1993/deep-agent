"""Bounded persistence failure facts; never inspect exception text or SQL values."""
from __future__ import annotations

from collections import deque
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re

from sqlalchemy.exc import DataError, DBAPIError, IntegrityError, OperationalError

from deep_agent import __version__

_PACKAGE = str(Path(__file__).resolve().parents[1]) + "/"
_CONFLICT_FIELDS = frozenset({
    "persistence_reason", "conflict_event_id", "message_ordinal", "part_index",
    "stored_part_kind", "incoming_part_kind", "conflict_field",
    "stored_part_count", "incoming_part_count",
})


@lru_cache(maxsize=1)
def _versions():
    facts = {"product_version": __version__}
    for package, key in (("pydantic-ai-slim", "pydantic_ai_version"),
                         ("pydantic-ai-harness", "pydantic_harness_version"),
                         ("SQLAlchemy", "sqlalchemy_version")):
        try:
            facts[key] = version(package)
        except PackageNotFoundError:
            facts[key] = "unavailable"
    return facts


def persistence_failure_fields(exc: Exception, *, store=None, messages=(), hook="unknown") -> dict:
    """Best effort only: malformed facts must not replace the write exception.

    Payloads, SQL statements/parameters, exception messages, filenames outside
    this package and traceback locals are deliberately not examined.
    """
    facts = {}
    try:
        facts.update(_versions())
        facts["write_hook"] = hook
        facts["write_message_count"] = len(messages)
        facts["write_part_count"] = sum(len(message.parts) for message in messages)
        bind = getattr(getattr(store, "_session_factory", None), "kw", {}).get("bind")
        dialect = getattr(getattr(bind, "dialect", None), "name", None)
        if dialect in ("mysql", "postgresql", "sqlite"):
            facts["db_dialect"] = dialect
        chain, seen = [], set()
        current = exc
        while current is not None and id(current) not in seen and len(chain) < 5:
            seen.add(id(current)); chain.append(current)
            current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
        facts["exception_chain"] = ":".join(type(item).__name__ for item in chain)[:180]
        facts["cause_type"] = type(chain[-1]).__name__
        facts["persistence_reason"] = "unexpected"
        for item in chain:
            if isinstance(item, IntegrityError): reason = "database_integrity"
            elif isinstance(item, OperationalError): reason = "database_operational"
            elif isinstance(item, DataError): reason = "database_data"
            elif isinstance(item, DBAPIError): reason = "database_driver"
            elif isinstance(item, TimeoutError): reason = "timeout"
            elif isinstance(item, ValueError): reason = "invalid_value"
            else: continue
            facts["persistence_reason"] = reason
            if isinstance(item, DBAPIError):
                # Only the numeric driver code and validated SQLSTATE, never
                # args[1] (typically the SQL error message and customer values).
                args = getattr(item.orig, "args", ())
                if args and type(args[0]) is int and 0 <= args[0] <= 10**7:
                    facts["database_error_code"] = args[0]
                state = getattr(item.orig, "sqlstate", None) or getattr(item.orig, "pgcode", None)
                if type(state) is str and re.fullmatch(r"[A-Z0-9]{5}", state):
                    facts["database_sqlstate"] = state
        locations = deque(maxlen=2)
        frame_count = 0
        for item in chain:
            tb = item.__traceback__
            while tb is not None and frame_count < 256:
                frame_count += 1
                filename = tb.tb_frame.f_code.co_filename
                if filename.startswith(_PACKAGE):
                    relative = filename[len(_PACKAGE):]
                    locations.append(f"{relative}:{tb.tb_lineno}:{tb.tb_frame.f_code.co_name}")
                tb = tb.tb_next
            if frame_count >= 256:
                break
        if locations:
            facts["failure_location"] = locations[-1]
        if len(locations) > 1:
            facts["failure_caller"] = locations[-2]
        # A typed conflict is the only source of structured comparison facts.
        from deep_agent.db.persistence_errors import ImmutableMessagePartConflict
        for item in chain:
            if isinstance(item, ImmutableMessagePartConflict):
                facts.update({key: value for key, value in item.diagnostics.items() if key in _CONFLICT_FIELDS})
                break
    except Exception:
        pass
    return facts
