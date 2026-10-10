"""Semantic regression cases for the OCR model reading projection."""
from __future__ import annotations

from copy import deepcopy
from html.parser import HTMLParser
import threading
import unittest

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.documents.adaptation import (
    ADAPTATION_VERSION, DocumentAdaptationCancelled, adapt_document,
)
from deep_agent.documents.models import DocumentPage, ParsedDocument


def document(text: str, *, page=23) -> ParsedDocument:
    return ParsedDocument("paddle", "PaddleOCR-VL-1.6",
                          [DocumentPage(page, text, native={"raw": "unchanged"})],
                          46, warnings=["recognition may be incomplete"], native={"source": "unchanged"})


class _TableEvidence(HTMLParser):
    """Observe cells and spans independently from the production renderer."""
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.cells, self.stack, self.tables = [], [], 0
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables += 1
        if tag in {"td", "th"}:
            cell = {"text": "", "tag": tag, "attrs": dict(attrs)}
            self.cells.append(cell)
            self.stack.append(cell)
        if tag == "br" and self.stack:
            self.stack[-1]["text"] += "\n"

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self.stack:
            self.stack[-1]["text"] += data


class _InlineEvidence(HTMLParser):
    """Observe emphasis/break roles and literal text in document order."""
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.events = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag in {"b", "strong", "i", "em", "br"}:
            self.events.append(("start", tag))

    def handle_endtag(self, tag):
        if tag in {"b", "strong", "i", "em"}:
            self.events.append(("end", tag))

    def handle_data(self, data):
        self.events.append(("text", data))


