import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { createHistoryFollower } from "../../../console/history-follower.js";
import { boundedStreamRequest } from "../../../console/stop-recovery.js";
import { historyThroughRun } from "../../../console/session-observer.js";

const turn = () => new Promise(setImmediate);
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };

function fixture(overrides = {}) {
  const calls = [];
  const timers = new Map();
  let timerId = 0;
  const follower = createHistoryFollower({
    readStatus: async () => ({ status: "running" }),
    readHistory: async () => ({ items: ["preview"] }),
    onStatus: (value) => calls.push(["status", value.status]),
    onHistory: (value) => calls.push(["history", value.items]),
    onError: (error) => calls.push(["error", error.message]),
    schedule: (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; },
    unschedule: (id) => timers.delete(id),
    ...overrides,
  });
  return {
    follower, calls, timers,
    async tick() {
      const [id, { fn }] = timers.entries().next().value;
      timers.delete(id);
      fn();
      await turn();
    },
  };
}

test("running history follows at one second without starting another model run", async () => {
  const h = fixture();
  try {
    await h.follower.start();
    assert.deepEqual(h.calls, [["status", "running"], ["history", ["preview"]], ["status", "running"]]);
    assert.equal([...h.timers.values()][0].delay, 1000);
    await h.tick();
    assert.equal(h.calls.filter(([kind]) => kind === "history").length, 2);
    assert.equal(h.timers.size, 1);
  } finally { h.follower.stop(); }
});

test("idle does not unlock until the final history request resolves", async () => {
  const gate = deferred();
  const h = fixture({ readStatus: async () => ({ status: "idle" }), readHistory: () => gate.promise });
  const pending = h.follower.start();
  await turn();
  assert.deepEqual(h.calls, []);
  gate.resolve({ items: ["final only"] });
  await pending;
  assert.deepEqual(h.calls, [["history", ["final only"]], ["status", "idle"]]);
  assert.equal(h.timers.size, 0);
  h.follower.stop();
});

test("a failed final history read never reports idle and retries", async () => {
  let fail = true;
  const h = fixture({ readStatus: async () => ({ status: "idle" }), readHistory: async () => {
    if (fail) throw new Error("HTTP 503");
    return { items: ["completed"] };
  } });
  try {
    await h.follower.start();
    assert.deepEqual(h.calls, [["error", "HTTP 503"]]);
    fail = false;
    await h.tick();
    assert.deepEqual(h.calls.at(-1), ["status", "idle"]);
    assert.equal(h.timers.size, 0);
  } finally { h.follower.stop(); }
});

test("unknown and unavailable run status preserve the previous state rather than inventing a terminal", async () => {
  const states = [{ status: "running" }, { status: "nonsense" }, new Error("offline"), { status: "idle" }];
  const h = fixture({ readStatus: async () => {
    const next = states.shift();
    if (next instanceof Error) throw next;
    return next;
  } });
  try {
    await h.follower.start();
    await h.tick();
    await h.tick();
    assert.equal(h.calls.some(([kind, status]) => kind === "status" && status === "idle"), false);
    assert.equal(h.calls.filter(([kind]) => kind === "history").length, 1);
    await h.tick();
    assert.deepEqual(h.calls.at(-1), ["status", "idle"]);
  } finally { h.follower.stop(); }
});

test("cancel_requested remains active until backend idle and final history", async () => {
  let status = "cancel_requested";
  const h = fixture({ readStatus: async () => ({ status }) });
  try {
    await h.follower.start();
    assert.equal(h.timers.size, 1);
    assert.deepEqual(h.calls.at(-1), ["status", "cancel_requested"]);
    status = "idle";
    await h.follower.refresh();
    assert.deepEqual(h.calls.at(-1), ["status", "idle"]);
    assert.equal(h.timers.size, 0);
  } finally { h.follower.stop(); }
});

test("slow history requests cannot overlap, even with repeated explicit refreshes", async () => {
  const gate = deferred();
  let reads = 0;
  const h = fixture({ readHistory: async () => { reads += 1; return gate.promise; } });
  try {
    const first = h.follower.start();
    await turn();
    const refresh = h.follower.refresh();
    const again = h.follower.refresh();
    assert.equal(reads, 1);
    gate.resolve({ items: [] });
    await Promise.all([first, refresh, again]);
    assert.equal(h.timers.size, 1);
    assert.equal([...h.timers.values()][0].delay, 0);
  } finally { h.follower.stop(); }
});

