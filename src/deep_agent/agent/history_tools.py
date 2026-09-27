from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Any, Literal

from pydantic_ai import FunctionToolset, ModelRetry, RunContext

from deep_agent.agent.deps import AgentDeps
from deep_agent.db.conversation import SqlConversationCoordinator
from deep_agent.db.event_codec import tool_part_references

_SENSITIVE_KEYS = frozenset(
    {
        "api_key", "authorization", "cookie", "headers", "metadata",
        "provider_details", "request_headers", "signature", "token",
    }
)
_SPACE_RE = re.compile(r"\s+")


def normalize_search_query(value: str) -> str:
    return _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", value).casefold()).strip()


def _bigrams(value: str) -> set[str]:
    compact = value.replace(" ", "")
    return {compact[index : index + 2] for index in range(max(0, len(compact) - 1))}


def score_search_text(query: str, text: str) -> float:
    normalized_query = normalize_search_query(query)
    normalized_text = normalize_search_query(text)
    if not normalized_query or not normalized_text:
        return 0.0
    score = 0.0
    if normalized_text == normalized_query:
        score += 1_000
    if normalized_query in normalized_text:
        score += 500 + min(100, len(normalized_query))
    words = [item for item in normalized_query.split(" ") if item]
    score += 40 * sum(item in normalized_text for item in words)
    pairs = _bigrams(normalized_query)
    if pairs:
        score += 100 * len(pairs & _bigrams(normalized_text)) / len(pairs)
    score += 4 * sum(char in normalized_text for char in set(normalized_query) if char.strip())
    return score


def _cursor(query: str, offset: int, scan_offset: int = 0, source_head: int | None = None) -> str:
    payload = {
        "q": hashlib.sha256(normalize_search_query(query).encode()).hexdigest()[:16],
        "o": offset,
        "s": scan_offset,
        "h": source_head,
    }
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _cursor_offset(query: str, cursor: str | None) -> tuple[int, int, int | None]:
    if not cursor:
        return 0, 0, None
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        expected = hashlib.sha256(normalize_search_query(query).encode()).hexdigest()[:16]
        if payload.get("q") != expected:
            raise ValueError
        offset, scan = int(payload.get("o") or 0), int(payload.get("s") or 0)
        if offset < 0 or scan < 0:
            raise ValueError
        head = payload.get("h")
        if head is not None and (not isinstance(head, int) or head < 0):
            raise ValueError
        return offset, scan, head
    except Exception as exc:
        raise ModelRetry("Invalid or stale history search cursor.") from exc


