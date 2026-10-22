import assert from "node:assert/strict";
import test from "node:test";
import { buildTrajectory, createTrajectoryController } from "../../../console/trajectory.js";

const fact = (id, sequence, ordinal, index, part, eventType = "assistant/message") => ({
  event_id: id, sequence, run_id: "run-1", turn_id: "turn-1", event_type: eventType,
  status: "completed", created_at: "2026-09-08T01:00:00Z",
  payload: {
    message: { kind: eventType === "assistant/message" ? "response" : "request", parts: [part] },
    source: { message_ordinal: ordinal, part_index: index, part_key: `${ordinal}:${index}` },
  },
});

test("part facts preserve assistant loop identity and pair independently completed parallel tools", () => {
  const facts = [
    fact("thinking", 1, 0, 0, { part_kind: "thinking", content: "先查询" }),
    fact("text", 2, 0, 1, { part_kind: "text", content: "我来查一下" }),
    fact("call-a", 3, 0, 2, { part_kind: "tool-call", tool_name: "lookup", tool_call_id: "a", args: { key: "a" } }),
    fact("call-b", 4, 0, 3, { part_kind: "tool-call", tool_name: "lookup", tool_call_id: "b", args: { key: "b" } }),
    fact("result-b", 5, 1, 1, { part_kind: "tool-return", tool_name: "lookup", tool_call_id: "b", content: "B结果" }, "tool/results"),
    fact("result-a", 6, 1, 0, { part_kind: "tool-return", tool_name: "lookup", tool_call_id: "a", content: "A结果" }, "tool/results"),
    fact("final", 7, 2, 0, { part_kind: "text", content: "最终答案" }),
  ];
  const early = buildTrajectory({ items: facts.slice(0, 5) });
  assert.equal(early.filter((row) => row.kind === "assistant").length, 1);
  assert.equal(early[0].payload.thinking, "先查询");
  assert.equal(early[0].payload.content, "我来查一下");
  assert.equal(early[0].payload.tool_calls.length, 2);
  assert.equal(early.find((row) => row.tool_call_id === "a").status, "pending");
  assert.equal(early.find((row) => row.tool_call_id === "b").status, "completed");
  const final = buildTrajectory({ items: [...facts].reverse() });
  assert.deepEqual(final.map((row) => row.id), ["thinking", "tool:a", "tool:b", "final"]);
  assert.equal(final[0].id, early[0].id);
  assert.deepEqual(final[0].native.event_ids, ["thinking", "text", "call-a", "call-b"]);
  assert.equal(final.find((row) => row.tool_call_id === "a").result.result, "A结果");
});

function withController(check) {
  const oldDocument = globalThis.document;
  const oldWindow = globalThis.window;
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      innerHTML: "", value: "", scrollTop: 0,
      classList: { toggle() {}, contains: () => true },
      listeners: new Map(),
      addEventListener(type, callback) { this.listeners.set(type, callback); }, setAttribute() {},
    });
    return elements.get(id);
  };
  globalThis.document = { getElementById: element, querySelector: element, querySelectorAll: () => [] };
  globalThis.window = { requestAnimationFrame: (fn) => fn() };
  try { check(createTrajectoryController({ state: {} }), elements); }
  finally { globalThis.document = oldDocument; globalThis.window = oldWindow; }
}

