import unittest

from deep_agent.services.wecom_app_format import format_markdown, split_text


class WecomAppFormatTests(unittest.TestCase):
    def assert_budget(self, messages):
        self.assertTrue(all(len(message.encode("utf-8")) <= 2048 for message in messages))

    def test_supported_format_and_heading(self):
        self.assertEqual(format_markdown("# 回答\n\n**加粗**、`code`\n> 引用\n1. 第一\n- 第二"),
                         ["**回答**\n\n**加粗**、`code`\n> 引用\n1. 第一\n- 第二"])

    def test_table_is_readable_fields(self):
        self.assertEqual(format_markdown("| 月份 | 金额 |\n| --- | ---: |\n| 一月 | 100 |\n| 二月 | 200 |"),
                         ["月份：一月；金额：100\n月份：二月；金额：200\n"])

    def test_image_becomes_link_and_parentheses_preserved(self):
        self.assertEqual(format_markdown("![图表](https://example.test/a(b).png)"),
                         ["[图表](https://example.test/a(b).png)"])

    def test_fenced_code_not_parsed_as_heading_table_or_image(self):
        content = "# heading\n  x = '**value**'\n| --- |\n![x](url)\n"
        self.assertEqual(format_markdown("```python\n" + content + "```"), [content])

    def test_plain_utf8_split_lossless(self):
        original = "开始😀" + "中" * 2500
        parts = split_text(original)
        self.assertEqual("".join(parts), original)
        self.assert_budget(parts)
        self.assertEqual("".join(format_markdown(original)), original)

    def test_breaks_prefer_paragraphs(self):
        original = "甲" * 300 + "\n\n" + "乙" * 500
        parts = format_markdown(original)
        self.assertEqual(parts[0], "甲" * 300 + "\n\n")
        self.assertEqual("".join(parts), original)

    def test_long_bold_reopens_each_chunk_without_losing_content(self):
        content = "中😀" * 1000
        parts = format_markdown("**" + content + "**")
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(p.startswith("**") and p.endswith("**") for p in parts))
        self.assertEqual("".join(p[2:-2] for p in parts), content)
        self.assert_budget(parts)

    def test_long_inline_code_closes_each_chunk(self):
        content = "abcdef" * 1000
        parts = format_markdown("`" + content + "`")
        self.assertEqual("".join(p[1:-1] for p in parts), content)
        self.assertTrue(all(p.startswith("`") and p.endswith("`") for p in parts))
        self.assert_budget(parts)

    def test_link_moves_intact_to_next_message(self):
        link = "[详情](https://example.test/report)"
        parts = format_markdown("a" * 2040 + link)
        self.assertEqual(parts, ["a" * 2040, link])

    def test_link_inside_bold_keeps_both_complete(self):
        link = "[详情](https://example.test/report)"
        parts = format_markdown("a" * 2040 + "**" + link + "**")
        self.assertEqual(parts, ["a" * 2040, "**" + link + "**"])

    def test_oversized_link_degrades_to_lossless_readable_url(self):
        url = "https://example.test/" + "a" * 3000
        parts = format_markdown("[详情](" + url + ")")
        self.assertEqual("".join(parts), "详情 (" + url + ")")
        self.assert_budget(parts)

    def test_boundary_budget_with_multibyte_styled_content(self):
        parts = format_markdown("a" * 2043 + "**😀**")
        self.assertEqual(parts, ["a" * 2043, "**😀**"])
        self.assert_budget(parts)

    def test_blank_input(self):
        self.assertEqual(format_markdown(" \n"), [])
        self.assertEqual(split_text(""), [])

    def test_url_special_characters_remain_unchanged(self):
        link = "[查询](https://example.test/a_b/**report**?q=one_two&x=1)"
        self.assertEqual(format_markdown(link), [link])

    def test_multiline_bold_is_closed_at_each_boundary(self):
        content = "第一行\n" + "中" * 1500
        parts = format_markdown("**" + content + "**")
        self.assertTrue(all(p.startswith("**") and p.endswith("**") for p in parts))
        self.assertEqual("".join(p[2:-2] for p in parts), content)
        self.assert_budget(parts)

    def test_fenced_code_long_line_preserves_all_characters(self):
        code = "    value = '" + "中文😀" * 1200 + "'\n"
        parts = format_markdown("~~~python\n" + code + "~~~")
        self.assertEqual("".join(parts), code)
        self.assert_budget(parts)

    def test_empty_table_retains_headers(self):
        self.assertEqual(format_markdown("| 月份 | 金额 |\n| --- | ---: |"),
                         ["月份；金额\n"])

    def test_short_table_row_retains_missing_column_label(self):
        self.assertEqual(format_markdown("| 月份 | 金额 |\n| --- | ---: |\n| 一月 |"),
                         ["月份：一月；金额：\n"])

    def test_single_line_triple_backtick_content_is_not_discarded(self):
        self.assertEqual(format_markdown("```hello```"), ["`hello`"])

    def test_multiple_backtick_inline_spans_preserve_content(self):
        self.assertEqual(format_markdown("前 ``hello`` 后"), ["前 `hello` 后"])
        self.assertEqual(format_markdown("``contains `literal` ticks``"),
                         ["contains `literal` ticks"])

    def test_nonempty_fence_like_code_line_is_not_discarded(self):
        self.assertEqual(format_markdown("```python\n```still code\n```"),
                         ["```still code\n"])

    def test_indented_heading_is_not_converted(self):
        self.assertEqual(format_markdown("    # comment"), ["    # comment"])
