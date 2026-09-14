import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
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
    sessionPage: { agentId: "agent", offset: 3 },
    normalizeSession: s => s, sessionLookupKey: s => s.id, sessionApiUrl: () => "/probe",
    daFetch: () => { calls++; return new Promise(resolve => {resolveDelete=resolve;}); },
    addSystemNote() {}, renderSessions() {}, newChat() {},
  });
  vm.runInContext(deletion, context);
  const first=context.deleteSession(session), second=context.deleteSession(session);
  assert.equal(calls,1);
  resolveDelete({ok:true}); await Promise.all([first,second]);
  assert.equal(context.sessionPage.offset,2);
  assert.equal(context.state.sessions.length,0);
});
