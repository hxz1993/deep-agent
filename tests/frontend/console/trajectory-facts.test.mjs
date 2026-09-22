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
      addEventListener() {}, setAttribute() {},
    });
    return elements.get(id);
  };
  globalThis.document = { getElementById: element, querySelector: element, querySelectorAll: () => [] };
  globalThis.window = { requestAnimationFrame: (fn) => fn() };
  try { check(createTrajectoryController({ state: {} }), elements); }
  finally { globalThis.document = oldDocument; globalThis.window = oldWindow; }
}

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
