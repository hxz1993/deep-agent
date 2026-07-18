import assert from "node:assert/strict";
import test from "node:test";

import { createSseParser, parseSseBlock, reduceToolEvents } from "../../../frontend/console/protocol.js";

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
