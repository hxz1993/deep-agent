"""Read-only synthetic financial rows for PTC transfer acceptance.

This fixture contains no customer data or credentials.  It deliberately returns
one complete result (no server pagination), so tests can distinguish model-output
spilling from data-source pagination.  Run with the repository interpreter:

    .venv/bin/python tests/integration/fixtures/ptc_glp_mcp.py --port 18087

The independently written expected-value oracle lives in private acceptance
materials, not in the server response or the model prompt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from typing import Literal

SOURCE_KEY = "synthetic-glp-ptc-v1"
PERIODS = (1, 2, 3, 4, 5)
SCENARIOS = ("Actual", "Budget")
Fault = Literal["none", "missing_row", "duplicate_row", "null_value", "wrong_type", "wrong_count", "incomplete"]


def build_dataset(entity_count: int = 1000, fault: Fault = "none") -> dict:
    """Build 10 rows per entity; amounts are deterministic integer CNY."""
    if isinstance(entity_count, bool) or not isinstance(entity_count, int) or not 1 <= entity_count <= 10_000:
        raise ValueError("entity_count must be an integer from 1 to 10000")
    if fault not in {"none", "missing_row", "duplicate_row", "null_value", "wrong_type", "wrong_count", "incomplete"}:
        raise ValueError("unsupported synthetic fault")
    rows = []
    for entity in range(1, entity_count + 1):
        for period in PERIODS:
            budget = 10_000 + entity * 7 + period * 113
            actual = budget + (entity % 11 - 5) * 23 + period * 17
            rows.append([f"E{entity:05d}", str(period), "Actual", actual])
            rows.append([f"E{entity:05d}", str(period), "Budget", budget])
    if fault == "missing_row":
        rows.pop()
    elif fault == "duplicate_row":
        rows[-1] = list(rows[0])
    elif fault == "null_value":
        rows[0][-1] = None
    elif fault == "wrong_type":
        rows[0][-1] = "not-a-number"
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {
        "schema_version": 1,
        "source_key": SOURCE_KEY,
        "synthetic": True,
        "cube_name": "synthetic_glp_revenue",
        "fixed_dimensions": {"Year": "2026", "Account": "SyntheticRevenue", "Currency": "CNY", "Views": "Periodic"},
        "varying_columns": ["Entity", "Period", "Scenario", "data"],
        "entity_count": entity_count,
        "periods": [str(value) for value in PERIODS],
        "scenarios": list(SCENARIOS),
        "row_count": len(rows) + (1 if fault == "wrong_count" else 0),
        "complete": fault != "incomplete",
        "rows_sha256": digest,
        "rows": rows,
    }


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18087)
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="streamable-http")
    args = parser.parse_args()
    mcp = FastMCP("ptc-glp-synthetic", host="127.0.0.1", port=args.port, stateless_http=True)

    @mcp.tool()
    def describe_glp_dataset() -> dict:
        """Describe a read-only synthetic data-transfer fixture, not real GLP accounts."""
        return {
            "source_key": SOURCE_KEY, "synthetic": True,
            "columns": {"Entity": "string, exclusive leaf entity", "Period": "string 1..5", "Scenario": "Actual or Budget", "data": "integer CNY, additive revenue"},
            "query_tool": "query_glp_rows",
            "default_rows": 10_000, "maximum_rows": 100_000,
            "pagination": "none: returns all requested entity/period/scenario rows once",
            "scope": "synthetic revenue transfer and deterministic reporting only; not GLP business acceptance",
        }

    @mcp.tool()
    def query_glp_rows(entity_count: int = 1000, fault: Fault = "none") -> dict:
        """Return complete synthetic Entity x 5 Period x 2 Scenario rows.

        Read-only and stateless. Default entity_count=1000 produces 10000 rows.
        entity_count=10000 produces 100000 rows. Leave fault='none' for reports;
        other fault values intentionally violate the data contract for rejection
        tests. Do not silently reduce the requested entity_count or drop rows.
        """
        return build_dataset(entity_count, fault)

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
