import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { createSessionObserver, historyThroughRun } from "../../../console/session-observer.js";
import { boundedStreamRequest } from "../../../console/stop-recovery.js";
import { normalizeConversationHistory } from "../../../console/conversation-items.js";
import {
  applyAssistantPresentationActions, assistantActionsFromSse, assistantActionsFromDeferred,
} from "../../../console/assistant-presentation.js";

const turn = () => new Promise(setImmediate);
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { resolve, promise }; };

function fixture(overrides = {}) {
  const calls = [];
  const connections = [];
  const timers = new Map();
  let nextTimer = 0;
  const observer = createSessionObserver({
    runId: "run-a",
    connect: async (signal) => {
      let socket;
      const body = new ReadableStream({ start(controller) {
        socket = controller;
        signal.addEventListener("abort", () => { try { controller.error(new DOMException("aborted", "AbortError")); } catch {} });
      } });
      connections.push({ signal, frame(event, data = {}) {
        socket.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
      }, close: () => socket.close() });
      return { ok: true, body };
    },
    onSync: (data) => calls.push(["sync", data.run_id]),
    onEvent: (data) => calls.push([data.event, data.data]),
    onEnd: (data) => calls.push(["end", data.run_id]),
    onFallback: () => calls.push(["fallback"]),
    onError: (error) => calls.push(["error", error.message]),
    schedule(fn, delay) { const id = ++nextTimer; timers.set(id, { fn, delay }); return id; },
    unschedule(id) { timers.delete(id); },
    ...overrides,
  });
  return { observer, calls, connections, timers, async start() {
    const ready = observer.start();
    await turn();
    return { ready, socket: connections.at(-1) };
  }, async tick(delay = 1500) {
    const [id, timer] = [...timers].find(([, timer]) => timer.delay === delay);
    timers.delete(id); timer.fn(); await turn();
  } };
}

function sync(socket) { socket.frame("observation_sync", { run_id: "run-a", replay_available: true }); }
function emit(socket, event, data = {}, run_id = "run-a") { socket.frame("observation", { run_id, event, data }); }

test("live deltas wait for the authoritative history barrier, then stream before Run completion", async () => {
  const gate = deferred();
  const h = fixture({ onSync: () => gate.promise });
  try {
    const { socket, ready } = await h.start();
    sync(socket);
    emit(socket, "text", { delta: "first" });
    emit(socket, "thinking", { delta: "working" });
    await turn();
    assert.deepEqual(h.calls, []);
    gate.resolve(); await ready; await turn();
    assert.deepEqual(h.calls, [["text", { delta: "first" }], ["thinking", { delta: "working" }]]);
    assert.equal(socket.signal.aborted, false);
    emit(socket, "text", { delta: "second" });
    await turn();
    assert.equal(h.calls.at(-1)[1].delta, "second");
  } finally { h.observer.stop(); }
});

test("only bound Run frames render; late text after terminal is ignored and end closes without subscribing again", async () => {
  const endGate = deferred();
  const h = fixture({ onEnd: () => endGate.promise });
  try {
    const { socket, ready } = await h.start(); sync(socket); await ready;
    emit(socket, "text", { delta: "wrong" }, "run-b");
    emit(socket, "final", { output: "answer" });
    emit(socket, "text", { delta: "late" });
    socket.frame("observation_end", { run_id: "run-a" });
    await turn();
    assert.equal(socket.signal.aborted, false, "terminal history must finish first");
    endGate.resolve(); await turn(); await turn();
    assert.deepEqual(h.calls, [["sync", "run-a"], ["final", { output: "answer" }]]);
    assert.equal(socket.signal.aborted, true);
    assert.equal(h.timers.size, 0);
    assert.equal(h.connections.length, 1);
  } finally { h.observer.stop(); }
});

test("closing/switching an observer aborts only its GET transport and ignores a late barrier", async () => {
  const gate = deferred();
  const h = fixture({ onSync: () => gate.promise });
  const { socket, ready } = await h.start();
  sync(socket); emit(socket, "text", { delta: "stale" }); await turn();
  h.observer.stop(); gate.resolve(); await ready; await turn();
  assert.deepEqual(h.calls, []);
  assert.equal(socket.signal.aborted, true);
  assert.equal(h.timers.size, 0);
});