test("manual history selection survives appended facts, checkpoints and history refresh", () => {
  withController((controller, elements) => {
    const first = fact("first", 1, 0, 0, { part_kind: "text", content: "旧内容" });
    const latest = fact("latest", 2, 1, 0, { part_kind: "text", content: "最新内容" });
    controller.setHistory({ session_id: "a", items: [first, latest] });
    assert.equal(controller.snapshot().selectedId, "latest", "new history opens at latest event");
    const click = elements.get("trajectoryList").listeners.get("click");
    click({ target: { closest: () => ({ dataset: { trajectoryId: "first" } }) } });
    assert.equal(controller.snapshot().selectedId, "first");

    const next = fact("next", 3, 2, 0, { part_kind: "text", content: "下一条" });
    controller.ingest({ event: "session_event_appended", data: { event: next } });
    controller.ingest({ event: "session_event_updated", data: { event: { ...latest, status: "interrupted" } } });
    assert.equal(controller.snapshot().selectedId, "first");
    assert.match(elements.get("inspectorContent").innerHTML, /旧内容/);
    controller.setHistory({ session_id: "a", items: [first, latest, next] }, { preserveView: true });
    assert.equal(controller.snapshot().selectedId, "first");
    assert.equal(controller.snapshot().selectionPinned, true);

    // Even if event IDs happen to overlap, another session must not inherit selection.
    controller.setHistory({ session_id: "b", items: [first, next] }, { preserveView: true });
    assert.equal(controller.snapshot().selectedId, "next");
    assert.equal(controller.snapshot().selectionPinned, false);
    controller.ingest({ event: "session_event_appended", data: { event: latest } });
    assert.equal(controller.snapshot().selectedId, "latest");
  });
});

test("reset and snapshot restore keep manual selection scoped to its loaded view", () => {
  withController((controller, elements) => {
    const first = fact("first", 1, 0, 0, { part_kind: "text", content: "first" });
    const latest = fact("latest", 2, 1, 0, { part_kind: "text", content: "latest" });
    controller.setHistory({ session_id: "a", items: [first, latest] });
    const automatic = controller.snapshot();
    elements.get("trajectoryList").listeners.get("click")({
      target: { closest: () => ({ dataset: { trajectoryId: "first" } }) },
    });
    const selected = controller.snapshot();
    controller.reset();
    assert.equal(controller.snapshot().selectionPinned, false);
    controller.setHistory({ session_id: "a", items: [first, latest] });
    controller.restore(selected);
    controller.ingest({ event: "session_event_updated", data: { event: latest } });
    assert.equal(controller.snapshot().selectedId, "first");
    controller.restore(automatic);
    controller.ingest({ event: "session_event_updated", data: { event: first } });
    assert.equal(controller.snapshot().selectedId, "first", "automatic snapshots keep following facts");
  });
});

test("execution errors show an escaped reason, code and phase once despite terminal SSE", () => {
  withController((controller, elements) => {
    controller.ingest({ event: "session", data: { run_id: "run-1" } });
    controller.ingest({ event: "text", data: { delta: "已经完成部分" } });
    const error = {
      event_id: "error-1", event_type: "execution/error", sequence: 8, run_id: "run-1",
      payload: { code: "provider_error", message: "模型服务失败 <script>alert(1)</script>", phase: "model" },
    };
    controller.ingest({ event: "session_event_appended", data: { event: error } });
    controller.ingest({ event: "error", data: { message: error.payload.message } });
    assert.equal(controller.events().filter((row) => row.kind === "error").length, 1);
    assert.equal(controller.events().some((row) => row.kind === "run"), false);
    const markup = elements.get("inspectorContent").innerHTML;
    assert.match(markup, /Error/);
    assert.match(markup, /provider_error/);
    assert.match(markup, /model/);
    assert.match(markup, /&lt;script&gt;/);
    assert.doesNotMatch(markup, /<script>/);
  });
});

test("cancellation closes live content without adding any terminal receipt", () => {
  withController((controller) => {
    controller.ingest({ event: "session", data: { run_id: "run-1" } });
    controller.ingest({ event: "text", data: { delta: "部分内容" } });
    controller.ingest({ event: "cancelled", data: {} });
    assert.equal(controller.events().length, 1);
    assert.equal(controller.events()[0].kind, "assistant");
    assert.equal(controller.events()[0].status, "interrupted");
  });
});

