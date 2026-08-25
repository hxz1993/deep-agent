import assert from "node:assert/strict";
import test from "node:test";

import {
  compactionPresentation,
  projectSessionEvents,
} from "../../../frontend/console/session-events.js";

const event = (sequence, eventType, overrides = {}) => ({
  event_id: `event-${sequence}`,
  sequence,
  schema_version: 1,
  event_type: eventType,
  status: null,
  turn_id: "turn-1",
  run_id: "run-1",
  message_id: null,
  part_index: null,
  call_id: null,
  tool_name: null,
  payload: {},
  created_at: `2026-08-26T00:00:0${Math.min(sequence, 9)}Z`,
  updated_at: `2026-08-26T00:00:0${Math.min(sequence, 9)}Z`,
  ...overrides,
});
test("Event API 确定性投影聊天、Plan 和真实位置的压缩事件", () => {
  const plan = {
    items: [{ id: "task-1", content: "读取数据", status: "in_progress" }],
    total: 1,
    completed: 0,
  };
  const response = {
    session_id: "session-1",
    items: [
      event(1, "run", { status: "completed" }),
      event(2, "user/message", {
        message_id: "message-user",
        part_index: 0,
        payload: {
          part: {
            content: [
              { kind: "text-content", content: "分析预算" },
              { kind: "attachment-ref", id: "file-1", name: "budget.xlsx", mime: "application/vnd.ms-excel" },
              { kind: "context-ref", key: "range", type: "deeptable.range", label: "预算!B2:C4" },
            ],
          },
        },
      }),
      event(3, "assistant/reasoning", {
        message_id: "message-response",
        part_index: 0,
        payload: { part: { content: "先读取数据" }, message_meta: { model_name: "model-1" } },
      }),
      event(4, "tool/call", {
        message_id: "message-response",
        part_index: 1,
        call_id: "call-plan",
        tool_name: "write_plan",
        payload: { part: { tool_name: "write_plan", tool_call_id: "call-plan", args: { items: plan.items } } },
      }),
      event(5, "compaction", {
        status: "completed",
        payload: {
          source: { through_sequence: 3 },
          budget: { estimated_before: 10000, estimated_after: 5000 },
          summary_call: { model_id: "summary-model" },
        },
      }),
      event(6, "tool/result", {
        message_id: "message-tool-result",
        part_index: 0,
        call_id: "call-plan",
        tool_name: "write_plan",
        payload: { part: { tool_name: "write_plan", tool_call_id: "call-plan", content: { ok: true }, metadata: { deepagent_plan: plan } } },
      }),
      event(7, "assistant/text", {
        message_id: "message-final",
        part_index: 0,
        payload: { part: { content: "已完成分析" }, message_meta: { finish_reason: "stop" } },
      }),
    ],
  };

  const projection = projectSessionEvents(response);
  assert.equal(projection.messageCount, 4);
  assert.equal(projection.messages[0].content, "分析预算");
  assert.equal(projection.messages[0].attachments[0].name, "budget.xlsx");
  assert.equal(projection.messages[0].context_refs[0].label, "预算!B2:C4");
  assert.deepEqual(
    projection.visualItems.map((item) => item.type),
    ["user", "assistant", "compaction", "assistant"],
  );
  const planResult = projection.messages
    .flatMap((message) => message.parts || [])
    .find((part) => part.type === "tool_result" && part.tool === "write_plan");
  assert.deepEqual(planResult.plan, plan);

  assert.deepEqual(compactionPresentation(response.items[4]), {
    event_id: "event-5",
    status: "completed",
    before_tokens: 10000,
    after_tokens: 5000,
    through_sequence: 3,
    updated_at: "2026-08-26T00:00:05Z",
    summary_model_id: "summary-model",
  });
});
