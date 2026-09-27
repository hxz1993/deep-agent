"""Produce content-free D01 compaction, suffix and history-read diagnostics."""
from __future__ import annotations
import argparse
from collections import Counter
import json
import os
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]

def chars(value):
    return len(json.dumps(value, ensure_ascii=False, default=str))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    helper = str(Path(__file__).resolve().parent)
    sys.path[:] = [x for x in sys.path if x != helper]
    sys.path[:0] = [str(args.source_root / "src"), str(ROOT)]
    os.environ["DEEP_AGENT_LOAD_DOTENV"] = "false"
    from deep_agent.agent.context.estimator import TokenEstimator
    from deep_agent.agent.context.compaction import _safe_boundaries
    from deep_agent.db.serialization import deserialize_message
    estimator = TokenEstimator.for_model("deepseek-flash", provider_type="deepseek")
    events = json.loads((args.case_dir / "events.synthetic.json").read_text())
    result = json.loads((args.case_dir / "result.private.json").read_text())
    read_names = {"search_conversation_history", "read_conversation_event", "read_tool_output", "read_tool_result"}
    reads = []
    tool_results = []
    for event in events:
        message = event.get("payload", {}).get("message") or {}
        if not isinstance(message, dict):
            continue
        for part in message.get("parts", []):
            if part.get("part_kind") != "tool-return":
                continue
            content = part.get("content")
            row = {"sequence": event["sequence"], "tool": part.get("tool_name"), "chars": chars(content),
                   "estimated_tokens_offline": estimator.text(json.dumps(content, ensure_ascii=False, default=str)),
                   "content_type": type(content).__name__}
            tool_results.append(row)
            if part.get("tool_name") in read_names:
                reads.append(row)
    compactions = []
    previous_sequence = 0
    for event in events:
        if event["event_type"] != "compaction":
            continue
        payload = event["payload"]; budget = payload.get("budget") or {}
        source = payload.get("source") or {}; replacement = payload.get("replacement") or {}
        through = source.get("through_sequence", 0); head = source.get("head_sequence", event["sequence"])
        suffix = [(e["sequence"], deserialize_message(e["payload"]["message"])) for e in events
                  if through < e["sequence"] <= head and isinstance(e.get("payload", {}).get("message"), dict)]
        messages = [m for _, m in suffix]
        boundaries = _safe_boundaries(messages)
        units = [{"from_sequence": suffix[a][0], "through_sequence": suffix[b - 1][0],
                  "messages": b - a, "estimated_tokens_offline": estimator.messages(messages[a:b]),
                  "part_kinds": [p.part_kind for m in messages[a:b] for p in m.parts]}
                 for a, b in zip(boundaries, boundaries[1:]) if a < b]
        interval_results = [r for r in tool_results if previous_sequence < r["sequence"] <= event["sequence"]]
        row = {"sequence": event["sequence"], "status": event["status"], "budget": budget,
               "source_from": source.get("from_sequence"), "source_through": through, "source_head": head,
               "replacement_chars": {k: chars(v) for k, v in replacement.items()},
               "raw_suffix_units": units,
               "tools_since_previous_compaction": dict(Counter(r['tool'] for r in interval_results)),
               "tool_result_chars_since_previous": sum(r['chars'] for r in interval_results),
               "history_read_chars_since_previous": sum(r['chars'] for r in interval_results if r['tool'] in read_names),
               "summary_usage": (payload.get("summary_call") or {}).get("usage"),
               "duration_ms": payload.get("duration_ms")}
        compactions.append(row)
        previous_sequence = event["sequence"]
    terminal_errors = []
    for path in args.case_dir.glob("*.sse.synthetic.json"):
        for event in json.loads(path.read_text()):
            if event.get("type") != "error":
                continue
            text = str(event.get("message", ""))
            match = re.search(r"Tool '([A-Za-z0-9_]+)' exceeded max retries count of ([0-9]+)", text)
            terminal_errors.append({"kind": "tool_retry_exhausted", "tool": match[1], "retry_limit": int(match[2])}
                                   if match else {"kind": "other_stream_error", "message_chars": len(text)})
    request_rows = [{k: r.get(k) for k in ('index', 'stream', 'message_count', 'estimated_tokens', 'schema_chars', 'requested_tools', 'elapsed_seconds', 'error_type')}
                    | {"provider_input_tokens": r.get('usage', {}).get('input_tokens'), "provider_output_tokens": r.get('usage', {}).get('output_tokens')}
                    for r in result['requests']]
    output = {"case_id": result['case_id'], "repeat": result['repeat'], "status": result['status'],
              "scope": "No business text, tool arguments, field values or gold. Offline suffix estimates use fresh conservative TokenEstimator; event budget is original runtime telemetry.",
              "compactions": compactions, "terminal_errors": terminal_errors, "history_reads": reads, "model_requests": request_rows,
              "tool_counts": dict(Counter(r['tool'] for r in tool_results)),
              "history_read_total_chars": sum(r['chars'] for r in reads),
              "history_read_max_chars": max((r['chars'] for r in reads), default=0)}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'diagnostic.json').write_text(json.dumps(output, ensure_ascii=False, indent=2))
    lines = ["# D01 匿名运行诊断", "", output['scope'], "", "| Sequence | Before request | After request | U | Retained tail | Summary chars | Evidence chars | 历史回读 chars | 后缀工作单元 Tokens |", "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for c in compactions:
        b=c['budget']; rc=c['replacement_chars']; u=b.get('budget_limit_tokens', b.get('target_tokens'))
        lines.append(f"| {c['sequence']} | {b.get('estimated_tokens_before')} | {b.get('estimated_tokens_after')} | {u} | {b.get('preserved_tail_tokens')} | {rc.get('summary_markdown')} | {rc.get('tool_evidence')} | {c['history_read_chars_since_previous']} | {[x['estimated_tokens_offline'] for x in c['raw_suffix_units']]} |")
    lines += ['', f"终止分类：{json.dumps(terminal_errors, ensure_ascii=False)}。历史回读 {len(reads)} 次，累计 {output['history_read_total_chars']} 字符，单次最大 {output['history_read_max_chars']} 字符。完整逐请求、每次后缀单元和工具回读大小见 diagnostic.json。", '']
    (args.output/'README.md').write_text('\n'.join(lines))
    print(json.dumps({'compactions':len(compactions),'history_reads':len(reads),'max_history_read_chars':output['history_read_max_chars']}))


if __name__ == '__main__':
    main()