test("network loss polls saved facts and reconnects the same Run only", async () => {
  const h = fixture();
  try {
    const { socket, ready } = await h.start(); sync(socket); await ready;
    socket.close(); await turn();
    assert.equal(h.calls.some(([kind]) => kind === "fallback"), true);
    assert.equal(h.calls.some(([kind]) => kind === "error"), true, "real disconnection remains visible");
    await h.tick();
    assert.equal(h.connections.length, 2);
    sync(h.connections[1]); await turn();
    assert.equal(h.calls.filter(([kind]) => kind === "sync").length, 2);
  } finally { h.observer.stop(); }
});

test("inactive/truncated Run 409 falls back once and never reconnects or follows a later Run", async () => {
  let requests = 0;
  const h = fixture({ connect: async () => { requests += 1; return { ok: false, status: 409 }; } });
  try {
    await h.observer.start(); await turn();
    assert.equal(requests, 1);
    assert.equal(h.calls.filter(([kind]) => kind === "fallback").length, 1);
    assert.equal(h.calls.some(([kind]) => kind === "error"), false, "an already ended Run is not a connection failure");
    assert.equal(h.timers.size, 0);
  } finally { h.observer.stop(); }
});

test("bounded queued replay resets instead of dropping arbitrary deltas or growing indefinitely", async () => {
  const gate = deferred();
  const h = fixture({ maxFrames: 2, onSync: () => gate.promise });
  try {
    const { socket } = await h.start(); sync(socket);
    emit(socket, "text", { delta: "1" }); emit(socket, "text", { delta: "2" });
    await turn(); gate.resolve(); await turn(); await turn();
    assert.equal(socket.signal.aborted, true);
    assert.equal(h.calls.some(([kind]) => kind === "text"), false);
    assert.equal(h.calls.some(([kind]) => kind === "fallback"), true);
  } finally { h.observer.stop(); }
});

test("server reset without run_id triggers recovery, not a frozen live bubble", async () => {
  const h = fixture();
  try {
    const { socket, ready } = await h.start(); sync(socket); await ready;
    socket.frame("observation_reset", { reason: "slow_consumer" }); await turn(); await turn();
    assert.equal(h.calls.some(([kind]) => kind === "fallback"), true);
    assert.equal(socket.signal.aborted, true);
    assert.equal(h.calls.some(([kind]) => kind === "error"), true, "slow consumers are not normal completion");
  } finally { h.observer.stop(); }
});

test("execution changes during subscribe quietly reconcile history without error toast or reconnect", async () => {
  const h = fixture();
  try {
    const { socket, ready } = await h.start();
    socket.frame("observation_reset", { reason: "execution_changed" });
    await ready; await turn();
    assert.equal(h.calls.some(([kind]) => kind === "fallback"), true);
    assert.equal(h.calls.some(([kind]) => kind === "error"), false);
    assert.equal(socket.signal.aborted, true);
    assert.equal(h.timers.size, 0);
  } finally { h.observer.stop(); }
});

test("replay keeps prior history and the target input exactly once, but neither target output nor future runs", () => {
  const item = (item_id, run_id, kind, sequence) => ({ item_id, run_id, kind, order: { sequence } });
  const history = { items: [item("old", "old", "assistant_message", 1), item("input", "run-a", "user_message", 2),
    item("partial", "run-a", "assistant_message", 3), item("tool", "run-a", "tool", 4),
    item("next", "run-b", "user_message", 5)] };
  assert.deepEqual(historyThroughRun(history, "run-a", { replay: true }).items.map((i) => i.item_id), ["old", "input"]);
  assert.deepEqual(historyThroughRun(history, "run-a").items.map((i) => i.item_id), ["old", "input", "partial", "tool"]);
  assert.equal(history.items.length, 5);
});

