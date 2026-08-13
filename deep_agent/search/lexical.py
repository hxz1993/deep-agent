from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

try:  # Keep startup resilient if an existing environment has not been rebuilt yet.
    from pypinyin import Style, lazy_pinyin
except ImportError:  # pragma: no cover - requirements install pypinyin in supported runtimes
    Style = None
    lazy_pinyin = None


_ASCII_PART_RE = re.compile(r"[a-z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_DASHES = str.maketrans({char: "-" for char in "‐‑‒–—―−"})


class FieldRole(StrEnum):
    CODE = "code"
    ALIAS = "alias"
    ATTRIBUTE = "attribute"
    TEXT = "text"


@dataclass(frozen=True)
class SearchField:
    name: str
    value: str
    role: FieldRole


@dataclass(frozen=True)
class SearchDocument:
    id: str
    fields: tuple[SearchField, ...]
    group: str = ""


@dataclass(frozen=True)
class SearchHit:
    document_id: str
    score: int
    rank_tier: int
    raw_score: float
    matched_via: str
    field: str
    group: str = ""


@dataclass(frozen=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    status: str


@dataclass(frozen=True)
class SearchConfig:
    enable_pinyin: bool = True
    enable_edit: bool = True
    token_aliases: Mapping[str, str] | None = None


@dataclass(frozen=True)
class _IndexedField:
    field: SearchField
    normalized: str
    compact: str
    tokens: tuple[str, ...]
    pinyin_full: str = ""
    pinyin_abbr: str = ""


@dataclass(frozen=True)
class _IndexedDocument:
    document: SearchDocument
    fields: tuple[_IndexedField, ...]


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").translate(_DASHES)
    value = _CAMEL_BOUNDARY_RE.sub(" ", value)
    value = value.casefold().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", value).strip()


def _compact(value: str) -> str:
    return "".join(char for char in normalize_text(value) if char.isalnum())


def tokenize(value: str, aliases: Mapping[str, str] | None = None) -> tuple[str, ...]:
    normalized = normalize_text(value)
    aliases = aliases or {}
    tokens: list[str] = []
    for token in _ASCII_PART_RE.findall(normalized):
        tokens.append(aliases.get(token, token))
    for segment in _CJK_RE.findall(normalized):
        chars = list(segment)
        if len(chars) == 1:
            tokens.append(aliases.get(chars[0], chars[0]))
        tokens.extend(
            aliases.get(chars[index] + chars[index + 1], chars[index] + chars[index + 1])
            for index in range(len(chars) - 1)
        )
        for alias, canonical in aliases.items():
            if any(ord(char) > 127 for char in alias) and alias in segment:
                tokens.append(canonical)
    return tuple(token for token in tokens if token)


def _contains_han(value: str) -> bool:
    return bool(_CJK_RE.search(value))


def _pinyin_forms(value: str) -> tuple[str, str]:
    if lazy_pinyin is None or Style is None or not _contains_han(value):
        return "", ""
    full = "".join(lazy_pinyin(value, errors="ignore")).casefold()
    abbreviation = "".join(
        lazy_pinyin(value, style=Style.FIRST_LETTER, errors="ignore")
    ).casefold()
    return full, abbreviation


class SearchIndex:
    """Small in-memory lexical index with deterministic, explainable ranking."""

    def __init__(
        self,
        documents: Sequence[SearchDocument],
        config: SearchConfig | None = None,
    ) -> None:
        self.config = config or SearchConfig()
        seen: set[str] = set()
        indexed: list[_IndexedDocument] = []
        for document in documents:
            if not document.id or document.id in seen:
                continue
            seen.add(document.id)
            fields: list[_IndexedField] = []
            for field in document.fields:
                normalized = normalize_text(field.value)
                if not normalized:
                    continue
                pinyin_full = ""
                pinyin_abbr = ""
                if self.config.enable_pinyin and field.role is FieldRole.ALIAS:
                    pinyin_full, pinyin_abbr = _pinyin_forms(field.value)
                fields.append(
                    _IndexedField(
                        field=field,
                        normalized=normalized,
                        compact=_compact(field.value),
                        tokens=tokenize(field.value, self.config.token_aliases),
                        pinyin_full=pinyin_full,
                        pinyin_abbr=pinyin_abbr,
                    )
                )
            indexed.append(_IndexedDocument(document=document, fields=tuple(fields)))
        self._documents = tuple(indexed)

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        groups: Sequence[str] = (),
    ) -> SearchResult:
        query_normalized = normalize_text(query)
        if not query_normalized or not self._documents:
            return SearchResult((), "not_found")
        group_set = {normalize_text(group) for group in groups if normalize_text(group)}
        candidates = tuple(
            document
            for document in self._documents
            if not group_set or normalize_text(document.document.group) in group_set
        )
        if not candidates:
            return SearchResult((), "not_found")

        hits = self._search_pass(query_normalized, candidates, allow_edit=False)
        if self.config.enable_edit and (not hits or hits[0].rank_tier < 620):
            hits = self._search_pass(query_normalized, candidates, allow_edit=True)
        hits.sort(key=_hit_sort_key)
        status = _result_status(hits)
        return SearchResult(tuple(hits[: max(1, top_k)]), status)

    def _search_pass(
        self,
        query: str,
        documents: Sequence[_IndexedDocument],
        *,
        allow_edit: bool,
    ) -> list[SearchHit]:
        query_tokens = set(tokenize(query, self.config.token_aliases))
        idf = _idf(documents, query_tokens)
        hits: list[SearchHit] = []
        for document in documents:
            best: SearchHit | None = None
            for field in document.fields:
                candidate = _score_field(query, field, document.document, allow_edit)
                if candidate is not None and (best is None or _better_hit(candidate, best)):
                    best = candidate

            bm25 = _bm25_document(document, query_tokens, idf)
            if bm25 > 0:
                candidate = SearchHit(
                    document_id=document.document.id,
                    group=document.document.group,
                    score=min(69, 50 + int(bm25)),
                    rank_tier=600,
                    raw_score=bm25,
                    matched_via="bm25",
                    field="text",
                )
                if best is None or _better_hit(candidate, best):
                    best = candidate
            if best is not None:
                hits.append(best)
        return hits


def _score_field(
    query: str,
    field: _IndexedField,
    document: SearchDocument,
    allow_edit: bool,
) -> SearchHit | None:
    role = field.field.role

    def hit(tier: int, raw: float, score: int, channel: str) -> SearchHit:
        return SearchHit(
            document_id=document.id,
            group=document.group,
            score=score,
            rank_tier=tier,
            raw_score=raw,
            matched_via=channel,
            field=field.field.name,
        )

    if query == field.normalized:
        if role is FieldRole.CODE:
            return hit(1000, 1, 100, "code_exact")
        if role is FieldRole.ALIAS:
            return hit(980, 1, 100, "alias_exact")
        if role is FieldRole.ATTRIBUTE:
            return hit(950, 1, 90, "attribute_exact")

    query_compact = _compact(query)
    if query_compact and query_compact == field.compact and query != field.normalized:
        if role is FieldRole.CODE:
            return hit(970, 1, 97, "code_compact_exact")
        if role is FieldRole.ALIAS:
            return hit(940, 1, 94, "alias_compact_exact")

    query_length = len(query)
    field_length = len(field.normalized)
    if role is FieldRole.CODE and field_length >= 3 and field.normalized in query:
        return hit(920, field_length / max(1, query_length), 92, "query_contains_code")
    if query_length >= 2:
        if role is FieldRole.CODE:
            if field.normalized.startswith(query):
                return hit(860, query_length / field_length, 86, "code_prefix")
            if query in field.normalized:
                return hit(800, query_length / field_length, 80, "code_contains")
            if field.normalized in query and field_length >= 2:
                return hit(720, field_length / query_length, 50, "query_contains_code")
        elif role is FieldRole.ALIAS:
            if field.normalized.startswith(query):
                return hit(840, query_length / field_length, 84, "alias_prefix")
            if query in field.normalized:
                return hit(780, query_length / field_length, 80, "alias_contains")
            if field.normalized in query and field_length >= 2:
                return hit(740, field_length / query_length, 60, "query_contains_alias")
        elif role is FieldRole.ATTRIBUTE:
            if query in field.normalized:
                return hit(700, query_length / field_length, 55, "attribute_contains")
            if field.normalized in query and field_length >= 2:
                return hit(680, field_length / query_length, 55, "query_contains_attribute")

    if role is FieldRole.ALIAS and _contains_han(query) and _contains_han(field.normalized):
        query_alias_tokens = set(tokenize(query))
        field_alias_tokens = set(field.tokens)
        overlap = query_alias_tokens & field_alias_tokens
        if overlap:
            coverage = len(overlap) / max(1, len(field_alias_tokens))
            if coverage >= 0.5:
                return hit(760, coverage, 70 + int(coverage * 9), "alias_token_overlap")

    ascii_query = query.isascii() and query.isalpha()
    if role is FieldRole.ALIAS and ascii_query:
        if field.pinyin_full and query == field.pinyin_full:
            return hit(670, 1, 67, "pinyin_full_exact")
        if len(query) >= 3 and field.pinyin_abbr and query == field.pinyin_abbr:
            return hit(650, 1, 65, "pinyin_abbr_exact")
        if len(query) >= 4 and field.pinyin_full.startswith(query):
            return hit(620, len(query) / len(field.pinyin_full), 62, "pinyin_prefix")

    if not allow_edit or len(query) < 3 or role is FieldRole.TEXT:
        return None
    # A code/attribute made from a different writing system is not a typo.
    # Treating it as one would silently turn edit distance into an accidental
    # cross-language translator.
    if _contains_han(query) != _contains_han(field.normalized):
        return None
    if not _edit_candidate(query, field.normalized, ascii_query):
        return None
    similarity = _edit_similarity(query, field.normalized)
    if similarity < _edit_threshold(len(query)):
        return None
    # Edit matching is only enabled after stronger lexical channels have proved
    # insufficient.  Once admitted, a close correction should outrank the weak
    # token overlap that triggered the fallback, while remaining below pinyin
    # and every direct field match.
    return hit(610, similarity, int(similarity * 100), f"edit_{role}")


def _idf(documents: Sequence[_IndexedDocument], query_tokens: set[str]) -> dict[str, float]:
    count = len(documents)
    result: dict[str, float] = {}
    for token in query_tokens:
        frequency = sum(token in {item for field in doc.fields for item in field.tokens} for doc in documents)
        result[token] = math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
    return result


def _bm25_field(tokens: tuple[str, ...], query_tokens: set[str], idf: Mapping[str, float]) -> float:
    if not tokens:
        return 0.0
    frequencies = Counter(tokens)
    length = max(1, len(tokens))
    score = 0.0
    for token in query_tokens:
        term_frequency = frequencies.get(token, 0)
        if term_frequency:
            score += idf[token] * (term_frequency * 2.2) / (
                term_frequency + 1.2 * (0.25 + 0.75 * length / 12)
            )
    return score


def _bm25_document(
    document: _IndexedDocument,
    query_tokens: set[str],
    idf: Mapping[str, float],
) -> float:
    text_tokens = {
        token
        for field in document.fields
        if field.field.role is FieldRole.TEXT
        for token in field.tokens
    }
    # A single normalized action word (query/write/delete) is not a subject
    # match.  Requiring at least one non-action overlap prevents a Chinese
    # request from ranking arbitrary English tools merely because both say
    # "list" or "get".
    subject_query_tokens = query_tokens - {"query", "write", "delete"}
    if subject_query_tokens and not (subject_query_tokens & text_tokens):
        return 0.0
    return sum(
        _bm25_field(field.tokens, query_tokens, idf)
        for field in document.fields
        if field.field.role is FieldRole.TEXT
    )


def _edit_candidate(query: str, candidate: str, ascii_query: bool) -> bool:
    if not query or not candidate or abs(len(query) - len(candidate)) > 2:
        return False
    if query[0] != candidate[0]:
        return False
    if ascii_query and (not candidate.isascii() or not candidate.isalpha()):
        return False
    return len(query) < 5 or query[-1] == candidate[-1]


def _edit_threshold(length: int) -> float:
    if length <= 2:
        return 2.0
    if length <= 4:
        return 0.85
    if length <= 8:
        return 0.78
    return 0.70


def _edit_similarity(left: str, right: str) -> float:
    length = max(len(left), len(right))
    return 1.0 if length == 0 else 1 - _levenshtein(left, right) / length


def _levenshtein(left: str, right: str) -> int:
    if len(left) > len(right):
        left, right = right, left
    previous = list(range(len(left) + 1))
    for row, right_char in enumerate(right, start=1):
        current = [row]
        for column, left_char in enumerate(left, start=1):
            cost = 0 if left_char == right_char else 1
            current.append(min(current[-1] + 1, previous[column] + 1, previous[column - 1] + cost))
        previous = current
    return previous[-1]


def _better_hit(left: SearchHit, right: SearchHit) -> bool:
    return _hit_sort_key(left) < _hit_sort_key(right)


def _hit_sort_key(hit: SearchHit) -> tuple[float | str, ...]:
    return (-hit.rank_tier, -hit.raw_score, hit.group, hit.document_id)


def _result_status(hits: Sequence[SearchHit]) -> str:
    if not hits:
        return "not_found"
    ordered = sorted(hits, key=_hit_sort_key)
    if (
        len(ordered) > 1
        and ordered[0].rank_tier == ordered[1].rank_tier
        and ordered[0].raw_score - ordered[1].raw_score < 0.05
    ):
        return "ambiguous"
    return "high_confidence" if ordered[0].rank_tier >= 920 else "low_confidence"
