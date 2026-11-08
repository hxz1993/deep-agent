import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { sessionAppQuery } from "../../../console/session-app-filter.js";
const source = await readFile(new URL("../../../console/chat.js", import.meta.url), "utf8");
const deletion = source.slice(source.indexOf("const sessionDeletesInFlight ="), source.indexOf("/* --------------------------- 会话重命名"));
const runningKeys = source.slice(source.indexOf("function runningSessionKeys()"), source.indexOf("const MAX_CONSOLE_CHAT_STREAMS"));
const idle = () => ({ ok: true, json: async () => ({ status: "idle" }) });
function loadDeletion(context, prefix = "") {
  Object.assign(context, { boundedStreamRequest: request => request(new AbortController().signal) });
  vm.runInContext(prefix + runningKeys + deletion, context);
}
test("failed deletion retains the list, pagination and current conversation", async () => {
  const session = { id: "one", agentId: "agent" }, notes = [];
  const context = vm.createContext({
    state: { sessions: [session], sessionId: "one", agentId: "agent" },
    sessionPage: { agentId: "agent", offset: 1 },
    normalizeSession: s => s, sessionLookupKey: s => s.id, sessionApiUrl: () => "/probe",
    daFetch: async (_, { method }) => method === "DELETE"
      ? ({ ok: false, status: 403, json: async () => ({ detail: "本地只读预览" }) }) : idle(),
    addSystemNote: text => notes.push(text), renderSessions() { throw new Error("must not remove the row"); }, newChat() { throw new Error("must not clear the chat"); },
  });
  loadDeletion(context);
  await context.deleteSession(session);
  assert.equal(context.state.sessions.length, 1);
  assert.equal(context.sessionPage.offset, 1);
  assert.match(notes[0], /只读预览/);
});

test("deletion requires a stopped run, including one started in another client", async () => {
  const session = { id: "one", agentId: "agent" }, calls = [], warnings = [];
  const state = { sessions: [session], sessionId: "one", agentId: "agent", observingRun: true };
  const context = vm.createContext({
    state, normalizeSession: s => s, sessionLookupKey: s => s.id, sessionApiUrl: (_, path) => path,
    daFetch: async (url, options) => {
      calls.push([url, options.method]);
      return { ok: true, json: async () => ({ status: "cancel_requested" }) };
    },
    showConsoleToast: text => warnings.push(text),
  });
  loadDeletion(context);
  await context.deleteSession(session);
  assert.deepEqual(calls, []);
  state.observingRun = false;
  await context.deleteSession(session);
  assert.deepEqual(calls, [["/run-status", undefined]]);
  assert.equal(warnings.length, 2);
  assert.match(warnings[1], /先停止生成/);
  assert.deepEqual(state.sessions, [session]);
});

test("repeated clicks while deletion is pending send once and decrement pagination once", async () => {
  const session = { id: "one", agentId: "agent" };
  let resolveDelete, calls = 0;
  const context = vm.createContext({
    state: { sessions: [session], sessionId: "other", agentId: "agent" },
    sessionPage: { agentId: "agent", offset: 3, ids: new Set(["one"]) },
    normalizeSession: s => s, sessionLookupKey: s => s.id, sessionApiUrl: () => "/probe",
    daFetch: (_, { method }) => method === "DELETE"
      ? new Promise(resolve => { calls++; resolveDelete=resolve; }) : Promise.resolve(idle()),
    addSystemNote() {}, renderSessions() {}, newChat() {}, refreshSessionFilterOptions: async () => {},
  });
  loadDeletion(context);
  const first=context.deleteSession(session), second=context.deleteSession(session);
  await new Promise(setImmediate);
  assert.equal(calls,1);
  resolveDelete({ok:true}); await Promise.all([first,second]);
  assert.equal(context.sessionPage.offset,2);
  assert.equal(context.state.sessions.length,0);
});

test("deletion invalidates an old pending page and does not change the opened conversation or filter", async () => {
  const controller = new AbortController();
  const page = { agentId: "agent", offset: 3, total: 8, ids: new Set(["one", "two"]), controller, loading: true };
  const context = vm.createContext({
    state: { sessions: [{ id: "one", agentId: "agent" }, { id: "two", agentId: "agent" }], sessionId: "two", agentId: "agent", sessionAppFilter: "app:budget001" },
    sessionPage: page, normalizeSession: s => s, sessionLookupKey: s => s.id, sessionApiUrl: () => "/probe",
    daFetch: async () => idle(), renderSessions() {}, refreshSessionFilterOptions: async () => {}, loadMoreSessions: async () => {},
  });
  loadDeletion(context);
  await context.deleteSession({ id: "one", agentId: "agent" });
  assert.equal(controller.signal.aborted, true);
  assert.notEqual(context.sessionPage, page);
  assert.equal(context.sessionPage.loading, false);
  assert.equal(context.sessionPage.total, 7);
  assert.equal(context.sessionPage.ids.has("one"), false);
  assert.equal(context.state.sessionId, "two");
  assert.equal(context.state.sessionAppFilter, "app:budget001");
});

test("delete followed by a new filter rejects its pre-delete page and reloads the active filter", async () => {
  const gets = [];
  let finishDelete;
  const original = { id: "one", agentId: "agent", title: "one" };
  const state = { agentId: "agent", sessionId: "other", sessions: [original], sessionAppFilter: "all" };
  const context = vm.createContext({
    state, window: {}, sessionAppQuery, URLSearchParams, AbortController, setTimeout, clearTimeout,
    document: { getElementById: () => ({ value: "" }) }, el: { sessionList: { scrollTop: 0 } },
    normalizeSession: value => value, sessionLookupKey: value => value.id, sessionApiUrl: (_, path) => path || "/delete",
    serviceUrl: value => value, renderSessions() {}, requestAnimationFrame() {}, refreshSessionFilterOptions: async () => {},
    daFetch: (url, options) => options.method === "DELETE"
      ? new Promise(resolve => { finishDelete = resolve; })
      : url === "/run-status" ? Promise.resolve(idle())
        : new Promise(resolve => gets.push({ url, resolve, signal: options.signal })),
    mergeSessionsFromBackend: rows => { state.sessions = rows.map(row => ({ id: row.session_id, agentId: row.agent_id })); },
  });
  loadDeletion(context, source.slice(source.indexOf("let sessionPage ="), source.indexOf("function autoGrow()")));
  const deleting = context.deleteSession(original);
  await new Promise(setImmediate);
  const stale = context.syncSessionsFromBackend();
  finishDelete({ ok: true });
  await new Promise(setImmediate);
  assert.equal(gets[0].signal.aborted, true);
  assert.equal(gets.length, 2);
  gets[0].resolve({ ok: true, json: async () => ({ items: [{ session_id: "one", agent_id: "agent" }], total: 1 }) });
  await stale;
  assert.equal(state.sessions.length, 0);
  gets[1].resolve({ ok: true, json: async () => ({ items: [], total: 0 }) });
  await deleting;
  assert.equal(state.sessions.length, 0);
  assert.equal(state.sessionId, "other");
  assert.equal(new URLSearchParams(gets[1].url.split("?")[1]).get("app_scope"), "all");
});