test("late failed assistant facts replace closed live content before the next successful run", () => {
  withController(controller => {
    const failed = { ...fact("failed-thinking", 1, 1, 0, { part_kind: "thinking", content: "部分思考" }), status: "failed" };
    failed.payload.message.timestamp = "2026-10-22T14:01:59.920349Z";
    const failure = { event_id: "failure", sequence: 2, run_id: "run-1", event_type: "execution/error", status: "failed",
      created_at: "2026-10-22T14:02:01.255650Z", payload: { code: "agent_run_failed", message: "本轮已停止" } };
    const success = { ...fact("successful-text", 3, 1, 0, { part_kind: "text", content: "成功回复" }), run_id: "run-2" };
    controller.ingest({ event: "session", data: { run_id: "run-1" } });
    controller.ingest({ event: "thinking", data: { delta: "部分思考" } });
    controller.ingest({ event: "session_event_updated", data: { event: failure } });
    controller.ingest({ event: "error", data: { message: "本轮已停止" } });
    // The terminal presentation frame can close the live row before its fact
    // arrives. A later checkpoint must still replace that same Run's row.
    controller.ingest({ event: "session_event_appended", data: { event: failed } });
    controller.ingest({ event: "session", data: { run_id: "run-2" } });
    controller.ingest({ event: "text", data: { delta: "成功回复" } });
    controller.ingest({ event: "session_event_appended", data: { event: success } });
    controller.ingest({ event: "final", data: { status: "completed" } });
    const liveRows = controller.events();
    assert.deepEqual(liveRows.map(row => [row.id, row.status]), [["failed-thinking", "failed"], ["failure", "failed"], ["successful-text", "completed"]]);
    controller.setHistory({ items: [failed, failure, success] });
    assert.deepEqual(controller.events(), liveRows, "refresh keeps the same rows, status, ordering and persisted timestamps");
  });
});

test("thinking-only assistant summaries reflect the same terminal state live and after refresh", () => {
  for (const [terminal, status, summary] of [["error", "failed", "模型响应失败"], ["cancelled", "interrupted", "已停止"], ["final", "completed", "模型思考"]]) {
    withController(controller => {
      controller.ingest({ event: "session", data: { run_id: "run-1" } });
      controller.ingest({ event: "thinking", data: { delta: "保留思考正文" } });
      assert.equal(controller.events()[0].summary, "模型处理中");
      controller.ingest({ event: terminal, data: {} });
      assert.equal(controller.events()[0].summary, summary);
      assert.equal(controller.events()[0].status, status);
      controller.setHistory({ items: [{ ...fact("thinking-only", 1, 1, 0, { part_kind: "thinking", content: "保留思考正文" }), status }] });
      assert.equal(controller.events()[0].summary, summary);
      assert.equal(controller.events()[0].payload.thinking, "保留思考正文");
    });
  }
});

test("updates and later parts of a persisted assistant loop keep the next live loop", () => {
  withController(controller => {
    const first = fact("first-thinking", 1, 1, 0, { part_kind: "thinking", content: "第一轮" });
    const laterPart = fact("first-text", 2, 1, 1, { part_kind: "text", content: "第一轮正文" });
    controller.ingest({ event: "session", data: { run_id: "run-1" } });
    controller.ingest({ event: "thinking", data: { delta: "第一轮" } });
    controller.ingest({ event: "session_event_appended", data: { event: first } });
    controller.ingest({ event: "thinking", data: { delta: "第二轮直播" } });
    controller.ingest({ event: "session_event_appended", data: { event: laterPart } });
    controller.ingest({ event: "session_event_updated", data: { event: { ...first, status: "completed" } } });
    const assistants = controller.events().filter(row => row.kind === "assistant");
    assert.equal(assistants.length, 2);
    assert.equal(assistants[0].payload.content, "第一轮正文");
    assert.equal(assistants[1].payload.thinking, "第二轮直播");
    assert.equal(assistants[1].status, "running");
  });
});

