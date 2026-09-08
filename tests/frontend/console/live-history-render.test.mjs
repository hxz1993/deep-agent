import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { projectConversationItems, latestPendingDeferredTool } from "../../../console/conversation-items.js";
import { buildTrajectory } from "../../../console/trajectory.js";

const source = readFileSync(new URL("../../../console/chat.js", import.meta.url), "utf8");
const history = (text = "第一轮") => [
  { item_id: "user", kind: "user_message", text: "查询", created_at: "2026-09-08T00:00:00Z", order: { sequence: 1 } },
  { item_id: "progress:0", kind: "assistant_message", text, phase: "process", status: "pending", created_at: "2026-09-08T00:00:01Z", order: { sequence: 2 } },
];

function renderHarness() {
  class Element {
    constructor(kind) { this.kind = kind; this.children = []; this.parentNode = null; this.scrollTop = 0; }
    appendChild(child) { this.insertBefore(child, null); }
    insertBefore(child, before) {
      child.remove();
      const index = before ? this.children.indexOf(before) : this.children.length;
      this.children.splice(index, 0, child);
      child.parentNode = this;
    }
    remove() {
      if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
      this.parentNode = null;
    }
    get lastElementChild() { return this.children.at(-1); }
  }
  const messages = new Element("messages");
  const state = { historyRendered: [], messageAutoFollow: true };
  const calls = [];
  const context = {
    state, el: { messages }, projectConversationItems, latestPendingDeferredTool,
    localDayKey: (date) => date.slice(0, 10),
    addDateSeparator: () => messages.appendChild(new Element("date")),
    addMessage: () => { const row = new Element("user"); messages.appendChild(row); return row; },
    createAssistantMessage: (_ts, _order, _before, options) => {
      const row = new Element("assistant");
      messages.appendChild(row);
      return { row, options, resumeProcess: () => { row.running = true; }, dispose: () => { row.disposed = true; } };
    },
    applyAssistantPresentationActions: (presenter, actions) => { presenter.row.actions = actions; },
    restoreHistoryDisclosure: (oldRow, row) => { row.expanded = oldRow.expanded; },
    mergeUiToolCall: (value) => value,
    updateHitlInputState() {}, updateComposerContextControls() {}, clearComposerPlan() {},
    scrollToBottom: (options = {}) => { if (state.messageAutoFollow || options.force) calls.push("scroll"); },
  };
  vm.createContext(context);
  const fn = source.slice(source.indexOf("function renderHistory("), source.indexOf("\nfunction restoreHistoryDisclosure("));
  vm.runInContext(`${fn}\nthis.render = renderHistory;`, context);
  return { ...context, messages, calls };
}

test("unchanged history preserves exact rows, disclosure and pending form DOM", () => {
  const h = renderHarness();
  h.render(history(), { running: true });
  const rows = [...h.messages.children];
  rows.at(-1).expanded = true;
  rows.at(-1).draft = "not submitted";
  h.render(history(), { updating: true, running: true });
  assert.deepEqual(h.messages.children, rows);
  assert.equal(h.messages.children.at(-1).draft, "not submitted");
  assert.equal(h.state.assistantPresenter.options.allowDeferred, false);
});

test("a same-sequence checkpoint update only replaces the changed assistant, preserving scroll and draft outside it", () => {
  const h = renderHarness();
  h.render(history(), { running: true });
  const user = h.messages.children[1];
  const assistant = h.messages.children[2];
  assistant.expanded = true;
  h.state.messageAutoFollow = false;
  h.messages.scrollTop = 37;
  const scrolls = h.calls.length;
  h.render(history("第一轮，工具返回后第二轮"), { updating: true, running: true });
  assert.equal(h.messages.children[1], user);
  assert.notEqual(h.messages.children[2], assistant);
  assert.equal(assistant.disposed, true, "old active process timers are disposed");
  assert.equal(h.messages.children[2].expanded, true);
  assert.equal(h.messages.children[2].running, true);
  assert.equal(h.messages.scrollTop, 37);
  assert.equal(h.calls.length, scrolls);
});

test("terminal history replaces temporary IDs and permits only genuine pending Deferred interaction", () => {
  const h = renderHarness();
  h.render(history(), { running: true });
  const final = [history()[0], {
    item_id: "tool:confirmed", kind: "tool", mode: "deferred", status: "awaiting_input",
    tool_name: "builtin__confirm", input: { message: "继续吗" }, call_id: "confirmed",
    created_at: "2026-09-08T00:00:02Z", order: { sequence: 3 },
  }];
  h.render(final, { updating: true, running: false });
  assert.equal(h.state.awaitingHitl, true);
  assert.equal(h.state.assistantPresenter.options.allowDeferred, true);
  assert.equal(h.state.assistantPresenter.options.autoAttachDeferred, false);
  assert.equal(h.messages.children.filter((row) => row.kind === "assistant").length, 1);
  assert.equal(h.state.assistantPresenter.row.actions.some((action) => action.type === "deferred"), true);
});

test("running history never activates a previous pending Deferred while another page resumes the run", () => {
  const h = renderHarness();
  const pending = [...history(), {
    item_id: "tool:confirmed", kind: "tool", mode: "deferred", status: "awaiting_input",
    tool_name: "builtin__confirm", input: { message: "继续吗" }, call_id: "confirmed",
    created_at: "2026-09-08T00:00:02Z", order: { sequence: 3 },
  }];
  h.render(pending, { running: true });
  assert.equal(h.state.awaitingHitl, false);
  assert.equal(h.state.assistantPresenter.options.allowDeferred, false);
});

test("progress trajectory presents a compact durable receipt without dumping snapshot prompts or full results", () => {
  const progress = {
    event_id: "progress", event_type: "run/progress", sequence: 2, run_id: "run", status: "running",
    payload: { revision: 4, messages: [
      { kind: "response", parts: [{ part_kind: "text", content: "private preview" }, { part_kind: "tool-call", tool_name: "lookup", args: { key: "private input" } }] },
      { kind: "request", parts: [{ part_kind: "tool-return", content: "large private result" }] },
    ] },
  };
  const [row] = buildTrajectory({ items: [progress] });
  assert.equal(row.id, "progress");
  assert.equal(row.status, "running");
  assert.equal(row.label, "运行中快照");
  assert.equal(row.payload.message_count, 2);
  assert.equal(row.payload.assistant_rounds, 1);
  assert.equal(row.payload.tool_calls, 1);
  assert.equal(row.payload.tool_results, 1);
  assert.equal(JSON.stringify(row).includes("private"), false);
  const [updated] = buildTrajectory({ items: [{ ...progress, status: "completed", payload: { revision: 5, message_count: 3 } }] });
  assert.equal(updated.id, row.id);
  assert.equal(updated.status, "completed");
  assert.equal(updated.summary, "已保存 3 条消息");
});
