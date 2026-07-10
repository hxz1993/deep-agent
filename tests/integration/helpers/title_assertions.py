from __future__ import annotations


def assert_title_differs_from_prompt(title: str | None, user_prompt: str | None) -> None:
    assert title, "expected non-empty title"
    assert user_prompt, "expected non-empty user_prompt"
    normalized_title = title.strip()
    normalized_prompt = user_prompt.strip()
    assert normalized_title != normalized_prompt, "title should differ from raw user prompt"


def assert_title_keywords(title: str, *keywords: str) -> None:
    lowered = title.lower()
    if not keywords:
        return
    assert any(keyword.lower() in lowered for keyword in keywords), (
        f"title {title!r} missing expected keywords: {keywords}"
    )


def assert_title_reasonable(title: str | None) -> None:
    assert title, "expected non-empty title"
    assert 1 <= len(title) <= 80, f"unexpected title length: {len(title)}"


def assert_fallback_title(title: str | None, user_prompt: str | None) -> None:
    assert title, "expected fallback title"
    assert user_prompt, "expected user_prompt for fallback comparison"
    assert title.strip() == user_prompt.strip() or user_prompt.strip() in title.strip()