test("an allowed text final stays an assistant trajectory row after a rejected output and History refresh", () => {
  withController(controller => {
    const rejected = { output_id: "rejected-json", name: "lease_result", accepted: false, status: "error", output: {}, run_id: "run-1", turn_id: "turn-1" };
    controller.ingest({ event: "session", data: { run_id: "run-1" } });
    controller.ingest({ event: "output", data: rejected });
    controller.ingest({ event: "text", data: { delta: "请补充合同附件。" } });
    controller.ingest({ event: "final", data: { status: "completed", output: "请补充合同附件。" } });
    let rows = controller.events();
    assert.deepEqual(rows.filter(row => row.kind === "output").map(row => row.status), ["failed"]);
    assert.equal(rows.filter(row => row.kind === "assistant").length, 1);
    assert.equal(rows.find(row => row.kind === "assistant").payload.content, "请补充合同附件。");
    controller.setHistory({ items: [
      { event_id: rejected.output_id, event_type: "output/attempt", sequence: 1, run_id: "run-1", turn_id: "turn-1", payload: rejected },
      fact("text-final", 2, 1, 0, { part_kind: "text", content: "请补充合同附件。" }),
    ] });
    rows = controller.events();
    assert.deepEqual(rows.filter(row => row.kind === "output").map(row => row.status), ["failed"]);
    assert.equal(rows.filter(row => row.kind === "assistant").length, 1);
    assert.equal(rows.find(row => row.kind === "assistant").payload.content, "请补充合同附件。");
  });
});

test("persisted tool facts and their following SSE presentation frames produce one row", () => {
  withController((controller) => {
    controller.ingest({ event: "session", data: { run_id: "run-1" } });
    const call = fact("call", 1, 0, 0, { part_kind: "tool-call", tool_name: "lookup", tool_call_id: "a", args: {} });
    const result = fact("result", 2, 1, 0, { part_kind: "tool-return", tool_name: "lookup", tool_call_id: "a", content: "完成" }, "tool/results");
    controller.ingest({ event: "session_event_appended", data: { event: call } });
    controller.ingest({ event: "tool_call", data: { tool: "lookup", tool_call_id: "a", args: {} } });
    controller.ingest({ event: "session_event_appended", data: { event: result } });
    controller.ingest({ event: "tool_result", data: { tool: "lookup", tool_call_id: "a", result: "完成" } });
    assert.equal(controller.events().length, 2);
    const tool = controller.events().find(row => row.kind === "tool");
    assert.equal(tool.status, "completed");
    assert.equal(tool.result.result, "完成");
  });
});

test("tool-only assistant responses remain visible and exportable without inventing text", () => {
  const facts = [];
  let sequence = 0;
  for (let loop = 0; loop < 4; loop += 1) {
    const ordinal = loop * 2 + 1;
    facts.push(fact(`empty-${loop}`, ++sequence, ordinal, 0, { part_kind: "thinking", content: "" }));
    for (let index = 0; index < (loop % 2 ? 2 : 1); index += 1) {
      const id = `call-${loop}-${index}`;
      facts.push(fact(id, ++sequence, ordinal, index + 1, {
        part_kind: "tool-call", tool_name: "lookup", tool_call_id: id, args: {},
      }));
      facts.push(fact(`result-${id}`, ++sequence, ordinal + 1, index, {
        part_kind: "tool-return", tool_name: "lookup", tool_call_id: id, content: "fixture result",
      }, "tool/results"));
    }
  }
  const rows = buildTrajectory({ items: facts });
  const assistants = rows.filter(row => row.kind === "assistant");
  assert.equal(assistants.length, 4);
  assert.equal(rows.filter(row => row.kind === "tool").length, 6);
  assert.equal(assistants.reduce((sum, row) => sum + row.native.event_ids.length, 0), 10);
  for (const row of assistants) {
    assert.equal(row.payload.content, null);
    assert.equal(row.payload.thinking, null);
    assert.equal(row.summary, `调用 ${row.payload.tool_calls.length} 个工具`);
  }
  assert.equal(JSON.parse(JSON.stringify(rows)).filter(row => row.kind === "assistant").length, 4);
});