test("Run-start sequence isolates a no-output deferred continuation from a later channel request", () => {
  const history = { items: [
    { item_id: "pending", run_id: "old", kind: "tool", order: { sequence: 3 } },
    { item_id: "next", run_id: "future", kind: "user_message", order: { sequence: 4 } },
  ] };
  for (const replay of [true, false]) {
    assert.deepEqual(historyThroughRun(history, "empty-resume", { replay, baselineSequence: 3 }).items.map((i) => i.item_id), ["pending"]);
  }
});

const source = readFileSync(new URL("../../../console/chat.js", import.meta.url), "utf8");
test("idle Console opens persisted history only, never an idle or future-Run SSE subscription", async () => {
  let persisted = 0; let subscriptions = 0;
  const ctx = vm.createContext({ boundedStreamRequest,
    daFetch: async () => ({ ok: true, json: async () => ({ status: "idle" }) }),
    sessionApiUrl: () => "/run-status",
    observePersistedSessionHistory: async (_session, _controller, _current, options) => {
      assert.equal(options.snapshotOnly, true); persisted += 1;
    },
    createSessionObserver: () => { subscriptions += 1; },
  });
  const code = source.slice(source.indexOf("async function observeSessionHistory("), source.indexOf("\nasync function observePersistedSessionHistory("));
  vm.runInContext(`${code}\nthis.observe = observeSessionHistory;`, ctx);
  await ctx.observe({ id: "s", agentId: "a" }, new AbortController(), () => true);
  assert.equal(persisted, 1); assert.equal(subscriptions, 0);
});

test("Console replays one active Run through its existing Presenter, reads committed input, then calibrates terminal History", async () => {
  const seen = [];
  let callbacks;
  let historyReads = 0;
  const items = [
    { item_id: "old", run_id: "old", kind: "assistant_message", order: { sequence: 1 } },
    { item_id: "input", run_id: "run-a", kind: "user_message", order: { sequence: 2 } },
    { item_id: "answer", run_id: "run-a", kind: "assistant_message", order: { sequence: 3 } },
    { item_id: "future", run_id: "run-b", kind: "user_message", order: { sequence: 4 } },
  ];
  const controller = new AbortController();
  const state = {};
  const ctx = vm.createContext({ boundedStreamRequest, state,
    daFetch: async (url) => ({ ok: true, json: async () => {
      if (url === "/run-status") return { status: "running", run_id: "run-a" };
      historyReads += 1; return { items };
    } }), sessionApiUrl: (_session, suffix) => suffix,
    normalizeConversationHistory, historyThroughRun,
    createSessionObserver(options) { callbacks = options; return { start: async () => {}, stop() {} }; },
    createAssistantMessage(_time, _point, _before, options) {
      seen.push(["presenter", options.autoAttachDeferred]);
      return { row: { remove() {} }, dispose() {} };
    },
    applyObservedHistory(history) { seen.push(["history", history.items.map((item) => item.item_id)]); },
    handleEvent(frame, _assistant, options) {
      assert.equal(options.allowDeferred, false, "observer never unlocks from its terminal SSE alone");
      seen.push(["event", frame.event]);
    },
    loadAllSessionEvents: async () => ({ response: { ok: false } }),
    setStreaming(value) { seen.push(["streaming", value]); },
    updateHitlInputState() {}, hideEmptyState() {}, loadContextStatus() {},
  });
  const code = source.slice(source.indexOf("async function observeSessionHistory("), source.indexOf("\nasync function observePersistedSessionHistory("));
  vm.runInContext(`${code}\nthis.observe = observeSessionHistory;`, ctx);
  await ctx.observe({ id: "s", agentId: "a" }, controller, () => !controller.signal.aborted);
  assert.equal(callbacks.runId, "run-a");
  await callbacks.onSync({ baseline_sequence: 1 }, controller.signal);
  await callbacks.onEvent({ event: "session", data: { session_id: "s", run_id: "run-a" } }, controller.signal);
  await callbacks.onEvent({ event: "session_event_appended", data: { event: { event_type: "user/message" } } }, controller.signal);
  await callbacks.onEvent({ event: "text", data: { delta: "incremental" } }, controller.signal);
  assert.equal(state.observingRun, true);
  assert.equal(seen.filter(([kind]) => kind === "presenter").every(([, auto]) => auto === false), true);
  assert.equal(seen.filter(([kind]) => kind === "history").every(([, ids]) => ids.join() === "old,input"), true);
  await callbacks.onEnd({}, controller.signal);
  assert.deepEqual(seen.filter(([kind]) => kind === "history").at(-1), ["history", ["old", "input", "answer"]]);
  assert.equal(historyReads, 3);
  assert.equal(state.observingRun, false);
  assert.equal(state.streamSessionConfirmed, false);
  controller.abort();
});

