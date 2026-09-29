"""Offline smoke evaluator for the included synthetic fixture, not MPDocBench.

Run with Python 3: python score_cells.py > cell-results.json
Strict original strings; only layout whitespace is removed. No numeric repairs.
Fails closed when table count, headers, rows, or column counts differ.
"""
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

BASE = Path(__file__).resolve().parent
EXPECTED = [
    ["1,234,567.89", "12.50%", "1,345,678.90", "13.05%"],
    ["(12,345.67)", "-0.80%", "23,456.78", "1.75%"],
    ["0.003", "3.00‰", "0.0025", "2.50‰"],
    ["1,234,567.89", "12.50%", "9,876.54", "0.25‰"],
    ["—", "0.00%", "0.00", "N/A"],
]
ROW_NAMES = ["营业收入", "净利润", "坏账率", "跨行金额", "缺失和零"]


class Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.table = self.row = self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.table is not None:
                raise ValueError("Nested tables need a different evaluator")
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = {"text": "", "attrs": dict(attrs)}

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.cell["text"] = re.sub(r"\s+", "", self.cell["text"])
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None


def score(path, chart=False):
    parser = Tables()
    raw = path.read_bytes()
    parser.feed(raw.decode())
    assert len(parser.tables) == 2, "Expected two page tables, or one data table and one chart table"
    data_tables = parser.tables[:1] if chart else parser.tables
    pages = []
    for page, rows in enumerate(data_tables, 1):
        assert len(rows) == 7 and [len(r) for r in rows] == [3, 4, 5, 5, 5, 5, 5]
        assert rows[0][0]["attrs"].get("rowspan") == "2"
        for i, year in [(1, "2025"), (2, "2026")]:
            assert year in rows[0][i]["text"]
            assert rows[0][i]["attrs"].get("colspan") == "2"
        assert [c["text"].replace("（", "(").replace("）", ")") for c in rows[1]] == ["金额(万元)", "比例", "金额(万元)", "比例"]
        errors = []
        for r, expected in enumerate(EXPECTED):
            assert ROW_NAMES[r] in rows[r + 2][0]["text"]
            for c, value in enumerate(expected):
                actual = rows[r + 2][c + 1]["text"]
                if actual != value:
                    errors.append({"row_name": ROW_NAMES[r], "value_column": c + 1,
                                   "expected": value, "actual": actual})
        pages.append({"page": page, "exact_data_cells": 20 - len(errors),
                      "total_data_cells": 20, "mismatches": errors})
    result = {"markdown_sha256": hashlib.sha256(raw).hexdigest(), "pages": pages}
    if chart:
        expected_chart = [["Q1", "1.25%"], ["Q2", "2.50%"], ["Q3", "1.90%"], ["Q4", "3.75%"]]
        actual_chart = [[c["text"] for c in r] for r in parser.tables[1][1:]]
        assert actual_chart == expected_chart, "Chart series mismatch"
        result["chart_exact_values"] = 4
        result["input_note"] = "Single raster image corresponding to PDF physical page 2"
    return result


if __name__ == "__main__":
    paths = ["mineru-free-pipeline.md", "mineru-local-basic/markdown.md",
             "mineru-local-standard/markdown.md"]
    results = {p: score(BASE / p) for p in paths}
    results["paddle16-demo.md"] = score(BASE / "paddle16-demo.md", chart=True)
    print(json.dumps(results, ensure_ascii=False, indent=2))