test("presentation-only tool result frames preserve persisted execution endpoints", () => {
  withController((controller) => {
    controller.ingest({ event: "session", data: { run_id: "run-1" } });
    const call = fact("call", 1, 0, 0, { part_kind: "tool-call", tool_name: "lookup", tool_call_id: "a" });
    const timing = { version: 1, kind: "tool_execution", duration_ms: 1234,
      started_at: "2026-10-15T01:00:00Z", completed_at: "2026-10-15T01:00:01.234Z" };
    const result = fact("result", 2, 1, 0, { part_kind: "tool-return", tool_name: "lookup", tool_call_id: "a",
      content: "完成", metadata: { deepagent_timing: timing } }, "tool/results");
    controller.ingest({ event: "session_event_appended", data: { event: call } });
    controller.ingest({ event: "session_event_appended", data: { event: result } });
    controller.ingest({ event: "tool_result", data: { tool: "lookup", tool_call_id: "a", result: "完成" } });
    const tool = controller.events().find(row => row.tool_call_id === "a");
    assert.equal(tool.duration_ms, timing.duration_ms);
    assert.equal(tool.timestamp, timing.started_at);
    assert.equal(tool.timestamp_end, timing.completed_at);
  });
});

test("reused tool call IDs in different loops retain distinct arguments, results, selection and SSE rows", () => {
  withController((controller, elements) => {
    controller.ingest({ event: "session", data: { run_id: "run-1" } });
    for (let loop = 0; loop < 2; loop += 1) {
      const call = fact(`call-${loop}`, loop * 3 + 1, loop * 2, 0, {
        part_kind: "tool-call", tool_name: "lookup", tool_call_id: "reused", args: { loop },
      });
      controller.ingest({ event: "session_event_appended", data: { event: call } });
      controller.ingest({ event: "tool_call", data: { tool: "lookup", tool_call_id: "reused", args: { loop } } });
      assert.equal(controller.events().filter((row) => row.kind === "tool").length, loop + 1);
      const result = fact(`result-${loop}`, loop * 3 + 2, loop * 2 + 1, 0, {
        part_kind: "tool-return", tool_name: "lookup", tool_call_id: "reused", content: `结果${loop}`,
      }, "tool/results");
      controller.ingest({ event: "session_event_appended", data: { event: result } });
      controller.ingest({ event: "tool_result", data: { tool: "lookup", tool_call_id: "reused", result: `结果${loop}` } });
      assert.match(elements.get("inspectorContent").innerHTML, new RegExp(`结果${loop}`));
    }
    const rows = controller.events().filter(row => row.kind === "tool");
    assert.equal(rows.length, 2);
    assert.equal(new Set(rows.map((row) => row.id)).size, 2);
    assert.deepEqual(rows.map((row) => row.payload.args), [{ loop: 0 }, { loop: 1 }]);
    assert.deepEqual(rows.map((row) => row.result.result), ["结果0", "结果1"]);
    assert.deepEqual(rows.map((row) => row.native.result_event_id), ["result-0", "result-1"]);
  });
});

test("上下文引用保留在用户输入中，不再派生独立轨迹，入库后只保留事实行", () => {
  withController((controller, elements) => {
    const refs = [{ type: "deeptable.range", key: "range-1", label: "选中区域" }];
    controller.addUser({ content: "分析选区", context_refs: refs });
    assert.equal(controller.events().length, 1);
    assert.equal(controller.events()[0].kind, "input");
    assert.deepEqual(controller.events()[0].payload.context_refs, refs);
    controller.ingest({ event: "session", data: { run_id: "run-context" } });
    assert.equal(controller.events()[0].run_id, "run-context");
    controller.ingest({ event: "session_event_appended", data: { event: {
      event_id: "user-context", event_type: "user/message", sequence: 1,
      run_id: "run-context", status: "completed",
      payload: { message: { kind: "request", parts: [{ part_kind: "user-prompt", content: [{ kind: "text-content", content: "分析选区" }, ...refs.map(ref => ({ kind: "context-ref", ...ref }))] }] } },
    } } });
    assert.equal(controller.events().length, 1);
    assert.equal(controller.events()[0].id, "user-context");
    assert.deepEqual(controller.events()[0].payload.context_refs, refs.map(ref => ({ kind: "context-ref", ...ref })));
    const markup = elements.get("inspectorContent").innerHTML;
    assert.match(markup, />用户</);
    assert.match(markup, />Summary</);
    assert.match(markup, />Completed</);
    assert.match(markup, /选中区域/);
    assert.doesNotMatch(markup, />USER<|>CONTEXT<|>摘要</);
  });
});