test("initial 409 with no target facts reads latest History once, readonly if another Run already started", async () => {
  let callbacks;
  const ctx = vm.createContext({
    state: {},
    createHistoryFollower(options) { callbacks = options; return { start: async () => {}, stop() {} }; },
    daFetch: async (url) => ({ ok: true, json: async () => url === "/run-status"
      ? { status: "running", run_id: "later-run" }
      : { items: [{ item_id: "new", run_id: "later-run", kind: "assistant_message", order: { sequence: 5 } }] } }),
    sessionApiUrl: (_session, suffix) => suffix,
    normalizeConversationHistory, historyThroughRun,
  });
  const code = source.slice(source.indexOf("async function observePersistedSessionHistory("), source.indexOf("\nfunction updateObservedSessionMetrics("));
  vm.runInContext(`${code}\nthis.observe = observePersistedSessionHistory;`, ctx);
  const controller = new AbortController();
  await ctx.observe({ id: "s" }, controller, () => true, { runId: "empty-resume", register: false });
  const status = await callbacks.readStatus(controller.signal);
  assert.equal(status.status, "idle", "target ended, do not follow the new Run");
  assert.equal(status.read_only, true, "newly persisted tools must not become actionable");
  const result = await callbacks.readHistory(controller.signal, status);
  assert.equal(result.history.items[0].item_id, "new", "one ordinary latest-history snapshot is allowed");
  controller.abort();
});

test("idle snapshot never rechecks status to start following a later Run", async () => {
  let callbacks; let requests = 0;
  const ctx = vm.createContext({ state: {},
    createHistoryFollower(options) { callbacks = options; return { start: async () => {}, stop() {} }; },
    daFetch: async () => { requests += 1; return { ok: true, json: async () => ({ status: "running", run_id: "new" }) }; },
  });
  const code = source.slice(source.indexOf("async function observePersistedSessionHistory("), source.indexOf("\nfunction updateObservedSessionMetrics("));
  vm.runInContext(`${code}\nthis.observe = observePersistedSessionHistory;`, ctx);
  const controller = new AbortController();
  await ctx.observe({}, controller, () => true, { snapshotOnly: true });
  assert.equal((await callbacks.readStatus(controller.signal)).status, "idle");
  assert.equal(requests, 0);
  controller.abort();
});

test("observer pending final stays locked without interactive widgets until end-history calibration; POST keeps its behavior", () => {
  const state = { streaming: true, observingRun: true, awaitingHitl: false };
  let widgets = 0;
  const presenter = { complete() {}, attachHitlWidget() { widgets += 1; } };
  const ctx = vm.createContext({ state, trajectory: null,
    applyAssistantPresentationActions, assistantActionsFromSse, assistantActionsFromDeferred,
    mergeUiToolCall: (call) => call,
    finalizeAutomaticContextCompaction() {}, clearComposerPlan() {}, updateHitlInputState() {}, scrollToBottom() {},
    setStreaming(value) { state.streaming = value; },
  });
  const code = source.slice(source.indexOf("function handleEvent("), source.indexOf("\nfunction renderUsage("));
  vm.runInContext(`${code}\nthis.handle = handleEvent;`, ctx);
  const final = { event: "final", data: { status: "pending", output: "", deferred: { calls: [{ tool: "confirm", tool_call_id: "one" }] } } };
  ctx.handle(final, presenter, { allowDeferred: false });
  assert.equal(state.streaming, true, "only onEnd successful History may unlock observation");
  assert.equal(state.awaitingHitl, false);
  assert.equal(widgets, 0);
  ctx.handle(final, presenter);
  assert.equal(state.streaming, false, "ordinary POST is unchanged");
  assert.equal(state.awaitingHitl, true);
  assert.equal(widgets, 1);
});
