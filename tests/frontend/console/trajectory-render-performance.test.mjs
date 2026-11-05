import assert from "node:assert/strict";
import test from "node:test";
import { createTrajectoryController } from "../../../console/trajectory.js";
import { trajectoryDomHarness } from "./trajectory-dom-harness.mjs";

function withController(check) {
  const oldDocument = globalThis.document, oldWindow = globalThis.window;
  const h = trajectoryDomHarness();
  globalThis.document = h.document; globalThis.window = h.window;
  const c = createTrajectoryController();
  try { check(c, h); } finally { c.destroy(); globalThis.document = oldDocument; globalThis.window = oldWindow; }
}
const send = (controller, event, data) => controller.ingest({ event, data });
const session = c => send(c, "session", { run_id: "run" });
const text = (c, delta) => send(c, "text", { delta });
const call = (c, id) => send(c, "tool_call", { tool_call_id: id, tool: "lookup", args: { key: id } });
const result = (c, id) => send(c, "tool_result", { tool_call_id: id, tool: "lookup", result: { value: id } });

test("hidden trajectory ingests all deltas without queuing DOM or icon work; opening shows current ordered facts", () => {
  withController((c, h) => {
    let icons = 0;
    h.window.lucide = { createIcons() { icons += 1; } };
    c.addUser({ content: "query" }); session(c);
    for (let i = 0; i < 1000; i++) text(c, `${i},`);
    call(c, "one"); result(c, "one"); text(c, "final");
    send(c, "final", {});
    assert.equal(h.frames.size, 0);
    assert.equal(h.element("trajectoryList").writes, 0);
    assert.equal(h.element("inspectorContent").writes, 0);
    assert.equal(icons, 0);
    const events = c.events();
    assert.ok(events.find(e => e.kind === "assistant").payload.content.endsWith("999,"));
    assert.equal(events.at(-1).payload.content, "final");
    c.selectView("trajectory"); h.flush();
    assert.deepEqual(h.element("trajectoryList").children.map(row => row.dataset.trajectoryId), c.events().map(e => e.id));
    assert.ok(h.element("inspectorContent").writes > 0);
  });
});

test("visible trajectory preserves existing row DOM, focus and scroll across later deltas and retains pinned detail", () => {
  withController((c, h) => {
    c.addUser({ content: "query" }); session(c); text(c, "first");
    call(c, "one"); result(c, "one");
    c.selectView("trajectory"); h.flush();
    const list = h.element("trajectoryList"), detail = h.element("inspectorContent");
    const firstRow = list.children[0]; firstRow.focus(); list.scrollTop = 91;
    list.listeners.get("click")({ target: { closest: () => firstRow } }); h.flush();
    const detailWrites = detail.writes, rowWrites = firstRow.writes;
    for (let i = 0; i < 10; i++) { text(c, "more"); h.flush(); }
    assert.equal(list.children[0], firstRow);
    assert.equal(h.document.activeElement, firstRow);
    assert.equal(firstRow.writes, rowWrites);
    assert.equal(detail.writes, detailWrites, "unrelated live content must not rebuild pinned input details");
    assert.equal(list.scrollTop, 91);
    c.selectView("conversation");
    text(c, "hidden"); h.flush();
    assert.equal(detail.writes, detailWrites);
    c.selectView("trajectory"); h.flush();
    assert.ok(detail.writes > detailWrites, "shared inspector is restored on returning to trajectory");
  });
});