test("detail tabs distinguish message content, tool input/result and semantic events", () => {
  withController((controller, elements) => {
    controller.setHistory({ items: [
      fact("reply", 1, 0, 0, { part_kind: "text", content: "assistant-output" }),
      fact("call", 2, 1, 0, { part_kind: "tool-call", tool_name: "lookup", tool_call_id: "call-1", args: { query: "input-only" } }),
      fact("result", 3, 2, 0, { part_kind: "tool-return", tool_name: "lookup", tool_call_id: "call-1", content: "result-only" }, "tool/results"),
      { event_id: "title", sequence: 4, event_type: "session/title", payload: { title: "saved-title", source: "user" } },
    ] });
    const detail = () => elements.get("inspectorContent").innerHTML;
    controller.restore({ selectedId: "reply", detailTab: "message" });
    assert.match(detail(), />Message</);
    assert.match(detail(), /assistant-output/);
    assert.doesNotMatch(detail(), />Input<|>Result<|>Payload</);
    controller.restore({ selectedId: "tool:call-1", detailTab: "input" });
    assert.match(detail(), /input-only/);
    assert.doesNotMatch(detail(), /result-only/);
    controller.restore({ selectedId: "tool:call-1", detailTab: "result" });
    assert.match(detail(), /result-only/);
    assert.doesNotMatch(detail(), /input-only/);
    controller.restore({ selectedId: "title", detailTab: "event" });
    assert.match(detail(), />Event</);
    assert.match(detail(), /saved-title/);
    assert.doesNotMatch(detail(), />Request<|>Result<|>Payload</);
  });
});

test("planning shows the protocol tool name and a shared escaped plan snapshot", () => {
  withController((controller, elements) => {
    const items = [
      { id: "one", content: "<script>bad</script>", status: "completed" },
      { id: "two", content: "second", active_form: "running-step", status: "in_progress" },
    ];
    controller.setHistory({ items: [
      fact("plan-call", 1, 0, 0, { part_kind: "tool-call", tool_name: "write_plan", tool_call_id: "plan-1", args: { items } }),
      fact("plan-result", 2, 1, 0, { part_kind: "tool-return", tool_name: "write_plan", tool_call_id: "plan-1", content: "Plan updated", metadata: { deepagent_plan: { items } } }, "tool/results"),
    ] });
    controller.restore({ selectedId: "tool:plan-1" });
    assert.match(elements.get("trajectoryList").innerHTML, /<strong>write_plan<\/strong>/);
    const html = elements.get("inspectorContent").innerHTML;
    assert.match(html, /composer-plan-item status-completed/);
    assert.match(html, /running-step/);
    assert.match(html, /1\/2/);
    assert.match(html, /&lt;script&gt;/);
    assert.doesNotMatch(html, /<script>|trajectory-plan-progress|创建或改写任务计划/);
  });
});


test("title Event keeps initial and latest title records without HTTP labels", () => {
  withController((controller, elements) => {
    controller.setHistory({ items: [
      { event_id: "initial-title", sequence: 1, event_type: "session/title", payload: { title: "initial-name", source: "fallback", source_event_id: "user-1" } },
      { event_id: "latest-title", sequence: 2, event_type: "session/title", payload: { title: "final-name", source: "model", source_event_id: "user-1" } },
    ] });
    controller.restore({ selectedId: "initial-title", detailTab: "event" });
    const html = elements.get("inspectorContent").innerHTML;
    assert.match(html, /initial-name/);
    assert.match(html, /final-name/);
    assert.match(html, /initial-title/);
    assert.match(html, /latest-title/);
    assert.doesNotMatch(html, />Request<|>Result</);
  });
});

