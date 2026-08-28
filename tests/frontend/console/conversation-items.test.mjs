import assert from "node:assert/strict";
import test from "node:test";

import {
  latestPendingDeferredTool,
  normalizeConversationHistory,
  projectConversationItems,
} from "../../../frontend/console/conversation-items.js";

const order = (sequence, partOffset = 0) => ({ sequence, part_offset: partOffset });
const base = (itemId, kind, sequence, overrides = {}) => ({
  item_id: itemId,
  kind,
  status: "completed",
  turn_id: "turn-1",
  run_id: "run-1",
  order: order(sequence),
  source_event_ids: [`event-${sequence}`],
  created_at: `2026-08-27T00:00:0${sequence}Z`,
  updated_at: `2026-08-27T00:00:0${sequence}Z`,
  ...overrides,
});

test("History 按 ConversationItem 顺序和稳定 ID 归一化", () => {
  const history = normalizeConversationHistory({
    schema_version: 1,
    session_id: "session-1",
    event_head_sequence: 3,
    items: [
      base("message:a", "assistant_message", 3, { text: "完成", phase: "final" }),
      base("message:u", "user_message", 1, { text: "分析预算" }),
      base("tool:call-1", "tool", 2, {
        call_id: "call-1",
        tool_name: "lookup",
        status: "completed",
        output: { ok: true },
      }),
    ],
  });
  assert.deepEqual(history.items.map((item) => item.item_id), ["message:u", "tool:call-1", "message:a"]);
  assert.deepEqual(projectConversationItems(history.items).map((node) => node.type), ["user", "assistant"]);
});

test("Deferred 由 ConversationItem 恢复而历史 Plan 不投影到输入区", () => {
  const plan = { items: [{ id: "1", content: "读取", status: "completed" }] };
  const items = [
    base("message:u", "user_message", 1, { text: "继续" }),
    base("plan:p", "plan", 2, { call_id: "p", plan, status: "completed" }),
    base("tool:confirm", "tool", 3, {
      call_id: "confirm",
      tool_name: "builtin__confirm",
      mode: "deferred",
      status: "awaiting_input",
    }),
  ];
  assert.equal(latestPendingDeferredTool(items)?.item_id, "tool:confirm");
});

test("History 投影保留用户附件和上下文，并排除 Plan、Compaction 与 Title", () => {
  const attachments = [{ id: "file-1", name: "预算.xlsx", mime: "application/vnd.ms-excel" }];
  const contextRefs = [{ type: "range", label: "预算区域" }];
  const nodes = projectConversationItems([
    base("message:u", "user_message", 1, {
      text: "分析预算",
      attachments,
      context_refs: contextRefs,
    }),
    base("plan:p", "plan", 2, { call_id: "p" }),
    base("compaction:c", "compaction", 3),
    base("title:t", "session_title", 4),
    base("message:a", "assistant_message", 5, { text: "完成", phase: "final" }),
  ]);

  assert.equal(nodes.length, 2);
  assert.equal(nodes[0].type, "user");
  assert.equal(nodes[0].itemId, "message:u");
  assert.equal(nodes[0].createdAt, "2026-08-27T00:00:01Z");
  assert.deepEqual(nodes[0].order, order(1));
  assert.equal(nodes[0].text, "分析预算");
  assert.deepEqual(nodes[0].attachments, attachments);
  assert.deepEqual(nodes[0].contextRefs, contextRefs);
  assert.equal(nodes[1].type, "assistant");
  assert.ok(nodes[1].actions.length > 0);
});