test("switching session ignores a late status response and does not request old history", async () => {
  let current = true;
  const gate = deferred();
  let historyReads = 0;
  const h = fixture({ isCurrent: () => current, readStatus: () => gate.promise,
    readHistory: async () => { historyReads += 1; return {}; } });
  const pending = h.follower.start();
  current = false;
  gate.resolve({ status: "running" });
  await pending;
  assert.equal(historyReads, 0);
  assert.deepEqual(h.calls, []);
  assert.equal(h.timers.size, 0);
  h.follower.stop();
});

test("switching Agent ignores a late history response, even if the old fetch ignores abort", async () => {
  const gate = deferred();
  let current = true;
  const h = fixture({ isCurrent: () => current, readHistory: () => gate.promise });
  const pending = h.follower.start();
  await turn();
  current = false;
  gate.resolve({ items: ["old Agent"] });
  await pending;
  assert.deepEqual(h.calls, [["status", "running"]]);
  assert.equal(h.timers.size, 0);
  h.follower.stop();
});

test("unload aborts pending requests, removes scheduled work and suppresses errors", async () => {
  let signal;
  const h = fixture({ readStatus: (requestSignal) => {
    signal = requestSignal;
    return new Promise(() => {});
  } });
  const pending = h.follower.start();
  await turn();
  h.follower.stop();
  await pending;
  assert.equal(signal.aborted, true);
  assert.equal(h.timers.size, 0);
  assert.deepEqual(h.calls, []);
});

test("hanging status and body reads time out then allow the next polling attempt", async () => {
  let first = true;
  const h = fixture({ timeoutMs: 5, readStatus: async () => {
    if (first) return new Promise(() => {});
    return { status: "idle" };
  } });
  try {
    await h.follower.start();
    assert.deepEqual(h.calls, [["error", "请求超时"]]);
    first = false;
    await h.tick();
    assert.deepEqual(h.calls.at(-1), ["status", "idle"]);
  } finally { h.follower.stop(); }
});

const source = readFileSync(new URL("../../../console/chat.js", import.meta.url), "utf8");
const presenterSource = readFileSync(new URL("../../../console/conversation-presenter.js", import.meta.url), "utf8");

test("Console checks the selected session identity after asynchronous UI tool loading", async () => {
  const gates = { a: deferred(), b: deferred() };
  const observed = [];
  const state = { sessionId: "a", agentId: "agent", historyController: null };
  const context = {
    state, AbortController, boundedStreamRequest,
    normalizeSession: (value) => value,
    stopHistoryObservation() { state.historyController?.abort(); state.historyController = null; },
    clearMessages() {}, hideEmptyState() {}, updateHitlInputState() {},
    trajectory: { setHistory() {} },
    loadUiTools: async () => gates[state.sessionId].promise,
    observeSessionHistory: async (session) => observed.push(session.id),
    loadContextStatus() {}, addSystemNote() {},
  };
  vm.createContext(context);
  const fn = source.slice(source.indexOf("async function loadSessionHistory("), source.indexOf("\nfunction stopHistoryObservation("));
  vm.runInContext(`${fn}\nthis.load = loadSessionHistory;`, context);
  const first = context.load({ id: "a", agentId: "agent" });
  await turn();
  state.sessionId = "b";
  const second = context.load({ id: "b", agentId: "agent" });
  gates.b.resolve();
  await second;
  gates.a.resolve();
  await first;
  assert.deepEqual(observed, ["b"]);
});

