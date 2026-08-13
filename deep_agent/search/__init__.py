"""Reusable, explainable text retrieval primitives."""

from deep_agent.search.lexical import (
    FieldRole,
    SearchConfig,
    SearchDocument,
    SearchField,
    SearchHit,
    SearchIndex,
    SearchResult,
    normalize_text,
    tokenize,
)

__all__ = [
    "FieldRole",
    "SearchConfig",
    "SearchDocument",
    "SearchField",
    "SearchHit",
    "SearchIndex",
    "SearchResult",
    "normalize_text",
    "tokenize",
]