test("visible selected live detail updates for new content, terminal status and same-id persisted replacement", () => {
  withController((c, h) => {
    session(c); text(c, "first"); c.selectView("trajectory"); h.flush();
    const detail = h.element("inspectorContent");
    const initial = detail.writes;
    text(c, " second"); h.flush();
    assert.equal(detail.writes, initial + 1);
    assert.match(detail.innerHTML, /first second/);
    send(c, "cancelled", {}); h.flush();
    assert.match(detail.innerHTML, /Interrupted|interrupted/);
    c.setHistory({ session_id: "s", items: [{
      event_id: "fact", sequence: 1, event_type: "assistant/message", run_id: "run", turn_id: "turn",
      created_at: "2026-11-05T00:00:00Z", status: "completed",
      payload: { message: { kind: "response", parts: [{ part_kind: "text", content: "saved" }] } },
    }] }, { preserveView: true }); h.flush();
    assert.match(detail.innerHTML, /saved/);
    assert.deepEqual(h.element("trajectoryList").children.map(row => row.dataset.trajectoryId), ["fact"]);
    const writes = detail.writes;
    send(c, "session_event_updated", { event: {
      event_id: "fact", sequence: 1, event_type: "assistant/message", run_id: "run", turn_id: "turn",
      created_at: "2026-11-05T00:00:00Z", status: "completed",
      payload: { message: { kind: "response", parts: [{ part_kind: "text", content: "saved updated" }] } },
    } }); h.flush();
    assert.ok(detail.writes > writes); assert.match(detail.innerHTML, /saved updated/);
  });
});


test("reset promptly releases hidden trajectory rows, details and queued rendering", () => {
  withController((c, h) => {
    c.addUser({ content: "old private question" }); session(c); text(c, "old private answer");
    c.selectView("trajectory"); h.flush();
    const list = h.element("trajectoryList"), detail = h.element("inspectorContent");
    const oldRows = [...list.children];
    assert.match(detail.innerHTML, /old private/);
    c.selectView("conversation");
    c.reset();
    assert.equal(h.frames.size, 0);
    assert.equal(c.events().length, 0);
    assert.equal(list.children.length, 0);
    assert.equal(detail.innerHTML, "");
    assert.ok(oldRows.every(row => row.parentNode === null));
    h.flush();
    assert.equal(list.children.length, 0);
    c.addUser({ content: "new question" });
    c.selectView("trajectory"); h.flush();
    assert.equal(list.children.length, 1);
    assert.ok(!oldRows.includes(list.children[0]));
    assert.doesNotMatch(detail.innerHTML, /old private/);
  });
});

test("real session switches release old DOM while ordinary same-session hidden updates retain rows", () => {
  withController((c, h) => {
    const history = (sessionId, content) => ({ session_id: sessionId, items: [{
      event_id: "shared-id", sequence: 1, event_type: "assistant/message", run_id: sessionId, turn_id: "turn",
      created_at: "2026-11-05T00:00:00Z", status: "completed",
      payload: { message: { kind: "response", parts: [{ part_kind: "text", content }] } },
    }] });
    c.setHistory(history("old", "private old answer"));
    c.selectView("trajectory"); h.flush();
    const list = h.element("trajectoryList"), detail = h.element("inspectorContent");
    const oldRow = list.children[0];
    c.selectView("conversation");
    c.setHistory(history("old", "private old update"), { preserveView: true });
    assert.equal(list.children[0], oldRow, "same-session hidden updates keep reusable rows");
    c.setHistory(history("new", "new answer"), { preserveView: true });
    assert.equal(list.children.length, 0);
    assert.equal(oldRow.parentNode, null);
    assert.equal(detail.innerHTML, "");
    assert.equal(h.frames.size, 0);
    c.selectView("trajectory"); h.flush();
    assert.equal(list.children.length, 1);
    assert.notEqual(list.children[0], oldRow, "a new session never reuses old rows even with the same event id");
    assert.match(detail.innerHTML, /new answer/);
    assert.doesNotMatch(detail.innerHTML, /private old/);
  });
});

test("hidden delta ingestion does not rescan input sequences for every text or thinking chunk", () => {
  withController((c) => {
    c.addUser({ content: "query" }); session(c);
    const input = c.events().find(event => event.kind === "input");
    const sequence = input.input_sequence;
    let reads = 0;
    Object.defineProperty(input, "input_sequence", { configurable: true, get() { reads += 1; return sequence; } });
    for (let i = 0; i < 1000; i++) {
      send(c, "thinking", { delta: "thinking " });
      text(c, "answer ");
    }
    assert.ok(reads <= 3, `input sequence was scanned ${reads} times for append-only deltas`);
    const assistant = c.events().find(event => event.kind === "assistant");
    assert.equal(assistant.payload.content, "answer ".repeat(1000));
    assert.equal(assistant.payload.thinking, "thinking ".repeat(1000));
  });
});