class DocumentAdaptationTests(unittest.TestCase):
    def adapt(self, text, *, estimator=None):
        return adapt_document(document(text), estimator or TokenEstimator.conservative())

    def test_plain_markdown_and_code_are_byte_for_byte_unchanged(self):
        text = ("# 条款\n\n12,500.00 元，1 234.50。🙂é\n"
                "`<table><tr><td>literal</td></tr></table>`\n"
                "``<img src='data:image/png;base64,AAA'>``\n"
                "```html\n<table style='x'><tr><td>代码</td></tr></table>\n```\n"
                "~~~html\n<img src='https://example.com/p.png' alt='示例'>\n~~~\n"
                "[原文](https://example.com/a?x=1&y=2)\n"
                "![图](images/a.png)\n普通 < 关系和 &amp; 字面量")
        result = self.adapt(text)
        self.assertEqual(result.pages[0].content, text)
        self.assertEqual(result.stats.table_count, 0)
        self.assertEqual(result.stats.image_placeholder_count, 0)

    def test_unclosed_fence_remains_protected_to_page_end(self):
        text = "正文\n```xml\n<table><tr><td>不是解析结果</td></tr></table>"
        self.assertEqual(self.adapt(text).pages[0].content, text)

    def test_markdown_escaped_html_and_image_syntax_are_literal_text(self):
        text = r'\<table style="literal">\<tr>\<td>123.45\</td>\</tr>\</table> \![literal](data:image/png;base64,AAA)'
        self.assertEqual(self.adapt(text).pages[0].content, text)

    def test_data_first_row_is_kept_under_an_empty_header(self):
        text = "前文\n<table style='width:100%'><tr><td>2027</td><td>12,500.00元</td></tr><tr><td>2028</td><td>13,000.00元</td></tr></table>\n脚注：单位为元。"
        result = self.adapt(text)
        content = result.pages[0].content
        self.assertIn("|||\n|---|---|\n|2027|12,500.00元|\n|2028|13,000.00元|", content)
        self.assertTrue(content.startswith("前文\n"))
        self.assertTrue(content.endswith("\n脚注：单位为元。"))
        self.assertEqual(result.stats.markdown_table_count, 1)

    def test_explicit_header_caption_lines_entities_and_pipes(self):
        text = ("<table><caption>租金 &amp; 税额</caption><thead><tr><th>期间</th><th>金额</th></tr></thead>"
                "<tbody><tr><td>第一行<br>第二行\n第三行</td><td>1|2 &#124; 3 &amp; 4</td></tr></tbody></table>")
        content = self.adapt(text).pages[0].content
        self.assertIn("租金 &amp; 税额\n\n|期间|金额|\n|---|---|", content)
        self.assertIn("第一行<br>第二行<br>第三行", content)
        self.assertIn(r"1\|2 \| 3 &amp; 4", content)

    def test_html_literal_text_does_not_acquire_markdown_or_html_roles(self):
        literal = "&lt;button&gt;金额&lt;/button&gt; &amp; &#124; *literal* _cost_center_ &#96;code&#96; [link](x) ![image](x) ~~plain~~"
        text = ("<table><tr><th>项目</th><th>金额</th></tr>"
                f"<tr><td>{literal}</td><td>123.45</td></tr>" +
                "<tr><td>普通项目</td><td>123.45</td></tr>" * 15 + "</table>")
        result = self.adapt(text)
        content = result.pages[0].content
        self.assertEqual(result.stats.markdown_table_count, 1)
        self.assertIn("&lt;button&gt;金额&lt;/button&gt; &amp;", content)
        self.assertNotIn("<button>", content)
        self.assertIn(r"\*literal\*", content)
        self.assertIn(r"\_cost\_center\_", content)
        self.assertIn(r"\`code\`", content)
        self.assertIn(r"\[link\]\(x\)", content)
        self.assertIn(r"\!\[image\]\(x\)", content)
        self.assertIn(r"\~\~plain\~\~", content)
        self.assertEqual(content.count("123.45"), 16)

    def test_actual_html_emphasis_and_breaks_keep_their_roles(self):
        text = ("<table><tr><th>项目</th><th>金额</th></tr>"
                "<tr><td><strong>实际粗体</strong> *字面星号* <em>实际斜体</em><br>第二行 &lt;br&gt;</td><td>123.45</td></tr>" +
                "<tr><td>普通项目</td><td>123.45</td></tr>" * 10 + "</table>")
        result = self.adapt(text)
        content = result.pages[0].content
        self.assertEqual(result.stats.markdown_table_count, 0)
        self.assertEqual(result.stats.compact_html_table_count, 1)
        self.assertEqual(_InlineEvidence(content).events, _InlineEvidence(text).events)
        self.assertIn("<strong>实际粗体</strong>", content)
        self.assertIn("*字面星号*", content)
        self.assertIn("<em>实际斜体</em><br>第二行 &lt;br&gt;", content)

    def test_adjacent_nested_and_text_neighbor_emphasis_keeps_html_roles(self):
        cases = (
            "<strong>literal *star*</strong><em>under_score</em><br>next",
            "<strong>outer <em>nested_under</em> end</strong>",
            "<em>outer <strong>nested*star</strong> end</em>",
            "前<strong>word</strong>后<em>斜体</em>尾",
            "<b>literal *star*</b><i>under_score</i>",
        )
        for inline in cases:
            with self.subTest(inline=inline):
                text = ("<table class='display'><tr><th>项目</th><th>金额</th></tr>"
                        f"<tr><td>{inline}</td><td>123.45</td></tr>" +
                        "<tr><td>普通项目</td><td>123.45</td></tr>" * 15 + "</table>")
                result = self.adapt(text)
                content = result.pages[0].content
                self.assertEqual(result.stats.markdown_table_count, 0)
                self.assertEqual(result.stats.compact_html_table_count, 1)
                self.assertEqual(_InlineEvidence(content).events, _InlineEvidence(text).events)
                self.assertEqual([cell["text"] for cell in _TableEvidence(content).cells],
                                 [cell["text"] for cell in _TableEvidence(text).cells])
                self.assertNotIn("class=", content)

    def test_literal_entity_text_is_not_decoded_twice_and_whitespace_survives(self):
        text = ("<table><tr><th>项目</th><th>金额</th></tr>"
                "<tr><td>  &amp;lt;button&amp;gt;  A  B\n下一行  </td><td>123.45</td></tr>" +
                "<tr><td>普通项目</td><td>123.45</td></tr>" * 10 + "</table>")
        result = self.adapt(text)
        self.assertEqual(result.stats.markdown_table_count, 0)
        self.assertEqual(result.stats.compact_html_table_count, 1)
        native_cells = _TableEvidence(text).cells
        projected_cells = _TableEvidence(result.pages[0].content).cells
        self.assertEqual([(cell["tag"], cell["text"]) for cell in projected_cells],
                         [(cell["tag"], cell["text"]) for cell in native_cells])
        self.assertEqual(projected_cells[2]["text"], "  &lt;button&gt;  A  B\n下一行  ")

    def test_cell_edge_whitespace_requires_html_semantics(self):
        values = (
            " leading", "trailing ", "  both  ", "\nleading", "trailing\n",
            "\tleading", "trailing\t", "&nbsp;leading", "trailing&#32;",
            "<span> wrapped </span>", "<br>leading", "trailing<br>",
        )
        for value in values:
            with self.subTest(value=value):
                text = ("<table class='display'><tr><th>项目</th><th>金额</th></tr>"
                        f"<tr><td>{value}</td><td>123.45</td></tr>" +
                        "<tr><td>普通项目</td><td>123.45</td></tr>" * 10 + "</table>")
                result = self.adapt(text)
                self.assertEqual(result.stats.markdown_table_count, 0)
                self.assertEqual(result.stats.compact_html_table_count, 1)
                self.assertEqual([(cell["tag"], cell["text"]) for cell in _TableEvidence(result.pages[0].content).cells],
                                 [(cell["tag"], cell["text"]) for cell in _TableEvidence(text).cells])

    def test_internal_whitespace_and_once_decoded_entities_remain_markdown_safe(self):
        text = ("<table><tr><th>项目</th><th>金额</th></tr>"
                "<tr><td>A  B &amp;lt;literal&amp;gt;\n下一行</td><td>123.45</td></tr>" +
                "<tr><td>普通项目</td><td>123.45</td></tr>" * 10 + "</table>")
        result = self.adapt(text)
        self.assertEqual(result.stats.markdown_table_count, 1)
        self.assertIn("|A  B &amp;lt;literal&amp;gt;<br>下一行|", result.pages[0].content)

    def test_native_cell_backticks_and_literal_tags_stay_semantically_equal(self):
        text = "<table class='visual'><tr><td>&lt;button&gt;金额&lt;/button&gt; &amp; &#124; *literal* `code` [link](x)</td></tr></table>"
        result = self.adapt(text)
        self.assertEqual([cell["text"] for cell in _TableEvidence(result.pages[0].content).cells],
                         [cell["text"] for cell in _TableEvidence(text).cells])
        self.assertNotIn("<button>", result.pages[0].content)
        self.assertIn("&lt;button&gt;", result.pages[0].content)
        self.assertEqual(result.stats.markdown_table_count, 0)

    def test_th_header_does_not_drop_first_row_or_duplicate_values(self):
        text = "<table><tr><th>年份</th><th>金额</th></tr><tr><td>2027</td><td>123.45</td></tr><tr><td>2027</td><td>123.45</td></tr></table>"
        content = self.adapt(text).pages[0].content
        self.assertEqual(content.count("|2027|123.45|"), 2)
        self.assertTrue(content.lstrip().startswith("|年份|金额|\n|---|---|"))

    def test_mixed_thead_cell_roles_are_not_upgraded_to_markdown_headers(self):
        for roles in (("th", "td", "td"), ("th", "td")):
            with self.subTest(roles=roles):
                heading = "".join(f"<{tag}>列{index}</{tag}>" for index, tag in enumerate(roles))
                data = "".join(f"<td>值{index}</td>" for index in range(len(roles)))
                text = f"<table class='layout'><thead><tr>{heading}</tr></thead><tbody><tr>{data}</tr></tbody></table>"
                result = self.adapt(text)
                evidence = _TableEvidence(result.pages[0].content)
                self.assertEqual([(cell["tag"], cell["text"]) for cell in evidence.cells],
                                 [(cell["tag"], cell["text"]) for cell in _TableEvidence(text).cells])
                self.assertIn("<thead>", result.pages[0].content)
                self.assertEqual(result.stats.markdown_table_count, 0)
                self.assertEqual(result.stats.compact_html_table_count, 1)

    def test_thead_td_cells_do_not_become_th_cells(self):
        text = "<table><thead><tr><td>原数据</td><td>原金额</td></tr></thead><tbody><tr><td>2027</td><td>123.45</td></tr></tbody></table>"
        result = self.adapt(text)
        self.assertEqual([cell["tag"] for cell in _TableEvidence(result.pages[0].content).cells], ["td"] * 4)
        self.assertIn("<thead>", result.pages[0].content)
        self.assertEqual(result.stats.markdown_table_count, 0)

    def test_tbody_row_header_roles_are_not_downgraded_to_data_cells(self):
        text = ("<table><thead><tr><th>项目</th><th>金额</th></tr></thead>"
                "<tbody><tr><th>年度租金</th><td>123.45</td></tr><tr><th>税额</th><td>6.78</td></tr></tbody></table>")
        result = self.adapt(text)
        self.assertEqual([(cell["tag"], cell["text"]) for cell in _TableEvidence(result.pages[0].content).cells],
                         [(cell["tag"], cell["text"]) for cell in _TableEvidence(text).cells])
        self.assertEqual(result.stats.markdown_table_count, 0)

    def test_multiple_thead_rows_retain_their_header_structure(self):
        text = ("<table><thead><tr><th>第一表头</th><th>金额</th></tr><tr><th>第二表头</th><th>单位</th></tr></thead>"
                "<tbody><tr><td>2027</td><td>123.45</td></tr></tbody></table>")
        result = self.adapt(text)
        self.assertEqual([(cell["tag"], cell["text"]) for cell in _TableEvidence(result.pages[0].content).cells],
                         [(cell["tag"], cell["text"]) for cell in _TableEvidence(text).cells])
        self.assertEqual(result.stats.markdown_table_count, 0)

    def test_merged_cells_retain_every_cell_span_and_caption(self):
        text = ("<table class='grid' style='a'><caption>付款计划</caption>"
                "<tr><th rowspan='2' scope='col'>年度</th><th colspan='2'>金额</th></tr>"
                "<tr><td style='b'>元</td><td>万元</td></tr>"
                "<tr><td>2027</td><td>10.00</td><td></td></tr>"
                "<tfoot><tr><td colspan='3'>脚注：未税</td></tr></tfoot></table>")
        result = self.adapt(text)
        content = result.pages[0].content
        original, projected = _TableEvidence(text), _TableEvidence(content)
        self.assertEqual([cell["text"] for cell in projected.cells], [cell["text"] for cell in original.cells])
        self.assertEqual([(cell["attrs"].get("rowspan"), cell["attrs"].get("colspan"), cell["attrs"].get("scope")) for cell in projected.cells],
                         [(cell["attrs"].get("rowspan"), cell["attrs"].get("colspan"), cell["attrs"].get("scope")) for cell in original.cells])
        self.assertIn("<caption>付款计划</caption>", content)
        self.assertIn("<tfoot>", content)
        self.assertNotIn("style=", content)
        self.assertNotIn("class=", content)
        self.assertEqual(result.stats.compact_html_table_count, 1)

    def test_nested_tables_retain_structure_values_and_order(self):
        text = "<table style='x'><tr><td>外前<table class='x'><tr><td>内A</td><td>内B</td></tr></table>外后</td><td>末列</td></tr></table>"
        result = self.adapt(text)
        evidence = _TableEvidence(result.pages[0].content)
        self.assertEqual(evidence.tables, 2)
        self.assertEqual([cell["text"] for cell in evidence.cells], ["外前外后", "内A", "内B", "末列"])
        self.assertLess(result.pages[0].content.index("外前"), result.pages[0].content.index("内A"))
        self.assertLess(result.pages[0].content.index("内B"), result.pages[0].content.index("外后"))
        self.assertEqual(result.stats.table_count, 2)
        self.assertEqual(result.stats.compact_html_table_count, 2)

    def test_unknown_subtree_is_local_and_following_valid_table_adapts(self):
        unknown = "<vendor-note style='retain-this'>保留<widget key='x'>字段</widget></vendor-note>"
        text = unknown + "\n<table><tr><td>2027</td><td>888.88</td></tr><tr><td>2028</td><td>999.99</td></tr></table>"
        result = self.adapt(text)
        self.assertTrue(result.pages[0].content.startswith(unknown))
        self.assertIn("|2027|888.88|", result.pages[0].content)
        self.assertEqual(result.stats.fallback_count, 1)

    def test_malformed_table_is_preserved_without_swallowing_next_table(self):
        broken = "<table><tr><td>未闭合</table>"
        text = broken + "\n<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"
        result = self.adapt(text)
        self.assertTrue(result.pages[0].content.startswith(broken))
        self.assertIn("|A|B|", result.pages[0].content)
        self.assertEqual(result.stats.fallback_count, 1)

    def test_ragged_rows_do_not_invent_missing_cells(self):
        text = "<table style='x'><tr><td>A</td><td>B</td></tr><tr><td>C</td></tr></table>"
        content = self.adapt(text).pages[0].content
        self.assertEqual([cell["text"] for cell in _TableEvidence(content).cells], ["A", "B", "C"])
        self.assertIn("<table>", content)

    def test_marked_declaration_is_local_raw_fallback(self):
        declaration = "<![unexpected payload]>"
        text = declaration + "<table><tr><td>甲</td><td>乙</td></tr><tr><td>丙</td><td>丁</td></tr></table>"
        result = self.adapt(text)
        self.assertTrue(result.pages[0].content.startswith(declaration))
        self.assertIn("|甲|乙|", result.pages[0].content)
        self.assertEqual(result.stats.fallback_count, 1)

    def test_unclosed_marked_declaration_does_not_crash_or_hide_later_content(self):
        for malformed in ("<![oops>", "<![oops[ x"):
            with self.subTest(malformed=malformed):
                for following in ("", "\n后续正文123.45", "\n后续正文<table><tr><th>项目</th><th>金额</th></tr><tr><td>2027</td><td>123.45</td></tr></table>末尾正文"):
                    with self.subTest(following=following):
                        text = malformed + following
                        result = self.adapt(text)
                        self.assertTrue(result.pages[0].content.startswith(malformed))
                        self.assertEqual(result.stats.fallback_count, 1)
                        if "<table>" in following:
                            self.assertIn("后续正文", result.pages[0].content)
                            self.assertIn("|2027|123.45|", result.pages[0].content)
                            self.assertTrue(result.pages[0].content.endswith("末尾正文"))
                        else:
                            self.assertEqual(result.pages[0].content, text)

    def test_marked_opener_in_html_attribute_remains_literal_attribute_text(self):
        text = '<table title="<![oops>"><tr><td>123.45</td></tr></table>'
        result = self.adapt(text)
        self.assertEqual([cell["text"] for cell in _TableEvidence(result.pages[0].content).cells], ["123.45"])
        self.assertIn('title="&lt;![oops&gt;"', result.pages[0].content)
        self.assertEqual(result.stats.fallback_count, 0)

    def test_malformed_opener_does_not_borrow_later_closing_text(self):
        text = "<![oops><p>后续 ]> 正文</p><table><tr><th>项目</th><th>金额</th></tr><tr><td>2027</td><td>123.45</td></tr></table>"
        result = self.adapt(text)
        self.assertTrue(result.pages[0].content.startswith("<![oops><p>后续 ]> 正文</p>"))
        self.assertIn("|2027|123.45|", result.pages[0].content)
        self.assertEqual(result.stats.fallback_count, 1)

    def test_truncated_tag_is_retained_and_reported_as_fallback(self):
        text = "原文金额123.45\n<table class='incomplete'"
        result = self.adapt(text)
        self.assertEqual(result.pages[0].content, text)
        self.assertGreater(result.stats.fallback_count, 0)

    def test_deep_html_falls_back_locally_without_recursion_failure(self):
        text = "<span>" * 1500 + "保留金额123.45" + "</span>" * 1500
        result = self.adapt(text)
        self.assertIn("保留金额123.45", result.pages[0].content)
        self.assertEqual(result.pages[0].content.count("<span>"), 1500)
        self.assertGreater(result.stats.fallback_count, 0)

    def test_table_stays_compact_html_when_markdown_costs_more_tokens(self):
        estimator = TokenEstimator("semantic-test", _counter=lambda value: 1000 if "|---" in value else len(value))
        result = self.adapt("<table style='x'><tr><td>A</td><td>B</td></tr></table>", estimator=estimator)
        self.assertEqual(result.pages[0].content, "<table><tr><td>A</td><td>B</td></tr></table>")
        self.assertEqual(result.stats.markdown_table_count, 0)
        self.assertEqual(result.stats.compact_html_table_count, 1)

    def test_images_keep_references_alt_and_bare_url_text(self):
        text = ("<img style='width:2px' src='https://example.com/a.png?x=1&amp;y=2' alt='金额图' title='原图'>"
                " ![Markdown图](images/b.png) https://example.com/raw.png\n"
                "<img src='data:image/png;base64,AAAA' alt='内嵌图'>\n"
                "![内嵌Markdown](data:image/jpeg;base64,BBBB)\n"
                "data:image/png;base64,THIS_IS_BARE_TEXT")
        result = self.adapt(text)
        content = result.pages[0].content
        self.assertIn('src="https://example.com/a.png?x=1&amp;y=2"', content)
        self.assertIn('alt="金额图"', content)
        self.assertIn("![Markdown图](images/b.png)", content)
        self.assertIn("https://example.com/raw.png", content)
        self.assertIn("data:image/png;base64,THIS_IS_BARE_TEXT", content)
        self.assertNotIn("AAAA", content)
        self.assertNotIn("BBBB", content)
        self.assertIn("alt: 内嵌图", content)
        self.assertIn("alt: 内嵌Markdown", content)
        self.assertEqual(result.stats.image_placeholder_count, 2)

    def test_data_images_in_unknown_subtree_are_redacted_but_code_is_protected(self):
        text = "<unknown><img src='data:image/png;base64,AAAA' alt='原图'></unknown>\n`<img src='data:image/png;base64,CODE'>`"
        result = self.adapt(text)
        self.assertNotIn("AAAA", result.pages[0].content)
        self.assertIn("alt: 原图", result.pages[0].content)
        self.assertIn("`<img src='data:image/png;base64,CODE'>`", result.pages[0].content)
        self.assertEqual(result.stats.image_placeholder_count, 1)
        self.assertEqual(result.stats.fallback_count, 1)

    def test_unknown_html_attribute_url_text_is_not_an_image_reference(self):
        text = '<unknown title="![literal](data:image/png;base64,LITERAL)">原文</unknown>'
        result = self.adapt(text)
        self.assertEqual(result.pages[0].content, text)
        self.assertEqual(result.stats.image_placeholder_count, 0)

    def test_projection_preserves_pages_native_metadata_and_input(self):
        native = ParsedDocument("glm", "glm-ocr", [DocumentPage(None, "<table><tr><td>1</td></tr></table>"),
                                                  DocumentPage(46, "", native={"blocks": []})],
                                46, "partial", ["unknown page boundaries"], {"response": "raw"})
        before = deepcopy(native)
        result = adapt_document(native, TokenEstimator.conservative())
        self.assertEqual(native, before)
        self.assertEqual([page.page for page in result.pages], [None, 46])
        self.assertEqual(result.pages[1].content, "")
        self.assertIs(result.pages[1].native, native.pages[1].native)
        self.assertEqual(result.stats.source_characters, sum(len(page.content) for page in native.pages))
        self.assertEqual(result.stats.adapted_characters, sum(len(page.content) for page in result.pages))
        self.assertEqual(result.stats.version, ADAPTATION_VERSION)

    def test_converted_table_is_separated_from_adjacent_prose(self):
        result = self.adapt("前文<table><tr><th>年度</th><th>金额</th></tr><tr><td>2027</td><td>123.45</td></tr></table>后文")
        self.assertIn("前文\n\n|年度|金额|", result.pages[0].content)
        self.assertIn("|2027|123.45|\n\n后文", result.pages[0].content)

    def test_statistics_use_the_same_estimator_and_preserve_duplicate_rows(self):
        text = "<table style='padding:100px'>" + "<tr><td>12,500.00</td><td>万元</td></tr>" * 100 + "</table>"
        native = document(text)
        estimator = TokenEstimator.conservative()
        result = adapt_document(native, estimator)
        self.assertEqual(result.pages[0].content.count("12,500.00"), 100)
        self.assertEqual(result.pages[0].content.count("万元"), 100)
        self.assertLess(result.stats.adapted_token_count, result.stats.native_token_count)
        self.assertEqual(result.stats.native_token_count, estimator.text(text))
        self.assertEqual(result.stats.adapted_token_count, estimator.text(result.pages[0].content))
        self.assertGreaterEqual(result.stats.elapsed_ms, 0)

    def test_cancel_before_or_during_adaptation_keeps_input_untouched(self):
        event = threading.Event()
        native = document("<table><tr><td>金额</td></tr></table>")
        before = deepcopy(native)
        event.set()
        with self.assertRaises(DocumentAdaptationCancelled):
            adapt_document(native, TokenEstimator.conservative(), cancel_event=event)
        event.clear()
        def counted(value):
            event.set()
            return len(value)
        with self.assertRaises(DocumentAdaptationCancelled):
            adapt_document(native, TokenEstimator("cancel-test", _counter=counted), cancel_event=event)
        self.assertEqual(native, before)


if __name__ == "__main__":
    unittest.main()
