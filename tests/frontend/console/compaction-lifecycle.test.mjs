import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../../console/chat.js", import.meta.url), "utf8");

function harness() {
  const rows = [];
  const timers = new Map();
  let timerId = 0;
  const noop = () => {};
  const state = { completedAutomaticCompactionEvents: new Set() };
  const context = {
    state, trajectory: null,
    window: { setTimeout: (fn) => { timers.set(++timerId, fn); return timerId; }, clearTimeout: (id) => timers.delete(id) },
    compactTokenLabel: String,
    contextCompactionPresentation: (data) => ({ message: data.message || "正在压缩上下文…" }),
    createContextCompactionRow: () => {
      const row = { removed: false, remove() { this.removed = true; } };
      rows.push(row);
      return row;
    },
    setContextCompactionRow: (row, message, options = {}) => Object.assign(row, { message, ...options }),
    cacheTrajectorySummary: noop, scrollToBottom: noop, clearComposerPlan: noop,
    applyAssistantPresentationActions: noop, assistantActionsFromSse: noop,
    addErrorTo: noop, updateHitlInputState: noop,
  };
  vm.createContext(context);
  const lifecycle = source.slice(source.indexOf("function clearAutomaticCompactionState("), source.indexOf("\nfunction showConsoleToast("));
  const handler = source.slice(source.indexOf("function handleEvent("), source.indexOf("\nfunction renderUsage("));
  vm.runInContext(`${lifecycle}\n${handler}`, context);
  return {
    rows, timers, state,
    send: (event, data) => context.handleEvent({ event, data }, { row: {} }),
    finalize: (compaction, status) => context.finalizeAutomaticContextCompaction({ compaction }, status),
  };
}

const progress = (id) => ({ persisted: false, event_id: id, phase: "summarizing", message: "正在生成摘要…" });
const completed = (id) => ({ persisted: true, event: { event_type: "compaction", event_id: id, status: "completed",
  payload: { budget: { estimated_tokens_before: 18000, estimated_tokens_after: 4000 } } } });

for (const terminal of ["error", "cancelled", "final"]) {
  test(`persisted completion closes the matching row before subsequent ${terminal}`, () => {
    const h = harness();
    h.send("context_compaction_progress", progress("compact-1"));
    h.send("session_event_updated", completed("compact-1"));
    assert.equal(h.rows.length, 1);
    assert.equal(h.rows[0].message, "上下文已压缩：18000 → 4000 Token");
    assert.equal(h.rows[0].active, false);
    assert.equal(h.timers.size, 0, "The delayed spinner must be cancelled");
    h.send(terminal, terminal === "final" ? { status: "pending" } : { message: "business failure" });
    assert.equal(h.rows.length, 1);
    assert.equal(h.rows[0].message, "上下文已压缩：18000 → 4000 Token");
    assert.notEqual(h.rows[0].failed, true);
    assert.equal(h.rows[0].removed, false);
  });
}

test("an unrelated completed event cannot close the active compaction", () => {
  const h = harness();
  h.send("context_compaction_progress", progress("current"));
  h.send("session_event_updated", completed("another"));
  assert.equal(h.rows.length, 0);
  assert.equal(h.state.pendingAutomaticCompaction.event_id, "current");
  h.send("session_event_updated", completed("current"));
  assert.equal(h.rows.length, 1);
});

test("duplicate completion and late progress stay closed while a new compaction can start", () => {
  const h = harness();
  h.send("context_compaction_progress", progress("first"));
  h.send("session_event_updated", completed("first"));
  h.send("session_event_updated", completed("first"));
  h.send("context_compaction_progress", progress("first"));
  assert.equal(h.rows.length, 1);
  assert.equal(h.timers.size, 0);
  h.send("context_compaction_progress", progress("second"));
  h.send("session_event_updated", completed("first"));
  assert.equal(h.state.pendingAutomaticCompaction.event_id, "second");
  h.send("session_event_updated", completed("second"));
  assert.equal(h.rows.length, 2);
  assert.ok(h.rows.every((row) => row.active === false && !row.failed));
});

test("pending final still reports a saved compaction when the completed update was not observed", () => {
  const h = harness();
  h.send("context_compaction_progress", progress("pending"));
  h.finalize({ triggered: true, summary_applied: true, compaction_event_saved: true,
    estimated_tokens_before: 18000, estimated_tokens_after: 4000 }, "pending");
  assert.equal(h.rows[0].message, "上下文已压缩：18000 → 4000 Token");
  assert.equal(h.timers.size, 0);
});

test("an unfinished later compaction can fail without rewriting the earlier successful row", () => {
  const h = harness();
  h.send("context_compaction_progress", progress("first"));
  h.send("session_event_updated", completed("first"));
  h.send("context_compaction_progress", progress("second"));
  h.send("error", { message: "summary unavailable" });
  assert.equal(h.rows.length, 2);
  assert.equal(h.rows[0].message, "上下文已压缩：18000 → 4000 Token");
  assert.equal(h.rows[1].message, "本次上下文压缩未完成，后续请求失败");
  assert.equal(h.rows[1].failed, true);
});
