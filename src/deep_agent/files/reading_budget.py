"""Token-bounded pages for both authorized reading tools.

These budgets deliberately do not share the general overflow threshold policy.
The envelope callback measures the exact result exposed to the model, including
continuation arguments and source positions, rather than only its text body.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.messages import ToolReturnPart

from deep_agent.agent.attachment_extractors import AttachmentParseError, DocumentPage
from deep_agent.agent.context.estimator import TokenEstimator


def reading_budget_tokens(context_window_tokens: int | None) -> int:
    return 48_000 if context_window_tokens is not None and context_window_tokens >= 256_000 else 24_000


@dataclass(frozen=True)
class ReadingBudget:
    estimator: TokenEstimator = field(default_factory=TokenEstimator.conservative)
    context_window_tokens: int | None = None

    @property
    def tokens(self) -> int:
        return reading_budget_tokens(self.context_window_tokens)

    def fits(self, value: Any) -> bool:
        # The context estimator uses canonical JSON; providers use the SDK's
        # serialized tool return (its key order may affect native tokenizers).
        # Check both, including the actual wire representation, at the boundary.
        return (
            self.estimator.text(value) <= self.tokens
            and self.estimator.text(ToolReturnPart("reader", value).model_response_str()) <= self.tokens
        )

    def item_may_fit(self, item: str) -> bool:
        """Bound probes of unusually long items before joining a candidate page.

        The character threshold selects an optimization, never an output cap.
        Even text with a high tokenizer compression ratio can pass in full.
        Shorter items still require the complete envelope check.
        """
        if len(item) <= self.tokens * 2:
            return True
        return fitting_prefix_count(
            len(item), lambda chars: self.fits({"content": item[:chars]}),
        ) == len(item)


def fitting_prefix_count(max_count: int, fits: Callable[[int], bool]) -> int:
    """Find a fitting prefix with bounded growing probes, then binary refinement.

    Only prefixes near one page are serialized. We never repeatedly estimate an
    entire source document. Every accepted prefix has been measured, so tokenizer
    boundary effects cannot produce an over-budget result.
    """
    if max_count == 0:
        return 0
    low, high = 0, 1
    while fits(high):
        low = high
        if high == max_count:
            return high
        high = min(max_count, high * 2)
    while low + 1 < high:
        middle = (low + high) // 2
        if fits(middle):
            low = middle
        else:
            high = middle
    return low


def paginate_items(
    items: Sequence[str],
    *,
    total_count: int,
    offset: int,
    limit: int,
    budget: ReadingBudget,
    envelope: Callable[[DocumentPage], Any],
    char_offset: int = 0,
) -> DocumentPage:
    """Keep whole source items; split only an item too large for an empty page.

    Offsets count Unicode code points within the rendered source item. Advancing
    to another item adds the same newline separator as the existing readers.
    """
    if isinstance(char_offset, bool) or not isinstance(char_offset, int) or char_offset < 0:
        raise AttachmentParseError("char_offset 必须是非负整数（从 0 开始）")
    if offset < 1 or limit < 1:
        raise AttachmentParseError("offset 和 limit 必须是正整数")
    count = min(len(items), limit)
    if char_offset and (not count or char_offset >= len(items[0])):
        raise AttachmentParseError("char_offset 超出当前项正文范围；请使用返回的续读位置")

    def whole_page(size: int) -> DocumentPage:
        has_more = offset + size <= total_count
        reason = ("limit" if size == limit else "output_size") if has_more else None
        return DocumentPage(
            content="\n".join(
                items[index][char_offset:] if index == 0 else items[index]
                for index in range(size)
            ),
            returned_count=size,
            has_more=has_more,
            next_offset=offset + size if has_more else None,
            truncated_by=reason,
            next_char_offset=0 if has_more else None,
        )

    if count == 0:
        page = whole_page(0)
    else:
        first = items[0]
        remaining_chars = len(first) - char_offset

        def first_complete_page(chars: int) -> DocumentPage:
            has_more = offset + 1 <= total_count
            return DocumentPage(
                content=first[char_offset : char_offset + chars], returned_count=1,
                has_more=has_more, next_offset=offset + 1 if has_more else None,
                next_char_offset=0 if has_more else None,
                truncated_by=("limit" if limit == 1 else "output_size") if has_more else None,
            )

        # Probe the first item by characters before considering whole items.
        # Otherwise every continuation of one huge line estimates the entire
        # remaining suffix again, making a full read quadratic in source size.
        first_chars = fitting_prefix_count(
            remaining_chars,
            lambda chars: budget.fits(envelope(first_complete_page(chars))),
        )
        first_fits = first_chars == remaining_chars and budget.fits(envelope(first_complete_page(first_chars)))
        if first_fits:
            checked: dict[int, bool] = {}

            def whole_fits(size: int) -> bool:
                # A giant later item must not be joined and estimated again
                # for every rejected binary-search candidate.
                for index in range(1, size):
                    if len(items[index]) > budget.tokens * 2:
                        if index not in checked:
                            checked[index] = budget.item_may_fit(items[index])
                        if not checked[index]:
                            return False
                return budget.fits(envelope(whole_page(size)))

            size = fitting_prefix_count(count, whole_fits)
            page = whole_page(size)
        else:

            def partial_page(chars: int) -> DocumentPage:
                return DocumentPage(
                    content=first[char_offset : char_offset + chars], returned_count=1, has_more=True,
                    next_offset=offset, next_char_offset=char_offset + chars,
                    truncated_by="item_size",
                )

            chars = fitting_prefix_count(
                min(first_chars, max(0, remaining_chars - 1)),
                lambda chars: budget.fits(envelope(partial_page(chars))),
            )
            if chars == 0:
                raise AttachmentParseError("返回元数据超过读取 Token 预算，无法安全续读")
            page = partial_page(chars)
    if not budget.fits(envelope(page)):
        raise AttachmentParseError("返回元数据超过读取 Token 预算")
    return page
