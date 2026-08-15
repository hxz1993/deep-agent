from __future__ import annotations

import unittest

from deep_agent.search import (
    FieldRole,
    SearchDocument,
    SearchField,
    SearchIndex,
    normalize_text,
)


class LexicalSearchTests(unittest.TestCase):
    def test_normalizes_unicode_camel_case_and_connectors(self) -> None:
        self.assertEqual(normalize_text("  Ｃｕｂｅ－Query_Data  "), "cube query data")
        self.assertEqual(normalize_text("platformFolderList"), "platform folder list")

    def test_field_priority_and_compact_code(self) -> None:
        index = SearchIndex(
            [
                SearchDocument(
                    id="code",
                    fields=(SearchField("name", "DB_014", FieldRole.CODE),),
                ),
                SearchDocument(
                    id="alias",
                    fields=(SearchField("label", "DB_014", FieldRole.ALIAS),),
                ),
            ]
        )

        exact = index.search("DB_014")
        compact = index.search("ＤＢ０１４")

        self.assertEqual(exact.hits[0].document_id, "code")
        self.assertEqual(exact.hits[0].matched_via, "code_exact")
        self.assertEqual(compact.hits[0].document_id, "code")
        self.assertEqual(compact.hits[0].matched_via, "code_compact_exact")

    def test_prefix_contains_and_attribute_matching(self) -> None:
        index = SearchIndex(
            [
                SearchDocument(
                    id="cube",
                    fields=(
                        SearchField("name", "cube_query_data", FieldRole.CODE),
                        SearchField("parameter", "cube_name", FieldRole.ATTRIBUTE),
                    ),
                )
            ]
        )

        self.assertEqual(index.search("cube_query").hits[0].matched_via, "code_prefix")
        self.assertEqual(index.search("cube_name").hits[0].matched_via, "attribute_exact")

    def test_pinyin_full_abbreviation_and_typo_fallback(self) -> None:
        index = SearchIndex(
            [
                SearchDocument(
                    id="member",
                    fields=(SearchField("title", "管理费用", FieldRole.ALIAS),),
                ),
                SearchDocument(
                    id="admin",
                    fields=(SearchField("title", "行政管理费用", FieldRole.ALIAS),),
                ),
            ]
        )

        self.assertEqual(index.search("guanlifeiyong").hits[0].document_id, "member")
        self.assertEqual(index.search("glfy").hits[0].matched_via, "pinyin_abbr_exact")
        typo = index.search("行政管里费用")
        self.assertEqual(typo.hits[0].document_id, "admin")
        self.assertIn(typo.hits[0].matched_via, {"alias_token_overlap", "edit_alias"})

    def test_short_query_is_not_fuzzy_and_equal_hits_are_ambiguous(self) -> None:
        index = SearchIndex(
            [
                SearchDocument(
                    id="a",
                    fields=(SearchField("title", "集团总部", FieldRole.ALIAS),),
                ),
                SearchDocument(
                    id="b",
                    fields=(SearchField("title", "集团总部", FieldRole.ALIAS),),
                ),
            ]
        )

        self.assertEqual(index.search("集").status, "not_found")
        result = index.search("集团总部", top_k=1)
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(len(result.hits), 1)

    def test_edit_distance_is_a_low_confidence_fallback(self) -> None:
        index = SearchIndex(
            [
                SearchDocument(
                    id="budget",
                    fields=(SearchField("name", "budget_model", FieldRole.CODE),),
                )
            ]
        )

        result = index.search("budegt_model")

        self.assertEqual(result.hits[0].document_id, "budget")
        self.assertEqual(result.hits[0].matched_via, "edit_code")
        self.assertEqual(result.status, "low_confidence")

    def test_stable_top_k_and_match_evidence(self) -> None:
        index = SearchIndex(
            [
                SearchDocument(
                    id=name,
                    fields=(SearchField("description", "查询预算数据", FieldRole.TEXT),),
                )
                for name in ("b", "a", "c")
            ]
        )

        result = index.search("预算数据", top_k=2)

        self.assertEqual([hit.document_id for hit in result.hits], ["a", "b"])
        self.assertEqual(result.hits[0].matched_via, "bm25")
        self.assertEqual(result.hits[0].field, "text")


if __name__ == "__main__":
    unittest.main()
