"""Synthetic OCR evidence: exact provenance, privacy and lossless layout slices."""
from copy import deepcopy
import asyncio
import io
import zipfile
import hashlib
from pathlib import Path
import json
import threading
import unittest

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.documents.adaptation import DocumentAdaptationCancelled, adapt_document
from deep_agent.documents.evidence import SCHEMA_VERSION, build_document_evidence
from deep_agent.documents.models import DocumentPage, ParsedDocument


ESTIMATOR = TokenEstimator.conservative()


def paddle(text, blocks, *, page=1, width=1000, height=1500, preprocess=None):
    native = {"prunedResult": {"width": width, "height": height, "parsing_res_list": blocks}}
    if preprocess is not None:
        native["prunedResult"]["doc_preprocessor_res"] = preprocess
    return DocumentPage(page, text, native=native)


def pb(text, bbox=None, label="text", **extra):
    return {"block_content": text, "block_label": label, "block_bbox": bbox, **extra}


def build(pages, provider="paddle", native=None, source_pages=None, status="complete"):
    original = ParsedDocument(provider, "fixture-ocr", pages, source_pages or len(pages), status=status, native=native or {})
    readable = adapt_document(original, ESTIMATOR)
    return original, readable, build_document_evidence(original, readable, "doc_fixture", ESTIMATOR)


def export(evidence, **summary):
    summary = {"source_pages": 1, "parsed_pages": 1, "page_numbers_known": True,
               "missing_pages": [], "coverage": "complete", **summary}
    return evidence.export_dict({"type": "attachment", "file_id": "local_fixture_input"}, "a" * 64,
                                summary, source_storage="local")


