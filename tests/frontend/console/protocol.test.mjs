import assert from "node:assert/strict";
import test from "node:test";

import {
  contextCompactionPresentation,
  createSseParser,
  parseSseBlock,
  reduceToolEvents,
} from "../../../frontend/console/protocol.js";

test("SSE 事件可以跨网络 chunk 解析", () => {
  const events = [];
  const parser = createSseParser((event) => events.push(event));
  parser.push('event: text\r\ndata: {"delta":"你');
  parser.push('好"}\r\n\r\nevent: final\ndata: {"status":"complete"}\n\n');
  parser.flush();
  assert.deepEqual(events, [
    { event: "text", data: { delta: "你好" } },
    { event: "final", data: { status: "complete" } },
  ]);
});

test("SSE 多行 data 和非 JSON 响应保留原文", () => {
  assert.deepEqual(parseSseBlock("event: error\ndata: bad\ndata: response"), {
    event: "error",
    data: { raw: "bad\nresponse" },
  });
});

test("相同 tool_call_id 合并，不同 id 保持独立", () => {
  assert.deepEqual(
    reduceToolEvents([
      { event: "tool_call", data: { tool_call_id: "a", tool: "clarify", args: {} } },
      { event: "tool_call", data: { tool_call_id: "a", tool: "clarify", args: { title: "确认" } } },
      { event: "tool_call", data: { tool_call_id: "b", tool: "search", args: { q: "A" } } },
      { event: "tool_result", data: { tool_call_id: "a", result: { confirmed: true } } },
    ]),
    [
      { tool_call_id: "a", status: "complete", tool: "clarify", args: { title: "确认" }, result: { confirmed: true } },
      { tool_call_id: "b", status: "pending", tool: "search", args: { q: "A" } },
    ],
  );
});

test("工具调用撤销后不再保留实时工具卡状态", () => {
  assert.deepEqual(
    reduceToolEvents([
      { event: "tool_call", data: { tool_call_id: "retry-1", tool: "workbook_get", args: {} } },
      { event: "tool_call_removed", data: { tool_call_id: "retry-1", reason: "model_retry" } },
      { event: "tool_call", data: { tool_call_id: "valid-2", tool: "workbook_get", args: { path: "/a" } } },
      { event: "tool_result", data: { tool_call_id: "valid-2", result: { ok: true } } },
    ]),
    [
      { tool_call_id: "valid-2", status: "complete", tool: "workbook_get", args: { path: "/a" }, result: { ok: true } },
    ],
  );
});

test("上下文压缩事件提供短暂状态文案", () => {
  assert.deepEqual(
    contextCompactionPresentation({ phase: "started" }),
    { phase: "started", message: "正在压缩上下文…", active: true },
  );
  assert.equal(
    contextCompactionPresentation({
      phase: "fallback",
      message: "已缩减较早上下文，继续生成…",
    }).message,
    "已缩减较早上下文，继续生成…",
  );
  assert.equal(
    contextCompactionPresentation({ phase: "ignored" }).active,
    false,
  );
});