test("live summaries normalize only appended text even when the answer has a long whitespace prefix", () => {
  withController((c) => {
    c.addUser({ content: "query" }); session(c);
    const chunks = [...Array.from({ length: 100 }, () => " \t\n".repeat(300)), "alpha", "\n\t", "beta  "];
    const full = chunks.join("");
    let scanned = 0;
    const replace = String.prototype.replace;
    String.prototype.replace = function(pattern, replacement) {
      if (pattern?.source === "\\s+" && pattern.global) scanned += String(this).length;
      return replace.call(this, pattern, replacement);
    };
    try {
      for (const chunk of chunks) text(c, chunk);
      send(c, "final", {});
    } finally {
      String.prototype.replace = replace;
    }
    const assistant = c.events().find(event => event.kind === "assistant");
    assert.equal(assistant.payload.content, full);
    assert.equal(assistant.summary, "alpha beta");
    assert.ok(scanned <= full.length * 2, `normalized ${scanned} characters for ${full.length} appended characters`);
  });
});

test("incremental summaries keep exact whitespace, UTF-16 and ellipsis behavior at every chunk boundary", () => {
  const cases = [
    [" \t", "\nA", "\tB", "  "],
    ["a".repeat(179), " ", "Z"],
    ["a".repeat(180), "\t ", "\u00a0"],
    ["\ufeff", "😀".repeat(100), "\nlast"],
    ["", "\r\n\t", " \u00a0"],
  ];
  for (const chunks of cases) withController((c) => {
    session(c);
    let content = "";
    for (const chunk of chunks) {
      text(c, chunk); content += chunk;
      const normalized = content.replace(/\s+/g, " ").trim();
      const expected = normalized.length > 180 ? `${normalized.slice(0, 180)}…` : normalized;
      const assistant = c.events().find(event => event.kind === "assistant");
      assert.equal(assistant.summary, content ? expected : "模型响应");
      assert.equal(assistant.payload.content, content || null);
    }
    send(c, "cancelled", {});
    const normalized = content.replace(/\s+/g, " ").trim();
    assert.equal(c.events().find(event => event.kind === "assistant").summary,
      content ? normalized.length > 180 ? `${normalized.slice(0, 180)}…` : normalized : "模型响应");
  });
});

test("input sequence counts refresh after history replacement, persisted user replacement and reset", () => {
  withController((c) => {
    const user = (id, sequence) => ({ event_id: id, sequence, event_type: "user/message", run_id: `r${sequence}`,
      turn_id: `t${sequence}`, created_at: "2026-11-05T00:00:00Z", status: "completed",
      payload: { message: { kind: "request", parts: [{ part_kind: "user-prompt", content: id }] } } });
    c.setHistory({ session_id: "s", items: [user("one", 1), user("two", 2)] });
    c.addUser({ content: "third" });
    assert.equal(c.events().filter(event => event.kind === "input").at(-1).input_sequence, 3);
    c.setHistory({ session_id: "s", items: [user("one", 1)] });
    c.addUser({ content: "second live" });
    assert.equal(c.events().filter(event => event.kind === "input").at(-1).input_sequence, 2);
    send(c, "session_event_appended", { event: user("second stored", 2) });
    send(c, "session_event_updated", { event: user("second stored", 2) });
    c.addUser({ content: "third again" });
    assert.deepEqual(c.events().filter(event => event.kind === "input").map(event => event.input_sequence), [1, 2, 3]);
    c.reset(); c.addUser({ content: "new" });
    assert.equal(c.events()[0].input_sequence, 1);
  });
});