class DocumentEvidenceTests(unittest.TestCase):
    def test_layout_retains_default_content_and_does_not_repeat_block_body(self):
        page = paddle("# 合同\n\n租金 12,000.00 元🙂\n\n尾注", [pb("合同", [10, 20, 300, 60], "title"),
                  pb("租金 12,000.00 元🙂", [10, 80, 800, 110]), pb("尾注", [10, 1400, 100, 1450], "footer")])
        before = deepcopy(page)
        parsed, readable, evidence = build([page])
        self.assertEqual(evidence.pages[0].content, readable.pages[0].content)
        self.assertEqual(parsed.pages[0], before)
        self.assertEqual(evidence.stats["appended_blocks"], 0)
        layout = evidence.pages[0].fragment_layout(0, len(evidence.pages[0].content))
        self.assertEqual(len(layout["blocks"]), 3)
        for item in layout["blocks"]:
            self.assertNotIn("content", item)
            self.assertNotIn("native_content", item)
            native = next(block for block in evidence.pages[0].blocks if block.block_id == item["block_id"])
            self.assertEqual(evidence.pages[0].content[item["char_start"]:item["char_end"]], native.native_content)
        self.assertEqual(layout["original_page_mapping"], "unverified")

    def test_repeated_native_text_gets_distinct_stable_ids_and_occurrences(self):
        _, _, evidence = build([paddle("编号：A1\n\n编号：A1", [pb("编号：A1", [1, 2, 99, 20]), pb("编号：A1", [1, 500, 99, 520])])])
        page = evidence.pages[0]
        self.assertEqual(page.content.count("编号：A1"), 2)
        self.assertNotEqual(page.blocks[0].block_id, page.blocks[1].block_id)
        self.assertLess(page.blocks[0].char_end, page.blocks[1].char_start)
        payload = export(evidence)
        self.assertEqual([b["block_id"] for b in payload["pages"][0]["blocks"]], [b.block_id for b in page.blocks])
        self.assertEqual([b["bbox"] for b in payload["pages"][0]["blocks"]], [[1, 2, 99, 20], [1, 500, 99, 520]])

    def test_missing_header_is_retained_with_its_true_coordinates(self):
        _, readable, evidence = build([paddle("正文", [pb("合同编号：X014", [10, 5, 400, 45], "header"), pb("正文", [10, 200, 300, 250])])])
        page = evidence.pages[0]
        self.assertEqual(page.content, readable.pages[0].content)
        self.assertIn("合同编号：X014", page.content)
        self.assertEqual(page.blocks[0].native_content, "合同编号：X014")
        self.assertEqual(page.content[page.blocks[0].char_start:page.blocks[0].char_end], "合同编号：X014")
        self.assertEqual(page.blocks[0].bbox, (10, 5, 400, 45))

    def test_plain_and_merged_tables_keep_all_native_cells_and_spans(self):
        for table in ("<table style='width:100%'><tr><td>A|B</td><td></td></tr><tr><td>12.00<br>含税</td><td>🙂</td></tr></table>",
                      '<table><caption>费用表</caption><tr><td rowspan="2">租金</td><td>10</td></tr><tr><td>20</td></tr></table>'):
            with self.subTest(table=table):
                _, readable, evidence = build([paddle("前言\n\n" + table + "\n\n后记", [pb(table, [50, 100, 900, 900], "table")])])
                block = evidence.pages[0].blocks[0]
                self.assertEqual(block.native_content, table)
                self.assertEqual(export(evidence)["pages"][0]["blocks"][0]["native_content"], table)
                self.assertEqual(evidence.pages[0].content, readable.pages[0].content)
                self.assertEqual(block.mapping_status, "exact")
                self.assertIn("前言", evidence.pages[0].content)
                self.assertIn("后记", evidence.pages[0].content)

    def test_unmatched_block_is_preserved_not_attached_to_similar_value(self):
        # Formatting may differ across the block and whole-page render. Keep the
        # whole original page and supplement one native block when no exact
        # character association is available, instead of changing either value.
        original = ParsedDocument("glm", "fixture", [DocumentPage(1, "prefix", native={"blocks": [{"content": "ABC 100", "bbox_2d": [1, 1, 99, 99]}]})], 1)
        from deep_agent.documents.adaptation import AdaptedDocument
        readable = adapt_document(original, ESTIMATOR)
        readable = AdaptedDocument([DocumentPage(1, "ABC 1 00\n保留内容")], readable.stats)
        evidence = build_document_evidence(original, readable, "doc_fixture", ESTIMATOR)
        page = evidence.pages[0]
        self.assertTrue(page.content.startswith("ABC 1 00\n保留内容"))
        self.assertEqual(page.blocks[0].mapping_status, "appended")
        self.assertEqual(page.content[page.blocks[0].char_start:page.blocks[0].char_end], "ABC 100")
        self.assertTrue(page.unmapped_spans)
        self.assertEqual(page.blocks[0].location_status, "missing_page_size")

    def test_native_block_short_id_never_maps_inside_another_id(self):
        _, _, evidence = build([paddle("编号：ABC1000", [pb("ABC100", [1, 1, 10, 10])])])
        page = evidence.pages[0]
        # Default supplementation preserves ABC100 independently of ABC1000.
        self.assertEqual(page.content[page.blocks[0].char_start:page.blocks[0].char_end], "ABC100")
        self.assertGreater(page.blocks[0].char_start, page.content.index("ABC1000") + len("ABC1000"))

    def test_paddle_unwarping_and_rotation_are_not_original_pdf_coordinates(self):
        for preprocess in ({"model_settings": {"use_doc_unwarping": True}, "angle": 0},
                           {"model_settings": {"use_doc_unwarping": False}, "angle": 90}):
            _, _, evidence = build([paddle("正文", [pb("正文", [10, 20, 300, 60])], preprocess=preprocess)])
            layout = evidence.pages[0].fragment_layout(0, len(evidence.pages[0].content))
            self.assertEqual(layout["coordinate_space"], "paddle_processed_page_pixels")
            self.assertEqual(layout["original_page_mapping"], "unavailable")
            self.assertEqual(layout["blocks"][0]["location_status"], "available")

    def test_glm_pixel_coordinates_use_physical_page_dimensions_not_1000_normalization(self):
        pages = [DocumentPage(2, "第二页表格", native={"blocks": [{"content": "第二页表格", "label": "table", "bbox_2d": [40, 50, 3500, 2700]}]})]
        _, _, evidence = build(pages, "glm", {"data_info": {"pages": [{"width": 100, "height": 100}, {"width": 3968, "height": 2806}]}}, source_pages=2)
        layout = evidence.pages[0].fragment_layout(0, len(evidence.pages[0].content))
        self.assertEqual((layout["width"], layout["height"]), (3968, 2806))
        self.assertEqual(layout["coordinate_space"], "glm_page_pixels")
        self.assertEqual(layout["blocks"][0]["bbox"], [40, 50, 3500, 2700])
        self.assertEqual(layout["blocks"][0]["location_status"], "available")
        self.assertEqual(layout["original_page_mapping"], "unverified")

    def test_conflicting_glm_page_dimensions_do_not_invent_a_scaling_transform(self):
        page = DocumentPage(1, "正文", native={"blocks": [{"content": "正文", "bbox_2d": [1, 1, 80, 90], "width": 200, "height": 300}]})
        _, _, evidence = build([page], "glm", {"data_info": {"pages": [{"width": 100, "height": 150}]}})
        layout = evidence.pages[0].fragment_layout(0, len(evidence.pages[0].content))
        self.assertIsNone(layout["width"])
        self.assertIsNone(layout["height"])
        self.assertEqual(layout["original_page_mapping"], "conflicting_page_size")
        self.assertEqual(layout["blocks"][0]["location_status"], "missing_page_size")
        self.assertEqual(layout["blocks"][0]["bbox"], [1, 1, 80, 90])

    def test_unknown_pages_missing_and_invalid_coordinates_are_explicit(self):
        cases = [(None, "missing_bbox"), ([0, 0, 1, 1], "available"), ([1, 2, 0, 3], "invalid_bbox"),
                 ([0, 0, 1001, 1499], "outside_page"), ([0, 0, 0, 1], "empty_bbox"), ([0, 0, float("nan"), 5], "invalid_bbox")]
        for bbox, status in cases:
            with self.subTest(bbox=bbox):
                _, _, evidence = build([paddle("x", [pb("x", bbox)])])
                self.assertEqual(evidence.pages[0].blocks[0].location_status, status)
                json.dumps(export(evidence), allow_nan=False)
        _, _, evidence = build([paddle("x", [pb("x", [1, 2, 3, 4])], page=None)])
        self.assertIsNone(evidence.pages[0].page)
        self.assertEqual(evidence.pages[0].blocks[0].location_status, "unknown_page")
        self.assertEqual(evidence.pages[0].geometry["original_page_mapping"], "unknown_page")

    def test_no_native_blocks_keeps_entire_text_as_unmapped(self):
        _, _, evidence = build([DocumentPage(1, "正文🙂\n\n|A|B|\n|--|--|\n|1|2|")], "glm")
        page = evidence.pages[0]
        self.assertEqual(page.unmapped_spans, ((0, len(page.content)),))
        self.assertEqual(page.blocks, ())
        self.assertEqual(page.fragment_layout(3, 8)["unmapped_spans"], [{"char_start": 3, "char_end": 8}])

    def test_fragment_offsets_reconstruct_unicode_and_reuse_complete_block_geometry(self):
        body = "中文🙂abcdef\n" * 100
        _, _, evidence = build([paddle(body, [pb(body, [1, 1, 900, 1200])])])
        page = evidence.pages[0]
        slices = []
        for start in range(0, len(page.content), 37):
            end = min(start + 37, len(page.content))
            slices.append(page.content[start:end])
            layout = page.fragment_layout(start, end)
            block = layout["blocks"][0]
            self.assertEqual(block["block_id"], "p1:b1")
            self.assertEqual(block["char_start"], 0)
            self.assertEqual(block["char_end"], len(body))
            self.assertEqual(block["fragment_start"], start)
            self.assertEqual(block["fragment_end"], end)
            self.assertNotIn("content", block)
        self.assertEqual("".join(slices), page.content)

    def test_export_is_complete_and_partial_is_not_promoted(self):
        _, _, evidence = build([paddle("第1页", [pb("第1页")]), paddle("第3页", [pb("第3页")], page=3)], source_pages=3, status="partial")
        payload = export(evidence, source_pages=3, parsed_pages=2, coverage="partial", missing_pages=[2])
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["summary"]["missing_pages"], [2])
        self.assertEqual([page["page_number"] for page in payload["pages"]], [1, 3])
        self.assertEqual(payload["source"]["storage"], "local")

    def test_export_whitelist_keeps_tables_but_removes_transport_artifacts(self):
        text = ('<table class="native"><tr><td rowspan="2">1,000.50</td></tr></table>\n'
                '<img src="data:image/png;base64,QUJDREVGRw==" alt="印章">\n'
                '[图](https://example.invalid/download.png?X-Amz-Signature=secret&X-Amz-Expires=600)\n'
                '[资料](https://example.invalid/public?q=hello)\n/tmp/private-page.png\nfile:///Users/test/raw.pdf')
        page = paddle(text, [pb(text, [1, 1, 500, 800], authorization="secret-header")])
        page.native.update(Authorization="secret-auth", outputImages={"signed": "secret-envelope"})
        parsed, readable, evidence = build([page])
        before = deepcopy(parsed)
        payload = export(evidence)
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in ("secret", "QUJDREVGRw", "data:image", "/tmp/private", "/Users/test", "Authorization", "outputImages"):
            self.assertNotIn(forbidden, serialized)
        native = payload["pages"][0]["blocks"][0]["native_content"]
        self.assertIn('<table class="native">', native)
        self.assertIn('rowspan="2"', native)
        self.assertIn("1,000.50", native)
        self.assertIn("印章", native)
        self.assertIn("https://example.invalid/public?q=hello", native)
        self.assertEqual(parsed, before)
        self.assertIn("secret", readable.pages[0].content)

    def test_empty_image_region_keeps_block_id_and_bbox_without_inline_bytes(self):
        _, _, evidence = build([paddle("正文", [pb("", [10, 20, 300, 400], "image"), pb("正文", [10, 450, 300, 500])])])
        page = evidence.pages[0]
        start, end = page.blocks[0].char_start, page.blocks[0].char_end
        layout = page.fragment_layout(start, end)
        self.assertEqual(layout["blocks"][0]["mapping_status"], "no_text")
        self.assertEqual(layout["blocks"][0]["bbox"], [10, 20, 300, 400])
        self.assertEqual(layout["blocks"][0]["block_id"], export(evidence)["pages"][0]["blocks"][0]["block_id"])

    def test_many_empty_native_blocks_have_bounded_progress_and_export_empty_native_text(self):
        from deep_agent.documents.cache import CachedDocument
        from deep_agent.documents.toolset import read_cached_document
        from deep_agent.files.reading_budget import ReadingBudget
        from deep_agent.files.schema import AttachmentFileRef
        native = DocumentPage(1, "", native={"blocks": [{"content": "", "label": "image", "bbox_2d": [1, 1, 8, 9], "width": 10, "height": 10} for _ in range(1500)]})
        parsed, readable, evidence = build([native], "glm")
        record = CachedDocument("doc_fixture", AttachmentFileRef(type="attachment", file_id="fixture"), "attachment:fixture", "0" * 64, parsed, readable)
        budget = ReadingBudget(estimator=ESTIMATOR, context_window_tokens=128000)
        offset, char_offset, pieces, block_ids, calls = 1, 0, [], set(), 0
        while True:
            result = read_cached_document(record, budget=budget, include_layout=True, evidence=evidence,
                                          offset=offset, char_offset=char_offset)
            self.assertTrue(budget.fits(result.model_dump(mode="json")))
            self.assertTrue(result.pages)
            for fragment in result.pages:
                self.assertTrue(fragment.content)
                pieces.append(fragment.content)
                block_ids.update(block["block_id"] for block in fragment.layout["blocks"])
            calls += 1
            self.assertLess(calls, 100)
            if not result.has_more:
                break
            next_read = result.next_read
            self.assertGreater((next_read["offset"], next_read["char_offset"]), (offset, char_offset))
            offset, char_offset = next_read["offset"], next_read["char_offset"]
        self.assertGreater(calls, 1)
        self.assertEqual("".join(pieces), evidence.pages[0].content)
        self.assertEqual(len(block_ids), 1500)
        self.assertTrue(all(block["native_content"] == "" for block in export(evidence)["pages"][0]["blocks"]))

    def test_export_preserves_native_polygon_without_repeating_it_in_model_view(self):
        polygon = [[10.5, 20], [290, 15.5], [305, 60], [8, 70]]
        _, _, evidence = build([paddle("正文", [pb("正文", [8, 15.5, 305, 70], block_polygon_points=polygon)])])
        self.assertEqual(export(evidence)["pages"][0]["blocks"][0]["polygon_points"], polygon)
        model_block = evidence.pages[0].fragment_layout(0, len(evidence.pages[0].content))["blocks"][0]
        self.assertNotIn("polygon_points", model_block)
        self.assertNotIn("ordinal", model_block)
        self.assertNotIn("fragment_start", model_block)
        self.assertNotIn("fragment_end", model_block)
        self.assertNotIn("mapping_status", model_block)
        self.assertEqual(model_block["block_id"], "p1:b1")

    def test_html_escaped_signed_url_and_credentials_are_redacted(self):
        from deep_agent.documents.evidence import _safe_text
        queries = (
            "download=1&amp;X-Amz-Signature=fixture-secret",
            "download=1&#38;X-Goog-Credential=fixture-secret",
            "download=1&#x26;X-Oss-Signature=fixture-secret",
            "download=1&amp;access_token=fixture-secret",
            "download=1&amp;%61uthorization=fixture-secret",
            "download=1&amp;&#116;oken=fixture-secret",
        )
        for query in queries:
            content = '<a href="https://example.invalid/file?' + query + '">保留说明</a>'
            with self.subTest(query=query):
                self.assertEqual(_safe_text(content), '<a href="[临时下载地址已省略]">保留说明</a>')
                _, _, evidence = build([paddle(content, [pb(content)])])
                serialized = json.dumps(export(evidence), ensure_ascii=False)
                self.assertNotIn("fixture-secret", serialized)
                self.assertIn("保留说明", serialized)

    def test_html_escaped_ordinary_url_is_not_decoded_or_rewritten(self):
        from deep_agent.documents.evidence import _safe_text
        for url in ("https://example.invalid/file?q=one&amp;page=2", "https://example.invalid/file?q=one&#38;page=2",
                    "https://example.invalid/file?q=one&#x26;page=2"):
            content = '<a href="' + url + '">普通链接</a>'
            with self.subTest(url=url):
                self.assertEqual(_safe_text(content), content)
                _, _, evidence = build([paddle(content, [pb(content)])])
                payload = export(evidence)
                self.assertEqual(payload["pages"][0]["native_content"], content)
                self.assertEqual(payload["pages"][0]["blocks"][0]["native_content"], content)

    def test_invalid_url_after_html_decoding_is_redacted_without_raising(self):
        from deep_agent.documents.evidence import _safe_text
        for url in ("https://[broken/file?x=1&amp;token=fixture-secret",
                    "https://&#91;broken/file?x=1&amp;token=fixture-secret"):
            content = '<a href="' + url + '">说明保留</a>'
            self.assertEqual(_safe_text(content), '<a href="[无效地址已省略]">说明保留</a>')
        self.assertEqual(_safe_text('<a href="https://fixture-user&#64;example.invalid/file">说明</a>'),
                         '<a href="[临时下载地址已省略]">说明</a>')

    def test_inline_base64_with_line_breaks_is_not_exported(self):
        content = '<img src="data:image/png;base64,QUJD\nREVGRw==" alt="保留说明">'
        _, _, evidence = build([paddle(content, [pb(content)])])
        serialized = json.dumps(export(evidence), ensure_ascii=False)
        self.assertNotIn("QUJD", serialized)
        self.assertNotIn("REVGRw", serialized)
        self.assertIn("保留说明", serialized)

    def test_status_argument_cannot_promote_partial_parser_result(self):
        _, _, evidence = build([paddle("已取得部分", [])], status="partial")
        payload = evidence.export_dict({"type": "attachment", "file_id": "fixture"}, "a" * 64,
                                      {"coverage": "unknown"}, source_storage="platform", status="complete")
        self.assertEqual(payload["status"], "partial")

    def test_published_schema_validates_runtime_payload_and_rejects_native_envelope(self):
        from jsonschema import Draft202012Validator, ValidationError
        path = Path(__file__).resolve().parents[2] / "docs/design/37-document-parsing/document-evidence.schema.json"
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        parsed, _, evidence = build([paddle("# 合同\n\n金额：12.00🙂", [pb("金额：12.00🙂", [1, 1, 9, 9], block_polygon_points=[[1, 1], [9, 1], [9, 9], [1, 9]])])])
        from deep_agent.documents.bundle import build_evidence_bundle
        raw = asyncio.run(build_evidence_bundle(export(evidence), parsed))
        with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
            payload = json.loads(bundle.read("evidence.json"))
        validator.validate(payload)
        invalid = deepcopy(payload)
        invalid["native"] = {"Authorization": "not-a-real-secret"}
        with self.assertRaises(ValidationError):
            validator.validate(invalid)
        invalid = deepcopy(payload)
        invalid["pages"][0]["original_page_mapping"] = "verified"
        with self.assertRaises(ValidationError):
            validator.validate(invalid)
        invalid = deepcopy(payload)
        invalid["source"]["storage"] = "skill"
        with self.assertRaises(ValidationError):
            validator.validate(invalid)

    def test_synthetic_published_examples_have_exact_hashes_valid_schema_and_spans(self):
        from jsonschema import Draft202012Validator
        base = Path(__file__).resolve().parents[2] / "docs/design/37-document-parsing"
        manifest = json.loads((base / "document-evidence-examples.manifest.json").read_text())
        schema = json.loads((base / "document-evidence.schema.json").read_text())
        validator = Draft202012Validator(schema)
        self.assertEqual(len(manifest["examples"]), 2)
        for item in manifest["examples"]:
            raw = (base / item["file_name"]).read_bytes()
            self.assertEqual(len(raw), item["byte_size"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256"])
            self.assertEqual(item["mime"], "application/zip")
            self.assertEqual(item["schema_version"], "document-evidence-v2")
            with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
                names = bundle.namelist()
                self.assertEqual(len(names), len(set(names)))
                payload = json.loads(bundle.read("evidence.json").decode("utf-8", errors="strict"))
                validator.validate(payload)
                self.assertEqual(payload, json.loads((base / item["readable_json"]).read_text()))
                expected_names = {"evidence.json"}
                saved = 0
                missing = []
                from PIL import Image
                for page in payload["pages"]:
                    image = page["image"]
                    if image["status"] == "saved":
                        saved += 1
                        expected_names.add(image["path"])
                        image_bytes = bundle.read(image["path"])
                        self.assertEqual(len(image_bytes), image["byte_size"])
                        self.assertEqual(hashlib.sha256(image_bytes).hexdigest(), image["sha256"])
                        with Image.open(io.BytesIO(image_bytes)) as decoded:
                            decoded.load()
                            self.assertEqual(decoded.size, (image["width"], image["height"]))
                        self.assertEqual(image["coordinate_mapping"], "matched")
                        self.assertEqual((page["width"], page["height"]), (image["width"], image["height"]))
                    else:
                        missing.append(page["page_ordinal"])
                        self.assertEqual(image["status"], "not_provided")
                        self.assertNotIn("path", image)
                self.assertEqual(set(names), expected_names)
                self.assertEqual(payload["images"]["saved"], saved)
                self.assertEqual(payload["images"]["expected"], len(payload["pages"]))
                self.assertEqual(payload["images"]["missing_page_ordinals"], missing)
                self.assertEqual(saved, 1)
                image_info = payload["pages"][0]["image"]
                self.assertEqual(image_info["role"], "processed_page" if payload["provider"] == "paddle" else "layout_visualization")
                self.assertEqual(image_info["annotated"], payload["provider"] == "glm")
                self.assertEqual(payload["images"]["status"], "complete" if saved else "unavailable")
            self.assertEqual(payload["source"]["sha256"], manifest["source_sha256"])
            ids = []
            for page in payload["pages"]:
                for block in page["blocks"]:
                    ids.append(block["block_id"])
                    self.assertLessEqual(block["char_start"], block["char_end"])
                    self.assertLessEqual(block["char_end"], len(page["content"]))
                    self.assertEqual(page["content"][block["char_start"]:block["char_end"]], block["native_content"])
            self.assertEqual(len(ids), len(set(ids)))

    def test_v2_schema_rejects_unsafe_or_incomplete_image_references(self):
        from jsonschema import Draft202012Validator, ValidationError
        base = Path(__file__).resolve().parents[2] / "docs/design/37-document-parsing"
        schema = json.loads((base / "document-evidence.schema.json").read_text())
        validator = Draft202012Validator(schema)
        payload = json.loads((base / "document-evidence-paddle.example.json").read_text())
        for key, value in (("path", "../private.png"), ("path", "/pages/page-0001.png"),
                           ("path", "pages\\page-0001.png"), ("path", "https://example.invalid/a.png"),
                           ("mime", "image/svg+xml"), ("status", "available"),
                           ("sha256", "wrong"), ("coordinate_mapping", "original_pdf_verified"),
                           ("role", "original_pdf"), ("annotated", True)):
            with self.subTest(key=key, value=value):
                invalid = deepcopy(payload)
                invalid["pages"][0]["image"][key] = value
                with self.assertRaises(ValidationError):
                    validator.validate(invalid)
        invalid = deepcopy(payload)
        invalid["pages"][0]["image"]["coordinate_mapping"] = "dimension_mismatch"
        with self.assertRaises(ValidationError):
            validator.validate(invalid)  # Mismatch must explain its reason.
        invalid["pages"][0]["image"]["reason_code"] = "dimension_mismatch"
        validator.validate(invalid)
        for status, mapping, code in (
            ("saved", "matched", "dimension_mismatch"),
            ("saved", "unverified", "arbitrary_code"),
            ("saved", "unverified", "network_error"),
            ("saved", "dimension_mismatch", "exif_orientation"),
            ("failed", "unverified", "arbitrary_code"),
            ("failed", "unverified", "not_provided"),
            ("not_provided", "unverified", "network_error"),
        ):
            with self.subTest(status=status, mapping=mapping, code=code):
                invalid = deepcopy(payload)
                if status == "saved":
                    invalid["pages"][0]["image"].update(coordinate_mapping=mapping, reason_code=code)
                else:
                    invalid["pages"][0]["image"] = {"status": status, "coordinate_mapping": mapping, "reason_code": code}
                with self.assertRaises(ValidationError):
                    validator.validate(invalid)
        for status in ("failed", "not_provided"):
            invalid = deepcopy(payload)
            invalid["pages"][0]["image"] = {"status": status, "coordinate_mapping": "unverified"}
            with self.assertRaises(ValidationError):
                validator.validate(invalid)
        for storage, file_id in (("local", "platform_example_source"), ("platform", "local_example_source")):
            with self.subTest(source_storage=storage, source_file_id=file_id):
                invalid = deepcopy(payload)
                invalid["source"]["storage"] = storage
                invalid["source"]["resource"]["file_id"] = file_id
                with self.assertRaises(ValidationError):
                    validator.validate(invalid)
        valid_skill = deepcopy(payload)
        valid_skill["source"].update(storage="skill", resource={"type": "skill", "skill_code": "test", "path": "files/source.pdf"})
        validator.validate(valid_skill)
        valid_platform = deepcopy(payload)
        valid_platform["source"].update(storage="platform", resource={"type": "attachment", "file_id": "example_platform_source"})
        validator.validate(valid_platform)
        receipt_validator = Draft202012Validator({"$ref": "#/$defs/fileReference", "$defs": schema["$defs"]})
        manifest = json.loads((base / "document-evidence-examples.manifest.json").read_text())
        receipt = {key: value for key, value in manifest["examples"][0].items() if key != "readable_json"}
        receipt.update(storage="local", file_id="local_synthetic")
        receipt_validator.validate(receipt)
        receipt["mime"] = "application/json"
        with self.assertRaises(ValidationError):
            receipt_validator.validate(receipt)

    def test_glm_legacy_cache_without_visualization_remains_explicit_and_schema_valid(self):
        from jsonschema import Draft202012Validator
        from deep_agent.documents.bundle import build_evidence_bundle
        base = Path(__file__).resolve().parents[2] / "docs/design/37-document-parsing"
        validator = Draft202012Validator(json.loads((base / "document-evidence.schema.json").read_text()))
        parsed, _, evidence = build([DocumentPage(1, "历史识别文字", native={"blocks": []})], "glm")
        raw = asyncio.run(build_evidence_bundle(export(evidence), parsed))
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertEqual(archive.namelist(), ["evidence.json"])
            payload = json.loads(archive.read("evidence.json"))
        validator.validate(payload)
        self.assertEqual(payload["images"]["status"], "unavailable")
        self.assertEqual(payload["images"]["missing_page_ordinals"], [1])
        self.assertEqual(payload["pages"][0]["image"], {
            "status": "not_provided", "reason_code": "not_provided", "coordinate_mapping": "unverified"})
        self.assertEqual(payload["pages"][0]["native_content"], "历史识别文字")
        self.assertEqual(payload["status"], "complete")

    def test_v1_history_examples_remain_valid_without_page_images(self):
        from jsonschema import Draft202012Validator
        base = Path(__file__).resolve().parents[2] / "docs/design/37-document-parsing/history/document-evidence-v1"
        validator = Draft202012Validator(json.loads((base / "document-evidence.schema.json").read_text()))
        manifest = json.loads((base / "document-evidence-examples.manifest.json").read_text())
        for item in manifest["examples"]:
            raw = (base / item["file_name"]).read_bytes()
            self.assertEqual(len(raw), item["byte_size"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256"])
            payload = json.loads(raw)
            validator.validate(payload)
            self.assertEqual(payload["schema_version"], "document-evidence-v1")
            self.assertNotIn("images", payload)
            self.assertTrue(all("image" not in page for page in payload["pages"]))

    def test_cancellation_and_page_mismatch_do_not_mutate_cached_document(self):
        parsed, readable, _ = build([paddle("正文", [pb("正文")])])
        before = deepcopy(parsed)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(DocumentAdaptationCancelled):
            build_document_evidence(parsed, readable, "doc_cancel", ESTIMATOR, cancel_event=cancel)
        self.assertEqual(parsed, before)
        from deep_agent.documents.adaptation import AdaptedDocument
        with self.assertRaises(ValueError):
            build_document_evidence(parsed, AdaptedDocument([], readable.stats), "doc_bad", ESTIMATOR)


if __name__ == "__main__":
    unittest.main()
