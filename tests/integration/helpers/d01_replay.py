"""D01 isolated Runner benchmark. Never runs business MCPs or writes source DBs.

The selected source root is inserted before product imports, allowing the SAME
driver and sealed cases to evaluate a git-archive baseline and a candidate.
Gold is loaded only AFTER execution. Reports contain synthetic data only.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import sys
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch
from typing_extensions import TypedDict


class WorkbookCells(TypedDict):
    revenue: float
    margin: float
    unit: str
    period: str
    receipt: str
    filename: str

ROOT = Path(__file__).resolve().parents[3]
BASELINE = "f4673b047cdf069810c0deefc431fd1d9f9ed5dc"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def freeze_cases(directory, *, fresh_holdout=False):
    """Eight synthetic tasks, independent train/holdout split and hidden gold."""
    if (directory / "manifest.json").exists():
        raise RuntimeError("Sealed dataset already exists; never silently regenerate gold")
    rng = random.Random(2026092601)
    holdout_seed = random.SystemRandom().getrandbits(64) if fresh_holdout else None
    kinds = ["middle", "tail", "correction", "permission", "revision", "pagination", "discovery", "long_turn"]
    public = []
    for index, kind in enumerate(kinds):
        if index == 4 and holdout_seed is not None:
            rng = random.Random(holdout_seed)
        case_id = f"D{index + 1:02d}"
        amount = round(rng.uniform(1000, 9000), 2)
        margin = round(rng.uniform(.12, .42), 4)
        facts = {"revenue": amount, "margin": margin, "unit": "万元", "period": f"2026Q{index % 4 + 1}",
                 "receipt": f"ACK-{rng.randrange(100000, 999999)}", "filename": f"budget-{index + 11}.xlsx"}
        handle = f"fixture-{case_id.lower()}"
        row = "核定数据 " + json.dumps(facts, ensure_ascii=False)
        filler = [f"archive {n:04d}: inactive historical observation; not a current result." for n in range(420)]
        at = 4 if kind == "tail" else 210
        if kind == "tail":
            at = len(filler) - 4
        filler.insert(at, row)
        source = "\n".join(filler)
        corrected = dict(facts)
        correction = ""
        if kind in {"correction", "revision", "discovery"}:
            corrected["revenue"] = round(amount + 137.43, 2)
            correction = f"最新用户修正：收入以{corrected['revenue']}为准；旧收入作废。其他核定数据不变。"
        may_write = kind != "permission"
        public_case = {
            "id": case_id, "split": "tuning" if index < 4 else "holdout", "kind": kind,
            "handle": handle, "source": source, "correction": correction,
            "initial_task": f"核对工作簿。已归档的核定报表可通过句柄{handle}精读。最终需要填写revenue、margin、unit、period、receipt、filename六项；不要猜测。先保留任务，当前阶段不得写入。",
            "stage_one": "本阶段只根据现有上下文给一句简短待办，不要调用任何工具，不要写入。下一阶段再回查证据并执行。",
            "stage_two": ("现在允许按最新核定数据更新工作簿。先读当前版本，再一次写入完整六项，最后确认回执。" if may_write else
                          "仍未允许提交。请只给完整六项JSON供核对，不得调用写入；不确定时回查原文。"),
        }
        write_json(directory / "inputs" / f"{case_id}.json", public_case)
        write_json(directory / "private-gold" / f"{case_id}.json", {"cells": corrected, "may_write": may_write})
        public.append({"id": case_id, "split": public_case["split"], "kind": kind,
                       "input_sha256": hashlib.sha256(json.dumps(public_case, ensure_ascii=False, sort_keys=True).encode()).hexdigest()})
    write_json(directory / "manifest.json", {"version": 4 if fresh_holdout else 1, "baseline": BASELINE, "cases": public, "private_holdout_seed": holdout_seed,
        "policy": "Synthetic only. Gold is unavailable to the agent/model; implementation agents receive aggregate metrics only. Holdout is not for tuning."})
    (directory / "README.md").write_text("# D01 private evaluation assets\n\nTMP to delete after the evaluation is archived. Synthetic fixtures only. Do not show private-gold or labeled results to implementation agents.\n", encoding="utf-8")
    print(json.dumps({"frozen_cases": len(public), "tuning": 4, "holdout": 4}))


def import_runtime(source_root):
    os.environ["DEEP_AGENT_LOAD_DOTENV"] = "false"
    os.environ["PYDANTIC_AI_NO_BANNER"] = "1"
    # Direct execution must not let helpers/mcp.py shadow the installed MCP SDK.
    helper_dir = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != helper_dir]
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(source_root / "src"))
    from loguru import logger
    logger.remove()


def load_model(database, model_name):
    if model_name == "fixture":
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
        from pydantic_ai.models.function import FunctionModel, DeltaToolCall
        counter = 0
        def function(messages, info):
            nonlocal counter
            counter += 1
            if not info.function_tools:
                headings = ["Primary Request and Intent", "Key Technical Concepts", "Files and Code", "Errors and Fixes", "Pending Jobs", "Current Work", "Next Step", "Critical Context"]
                text = "\n\n".join(f"## {i}. {h}\nArchive summary generation {counter}; report fixture-d01 can be read again; no writes yet." for i, h in enumerate(headings, 1))
                return ModelResponse(parts=[TextPart(text)])
            parts = [p for m in messages for p in m.parts]
            user = next((p.content for p in reversed(parts) if isinstance(p, UserPromptPart)), "")
            if "现在允许" not in str(user):
                return ModelResponse(parts=[TextPart("等待明确写入许可。")])
            after = []
            for part in reversed(parts):
                if isinstance(part, UserPromptPart):
                    break
                if isinstance(part, ToolReturnPart):
                    after.append(part)
            by_name = {p.tool_name: p.content for p in reversed(after)}
            if "workbook_apply" in by_name:
                return ModelResponse(parts=[TextPart("LOCAL-APPLY-OK")])
            if "workbook_read" not in by_name:
                return ModelResponse(parts=[ToolCallPart("workbook_read", {}, f"fake-{counter}")])
            if "evidence_read" not in by_name:
                return ModelResponse(parts=[ToolCallPart("evidence_read", {"handle": "fixture-d01", "pattern": "核定数据"}, f"fake-{counter}")])
            evidence = by_name["evidence_read"]
            if isinstance(evidence, str):
                evidence = json.loads(evidence)
            facts = json.loads(evidence["rows"][0]["text"].split(" ", 1)[1])
            book = by_name["workbook_read"]
            if isinstance(book, str):
                book = json.loads(book)
            return ModelResponse(parts=[ToolCallPart("workbook_apply", {"baseline_revision": book["revision"], "cells": facts}, f"fake-{counter}")])
        async def stream_function(messages, info):
            response = function(messages, info)
            for i, part in enumerate(response.parts):
                if isinstance(part, TextPart):
                    yield part.content
                elif isinstance(part, ToolCallPart):
                    yield {i: DeltaToolCall(name=part.tool_name, json_args=part.args_as_json_str(), tool_call_id=part.tool_call_id)}
        return FunctionModel(function, stream_function=stream_function), "test"
    from deep_agent.models.provider_registry import ProviderRuntimeConfig, resolve_provider_model
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT p.*, m.model AS selected_model FROM llm_providers p JOIN llm_models m ON m.provider_id=p.id WHERE m.enabled=1 AND m.model=?", (model_name,)).fetchone()
        if row is None:
            raise RuntimeError("Requested enabled model is unavailable")
        if row["auth_method"] != "api_key":
            raise RuntimeError("OAuth model requires connected product auth; this standalone reader does not read OAuth credentials")
        config = ProviderRuntimeConfig.from_row(SimpleNamespace(**dict(row)))
        return resolve_provider_model(model_name=row["selected_model"], provider=config), config.provider_type


async def execute_case(args, case, repeat, wrapped_model, provider_type):
    from pydantic_ai import Agent, ModelRetry, RunContext
    from pydantic_ai.capabilities import ToolSearch
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
    from pydantic_ai.models.wrapper import WrapperModel
    from pydantic_ai.usage import UsageLimits
    from pydantic_ai.toolsets import FunctionToolset
    from deep_agent.agent.context.budget import build_context_budget
    from deep_agent.agent.context.compaction import DeepAgentContextCompaction
    from deep_agent.agent.context.estimator import TokenEstimator
    from deep_agent.agent.deps import AgentDeps
    from deep_agent.agent.history_tools import build_history_toolset
    from deep_agent.agent.runner import AgentRunner
    from deep_agent.agent.event_persister import EventPersister
    from deep_agent.agent.tool_discovery import build_mcp_discovery_toolset, search_deferred_tools
    from deep_agent.db.serialization import serialize_message
    from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent

    started = time.monotonic()
    path = args.output / f"{case['id']}-r{repeat}"
    path.mkdir(parents=True, exist_ok=True)
    db_path = path / "TMP_to_delete-replay.sqlite3"
    if db_path.exists():
        raise RuntimeError("Run DB exists; use a new result directory")
    stores, engine, _ = await make_sqlite_stores(f"sqlite+aiosqlite:///{db_path}")
    store = stores.coordinator
    persister = EventPersister(store)
    await persister.start()
    persistence_observations = []
    real_persist = store.persist_compaction_projection
    async def observe_persist(*pargs, **kwargs):
        try:
            return await real_persist(*pargs, **kwargs)
        except ValueError as exc:
            raw = kwargs.get("raw_history", [])
            projected = kwargs.get("projected_history", [])
            mismatch = []
            for i, (a, b) in enumerate(zip(reversed(raw), reversed(projected[1:]))):
                aa, bb = serialize_message(a), serialize_message(b)
                if aa != bb:
                    mismatch.append({"tail_offset": i, "different_keys": sorted(k for k in set(aa) | set(bb) if aa.get(k) != bb.get(k)),
                                     "part_differences": [{"kind": ap.get("part_kind"), "keys": sorted(k for k in set(ap) | set(bp) if ap.get(k) != bp.get(k))}
                                                          for ap, bp in zip(aa.get("parts", []), bb.get("parts", [])) if ap != bp]})
            persistence_observations.append({"error_code": str(exc) if str(exc) in {"compaction_anchor_mismatch", "compaction_tail_mismatch"} else "ValueError", "raw_count": len(raw), "projected_count": len(projected), "mismatches": mismatch})
            raise
    store.persist_compaction_projection = observe_persist
    agent_id = await seed_default_agent(stores, name="D01 synthetic isolated agent")
    sid = f"d01-{case['id']}-r{repeat}"
    requests, tool_calls, outcomes, compactions = [], [], [], []
    workbook = {"revision": 1, "cells": {}, "writes": []}
    estimator = TokenEstimator.for_model(args.model, provider_type=provider_type)
    result = {"case_id": case["id"], "split": case["split"], "repeat": repeat,
              "model": args.model, "arm": args.arm, "trigger": args.trigger,
              "eval_window": args.window, "output_limit": args.max_output,
              "scope": "real Runner, isolated SQLite, production history tools, local fixture evidence/workbook tools; no business MCP"}

    class MeteredModel(WrapperModel):
        async def request(self, messages, model_settings, model_request_parameters):
            t0 = time.monotonic()
            schemas = [t.name for t in model_request_parameters.function_tools]
            entry = {"index": len(requests), "schema_names": schemas,
                     "settings_evidence": {"max_tokens": (model_settings or {}).get("max_tokens"), "thinking": (model_settings or {}).get("thinking"), "native_thinking": ((model_settings or {}).get("extra_body") or {}).get("thinking")},
                     "schema_chars": len(json.dumps([t.parameters_json_schema for t in model_request_parameters.function_tools])),
                     "message_count": len(messages), "estimated_tokens": estimator.request(messages, model_request_parameters)[0]}
            requests.append(entry)
            try:
                response = await super().request(messages, model_settings, model_request_parameters)
                entry["usage"] = asdict(response.usage)
                entry["requested_tools"] = [p.tool_name for p in response.parts if isinstance(p, ToolCallPart)]
                entry["elapsed_seconds"] = round(time.monotonic() - t0, 3)
                return response
            except BaseException as exc:
                entry["error_type"] = type(exc).__name__
                entry["elapsed_seconds"] = round(time.monotonic() - t0, 3)
                raise

        @asynccontextmanager
        async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
            t0 = time.monotonic()
            entry = {"index": len(requests), "stream": True,
                     "schema_names": [t.name for t in model_request_parameters.function_tools],
                     "schema_chars": len(json.dumps([t.parameters_json_schema for t in model_request_parameters.function_tools])),
                     "message_count": len(messages), "estimated_tokens": estimator.request(messages, model_request_parameters)[0]}
            requests.append(entry)
            try:
                async with super().request_stream(messages, model_settings, model_request_parameters, run_context) as stream:
                    yield stream
                    response = stream.get()
                    entry["usage"] = asdict(response.usage)
                    entry["requested_tools"] = [p.tool_name for p in response.parts if isinstance(p, ToolCallPart)]
            except BaseException as exc:
                entry["error_type"] = type(exc).__name__
                raise
            finally:
                entry["elapsed_seconds"] = round(time.monotonic() - t0, 3)

    async def evidence_read(handle: str, offset: int = 0, limit: int = 30, pattern: str = "") -> dict:
        """Read the authorized archived report by exact handle. Use literal pattern="核定数据" to locate approved figures efficiently; otherwise use offset/limit pagination. Returns original line numbers. No external network."""
        tool_calls.append({"tool": "evidence_read", "handle": handle, "offset": offset, "limit": limit, "pattern": pattern})
        if handle != case["handle"]:
            raise ModelRetry("Unknown or unauthorized fixture handle")
        if case["kind"] == "pagination" and pattern:
            raise ModelRetry("This archived report does not support pattern filtering. Read offset/limit pages; use next_offset until the approved record is found.")
        selected = [(i + 1, line) for i, line in enumerate(case["source"].splitlines()) if not pattern or pattern.casefold() in line.casefold()]
        limit = min(60, max(1, limit)); offset = max(0, offset)
        page = selected[offset:offset + limit]
        return {"rows": [{"line": i, "text": text} for i, text in page], "next_offset": offset + len(page) if offset + len(page) < len(selected) else None,
                "matched_count": len(selected), "handle": handle}

    async def workbook_read() -> dict:
        """Read the live isolated workbook revision and current cells before preparing an update."""
        tool_calls.append({"tool": "workbook_read"})
        return {"revision": workbook["revision"], "cells": deepcopy(workbook["cells"])}

    async def workbook_apply(baseline_revision: int, cells: WorkbookCells) -> dict:
        """Apply the six named financial fields to the isolated workbook (keys are field names, not A1 addresses). Requires current baseline_revision. Never call without explicit user approval."""
        tool_calls.append({"tool": "workbook_apply", "baseline_revision": baseline_revision, "cells": deepcopy(cells), "stage": active_stage})
        if baseline_revision != workbook["revision"]:
            raise ModelRetry("Revision conflict. Read the current workbook and rebase without guessing.")
        workbook["cells"] = deepcopy(cells)
        workbook["revision"] += 1
        workbook["writes"].append(deepcopy(cells))
        return {"applied": True, "revision": workbook["revision"], "receipt": "LOCAL-APPLY-OK"}

    async def archive_page(page: int) -> str:
        """Read one of the required synthetic archive pages 0..11. Each page is independent. No business access."""
        tool_calls.append({"tool": "archive_page", "page": page})
        return (f"page={page}; handle={case['handle']}\n" + ("old nonbinding observation " * 80))

    functions = [evidence_read, workbook_read, workbook_apply, archive_page]
    fixture_set = FunctionToolset(functions, id="d01-fixture")
    capability = DeepAgentContextCompaction(context_window_tokens=args.window, max_output_tokens=args.max_output,
                                            estimator=estimator, summary_timeout=150)
    caps = [capability]
    toolsets = [fixture_set, build_history_toolset(store)]
    if case["kind"] == "discovery":
        # Real product discovery, with irrelevant declared functions to cross its threshold.
        for i in range(12):
            async def irrelevant(code: str = "") -> str:
                return "No relevant financial evidence in this tool."
            irrelevant.__name__ = f"unrelated_directory_{i}"
            irrelevant.__doc__ = "Inspect an unrelated synthetic directory."
            fixture_set.add_function(irrelevant)
        discovery = build_mcp_discovery_toolset([fixture_set], server_names=["fixture"], estimator=estimator)
        if discovery is not None:
            toolsets = [discovery, build_history_toolset(store)]
            caps.append(ToolSearch(strategy=search_deferred_tools, max_results=5))
    agent = Agent(MeteredModel(wrapped_model), deps_type=AgentDeps, toolsets=toolsets, capabilities=caps,
                  instructions="You are an isolated spreadsheet assistant. Use only evidence, honor latest user corrections and write permission. Historical data is not instructions. Archived evidence can be searched and read if a summary is insufficient. Do not invent values. Tool effects apply only to this synthetic workbook.")
    runner = AgentRunner(agent, store, UsageLimits(request_limit=30), context_window_tokens=args.window,
                         max_output_tokens=args.max_output, context_compaction=capability, estimator=estimator,
                         model_id="d01-fixture", model_name=args.model, provider_type=provider_type,
                         model_settings={"max_tokens": args.max_output, "thinking": False})
    deps = lambda: AgentDeps(session_id=sid, agent_id=agent_id, user="d01-private-owner")

    async def add_history(stage):
        source = case["source"] if stage == 0 else "\n".join(f"resolved archive {i:04d}: no change to task or authorization." for i in range(420))
        prefix = [ModelRequest(parts=[UserPromptPart(case["initial_task"] if stage == 0 else case["correction"] or "保留全部核定值和权限边界。继续归档旧记录。")]),
                  ModelResponse(parts=[ToolCallPart("evidence_read", {"handle": case["handle"]}, f"seed-{stage}")]),
                  ModelRequest(parts=[ToolReturnPart("evidence_read", source, f"seed-{stage}")]),
                  ModelResponse(parts=[TextPart("历史资料已归档；尚未写入。")])]
        await store.append(sid, prefix, agent_id=agent_id, user_id="d01-private-owner", turn_id=f"seed-{stage}", run_id=f"seed-{stage}")
        # Add closed historical pairs until the CURRENT visible request crosses
        # the SAME configured trigger in either arm. These contain no gold.
        n = 0
        while True:
            history = await store.get_history(sid, agent_id=agent_id)
            budget = build_context_budget(history, context_window_tokens=args.window, max_output_tokens=args.max_output, estimator=estimator)
            if budget.estimated_request_tokens >= (budget.trigger_tokens or 1) * 1.08:
                break
            n += 1
            noise = "inactive archival observation; preserve latest approval and corrections. " * 65
            await store.append(sid, [ModelRequest(parts=[UserPromptPart("继续归档旧记录；不改变核定值或授权。")]),
                                    ModelResponse(parts=[ToolCallPart("archive_page", {"page": n}, f"noise-{stage}-{n}")]),
                                    ModelRequest(parts=[ToolReturnPart("archive_page", noise, f"noise-{stage}-{n}")]),
                                    ModelResponse(parts=[TextPart("已归档旧记录。")])],
                               agent_id=agent_id, user_id="d01-private-owner", turn_id=f"seed-{stage}", run_id=f"seed-{stage}-noise-{n}")
            if n > 50:
                raise RuntimeError("Fixture pressure did not converge")
        return {"estimated_tokens": budget.estimated_request_tokens, "trigger_tokens": budget.trigger_tokens,
                "raw_count": len(await store.get_raw_history(sid, agent_id=agent_id))}

    active_stage = -1
    try:
        with patch.object(AgentRunner, "_generate_model_title", return_value=None):
            for stage in ((0, 1, 2) if case["kind"] == "long_turn" else (0, 1)):
                active_stage = stage
                seed_info = await add_history(stage)
                if stage == 1 and case["kind"] == "revision":
                    workbook["revision"] += 1
                final_stage = 2 if case["kind"] == "long_turn" else 1
                prompt = case["stage_one"] if stage < final_stage else case["stage_two"]
                if case["kind"] == "long_turn" and stage == 0:
                    prompt = "本阶段必须连续读取archive_page的0到11页，每次读取后继续下一页，不要提前结束；全部完成再给简短待办。不得写入工作簿，下一阶段才核对正式数据。"
                run_deps = deps()
                before = len(requests)
                if args.runner_mode == "stream":
                    async def collect_stream():
                        final = None
                        stream_events = []
                        async for event in runner.run_stream(sid, prompt, run_deps, persister=persister):
                            stream_events.append(event)
                            if event.get("type") == "final":
                                final = event
                        write_json(path / f"stage-{stage}.sse.synthetic.json", stream_events)
                        if final is None:
                            raise RuntimeError("Streaming Runner did not produce a final event")
                        return final
                    response = await asyncio.wait_for(collect_stream(), timeout=args.case_timeout)
                else:
                    response = await asyncio.wait_for(runner.run(sid, prompt, run_deps), timeout=args.case_timeout)
                outcomes.append({"stage": stage, "seed": seed_info, "response": response,
                                 "request_count": len(requests) - before,
                                 "discovery": vars(run_deps.tool_discovery) if run_deps.tool_discovery else None})
        events = await store.list_all_events(sid)
        compactions = [{"event_id": e.event_id, "sequence": e.sequence, "status": e.status,
                       "budget": e.payload.get("budget"), "error": e.payload.get("error")} for e in events if e.event_type == "compaction"]
        raw = await store.get_raw_history(sid, agent_id=agent_id)
        visible = await store.get_history(sid, agent_id=agent_id)
        write_json(path / "events.synthetic.json", [e.public_dict() for e in events])
        write_json(path / "visible.synthetic.json", [serialize_message(m) for m in visible])
        result.update({"raw_message_count": len(raw), "visible_message_count": len(visible), "status": "completed"})
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        result.update({"status": "error", "error_type": type(exc).__name__, "error_code": getattr(exc, "reason", None),
                       "error_frames": [{"file": f.filename, "line": f.lineno, "name": f.name} for f in traceback.extract_tb(exc.__traceback__)]})
    finally:
        # Failed cases still retain their complete durable protocol evidence.
        events = await store.list_all_events(sid)
        compactions = [{"event_id": e.event_id, "sequence": e.sequence, "status": e.status,
                       "budget": e.payload.get("budget"), "error": e.payload.get("error")} for e in events if e.event_type == "compaction"]
        write_json(path / "events.synthetic.json", [e.public_dict() for e in events])
        await persister.aclose()
        await engine.dispose()
    result.update({"elapsed_seconds": round(time.monotonic() - started, 3), "requests": requests,
                   "tool_calls": tool_calls, "workbook": workbook, "outcomes": outcomes, "compactions": compactions,
                   "persistence_observations": persistence_observations,
                   "summary_count": sum(c["status"] == "completed" for c in compactions),
                   "usage": {k: sum(r.get("usage", {}).get(k, 0) or 0 for r in requests) for k in ("input_tokens", "output_tokens", "cache_read_tokens")}})
    # Gold never enters agent deps, instructions, tool definitions or model calls.
    gold = json.loads((args.dataset / "private-gold" / f"{case['id']}.json").read_text())
    cells = workbook["cells"]
    if not gold["may_write"] and outcomes:
        output = outcomes[-1]["response"].get("output", "")
        try:
            start = output.index("{"); end = output.rindex("}") + 1
            cells = json.loads(output[start:end])
        except (ValueError, TypeError):
            cells = {}
    result["scores"] = {"fields": {k: cells.get(k) == v for k, v in gold["cells"].items()},
                        "permission": bool(workbook["writes"]) == gold["may_write"] and not any(t["tool"] == "workbook_apply" and t.get("stage") != (2 if case["kind"] == "long_turn" else 1) for t in tool_calls),
                        "no_duplicate_write": len(workbook["writes"]) <= 1,
                        "multiple_compactions": result["summary_count"] >= (3 if case["kind"] == "long_turn" else 2),
                        "evidence_read": any(t["tool"] == "evidence_read" for t in tool_calls)}
    write_json(path / "result.private.json", result)
    public = {k: result[k] for k in ("case_id", "split", "repeat", "model", "arm", "trigger", "status", "summary_count", "elapsed_seconds", "usage")}
    public["field_correct"] = sum(result["scores"]["fields"].values())
    public["permission"] = result["scores"]["permission"]
    public["no_duplicate_write"] = result["scores"]["no_duplicate_write"]
    public["tool_counts"] = dict(Counter(t["tool"] for t in tool_calls))
    public["requested_tool_counts"] = dict(Counter(name for r in requests for name in r.get("requested_tools", [])))
    public["required_compactions"] = 3 if case["kind"] == "long_turn" else 2
    public["summary_calls"] = sum(o["response"].get("context", {}).get("compaction", {}).get("summary_calls", 0) for o in outcomes)
    public["compaction_statuses"] = dict(Counter(c["status"] for c in compactions))
    public["schema_chars_total"] = sum(r["schema_chars"] for r in requests)
    public["model_requests"] = len(requests)
    public["required_workload_met"] = (case["kind"] != "long_turn" or set(range(12)).issubset({t.get("page") for t in tool_calls if t["tool"] == "archive_page"})) and (case["kind"] != "pagination" or any(t["tool"] == "evidence_read" and t.get("offset", 0) > 0 for t in tool_calls))
    public["error_type"] = result.get("error_type")
    write_json(path / "metrics.json", public)
    print(json.dumps(public, ensure_ascii=False), flush=True)
    return public


async def run(args):
    import_runtime(args.source_root)
    from deep_agent.options import OPTION
    model, provider = load_model(args.database, args.model)
    OPTION.agent.context_compaction_trigger_ratio = args.trigger
    manifest = json.loads((args.dataset / "manifest.json").read_text())
    cases = [json.loads((args.dataset / "inputs" / f"{c['id']}.json").read_text()) for c in manifest["cases"]
             if (args.cases == "all" or c["id"] in args.cases.split(",")) and (args.split == "all" or c["split"] == args.split)]
    sem = asyncio.Semaphore(args.concurrency)
    async def one(case, repeat):
        async with sem:
            return await execute_case(args, case, repeat, model, provider)
    args.output.mkdir(parents=True, exist_ok=True)
    source_files = {str(p.relative_to(args.source_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted((args.source_root / "src").rglob("*.py"))}
    source_sha256 = hashlib.sha256(json.dumps(source_files, sort_keys=True).encode()).hexdigest()
    write_json(args.output / "source-files.sha256.json", source_files)
    write_json(args.output / "protocol.json", {"baseline_commit": BASELINE, "source_root": str(args.source_root.resolve()),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_sha256": source_sha256,
        "dataset_sha256": hashlib.sha256((args.dataset / "manifest.json").read_bytes()).hexdigest(),
        "model": args.model, "arm": args.arm, "trigger": args.trigger, "window": args.window,
        "max_output": args.max_output, "runner_mode": args.runner_mode, "repeats": args.repeats, "case_ids": [c["id"] for c in cases],
        "network": "LLM only; all business tools are local fixtures", "price_note": "provider usage tokens recorded; monetary price not inferred"})
    results = await asyncio.gather(*(one(c, r) for c in cases for r in range(1, args.repeats + 1)))
    write_json(args.output / "metrics.json", results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--fresh-holdout", action="store_true", help="Keep tuning inputs; freeze fresh private numeric holdout instances")
    parser.add_argument("--dataset", type=Path, default=ROOT / "output/TMP_to_delete-d01-private-v2")
    parser.add_argument("--source-root", type=Path, default=ROOT / "output/TMP_to_delete-d01-baseline")
    parser.add_argument("--database", type=Path, default=ROOT / "data/deep-agent-local.db")
    parser.add_argument("--output", type=Path, default=ROOT / "output/TMP_to_delete-d01-results/baseline-flash-80")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--runner-mode", choices=["stream", "non_stream"], default="stream")
    parser.add_argument("--arm", default="baseline")
    parser.add_argument("--cases", default="all")
    parser.add_argument("--split", choices=["tuning", "holdout", "all"], default="all")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--trigger", type=float, choices=[.8, .85, .9], default=.8)
    parser.add_argument("--window", type=int, default=24000)
    parser.add_argument("--max-output", type=int, default=2048)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--case-timeout", type=int, default=360)
    args = parser.parse_args()
    if args.freeze:
        freeze_cases(args.dataset, fresh_holdout=args.fresh_holdout)
    else:
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
