import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { sessionAppQuery } from "../../../console/session-app-filter.js";
const source = await readFile(new URL("../../../console/chat.js", import.meta.url), "utf8");
const deletion = source.slice(source.indexOf("const sessionDeletesInFlight ="), source.indexOf("/* --------------------------- 会话重命名"));
test("failed deletion retains the list, pagination and current conversation", async () => {
  const session = { id: "one", agentId: "agent" }, notes = [];
  const context = vm.createContext({
    state: { sessions: [session], sessionId: "one", agentId: "agent" },
    sessionPage: { agentId: "agent", offset: 1 },
    normalizeSession: s => s, sessionLookupKey: s => s.id, sessionApiUrl: () => "/probe",
    daFetch: async () => ({ ok: false, status: 403, json: async () => ({ detail: "本地只读预览" }) }),
    addSystemNote: text => notes.push(text), renderSessions() { throw new Error("must not remove the row"); }, newChat() { throw new Error("must not clear the chat"); },
  });
  vm.runInContext(deletion, context);
  await context.deleteSession(session);
  assert.equal(context.state.sessions.length, 1);
  assert.equal(context.sessionPage.offset, 1);
  assert.match(notes[0], /只读预览/);
});


test("repeated clicks while deletion is pending send once and decrement pagination once", async () => {
  const session = { id: "one", agentId: "agent" };
  let resolveDelete, calls = 0;
  const context = vm.createContext({
    state: { sessions: [session], sessionId: "other", agentId: "agent" },
    sessionPage: { agentId: "agent", offset: 3, ids: new Set(["one"]) },
    normalizeSession: s => s, sessionLookupKey: s => s.id, sessionApiUrl: () => "/probe",
    daFetch: () => { calls++; return new Promise(resolve => {resolveDelete=resolve;}); },
    addSystemNote() {}, renderSessions() {}, newChat() {}, refreshSessionFilterOptions: async () => {},
  });
  vm.runInContext(deletion, context);
  const first=context.deleteSession(session), second=context.deleteSession(session);
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
    daFetch: async () => ({ ok: true }), renderSessions() {}, refreshSessionFilterOptions: async () => {}, loadMoreSessions: async () => {},
  });
  vm.runInContext(deletion, context);
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
    normalizeSession: value => value, sessionLookupKey: value => value.id, sessionApiUrl: () => "/delete",
    serviceUrl: value => value, renderSessions() {}, requestAnimationFrame() {}, refreshSessionFilterOptions: async () => {},
    daFetch: (url, options) => options.method === "DELETE"
      ? new Promise(resolve => { finishDelete = resolve; })
      : new Promise(resolve => gets.push({ url, resolve, signal: options.signal })),
    mergeSessionsFromBackend: rows => { state.sessions = rows.map(row => ({ id: row.session_id, agentId: row.agent_id })); },
  });
  vm.runInContext(source.slice(source.indexOf("let sessionPage ="), source.indexOf("function autoGrow()")) + deletion, context);
  const deleting = context.deleteSession(original);
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