test("legacy model intervals cover generation and ignore mutable database update times", () => {
  const first = fact("thinking", 1, 31, 0, { part_kind: "thinking", content: "fixture" });
  first.payload.message.timestamp = "2026-10-15T13:33:12.565091Z";
  first.created_at = "2026-10-15T13:36:06Z";
  first.updated_at = "2026-10-15T13:37:16Z";
  const call = fact("call", 2, 31, 1, {part_kind: "tool-call", tool_name: "write_plan", tool_call_id: "c"});
  call.payload.message.timestamp = first.payload.message.timestamp;
  call.created_at = call.updated_at = "2026-10-15T13:36:07Z";
  const assistant = buildTrajectory({items: [first, call]})[0];
  assert.equal(assistant.duration_ms, 173435);
  assert.equal(assistant.duration_source, "estimated");
  assert.equal(assistant.timestamp_end, first.created_at);
  first.updated_at = "2026-10-16T00:00:00Z";
  assert.equal(buildTrajectory({items:[first,call]})[0].duration_ms, assistant.duration_ms);
});

test("measured model and parallel tool durations survive event grouping and reused IDs", () => {
  const timing = (kind, duration_ms, started_at = "2026-10-15T13:33:11Z", completed_at = "2026-10-15T13:36:04Z") => ({version:1, kind, duration_ms, started_at, completed_at});
  const call = fact("call", 1, 1, 0, {part_kind:"tool-call", tool_name:"lookup",tool_call_id:"c"});
  call.payload.message.metadata = {deepagent_timing:timing("model_request",172751)};
  const result = fact("result", 2, 2, 0, {part_kind:"tool-return",tool_name:"lookup",tool_call_id:"c",content:"ok",metadata:{deepagent_timing:timing("tool_execution", 812)}}, "tool/results");
  const later = fact("later", 3, 3, 0, {part_kind:"tool-call",tool_name:"lookup",tool_call_id:"c"});
  later.payload.message.metadata = {deepagent_timing:timing("model_request", 58783)};
  const rows = buildTrajectory({items:[call,result,later]});
  assert.deepEqual(rows.filter(x=>x.kind==="assistant").map(x=>x.duration_ms), [172751,58783]);
  assert.equal(rows.find(x=>x.id==="tool:c").duration_ms,812);
  assert.equal(rows.find(x=>x.id==="tool:c").duration_source,"execution");
  assert.equal(rows.filter(x=>x.tool_call_id==="c").at(-1).duration_ms,undefined);
});

test("missing or invalid timing stays unknown instead of becoming zero or a database interval", () => {
  const row = fact("unknown", 1, 0, 0, {part_kind:"text",content:"fixture"});
  row.updated_at = "2026-10-16T00:00:00Z";
  row.payload.message.metadata = {deepagent_timing:{version:1,kind:"model_request",duration_ms:null}};
  assert.equal(buildTrajectory({items:[row]})[0].duration_ms,undefined);
  withController((controller,elements)=>{
    controller.setHistory({session_id:"unknown",items:[row]});
    assert.match(elements.get("inspectorContent").innerHTML,/Duration unknown/);
  });
});

test("legacy tool intervals use result production time and never count deferred human waiting", () => {
  const call = fact("call",1,0,0,{part_kind:"tool-call",tool_name:"lookup",tool_call_id:"a"});
  const result = fact("result",2,1,0,{part_kind:"tool-return",tool_name:"lookup",tool_call_id:"a",content:"ok",timestamp:"2026-09-08T01:00:02Z"},"tool/results");
  result.updated_at = "2026-09-09T00:00:00Z";
  assert.equal(buildTrajectory({items:[call,result]}).find(x=>x.tool_call_id==="a").duration_ms,2000);
  call.payload.message.parts[0].metadata = {widget:"builtin.confirm"};
  assert.equal(buildTrajectory({items:[call,result]}).find(x=>x.tool_call_id==="a").duration_ms,undefined);
});

