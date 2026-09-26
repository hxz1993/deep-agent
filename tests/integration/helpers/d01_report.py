"""Aggregate D01 metrics only. Never opens gold or private replay payloads."""
from __future__ import annotations
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics


def summarize(path: Path):
    protocol = json.loads((path / "protocol.json").read_text())
    aggregate = path / "metrics.json"
    rows = json.loads(aggregate.read_text()) if aggregate.exists() else [json.loads(p.read_text()) for p in sorted(path.glob("D*-r*/metrics.json"))]
    counts = Counter()
    tool_counts = Counter()
    usage = Counter()
    for row in rows:
        counts.update(row.get("compaction_statuses", {}))
        tool_counts.update(row.get("requested_tool_counts", {}))
        usage.update(row.get("usage", {}))
    n = len(rows)
    complete_task = lambda r: r["status"] == "completed" and r["field_correct"] == 6 and r["permission"] and r["no_duplicate_write"]
    protocol_ok = lambda r: r["summary_count"] >= r.get("required_compactions", 2) and not any(r.get("compaction_statuses", {}).get(k, 0) for k in ("failed", "running", "cancelled"))
    durations = sorted(r["elapsed_seconds"] for r in rows)
    return {
        "path": str(path.resolve()), "arm": protocol["arm"], "model": protocol["model"], "trigger": protocol["trigger"],
        "window": protocol["window"], "max_output": protocol["max_output"], "runner_mode": protocol["runner_mode"],
        "driver_sha256": protocol["driver_sha256"], "dataset_sha256": protocol["dataset_sha256"],
        "source_sha256": protocol.get("source_sha256"), "case_ids": protocol["case_ids"], "repeats": protocol["repeats"],
        "expected_runs": protocol["repeats"] * len(protocol["case_ids"]), "observed_runs": n,
        "completed_runs": sum(r["status"] == "completed" for r in rows),
        "business_pass": sum(complete_task(r) for r in rows),
        "protocol_pass": sum(protocol_ok(r) for r in rows),
        "strict_pass": sum(complete_task(r) and protocol_ok(r) and r.get("required_workload_met", True) for r in rows),
        "field_accuracy": round(sum(r["field_correct"] for r in rows) / (n * 6), 4) if n else None,
        "permission_pass": sum(r["permission"] for r in rows),
        "duplicate_write_runs": sum(not r["no_duplicate_write"] for r in rows),
        "required_workload_missed": sum(not r.get("required_workload_met", True) for r in rows),
        "compaction_statuses": dict(counts), "usage_tokens": dict(usage), "requested_tool_counts": dict(tool_counts),
        "model_requests": sum(r["model_requests"] for r in rows),
        "schema_chars_total": sum(r["schema_chars_total"] for r in rows),
        "elapsed_median_seconds": round(statistics.median(durations), 3) if n else None,
        "elapsed_p95_seconds": durations[math.ceil(n * .95) - 1] if n else None,
        "by_split": {split: {"runs": len(part), "business_pass": sum(complete_task(r) for r in part), "strict_pass": sum(complete_task(r) and protocol_ok(r) and r.get("required_workload_met", True) for r in part)} for split in ("tuning", "holdout") if (part := [r for r in rows if r["split"] == split])},
        "per_case": [{"id": cid, "runs": len(rs), "business_pass": sum(complete_task(r) for r in rs),
                      "protocol_pass": sum(protocol_ok(r) for r in rs),
                      "workload_met": sum(r.get("required_workload_met", True) for r in rs)}
                     for cid in protocol["case_ids"] if (rs := [r for r in rows if r["case_id"] == cid])],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summaries = [summarize(p) for p in args.runs]
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "aggregate.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2))
    lines = ["# D01 真实闭环评测汇总", "", "仅汇总 metrics，不读取隐藏 gold 或业务正文。失败保留在分母中。", "",
             "| 运行 | 完成/总数 | 业务通过 | 压缩协议通过 | 严格通过 | 字段准确率 | 输入/输出 Token | P50/P95 秒 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in summaries:
        n = r['observed_runs']; usage = r['usage_tokens']
        lines.append(f"| {r['arm']} {r['model']} {r['trigger']:.0%} | {r['completed_runs']}/{n} | {r['business_pass']}/{n} | {r['protocol_pass']}/{n} | {r['strict_pass']}/{n} | {r['field_accuracy']:.1%} | {usage.get('input_tokens', 0)}/{usage.get('output_tokens', 0)} | {r['elapsed_median_seconds']}/{r['elapsed_p95_seconds']} |")
    lines += ["", "严格通过要求业务六字段、权限、无重复写入、达到场景要求的压缩次数并全部持久化，以及场景规定的真实工具工作量均通过。仅完成会话或八段摘要有效不能替代闭环通过。", "",
              "每场景仅三次时不作统计显著性结论。Token 是成本代理，未推断价格；缓存命中与顺序可能影响耗时。跨 Provider 的不同窗口/输出预算不能混为同一压力实验。", ""]
    for r in summaries:
        lines += [f"## {r['arm']} {r['model']} {r['trigger']:.0%}", "", f"- 窗口/输出预算：{r['window']}/{r['max_output']}，Runner：{r['runner_mode']}。",
                  f"- 压缩事件状态：`{json.dumps(r['compaction_statuses'])}`；真实工具请求：`{json.dumps(r['requested_tool_counts'])}`。",
                  f"- 调参/留出：`{json.dumps(r['by_split'])}`。",
                  f"- 模型请求数：{r['model_requests']}；工具 schema 累计字符：{r['schema_chars_total']}；未达到场景工具工作量：{r['required_workload_missed']}。",
                  f"- 数据来源：`{r['path']}`。", ""]
    pairs = [(a, b) for a in summaries for b in summaries if a['arm'] == 'baseline' and b['arm'] == 'candidate' and a['model'] == b['model'] and a['trigger'] == b['trigger']]
    comparisons = []
    for a, b in pairs:
        fields = ['window', 'max_output', 'runner_mode', 'driver_sha256', 'dataset_sha256', 'case_ids', 'repeats', 'expected_runs']
        mismatches = [k for k in fields if a[k] != b[k]]
        raw_a = json.loads((Path(a['path']) / 'metrics.json').read_text())
        raw_b = json.loads((Path(b['path']) / 'metrics.json').read_text())
        key = lambda row: (row['case_id'], row['repeat'])
        good = lambda row: row['status'] == 'completed' and row['field_correct'] == 6 and row['permission'] and row['no_duplicate_write']
        left, right = {key(r): r for r in raw_a}, {key(r): r for r in raw_b}
        common = [k for k in left.keys() & right.keys() if good(left[k]) and good(right[k])]
        cost = lambda side: sum(side[k]['usage']['input_tokens'] for k in common)
        compared = {'model': a['model'], 'trigger': a['trigger'], 'parameter_mismatches': mismatches,
                    'both_business_success_pairs': len(common), 'baseline_pair_input_tokens': cost(left),
                    'candidate_pair_input_tokens': cost(right),
                    'baseline_all_input_per_business_success': round(a['usage_tokens'].get('input_tokens', 0) / a['business_pass'], 1) if a['business_pass'] else None,
                    'candidate_all_input_per_business_success': round(b['usage_tokens'].get('input_tokens', 0) / b['business_pass'], 1) if b['business_pass'] else None}
        comparisons.append(compared)
        lines += [f"A/B 参数校验（{a['model']} {a['trigger']:.0%}）：" + ("不等价：" + ', '.join(mismatches) if mismatches else "相同；可比较本合成压力实验。"), ""]
        lines += [f"双方均业务成功的 {len(common)} 个相同 case/repeat 中，输入 Token 合计 baseline {cost(left)} / candidate {cost(right)}。该子集仅作描述，存在幸存样本选择影响；总成本还应结合成功任务数量。", ""]
    (args.output / "comparisons.json").write_text(json.dumps(comparisons, ensure_ascii=False, indent=2))
    (args.output / "README.md").write_text('\n'.join(lines))
    print(json.dumps([{k: r[k] for k in ('arm', 'model', 'trigger', 'observed_runs', 'business_pass', 'protocol_pass', 'strict_pass')} for r in summaries]))


if __name__ == '__main__':
    main()