def _preview(text: str, query: str, limit: int = 360) -> str:
    normalized = normalize_search_query(text)
    needle = normalize_search_query(query)
    index = normalized.find(needle)
    if index < 0:
        return normalized[:limit]
    start = max(0, index - limit // 3)
    return normalized[start : start + limit]


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact(child)
            for key, child in value.items()
            if key.casefold() not in _SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _artifact_id(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("artifact_id", "overflow_handle", "platform_file_id", "file_id"):
            if value.get(key):
                return str(value[key])
        for child in value.values():
            found = _artifact_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _artifact_id(child)
            if found:
                return found
    return None


def build_history_toolset(
    store: SqlConversationCoordinator,
) -> FunctionToolset[AgentDeps]:
    toolset = FunctionToolset[AgentDeps](id="deepagent-history")

    @toolset.tool
    async def search_conversation_history(
        ctx: RunContext[AgentDeps],
        query: str = "",
        scope: Literal["current_session", "my_sessions"] = "current_session",
        session_id: str | None = None,
        event_types: list[str] | None = None,
        tool_name: str | None = None,
        call_id: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        from_sequence: int | None = None,
        through_sequence: int | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Find prior results before repeating business queries. Use a tool/call ID or
        a current-session source sequence range when query text is unknown. Follow
        next_cursor even on an empty page; ranking covers each bounded scan page.
        """

        has_query = bool(normalize_search_query(query))
        if not has_query and not (tool_name or call_id or from_sequence is not None or through_sequence is not None):
            raise ModelRetry("Empty query requires tool_name, call_id, or a source sequence range")
        if (from_sequence is not None or through_sequence is not None) and scope != "current_session":
            raise ModelRetry("Source sequence ranges require current_session scope")
        if any(value is not None and value < 1 for value in (from_sequence, through_sequence)):
            raise ModelRetry("Source sequence bounds must be positive")
        if from_sequence is not None and through_sequence is not None and from_sequence > through_sequence:
            raise ModelRetry("from_sequence must not exceed through_sequence")
        if not ctx.deps.agent_id or not ctx.deps.user:
            raise ModelRetry("Conversation history requires an authenticated agent and user")
        selected_session = None
        if scope == "current_session":
            selected_session = session_id or ctx.deps.session_id
            if selected_session != ctx.deps.session_id:
                raise ModelRetry("current_session cannot target another session")
        elif session_id is not None:
            selected_session = session_id
        cursor_key = json.dumps({
            "query": normalize_search_query(query), "scope": scope, "session": selected_session,
            "agent": ctx.deps.agent_id, "user": ctx.deps.user,
            "event_types": sorted(event_types or []), "tool_name": tool_name, "call_id": call_id,
            "created_after": created_after, "created_before": created_before,
            "from_sequence": from_sequence, "through_sequence": through_sequence,
        }, sort_keys=True, default=str)
        offset, scan_offset, source_head = _cursor_offset(cursor_key, cursor)
        effective_through = through_sequence
        if source_head is not None:
            effective_through = min(through_sequence, source_head) if through_sequence is not None else source_head
        scan = await store.search_event_candidates(
            agent_id=ctx.deps.agent_id,
            user_id=ctx.deps.user,
            session_id=selected_session,
            event_types=event_types,
            tool_name=tool_name,
            call_id=call_id,
            created_after=created_after,
            created_before=created_before,
            from_sequence=from_sequence, through_sequence=effective_through,
            offset=scan_offset, limit=200,
        )
        source_head = scan["source_head"]
        ranked = [
            (score_search_text(query, str(row.get("search_text") or "")) if has_query else 1.0, row)
            for row in scan["items"]
        ]
        ranked = [item for item in ranked if item[0] > 0]
        ranked.sort(
            key=lambda item: (
                item[0], item[1].get("created_at"), item[1].get("sequence")
            ),
            reverse=True,
        )
        page_size = min(max(limit, 1), 50)
        page = ranked[offset : offset + page_size]
        items: list[dict[str, Any]] = []
        for score, row in page:
            complete: bool | None = None
            artifact: str | None = None
            if row.get("event_type") == "tool/results":
                detail = await store.read_authorized_events(
                    agent_id=ctx.deps.agent_id,
                    user_id=ctx.deps.user,
                    event_id=str(row["event_id"]),
                )
                if detail:
                    result_parts = [
                        ref["part"]
                        for ref in tool_part_references(detail[0])
                        if ref["role"] == "result"
                    ]
                    metadata_items = [
                        part.get("metadata") or {}
                        for part in result_parts
                        if isinstance(part, dict)
                    ]
                    complete = bool(result_parts) and all(
                        not bool(metadata.get("truncated") or metadata.get("spill_failed"))
                        for metadata in metadata_items
                    )
                    artifact = _artifact_id(result_parts)
            items.append(
                {
                    "session_id": row["session_id"],
                    "session_title": row.get("title"),
                    "event_id": row["event_id"],
                    "sequence": row["sequence"],
                    "turn_id": row.get("turn_id"),
                    "run_id": row.get("run_id"),
                    "event_type": row["event_type"],
                    "tool_name": row.get("tool_name"),
                    "call_id": row.get("call_id"),
                    "matched_parts": [
                        *(row.get("tool_matches") or []),
                        *(row.get("attachment_matches") or []),
                    ],
                    "preview": _preview(str(row.get("search_text") or ""), query),
                    "result_complete": complete,
                    "artifact_id": artifact,
                    "score": round(score, 3),
                }
            )
        next_offset = offset + len(page)
        next_cursor = (
            _cursor(cursor_key, next_offset, scan_offset, source_head) if next_offset < len(ranked)
            else _cursor(cursor_key, 0, scan["next_offset"], source_head) if scan["next_offset"] is not None else None
        )
        return {
            "items": items,
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
            "search_complete": next_cursor is None,
            "scanned_count": scan["scanned_count"],
            "ranking_scope": "scan_page",
        }

    @toolset.tool
    async def read_conversation_event(
        ctx: RunContext[AgentDeps],
        event_id: str | None = None,
        call_id: str | None = None,
        before: int = 0,
        after: int = 0,
        part_offset: int | None = None,
        offset: int = 0,
        limit: int = 12_000,
        from_sequence: int | None = None,
        through_sequence: int | None = None,
    ) -> dict[str, Any]:
        """Read saved evidence before rerunning a business query. Select an exact
        Event/part or current-session Call ID. Large results return explicit JSON
        text fragments; follow next_read and concatenate content to recover them.
        """

        if not ctx.deps.agent_id or not ctx.deps.user:
            raise ModelRetry("Conversation history requires an authenticated agent and user")
        if bool(event_id) == bool(call_id):
            raise ModelRetry("Provide exactly one of event_id or call_id")
        if part_offset is not None and (not event_id or before or after or part_offset < 0):
            raise ModelRetry("part_offset requires an exact event_id without adjacent events")
        if offset < 0 or not 1 <= limit <= 32_000 or not 0 <= before <= 20 or not 0 <= after <= 20:
            raise ModelRetry("offset must be non-negative; limit 1..32000; before/after 0..20")
        if any(value is not None and value < 1 for value in (from_sequence, through_sequence)):
            raise ModelRetry("Source sequence bounds must be positive")
        if from_sequence is not None and through_sequence is not None and from_sequence > through_sequence:
            raise ModelRetry("from_sequence must not exceed through_sequence")
        batch = await store.read_authorized_events(
            agent_id=ctx.deps.agent_id,
            user_id=ctx.deps.user,
            event_id=event_id,
            session_id=ctx.deps.session_id if call_id else None,
            call_id=call_id,
            before=before,
            after=after,
            from_sequence=from_sequence, through_sequence=through_sequence, page_size=100,
        )
        rows = batch["items"]
        if not rows and batch["next_sequence"] is None and from_sequence is None:
            raise ModelRetry("Conversation event not found or not authorized")
        events = []
        for row in rows:
            item = row.public_dict()
            if part_offset is not None:
                parts = (item.get("payload", {}).get("message") or {}).get("parts", [])
                if part_offset >= len(parts):
                    raise ModelRetry("part_offset is outside the original Event")
                item["payload"] = {"part_offset": part_offset, "part": parts[part_offset]}
            item["payload"] = _redact(item["payload"])
            events.append(item)
        encoded = json.dumps(events, ensure_ascii=False, separators=(",", ":"), default=str)
        if offset > len(encoded):
            raise ModelRetry("offset exceeds this read page")
        end = min(len(encoded), offset + limit)
        next_read = None
        if end < len(encoded) or batch["next_sequence"] is not None:
            next_read = {"event_id": event_id, "call_id": call_id, "before": before, "after": after,
                         "part_offset": part_offset, "limit": limit, "through_sequence": through_sequence,
                         "from_sequence": from_sequence if end < len(encoded) else batch["next_sequence"],
                         "offset": end if end < len(encoded) else 0}
            next_read = {key: value for key, value in next_read.items() if value is not None}
        complete_page = offset == 0 and end == len(encoded)
        return {
            "events": events if complete_page else [],
            "content": None if complete_page else encoded[offset:end],
            "content_format": "events" if complete_page else "json_fragment",
            "offset": offset, "next_offset": end if end < len(encoded) else None,
            "total_chars": len(encoded), "has_more": next_read is not None, "next_read": next_read,
        }

    return toolset


__all__ = ["build_history_toolset", "normalize_search_query", "score_search_text"]
