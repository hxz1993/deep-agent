import assert from "node:assert/strict";
import test from "node:test";
import { assistantActionsFromSse, assistantActionsFromHistoryItems, applyAssistantPresentationActions } from "../../../console/assistant-presentation.js";
import { normalizeConversationHistory, projectConversationItems } from "../../../console/conversation-items.js";
import { buildTrajectory } from "../../../console/trajectory.js";
import { renderStructuredOutput } from "../../../console/structured-output.js";

test("unknown SSE and History extension kinds produce no reply, tool, or UI action", () => {
  const future = { name: "future_result", output: { text: "DO_NOT_RENDER_AS_REPLY" }, accepted: true,
    status: "completed", tool: "confirm", args: { execute: true }, future_extension: { code: "alert(1)" } };
  for (const kind of ["output_v2", "future_event", "future_approval"]) {
    const calls = [];
    const actions = assistantActionsFromSse(kind, future);
    applyAssistantPresentationActions(new Proxy({}, { get: (_target, key) => (...args) => calls.push([key, args]) }), actions);
    assert.deepEqual(actions, []);
    assert.deepEqual(calls, []);
  }
  const history = normalizeConversationHistory({ schema_version: 1, future_extension: future,
    items: [{ ...future, item_id: "future", kind: "future_output", order: { sequence: 1 } }] });
  assert.deepEqual(projectConversationItems(history.items), []);
  assert.equal(assistantActionsFromHistoryItems(history.items).some(action => ["text", "structured_output", "tool_call", "deferred"].includes(action.type)), false);
});

test("known accepted output ignores envelope extensions while retaining native JSON data safely", () => {
  const output = { text: "<img src=x onerror=alert(1)>", future_business_field: { no: false, count: 0, nullable: null } };
  const extension = { execute: "alert(1)", tool: "confirm" };
  const frame = { output_id: "same-output-id", accepted: true, status: "completed", name: "lease_result", output,
    future_extension: extension };
  const actions = assistantActionsFromSse("output", frame);
  assert.deepEqual(actions, [{ type: "structured_output", name: "lease_result", output, outputId: "same-output-id" }]);
  const item = { kind: "structured_output", item_id: "same-output-id", status: "completed", name: "lease_result", output,
    future_extension: extension, order: { sequence: 1 } };
  assert.deepEqual(projectConversationItems([item])[0].actions[0], actions[0]);
  const html = renderStructuredOutput({ name: "lease_result", output, mode: "grouped" });
  assert.doesNotMatch(html, /<img\b|onerror="/);
  assert.match(html, /&lt;img/);
  assert.match(html, /future_business_field/);
  assert.deepEqual(output.future_business_field, { no: false, count: 0, nullable: null });
  assert.equal(JSON.stringify(actions).includes("future_extension"), false);
});

test("unknown raw event envelopes do not create trajectory rows or executable tool cards", () => {
  const rows = buildTrajectory({ items: [{ event_id: "future", event_type: "future/output", sequence: 1,
    status: "completed", payload: { output: { text: "DO_NOT_RENDER_AS_REPLY" }, tool_name: "confirm", accepted: true,
      future_extension: { script: "alert(1)" } } }] });
  assert.deepEqual(rows, []);
});
