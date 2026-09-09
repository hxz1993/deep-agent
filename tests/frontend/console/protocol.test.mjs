import assert from "node:assert/strict";
import test from "node:test";

import {
  contextCompactionPresentation,
  createSseParser,
  parseSseBlock,
} from "../../../console/protocol.js";

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

test("上下文压缩事件提供短暂状态文案", () => {
  assert.deepEqual(
    contextCompactionPresentation({ phase: "preparing" }),
    { phase: "preparing", message: "正在准备上下文压缩…", active: true },
  );
  assert.equal(
    contextCompactionPresentation({
      phase: "committing",
      message: "正在写入压缩事件…",
    }).message,
    "正在写入压缩事件…",
  );
  assert.equal(
    contextCompactionPresentation({ phase: "ignored" }).active,
    false,
  );
});