test("duration toggle preserves selection/order and classifies timing boundaries without treating unknown as fast", () => {
  withController((controller, elements) => {
    const durations = [5000, 5001, 10000, 10001, 20000, 20001];
    const items = durations.map((duration_ms, i) => {
      const item = fact(`model-${i}`, i+1, i, 0, {part_kind:"text",content:`response ${i}`});
      item.payload.message.metadata = {deepagent_timing:{version:1,kind:"model_request",duration_ms,started_at:"2026-10-15T00:00:00Z",completed_at:new Date(Date.parse("2026-10-15T00:00:00Z")+duration_ms).toISOString()}};
      return item;
    });
    items.push(fact("unknown", 7, 7, 0, {part_kind:"text",content:"unknown"}));
    controller.setHistory({session_id:"duration-toggle",items});
    const before = controller.events().map(x=>x.id);
    const selected = controller.snapshot().selectedId;
    const list = elements.get("trajectoryList");
    assert.match(list.innerHTML, /<time>/);
    list.scrollTop = 50;
    const toggle = elements.get("trajectoryClockToggle").listeners.get("click");
    toggle();
    assert.equal(controller.snapshot().clockMode,"duration");
    assert.deepEqual(controller.events().map(x=>x.id),before);
    assert.equal(controller.snapshot().selectedId,selected);
    assert.equal(list.scrollTop,50);
    assert.doesNotMatch(list.innerHTML,/<time>/);
    assert.equal((list.innerHTML.match(/trajectory-duration fast/g)||[]).length,1);
    assert.equal((list.innerHTML.match(/trajectory-duration steady/g)||[]).length,2);
    assert.equal((list.innerHTML.match(/trajectory-duration elevated/g)||[]).length,2);
    assert.equal((list.innerHTML.match(/trajectory-duration slow/g)||[]).length,1);
    assert.match(list.innerHTML,/trajectory-duration unknown/);
    const snapshot = controller.snapshot();
    toggle();
    assert.match(list.innerHTML,/<time>/);
    controller.restore(snapshot);
    assert.equal(controller.snapshot().clockMode,"duration");
    assert.match(list.innerHTML,/trajectory-duration slow/);
  });
});


test("assistant output links follow the source response when call IDs are reused", () => {
  withController((controller, elements) => {
    const response = (id, seq, ordinal) => fact(id, seq, ordinal, 0,
      { part_kind: "tool-call", tool_name: "decision", tool_call_id: "reused-call", args: { score: ordinal } });
    const output = (id, seq, sourceId, ordinal) => ({
      event_id: id, sequence: seq, turn_id: "turn-1", run_id: "run-1", event_type: "output/attempt", status: "completed",
      payload: { name: "decision", tool_call_id: "reused-call", attempt: ordinal, accepted: true, output: { score: ordinal },
        source: { event_ids: [sourceId], message_ordinal: ordinal, part_index: 0 } },
    });
    controller.setHistory({ items: [response("response-1", 1, 1), output("output-1", 2, "response-1", 1),
      response("response-2", 3, 2), output("output-2", 4, "response-2", 2)] });
    controller.restore({ selectedId: "response-1", detailTab: "summary" });
    const detail = () => elements.get("inspectorContent").innerHTML;
    assert.match(detail(), /data-trajectory-event-target="output-1"/);
    assert.doesNotMatch(detail(), /data-trajectory-event-target="output-2"/);
    controller.restore({ selectedId: "output-1", detailTab: "summary" });
    assert.match(detail(), /data-trajectory-detail-tab-target="output"/);
    assert.match(detail(), /score/);
    assert.match(detail(), />Output</);
    assert.match(detail(), />Raw</);
    assert.doesNotMatch(detail(), />Schema<|>Event</);
    assert.doesNotMatch(detail(), /查看输出|输出信息|原始记录/);
  });
});

test("output details have only Summary, Output and Raw and show the attempt payload as Raw", () => {
  withController((controller, elements) => {
    const payload = { name: "decision", attempt: 1, accepted: true, output: false, source: { event_ids: [] } };
    controller.setHistory({ items: [{ event_id: "output", sequence: 1, event_type: "output/attempt", payload }] });
    controller.restore({ selectedId: "output", detailTab: "raw" });
    const markup = elements.get("inspectorContent").innerHTML;
    assert.deepEqual([...markup.matchAll(/data-trajectory-detail-tab="([^"]+)"/g)].map(match => match[1]), ["summary", "output", "raw"]);
    assert.match(markup, /&quot;accepted&quot;: true/);
    assert.match(markup, /&quot;output&quot;: false/);
    assert.match(markup, /&quot;attempt&quot;: 1/);
    assert.doesNotMatch(markup, /json_schema|historical snapshot|current Agent configuration/);
  });
});
