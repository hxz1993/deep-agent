"""Correct the unreleased 002 Binding schema on a stopped local SQLite runtime.

Default is read-only. --apply first creates a private TMP_to_delete backup,
then changes schema and data in one transaction. Never accesses a profile,
remote database, platform service, or Alembic stamp.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

from deep_agent.channels.metadata import channel_session_title, normalize_channel_origin, normalize_peer_metadata


def _rows(db: sqlite3.Connection, table: str, key: str) -> dict[str, dict]:
    return {row[key]: dict(row) for row in db.execute(f'SELECT * FROM "{table}"')}


def _json(value: object) -> dict:
    if value is None:
        return {}
    result = json.loads(value) if isinstance(value, str) else value
    if not isinstance(result, dict):
        raise ValueError("Channel metadata must be a JSON object")
    return result


def _dump(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _foreign_key_violations(db: sqlite3.Connection) -> set[tuple]:
    return {tuple(row) for row in db.execute("PRAGMA foreign_key_check")}


def _orphan_event_ids(db: sqlite3.Connection, violations: set[tuple]) -> set[str]:
    """Whitelist only missing Session parents, never another Event foreign key."""
    keys = {row[0]: tuple(row) for row in db.execute("PRAGMA foreign_key_list(chat_session_events)")}
    identifiers = set()
    for table, rowid, parent, key_id in violations:
        key = keys.get(key_id)
        if table != "chat_session_events" or parent != "chat_sessions" or not key or key[3:5] != ("session_id", "session_id"):
            raise ValueError("Existing database has foreign key violations outside orphan events")
        event = db.execute("SELECT event_id, session_id FROM chat_session_events WHERE rowid=?", (rowid,)).fetchone()
        if event is None or db.execute("SELECT 1 FROM chat_sessions WHERE session_id=?", (event[1],)).fetchone():
            raise ValueError("Unrecognized orphan Event foreign key violation")
        identifiers.add(event[0])
    return identifiers


def _plan(db: sqlite3.Connection, *, preserve_orphan_events: bool = False) -> tuple[dict, dict, dict, set[str]]:
    versions = [row[0] for row in db.execute("SELECT version_num FROM alembic_version")]
    if versions != ["002"]:
        raise ValueError("Channel metadata repair requires database revision 002")
    columns = {row[1] for row in db.execute("PRAGMA table_info(channel_session_bindings)")}
    if not {"peer_metadata", "peer_display_name"} & columns:
        raise ValueError("Unrecognized channel Binding schema")
    violations = _foreign_key_violations(db)
    if violations and not preserve_orphan_events:
        raise ValueError("Existing database has foreign key violations")
    orphan_ids = _orphan_event_ids(db, violations) if violations else set()
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity check failed")
    account_types = {row[0]: row[1] for row in db.execute("SELECT id, channel_type FROM channel_accounts")}
    bindings = _rows(db, "channel_session_bindings", "id")
    sessions = _rows(db, "chat_sessions", "session_id")
    events = _rows(db, "chat_session_events", "event_id")
    runs = _rows(db, "channel_message_runs", "id")
    expected = deepcopy({"bindings": bindings, "sessions": sessions, "events": events, "runs": runs})
    report = {"schema_changed": "peer_metadata" not in columns or "peer_display_name" in columns,
              "bindings_changed": 0, "origins_changed": 0, "titles_changed": 0}
    if preserve_orphan_events:
        report["preserved_orphan_events"] = len(orphan_ids)
    keys: set[tuple] = set()
    by_peer = {}
    aliases = {"userid": "single", "chatid": "group"}
    for identifier, binding in expected["bindings"].items():
        kind = account_types.get(binding["account_id"])
        if kind == "wecom":
            binding["peer_type"] = aliases.get(binding["peer_type"], binding["peer_type"])
        key = (binding["account_id"], binding["peer_type"], binding["peer_id"])
        if key in keys:
            raise ValueError("Cannot repair channel metadata: conflicting bindings exist")
        keys.add(key)
        metadata = normalize_peer_metadata(
            binding["peer_type"], binding["peer_id"], _json(binding.get("peer_metadata")),
            legacy_display_name=binding.pop("peer_display_name", None),
        )
        binding["peer_metadata"] = _dump(metadata)
        by_peer[key] = metadata
        original = bindings[identifier]
        if "peer_display_name" in original or original.get("peer_type") != binding["peer_type"] or _json(original.get("peer_metadata")) != metadata:
            report["bindings_changed"] += 1
    origins = {}
    latest_titles = {}
    for identifier, event in expected["events"].items():
        # Unrelated historical orphan Events must remain byte-for-byte untouched,
        # including legacy title/source payloads that this repair would normalize.
        if identifier in orphan_ids:
            continue
        if event["event_type"] != "session/title":
            continue
        payload = _json(event["payload"])
        session_id = event["session_id"]
        latest = latest_titles.get(session_id)
        if latest is None or event["sequence"] > latest["sequence"]:
            latest_titles[session_id] = event
        origin = payload.get("channel")
        if payload.get("source") != "channel" or not isinstance(origin, dict):
            continue
        original_origin = deepcopy(origin)
        if origin.get("channel_type") == "wecom":
            origin = {**origin, "peer_type": aliases.get(origin.get("peer_type"), origin.get("peer_type"))}
        normalized = normalize_channel_origin(origin)
        known = by_peer.get((origin.get("account_id"), origin.get("peer_type"), origin.get("peer_id")), {})
        normalized["peer_metadata"] = normalize_peer_metadata(
            str(origin.get("peer_type") or ""), str(origin.get("peer_id") or ""),
            {**known, **normalized["peer_metadata"]},
        )
        if normalized != original_origin:
            payload["channel"] = normalized
            event["payload"] = _dump(payload)
            report["origins_changed"] += 1
        origins.setdefault(session_id, normalized)
    for session_id, event in latest_titles.items():
        payload = _json(event["payload"])
        origin = origins.get(session_id)
        session = expected["sessions"].get(session_id)
        if not origin or not session or payload.get("source") not in {"channel", "fallback", "model"}:
            continue
        if payload.get("preserved_existing_title") or payload.get("preserved_manual_title"):
            continue
        # Untracked direct title edits must not be mistaken for an automatic title.
        if session["title"] != payload.get("title"):
            continue
        title = channel_session_title(origin["peer_type"], origin["peer_id"], origin["peer_metadata"])
        if title != session["title"]:
            session["title"] = title
            event["payload"] = _dump({**payload, "title": title})
            report["titles_changed"] += 1
    before = {"bindings": bindings, "sessions": sessions, "events": events, "runs": runs}
    return report, before, expected, columns


def repair_sqlite_peer_metadata(path: Path, *, apply: bool = False, preserve_orphan_events: bool = False) -> dict:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("SQLite path must name an existing file")
    with sqlite3.connect(f"{path.as_uri()}?mode={'rw' if apply else 'ro'}", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        report, before, expected, columns = _plan(db, preserve_orphan_events=preserve_orphan_events)
        original_violations = _foreign_key_violations(db)
        orphan_ids = _orphan_event_ids(db, original_violations) if original_violations else set()
        report["applied"] = apply
        if not apply:
            return report
        if "peer_display_name" in columns and sqlite3.sqlite_version_info < (3, 35, 0):
            raise ValueError("SQLite 3.35+ is required for safe native DROP COLUMN")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(f"{path.stem}-peer-metadata-TMP_to_delete-{timestamp}.db")
        with sqlite3.connect(backup) as target:
            os.chmod(backup, 0o600)
            db.backup(target)
        report["backup"] = str(backup)
        try:
            db.execute("BEGIN IMMEDIATE")
            _, locked_before, _, _ = _plan(db, preserve_orphan_events=preserve_orphan_events)
            if locked_before != before or _foreign_key_violations(db) != original_violations:
                raise ValueError("Database changed during repair preparation; stop API/Gateway first")
            if "peer_metadata" not in columns:
                db.execute("ALTER TABLE channel_session_bindings ADD COLUMN peer_metadata TEXT NOT NULL DEFAULT '{}'")
            for identifier, binding in expected["bindings"].items():
                old = before["bindings"][identifier]
                if binding["peer_type"] != old["peer_type"] or binding["peer_metadata"] != old.get("peer_metadata"):
                    db.execute("UPDATE channel_session_bindings SET peer_type=?, peer_metadata=? WHERE id=?",
                               (binding["peer_type"], binding["peer_metadata"], identifier))
            if "peer_display_name" in columns:
                db.execute("ALTER TABLE channel_session_bindings DROP COLUMN peer_display_name")
            for identifier, event in expected["events"].items():
                if event["payload"] != before["events"][identifier]["payload"]:
                    db.execute("UPDATE chat_session_events SET payload=? WHERE event_id=?", (event["payload"], identifier))
            for identifier, session in expected["sessions"].items():
                if session["title"] != before["sessions"][identifier]["title"]:
                    db.execute("UPDATE chat_sessions SET title=? WHERE session_id=?", (session["title"], identifier))
            actual = {
                "bindings": _rows(db, "channel_session_bindings", "id"),
                "sessions": _rows(db, "chat_sessions", "session_id"),
                "events": _rows(db, "chat_session_events", "event_id"),
                "runs": _rows(db, "channel_message_runs", "id"),
            }
            if actual != expected:
                raise ValueError("Repair verification failed; transaction rolled back")
            if any(actual["events"].get(identifier) != before["events"][identifier] for identifier in orphan_ids):
                raise ValueError("Existing orphan Event was modified; transaction rolled back")
            if _foreign_key_violations(db) != original_violations or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Repair integrity verification failed")
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--preserve-orphan-events", action="store_true",
                        help="Preserve pre-existing orphan Session Events unchanged; reject every other or new foreign key violation")
    args = parser.parse_args()
    print(json.dumps(repair_sqlite_peer_metadata(args.sqlite, apply=args.apply,
                                               preserve_orphan_events=args.preserve_orphan_events), ensure_ascii=False))


if __name__ == "__main__":
    main()
