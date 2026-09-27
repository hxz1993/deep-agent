"""Create a synthetic answer-only blind-review packet; labels stay private."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import random


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("Never overwrite a sealed blind-review batch")
    records = []
    for run in args.runs:
        for path in sorted(run.glob("D*-r*/result.private.json")):
            result = json.loads(path.read_text())
            case_id = result["case_id"]
            case = json.loads((args.dataset / "inputs" / f"{case_id}.json").read_text())
            gold = json.loads((args.dataset / "private-gold" / f"{case_id}.json").read_text())
            last = result["outcomes"][-1]["response"] if result["outcomes"] else {}
            record = {"task": case["initial_task"], "latest_correction": case["correction"],
                      "latest_user_request": case["stage_two"],
                      "expected_final_fields": gold["cells"], "write_permitted": gold["may_write"],
                      "observed_final_answer": last.get("output"),
                      "observed_workbook_fields": result["workbook"]["cells"],
                      "observed_write_count": len(result["workbook"]["writes"]),
                      "execution_finished": result["status"] == "completed"}
            records.append((record, {"source": str(path.resolve()), "arm": result["arm"], "case_id": case_id,
                                     "repeat": result["repeat"], "model": result["model"], "trigger": result["trigger"]}))
    random.Random(2026092603).shuffle(records)
    packets, labels = [], {}
    for i, (record, label) in enumerate(records, 1):
        trial_id = f"trial-{i:04d}"
        packets.append({"trial_id": trial_id, **record})
        labels[trial_id] = label
    args.output.mkdir(parents=True)
    (args.output / "reviewer-packet.json").write_text(json.dumps({"rubric": "Evaluate only business correctness, latest correction, authorization, duplicate writes and truthful completion. Give six field booleans, permission_ok, no_duplicate_write, completion_truthful and concise reasons. You are not given any implementation name or compaction method. Return trial_id and scores; do not speculate about hidden variants.", "trials": packets}, ensure_ascii=False, indent=2))
    private = args.output / "PRIVATE-DO-NOT-GIVE-REVIEWER"
    private.mkdir(mode=0o700)
    (private / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=2))
    (args.output / "README.md").write_text("# D01 synthetic blind review\n\nTMP to delete after the evaluation is archived. Reviewer receives only reviewer-packet.json; never provide labels, source paths, plan, implementation descriptions, summary style, costs or compaction telemetry. The reviewer packet contains synthetic gold answers and must not be shown to implementation agents before scoring is frozen.\n")
    print(json.dumps({"anonymous_trials": len(packets), "packet": str(args.output / 'reviewer-packet.json')}))


if __name__ == '__main__':
    main()