test("Console observed cancellation only requests control and final reconciliation, never a new chat stream", async () => {
  const calls = [];
  const state = { sessionId: "s", agentId: "a", observingRun: true,
    historyController: new AbortController(), historyFollower: { refresh: () => calls.push("refresh") } };
  const context = { state, AbortController, el: { stopBtn: {} },
    sessionApiUrl: (session, path) => `${session.id}${path}`,
    daFetch: async (url, opts) => { calls.push([url, opts.method]); return {}; },
    verifyStreamStop: async ({ request }) => { await request("cancel"); await request("status"); return { status: "idle" }; },
    showConsoleToast() {},
  };
  vm.createContext(context);
  const fn = source.slice(source.indexOf("async function stopObservedRun()"), source.indexOf("\nfunction setStreaming("));
  vm.runInContext(`${fn}\nthis.stop = stopObservedRun;`, context);
  await context.stop();
  assert.deepEqual(calls, [["s/cancel", "POST"], ["s/run-status", "GET"], "refresh"]);
  assert.equal(state.observingRun, true, "a successful final history read still owns unlocking");
});

test("Console old observed cancel response cannot affect another selected session", async () => {
  const gate = deferred();
  let refreshes = 0;
  const state = { sessionId: "old", agentId: "a", observingRun: true,
    historyController: new AbortController(), historyFollower: { refresh: () => { refreshes += 1; } } };
  const context = { state, AbortController, el: { stopBtn: {} }, verifyStreamStop: () => gate.promise, showConsoleToast() {} };
  vm.createContext(context);
  const fn = source.slice(source.indexOf("async function stopObservedRun()"), source.indexOf("\nfunction setStreaming("));
  vm.runInContext(`${fn}\nthis.stop = stopObservedRun;`, context);
  const stopping = context.stop();
  state.sessionId = "new";
  state.historyController = new AbortController();
  state.stopRequested = true;
  gate.resolve({ status: "idle" });
  await stopping;
  assert.equal(refreshes, 0);
  assert.equal(state.stopRequested, true);
});

