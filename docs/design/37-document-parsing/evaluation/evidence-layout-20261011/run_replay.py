"""Replay private authorized native artifacts through current production readers.

No network, no source mutation. Checks source hashes, both 24K/48K envelopes,
Unicode ranges, block/export identity, and repeated deterministic reconstruction.
Only summary metrics are suitable for publication; artifacts remain private.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import time

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.documents.adaptation import adapt_document
from deep_agent.documents.cache import CachedDocument
from deep_agent.documents.evidence import build_document_evidence
from deep_agent.documents.models import DocumentPage, ParsedDocument
from deep_agent.documents.toolset import read_cached_document
from deep_agent.files.reading_budget import ReadingBudget
from deep_agent.files.schema import AttachmentFileRef
from pydantic_ai.messages import ToolReturnPart

ESTIMATOR = TokenEstimator.conservative()


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


def read_all(record, evidence, window, include_layout):
    budget = ReadingBudget(ESTIMATOR, window)
    expected = evidence.pages if include_layout else record.readable.pages
    pieces = [[] for _ in expected]
    seen = set()
    params = {"include_layout": include_layout}
    batches = []
    exported_blocks = {b.block_id: b for page in evidence.pages for b in page.blocks}
    received_blocks = set()
    while True:
        result = read_cached_document(record, budget=budget, evidence=evidence, **params)
        wire = result.model_dump(mode="json")
        assert budget.fits(wire), "Complete result wrapper exceeds budget"
        for index, fragment in enumerate(result.pages, result.offset - 1):
            assert fragment.content == expected[index].content[fragment.char_offset:fragment.char_end]
            pieces[index].append((fragment.char_offset, fragment.char_end, fragment.content))
            if include_layout:
                for block in fragment.layout["blocks"]:
                    original = exported_blocks[block["block_id"]]
                    for key, value in original.metadata().items():
                        if key == "ordinal" or (key == "mapping_status" and value == "exact"):
                            continue
                        assert block[key] == value, "Block metadata differs from complete export"
                    assert block.get("fragment_start", block["char_start"]) >= fragment.char_offset
                    assert block.get("fragment_end", block["char_end"]) <= fragment.char_end
                    received_blocks.add(block["block_id"])
        batches.append({"offset": result.offset, "char_offset": result.char_offset, "pages": result.returned_count,
                        "tokens": ESTIMATOR.text(wire),
                        "wire_tokens": ESTIMATOR.text(ToolReturnPart("parse_document", wire).model_response_str()),
                        "has_more": result.has_more})
        if not result.has_more:
            break
        params = dict(result.next_read)
        params.pop("resource")
        assert params["include_layout"] is include_layout
        position = (params["offset"], params["char_offset"])
        assert position not in seen, "Continuation loop"
        seen.add(position)
    for page, fragments in zip(expected, pieces, strict=True):
        cursor = 0
        for start, end, text in fragments:
            assert start == cursor, "Missing or duplicated source range"
            assert end - start == len(text)
            cursor = end
        assert cursor == len(page.content)
        assert "".join(text for _, _, text in fragments) == page.content
    if include_layout:
        assert received_blocks == set(exported_blocks), "Export block absent from complete model reads"
    return {"budget": budget.tokens, "layout": include_layout, "calls": len(batches),
            "sum_return_tokens": sum(b["tokens"] for b in batches), "max_return_tokens": max(b["tokens"] for b in batches),
            "reconstructed": True, "all_blocks_seen": len(received_blocks), "batches": batches}


def evaluate(path, source, repeats):
    started = time.monotonic()
    data = json.loads(path.read_text())
    data["pages"] = [DocumentPage(**page) for page in data["pages"]]
    parsed = ParsedDocument(**data)
    readable = adapt_document(parsed, ESTIMATOR)
    record = CachedDocument("doc_replay_" + source["sha256"][:16], AttachmentFileRef(type="attachment", file_id="local_isolated_authorized_source"),
                            "attachment:local_isolated_authorized_source", source["sha256"], parsed, readable)
    evidence = build_document_evidence(parsed, readable, record.document_id, ESTIMATOR)
    whole = read_cached_document(record, budget=ReadingBudget(ESTIMATOR, 256000), evidence=evidence)
    exported = evidence.export_dict(record.resource.model_dump(mode="json"), record.source_hash, whole.summary.model_dump(mode="json"), source_storage="local")
    assert len(exported["pages"]) == len(parsed.pages)
    assert exported["source"]["sha256"] == source["sha256"]
    results = []
    for window in (128000, 256000):
        for layout in (False, True):
            first = None
            for _ in range(repeats):
                current = read_all(record, evidence, window, layout)
                if first is not None:
                    assert current == first, "Repeated cache reads changed"
                first = current
            results.append(first)
    raw_blocks = []
    for p in parsed.pages:
        blocks = p.native.get("prunedResult", {}).get("parsing_res_list", []) if parsed.provider == "paddle" else p.native.get("blocks", [])
        raw_blocks.extend(b for b in blocks if isinstance(b, dict))
    assert len(raw_blocks) == sum(len(p.blocks) for p in evidence.pages), "Native block count changed"
    return {"case_hash": source["sha256"][:16], "provider": parsed.provider, "source_pages": source["pages"],
            "parsed_pages": len(parsed.pages), "status": parsed.status, "mode": "historical-replay",
            "body_tokens": sum(ESTIMATOR.text(p.content) for p in readable.pages),
            "layout_body_tokens": sum(ESTIMATOR.text(p.content) for p in evidence.pages),
            "export_bytes": len(json.dumps(exported, ensure_ascii=False, separators=(",", ":")).encode()),
            "evidence_stats": evidence.stats,
            "location_statuses": dict(Counter(b.location_status for p in evidence.pages for b in p.blocks)),
            "original_mapping_statuses": dict(Counter(p.geometry["original_page_mapping"] for p in evidence.pages)),
            "repeat_per_combination": repeats, "reads": results, "seconds": round(time.monotonic() - started, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True, help="product-runtime local-private root")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    os.umask(0o077)
    rows = json.loads((args.fixtures / "manifest.json").read_text())
    results = []
    for row in rows:
        if row["mime_type"] != "application/pdf":
            continue
        assert hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest() == row["sha256"], "Source hash differs from frozen manifest"
        for provider in ("paddle", "glm"):
            path = args.fixtures / "production-initial" / row["sha256"][:16] / provider / "normalized-result.json"
            result = evaluate(path, row, args.repeats)
            results.append(result)
            save(args.output / "replay-summary.json", results)
            print(json.dumps({"provider": provider, "pages": row["pages"], "reads": [{k: x[k] for k in ("budget", "layout", "calls")} for x in result["reads"]]}, ensure_ascii=False), flush=True)
    assert len(results) == 12

if __name__ == "__main__":
    main()
