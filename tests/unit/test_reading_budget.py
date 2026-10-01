from __future__ import annotations

import unittest

from deep_agent.agent.attachment_extractors import AttachmentParseError
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.files.reading_budget import (
    ReadingBudget,
    fitting_prefix_count,
    paginate_items,
    reading_budget_tokens,
)


class ReadingBudgetTests(unittest.TestCase):
    def test_context_window_alone_selects_two_tiers(self) -> None:
        for window, expected in ((None, 24_000), (0, 24_000), (128_000, 24_000),
                                 (255_999, 24_000), (256_000, 48_000),
                                 (1_000_000, 48_000)):
            with self.subTest(window=window):
                self.assertEqual(reading_budget_tokens(window), expected)
                self.assertEqual(ReadingBudget(context_window_tokens=window).tokens, expected)

    def setUp(self) -> None:
        # Scale a deterministic tokenizer to exercise tiny pages while keeping
        # the production 24K/48K policy intact. JSON wrapping consumes 14 chars.
        self.budget = ReadingBudget(TokenEstimator("test", _counter=lambda text: len(text) * 1_000))
        self.envelope = lambda page: {"content": page.content}

    def page(self, items: list[str], **kwargs):
        return paginate_items(items, budget=self.budget, envelope=self.envelope, **kwargs)

    def test_full_serialized_envelope_counts_towards_budget(self) -> None:
        result = self.page(["a" * 100], total_count=1, offset=1, limit=1)
        self.assertEqual(result.content, "a" * 10)
        self.assertEqual(result.next_char_offset, 10)
        self.assertTrue(self.budget.fits(self.envelope(result)))
        self.assertFalse(self.budget.fits({"content": result.content + "a"}))

    def test_nonfitting_next_item_is_deferred_whole(self) -> None:
        result = self.page(["first", "second"], total_count=2, offset=1, limit=2)
        self.assertEqual(result.content, "first")
        self.assertEqual(result.returned_count, 1)
        self.assertEqual((result.next_offset, result.next_char_offset), (2, 0))
        self.assertEqual(result.truncated_by, "output_size")

    def test_explicit_item_limit_remains_a_source_range(self) -> None:
        result = self.page(["a", "b", "c"], total_count=3, offset=1, limit=2)
        self.assertEqual(result.content, "a\nb")
        self.assertEqual(result.returned_count, 2)
        self.assertEqual(result.truncated_by, "limit")
        self.assertEqual(result.next_offset, 3)

    def test_continuation_can_finish_item_and_read_following_items(self) -> None:
        result = self.page(["abcdef", "next"], total_count=2, offset=1, limit=2, char_offset=4)
        self.assertEqual(result.content, "ef\nnext")
        self.assertEqual(result.returned_count, 2)
        self.assertFalse(result.has_more)

    def test_unicode_long_item_never_loses_or_repeats_characters(self) -> None:
        original = "中文🙂abc\"\\\t" * 35
        fragments: list[str] = []
        char_offset = 0
        for _ in range(len(original)):
            result = self.page([original], total_count=1, offset=1, limit=1, char_offset=char_offset)
            self.assertTrue(result.content)
            self.assertTrue(self.budget.fits(self.envelope(result)))
            fragments.append(result.content)
            if not result.has_more:
                self.assertIsNone(result.next_offset)
                self.assertIsNone(result.next_char_offset)
                break
            self.assertEqual(result.next_offset, 1)
            self.assertGreater(result.next_char_offset, char_offset)
            char_offset = result.next_char_offset
        else:
            self.fail("Unicode continuation did not terminate")
        self.assertEqual("".join(fragments), original)

    def test_thousands_of_short_items_fit_without_old_count_cap(self) -> None:
        budget = ReadingBudget(TokenEstimator.conservative())
        items = ["x"] * 5_000
        result = paginate_items(items, total_count=len(items), offset=1,
                                limit=len(items), budget=budget, envelope=self.envelope)
        self.assertEqual(result.content, "\n".join(items))
        self.assertEqual(result.returned_count, len(items))
        self.assertFalse(result.has_more)

    def test_empty_items_and_eof_keep_completed_page(self) -> None:
        for items, total, offset in (([], 0, 1), ([], 2, 3), ([""], 1, 1)):
            with self.subTest(items=items, offset=offset):
                result = self.page(items, total_count=total, offset=offset, limit=2)
                self.assertEqual(result.content, "")
                self.assertEqual(result.returned_count, len(items))
                self.assertFalse(result.has_more)

    def test_oversized_metadata_fails_instead_of_returning_empty_loop(self) -> None:
        with self.assertRaisesRegex(AttachmentParseError, "Token 预算"):
            paginate_items(["a"], total_count=1, offset=1, limit=1, budget=self.budget,
                           envelope=lambda page: {"content": page.content, "metadata": "x" * 30})

    def test_invalid_character_cursor_is_rejected(self) -> None:
        for items, cursor in ((["abc"], -1), (["abc"], 3), (["abc"], True),
                              (["abc"], 1.5), (["abc"], None), ([], 1)):
            with self.subTest(items=items, cursor=cursor):
                with self.assertRaises(AttachmentParseError):
                    self.page(items, total_count=len(items), offset=1, limit=2, char_offset=cursor)

    def test_prefix_scan_uses_bounded_probes(self) -> None:
        probes: list[int] = []

        def fits(count: int) -> bool:
            probes.append(count)
            return count <= 1_234

        self.assertEqual(fitting_prefix_count(1_000_000, fits), 1_234)
        self.assertLess(len(probes), 25)
        self.assertLessEqual(max(probes), 2_048)
        self.assertEqual(fitting_prefix_count(0, fits), 0)

    def test_huge_item_continuation_never_estimates_entire_remaining_suffix(self) -> None:
        sizes: list[int] = []

        def counter(text: str) -> int:
            sizes.append(len(text))
            return len(text)

        budget = ReadingBudget(TokenEstimator("test", _counter=counter))
        original = "x" * 2_000_000
        cursor = 0
        for _ in range(3):
            page = paginate_items([original], total_count=1, offset=1, limit=1,
                                  char_offset=cursor, budget=budget, envelope=self.envelope)
            self.assertTrue(page.content)
            self.assertGreater(page.next_char_offset, cursor)
            cursor = page.next_char_offset
        self.assertLessEqual(max(sizes), 32_800)

    def test_sdk_serialized_key_order_is_checked_alongside_canonical_json(self) -> None:
        def counter(text: str) -> int:
            return 25_000 if text.startswith('{"z"') else len(text)

        budget = ReadingBudget(TokenEstimator("test", _counter=counter))
        value = {"z": 1, "a": 2}
        self.assertLess(budget.estimator.text(value), budget.tokens)
        self.assertFalse(budget.fits(value))

    def test_giant_later_item_is_not_repeatedly_serialized_during_item_search(self) -> None:
        sizes: list[int] = []

        def counter(text: str) -> int:
            sizes.append(len(text))
            return len(text)

        budget = ReadingBudget(TokenEstimator("test", _counter=counter))
        items = ["before"] * 31 + ["x" * 2_000_000, "after"]
        page = paginate_items(items, total_count=len(items), offset=1, limit=len(items),
                              budget=budget, envelope=self.envelope)
        self.assertEqual(page.returned_count, 31)
        self.assertEqual(page.next_offset, 32)
        self.assertLessEqual(max(sizes), 32_800)

    def test_long_item_optimization_is_not_a_character_return_cap(self) -> None:
        budget = ReadingBudget(TokenEstimator("test", _counter=lambda text: len(text) // 10))
        items = ["before", "x" * 120_000]
        page = paginate_items(items, total_count=2, offset=1, limit=2,
                              budget=budget, envelope=self.envelope)
        self.assertEqual(page.content, "\n".join(items))
        self.assertFalse(page.has_more)


if __name__ == "__main__":
    unittest.main()