test("running History is presentation-only even when the tool catalog can restore a UI widget", () => {
  assert.match(source, /allowDeferred: !running/);
  assert.match(source, /autoAttachDeferred: false/);
  assert.match(presenterSource, /if \(autoAttachDeferred\) maybeAttachHitlWidget\(existing, merged\)/);
  assert.match(presenterSource, /if \(autoAttachDeferred\) maybeAttachHitlWidget\(block, merged\)/);
  assert.match(presenterSource, /attachHitlWidget\(call\) \{\s*if \(!allowDeferred\) \{[\s\S]*?return;/);
  assert.match(source, /state\.awaitingHitl = !running && latestPendingDeferredTool/);
});

test("leaving an initially unavailable history view clears its loading lock and refreshes send controls", () => {
  const historyController = new AbortController();
  let stopped = false;
  let locked = true;
  const state = { historyLoading: true, observingRun: false, historyController,
    historyFollower: { stop: () => { stopped = true; } }, historyObservationPending: true };
  const context = { state, updateHitlInputState: () => { locked = state.historyLoading; } };
  vm.createContext(context);
  const fn = source.slice(source.indexOf("function stopHistoryObservation()"), source.indexOf("\nasync function observeSessionHistory("));
  vm.runInContext(`${fn}\nthis.leave = stopHistoryObservation;`, context);
  context.leave();
  assert.equal(stopped, true);
  assert.equal(historyController.signal.aborted, true);
  assert.equal(locked, false);
  assert.equal(state.historyObservationPending, false);
});

async function observedMetricsFixture(options = {}) {
  const selected = { id: "session", agentId: "agent", messageCount: 1, turnCount: 1 };
  const state = { sessionId: selected.id, agentId: selected.agentId, sessions: [selected], abortController: null };
  const requests = [];
  let callbacks;
  let renders = 0;
  let trajectoryUpdates = 0;
  let trajectoryItems = [];
  let editingTitle = false;
  const controller = new AbortController();
  const context = {
    state, boundedStreamRequest, historyThroughRun,
    createHistoryFollower(options) { callbacks = options; return { start: async () => {}, stop() {} }; },
    loadAllSessionEvents() { const gate = deferred(); requests.push(gate); return gate.promise; },
    trajectory: { setHistory(history) { trajectoryUpdates += 1; trajectoryItems = history.items; } },
    applyObservedHistory() {}, loadContextStatus() {},
    el: { sessionList: { querySelector: () => editingTitle } },
    renderSessions() { renders += 1; },
  };
  vm.createContext(context);
  const fn = source.slice(source.indexOf("async function observePersistedSessionHistory("), source.indexOf("\nfunction applyObservedHistory("));
  vm.runInContext(`${fn}\nthis.observe = observePersistedSessionHistory;`, context);
  await context.observe(selected, controller, () => !controller.signal.aborted && state.sessionId === selected.id && state.agentId === selected.agentId, options);
  return {
    selected, state, requests, controller,
    history(status) { callbacks.onHistory({ history: { items: [] }, signature: status, changed: true }, { status }); },
    finish(index, items) { requests[index].resolve({ response: { ok: true }, data: { items } }); },
    get renders() { return renders; }, get trajectoryUpdates() { return trajectoryUpdates; },
    get trajectoryItems() { return trajectoryItems; },
    editTitle() { editingTitle = true; },
  };
}

test("observed terminal reuses serialized event reads to refresh canonical Session metrics once", async () => {
  const h = await observedMetricsFixture();
  const user = { event_type: "user/message", turn_id: "turn-1" };
  h.history("running");
  h.history("idle");
  await turn();
  assert.equal(h.requests.length, 1, "terminal queues one audit refresh, not a concurrent request");
  h.finish(0, [user]);
  await turn();
  assert.equal(h.requests.length, 2);
  h.finish(1, [user,
    { event_type: "assistant/message", turn_id: "turn-1" },
    { event_type: "tool/results", turn_id: "turn-1" },
    { event_type: "model/request", turn_id: "turn-2" },
    { event_type: "run/progress", turn_id: "turn-3" },
    { event_type: "compaction", turn_id: "turn-3" },
    { event_type: "session/title", turn_id: "turn-3" },
  ]);
  await turn();
  await turn();
  assert.equal(h.selected.messageCount, 4);
  assert.equal(h.selected.turnCount, 2);
  assert.equal(h.renders, 1);
  assert.equal(h.requests.length, 2, "metrics add no request or polling loop");
  h.controller.abort();
});

test("late observed metrics cannot update a switched Agent or a new local SSE run", async () => {
  for (const leave of [(h) => { h.state.agentId = "other-agent"; }, (h) => { h.state.abortController = new AbortController(); }]) {
    const h = await observedMetricsFixture();
    h.history("running");
    h.history("idle");
    await turn();
    leave(h);
    h.finish(0, []);
    await turn();
    assert.equal(h.selected.messageCount, 1);
    assert.equal(h.trajectoryUpdates, 0);
    assert.equal(h.requests.length, 1, "queued old-session refresh is discarded too");
    h.controller.abort();
  }
});

test("observed statistics refresh preserves an in-progress sidebar title edit", async () => {
  const h = await observedMetricsFixture();
  h.editTitle();
  h.history("idle");
  await turn();
  h.finish(0, []);
  await turn();
  assert.equal(h.selected.messageCount, 0);
  assert.equal(h.selected.turnCount, 0);
  assert.equal(h.renders, 0);
  h.controller.abort();
});

test("fallback trajectory uses the same target Run boundary as History, including terminal races", async () => {
  const facts = [
    { event_id: "prior", sequence: 1, run_id: "prior", event_type: "user/message", turn_id: "prior" },
    { event_id: "target", sequence: 2, run_id: "target", event_type: "assistant/message", turn_id: "target" },
    { event_id: "future", sequence: 3, run_id: "future", event_type: "user/message", turn_id: "future" },
  ];
  for (const [options, expected] of [
    [{ runId: "target", baselineSequence: 1 }, ["prior", "target"]],
    [{ runId: "target" }, ["prior", "target"]],
    [{ runId: "empty-resume", baselineSequence: 1 }, ["prior"]],
    [{ runId: "empty-resume" }, ["prior", "target", "future"]],
  ]) {
    const h = await observedMetricsFixture(options);
    try {
      h.history("idle"); await turn();
      h.finish(0, facts); await turn(); await turn();
      assert.deepEqual(h.trajectoryItems.map((item) => item.event_id), expected);
      assert.equal(h.selected.messageCount, expected.length, "metrics use the same bounded facts");
      assert.equal(h.requests.length, 1, "no additional observation of the future Run");
    } finally { h.controller.abort(); }
  }
});
