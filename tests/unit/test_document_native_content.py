"""Synthetic evidence cases: no real contracts or credentials in fixtures."""
from copy import deepcopy
import io
import json
import unittest

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.documents.adaptation import adapt_document
from deep_agent.documents.cache import CachedDocument
from deep_agent.documents.models import DocumentPage, ParsedDocument
from deep_agent.documents.native_content import supplement_native_blocks
from deep_agent.documents.providers import _glm_pages, _paddle_pages, _result
from deep_agent.documents.models import DocumentSource
from deep_agent.documents.toolset import read_cached_document, build_document_toolset
from deep_agent.documents.settings import DocumentParsingRuntimeConfig
from deep_agent.files.reading_budget import ReadingBudget
from deep_agent.files.schema import AttachmentFileRef


def paddle_page(markdown, blocks):
    native = {"prunedResult": {"parsing_res_list": blocks,
               "model_settings": {"markdown_ignore_labels": ["header", "footer", "footnote"]}},
              "markdown": {"text": markdown}}
    return _paddle_pages(json.dumps({"result": {"layoutParsingResults": [native]}}).encode())[0]


def block(content, label="text"):
    return {"block_content": content, "block_label": label}


class DocumentNativeContentTests(unittest.TestCase):
    def test_paddle_recognized_header_is_not_lost_by_markdown_export(self):
        page = paddle_page("# 合同\n\n正文", [block("合同编号：AB015S02001", "header"), block("合同", "title"), block("正文")])
        original = deepcopy(page.native)
        result = adapt_document(ParsedDocument("paddle", "PaddleOCR-VL-1.6", [page], 1), TokenEstimator.conservative())
        self.assertTrue(result.pages[0].content.startswith(page.content))
        self.assertIn("【原始布局块 1 · header】\n\n合同编号：AB015S02001", result.pages[0].content)
        self.assertEqual(result.stats.supplemented_block_count, 1)
        self.assertEqual(page.native, original)
        self.assertEqual(page.content, "# 合同\n\n正文")

    def test_all_omitted_labels_and_competing_values_are_retained_in_native_order(self):
        blocks = [block("A-001", "header"), block("金额：12.00", "text"),
                  block("脚注：含税7%", "footnote"), block("金额：21.00", "aside_text"),
                  block("签字日期：2028-03-04", "footer"), block("页码1", "number"), block("未知文字🙂", "new_label")]
        content, count = supplement_native_blocks(paddle_page("金额：12.00", blocks), "paddle")
        self.assertEqual(count, 6)
        positions = [content.index(b["block_content"]) for b in blocks if b["block_label"] != "text"]
        self.assertEqual(positions, sorted(positions))
        for b in blocks:
            self.assertIn(b["block_content"], content)

    def test_existing_representations_are_not_repeated_but_repeated_native_blocks_survive(self):
        page = paddle_page("# 标题\n\n合同编号：AB001\n\n<div style='x'>甲 &amp; 乙</div>",
                           [block("标题", "title"), block("合同编号：AB001", "header"), block("甲 & 乙"),
                            block("合同编号：AB001", "footer")])
        content, count = supplement_native_blocks(page, "paddle")
        self.assertEqual(count, 1)
        self.assertEqual(content.count("合同编号：AB001"), 2)
        self.assertNotIn("【原始布局块 1", content)
        self.assertNotIn("【原始布局块 3", content)

    def test_equality_does_not_merge_spaces_digits_case_or_conflicts(self):
        page = paddle_page("编号：ab001\n金额：100", [block("编号：AB001", "header"), block("金额：1 00", "footer")])
        content, count = supplement_native_blocks(page, "paddle")
        self.assertEqual(count, 2)
        self.assertIn("编号：AB001", content)
        self.assertIn("金额：1 00", content)

    def test_missing_layout_and_blank_blocks_do_not_invent_information(self):
        for native in ({}, {"prunedResult": None}, {"prunedResult": {"parsing_res_list": [None, block(""), block("  ")]}}):
            self.assertEqual(supplement_native_blocks(DocumentPage(1, "正文", native=native), "paddle"), ("正文", 0))

    def test_short_values_embedded_in_other_ids_are_not_counted_as_coverage(self):
        for body, missing in (("应付金额：12345 元", "123"), ("合同类别 Alpha", "A"),
                              ("编号：AB-0123", "AB"), ("编号：0AB01", "AB01")):
            content, count = supplement_native_blocks(paddle_page(body, [block(missing, "header")]), "paddle")
            self.assertEqual(count, 1)
            self.assertIn("【原始布局块 1 · header】\n\n" + missing, content)

    def test_unknown_markup_is_not_ignored_for_equality(self):
        page = paddle_page("abc <bar> xyz", [block("abc <foo> xyz", "aside_text")])
        content, count = supplement_native_blocks(page, "paddle")
        self.assertEqual(count, 1)
        self.assertIn("abc <foo> xyz", content)

    def test_glm_paged_markdown_retains_native_layout_for_supplement(self):
        body = {"md_results": ["正文", "第二页"], "layout_details": [
            [{"label": "header", "content": "编号：Z-007"}, {"label": "text", "content": "正文"}],
            [{"label": "text", "content": "第二页"}, {"label": "footer", "content": "原件脚注"}]]}
        original = deepcopy(body)
        pages = _glm_pages(body)
        adapted = adapt_document(ParsedDocument("glm", "glm-ocr", pages, 2), TokenEstimator.conservative())
        self.assertIn("编号：Z-007", adapted.pages[0].content)
        self.assertIn("原件脚注", adapted.pages[1].content)
        self.assertEqual(adapted.stats.supplemented_block_count, 2)
        self.assertEqual(body, original)

    def test_glm_block_primary_view_is_not_duplicated(self):
        pages = _glm_pages({"md_results": "full text", "layout_details": [[{"content": "编号：G-2"}, {"content": "正文"}]]})
        adapted = adapt_document(ParsedDocument("glm", "glm-ocr", pages, 1), TokenEstimator.conservative())
        self.assertEqual(adapted.pages[0].content, pages[0].content)
        self.assertEqual(adapted.stats.supplemented_block_count, 0)

    def test_glm_short_markdown_keeps_extra_native_pages(self):
        for markdown in (["第一页"], []):
            body = {"md_results": markdown, "layout_details": [[{"content": "第一页"}], [{"content": "第二页🙂"}]]}
            pages = _glm_pages(body)
            self.assertEqual([p.page for p in pages], [1, 2])
            self.assertEqual(pages[1].content, "第二页🙂")
            parsed = _result("glm", DocumentSource("fixture.pdf", "application/pdf", b"fixture", 2), pages, body)
            self.assertEqual(parsed.status, "complete")
            self.assertTrue(any("额外原始布局页" in w for w in parsed.warnings))


    def test_restored_markup_uses_existing_conservative_adapter(self):
        text = '<table style="x"><tr><td rowspan="2">12.00</td><td>🙂</td></tr><tr><td>21.00</td></tr></table>\n`<header>literal</header>`'
        page = paddle_page("正文", [block(text, "footnote")])
        adapted = adapt_document(ParsedDocument("paddle", "VL-1.6", [page], 1), TokenEstimator.conservative())
        self.assertIn('rowspan="2"', adapted.pages[0].content)
        self.assertIn("12.00", adapted.pages[0].content)
        self.assertIn("21.00", adapted.pages[0].content)
        self.assertIn("`<header>literal</header>`", adapted.pages[0].content)
        self.assertNotIn('style="x"', adapted.pages[0].content)

    def test_code_language_metadata_is_not_a_display_filter(self):
        text = '<pre class="example" style="x"><code class="language-python">print(42)</code></pre>'
        adapted = adapt_document(ParsedDocument("glm", "glm-ocr", [DocumentPage(1, text)], 1), TokenEstimator.conservative())
        self.assertIn('class="language-python"', adapted.pages[0].content)
        self.assertIn('class="example"', adapted.pages[0].content)
        self.assertNotIn('style="x"', adapted.pages[0].content)

    def test_restored_text_is_budgeted_and_continues_losslessly(self):
        page = paddle_page("正文", [block("页眉编号：A-007", "header"), block("中文🙂 12.50元\n" * 12000, "footnote")])
        parsed = ParsedDocument("paddle", "PaddleOCR-VL-1.6", [page], 1)
        estimator = TokenEstimator.conservative()
        readable = adapt_document(parsed, estimator)
        cached = CachedDocument("doc_fixture", AttachmentFileRef(type="attachment", file_id="fixture"), "fixture", "hash", parsed, readable)
        for window, maximum in ((128000, 24000), (256000, 48000)):
            kwargs, seen, joined = {}, set(), ""
            for _ in range(30):
                output = read_cached_document(cached, budget=ReadingBudget(estimator, window), **kwargs)
                self.assertLessEqual(estimator.text(output.model_dump(mode="json")), maximum)
                self.assertTrue(any("未识别到不等于" in w for w in output.warnings))
                self.assertTrue(any("已补回" in w for w in output.warnings))
                self.assertEqual(output.pages[0].char_offset, len(joined))
                joined += output.pages[0].content
                if not output.has_more:
                    break
                kwargs = {k: v for k, v in output.next_read.items() if k != "resource"}
                cursor = (kwargs["offset"], kwargs["char_offset"])
                self.assertNotIn(cursor, seen)
                seen.add(cursor)
            else:
                self.fail("continuation did not terminate")
            self.assertEqual(joined, readable.pages[0].content)
            self.assertEqual(output.summary.coverage, "complete")

    def test_tool_instructions_distinguish_missing_evidence_from_blank_original(self):
        toolset = build_document_toolset(DocumentParsingRuntimeConfig("provider", "paddle", "fixture"))
        description = toolset.tools["parse_document"].tool_def.description
        self.assertIn("未识别到不等于原件无内容", description)
        self.assertIn("不能把其他单据编号", description)
