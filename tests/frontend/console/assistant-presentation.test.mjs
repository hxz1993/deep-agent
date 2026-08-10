import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  buildHistoryVisualItems,
  processDisclosureLabel,
  splitAssistantHistoryRecords,
} from "../../../frontend/console/assistant-presentation.js";

const chatSource = await readFile(
  new URL("../../../frontend/console/chat.js", import.meta.url),
  "utf8",
);
const workspaceCss = await readFile(
  new URL("../../../frontend/console/workspace.css", import.meta.url),
  "utf8",
);

test("连续 assistant 历史按 user turn 合并为一个可视回答", () => {
  const items = buildHistoryVisualItems([
    { role: "user", content: "问题一", branch_point: 0 },
    { role: "assistant", parts: [{ type: "text", content: "中间" }], branch_point: 1 },
    { role: "assistant", parts: [{ type: "text", content: "最终" }], branch_point: 2 },
    { role: "user", content: "问题二", branch_point: 3 },
    { role: "assistant", parts: [{ type: "text", content: "回答二" }], branch_point: 4 },
  ]);

  assert.deepEqual(items.map((item) => item.type), ["user", "assistant", "user", "assistant"]);
  assert.equal(items[1].records.length, 2);
  assert.equal(items[3].records.length, 1);
});

test("Checkpoint 边界会截断连续 assistant 合并", () => {
  const items = buildHistoryVisualItems([
    { role: "user", content: "问题", branch_point: 0 },
    { role: "assistant", parts: [{ type: "text", content: "边界前" }], branch_point: 1 },
    { role: "assistant", parts: [{ type: "text", content: "边界后" }], branch_point: 4 },
  ], { source_last_sequence: 2 });

  assert.deepEqual(items.map((item) => item.type), ["user", "assistant", "checkpoint", "assistant"]);
  assert.equal(items[1].records[0].parts[0].content, "边界前");
  assert.equal(items[3].records[0].parts[0].content, "边界后");
});

test("最终文本常显，之前文本、thinking 和工具进入处理过程", () => {
  const split = splitAssistantHistoryRecords([
    {
      role: "assistant",
      parts: [
        { type: "thinking", content: "先分析" },
        { type: "text", content: "我先查一下" },
        { type: "tool_call", tool: "lookup", tool_call_id: "call-1", args: {} },
        { type: "tool_result", tool: "lookup", tool_call_id: "call-1", result: { ok: true } },
      ],
    },
    {
      role: "assistant",
      parts: [
        { type: "text", content: "过渡说明" },
        { type: "text", content: "这是最终答案" },
      ],
    },
  ]);

  assert.equal(split.finalText, "这是最终答案");
  assert.deepEqual(
    split.processParts.map((part) => part.type),
    ["thinking", "text", "tool_call", "tool_result", "text"],
  );
});

test("尾部仍是 Pending 工具时不伪造最终答案，interrupted 状态保留", () => {
  const split = splitAssistantHistoryRecords([
    {
      role: "assistant",
      status: "interrupted",
      parts: [
        { type: "text", content: "尚未完成" },
        { type: "tool_call", tool: "builtin__confirm", tool_call_id: "pending-1", args: { message: "继续？" } },
      ],
    },
  ]);

  assert.equal(split.finalText, "");
  assert.equal(split.interrupted, true);
  assert.deepEqual(split.processParts.map((part) => part.type), ["text", "tool_call"]);
});

test("过程、工具和 HITL 默认折叠且等待操作标题清晰", () => {
  assert.equal(processDisclosureLabel(), "处理过程");
  assert.equal(processDisclosureLabel({ running: true }), "处理过程 · 处理中");
  assert.equal(processDisclosureLabel({ pending: true }), "处理过程 · 等待操作");
  assert.match(chatSource, /processBlock\.className = "assistant-process hidden"/);
  assert.match(chatSource, /block\.className = `tool-block\$\{isToolSearch \? " tool-search-block" : ""\}`/);
  assert.match(chatSource, /block\.classList\.add\("hitl-pending"\)/);
  assert.doesNotMatch(chatSource, /block\.classList\.add\("open", "hitl-pending"\)/);
  assert.match(chatSource, /textContent = `等待操作 · \$\{defaultTitle\}`/);
});

test("思考过程是独立的二级折叠，默认关闭且正文不受影响", () => {
  assert.match(
    chatSource,
    /<button type="button" class="assistant-thinking-head" aria-expanded="false">/,
  );
  assert.match(
    chatSource,
    /const block = thinkingBlock;[\s\S]*block\.classList\.toggle\("open", open\)/,
  );
  assert.match(
    chatSource,
    /thinkingHead\.setAttribute\("aria-expanded", String\(open\)\)/,
  );
  assert.match(workspaceCss, /\.assistant-thinking-body\s*\{[\s\S]*?display:\s*none;/);
  assert.match(
    workspaceCss,
    /\.assistant-thinking\.open \.assistant-thinking-body\s*\{\s*display:\s*block;/,
  );
  assert.doesNotMatch(chatSource, /thinkingBlock\.classList\.add\("open"\)/);
  assert.match(chatSource, /finalContent\.className = "assistant-final markdown streaming"/);
});

test("实时 deferred 续跑复用同一可视回答且 removed 即时删除工具卡", () => {
  assert.match(
    chatSource,
    /const assistant = state\.assistantPresenter\?\.row\?\.isConnected[\s\S]*\? state\.assistantPresenter[\s\S]*: createAssistantMessage\(\)/,
  );
  assert.match(
    chatSource,
    /removeToolCall\(data\)[\s\S]*block\.remove\(\)[\s\S]*toolBlocks\.delete\(key\)/,
  );
  assert.match(chatSource, /case "final":[\s\S]*assistant\.complete\(data\.output\)/);
  assert.match(chatSource, /case "cancelled":[\s\S]*assistant\.markInterrupted\(\)/);
  assert.match(chatSource, /case "error":[\s\S]*addErrorTo\(assistant/);
});
