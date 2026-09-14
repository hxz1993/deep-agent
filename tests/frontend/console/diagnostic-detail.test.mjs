import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { diagnosticRunPresentation, findConversationFocus, savedRunFacts } from "../../../console/diagnostic-detail.js";
import { createConversationPresenter } from "../../../console/conversation-presenter.js";
import { applyAssistantPresentationActions, assistantActionsFromHistoryItems } from "../../../console/assistant-presentation.js";
import { renderSessionInspector } from "../../../console/session-inspector.js";

// The imports above run without window/document and catch both accidental
// browser initialization and ESM parse failures (not a regex-only check).
test("shared readonly modules import without browser globals or network initialization", async () => {
  assert.equal(typeof globalThis.window, "undefined");
  const source = await readFile(new URL("../../../console/diagnostic-detail.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /from ["'][^"']*(?:chat\.js|workspace\.js)["']/);
  assert.doesNotMatch(source, /(?:fetch|daFetch|EventSource|setInterval|readSse)\s*\(/);
  assert.doesNotMatch(source, /<(?:textarea|input[^>]+type="file")/);
  assert.match(source, /createConversationPresenter\(\{ messages, readOnly: true/);
  assert.match(source, /createTrajectoryController\(\{ state, host: root, readOnly: true/);
  assert.match(source, /trajectory\.destroy\(\); disposeImagePreviews\(\)/);
});

test("readonly detail uses the pristine workspace shell and does not restyle its content", async () => {
  const source = await readFile(new URL("../../../console/diagnostic-detail.js", import.meta.url), "utf8");
  const shell = await readFile(new URL("../../../console/session-view-shell.js", import.meta.url), "utf8");
  const workspace = await readFile(new URL("../../../console/workspace.js", import.meta.url), "utf8");
  const css = await readFile(new URL("../../../console/diagnostic-detail.css", import.meta.url), "utf8");
  assert.match(source, /createReadonlySessionShell\(\)/);
  assert.match(workspace, /registerSessionViewTemplate\(source\)/);
  assert.match(shell, /source\.querySelector\("\.app > \.chat"\)/);
  assert.match(shell, /workspaceSource\.cloneNode\(true\)/);
  assert.doesNotMatch(source, /<header class="chat-header|<nav class="chat-view-tabs|<header class="trajectory-toolbar/);
  assert.doesNotMatch(source, /data-detail-logs|data-diagnostic-run-logs|diagnostic-detail-avatar/);
  assert.doesNotMatch(css, /\.inspector-facts|\.inspector-tabs|\.msg-row|\.date-separator|\.trajectory-toolbar/);
});

test("question/run focus follows exact saved event identities instead of text", () => {
  const items = [
    { item_id: "message:a", kind: "user_message", text: "same question", source_event_ids: ["a"], order: { sequence: 1 } },
    { item_id: "message:b", kind: "user_message", text: "same question", source_event_ids: ["b"], order: { sequence: 4 } },
  ];
  const events = [{ event_id: "a", run_id: "run-a" }, { event_id: "b", run_id: "run-b" }];
  assert.equal(findConversationFocus(items, events, { eventId: "b" }).item_id, "message:b");
  assert.equal(findConversationFocus(items, events, { runId: "run-b" }).item_id, "message:b");
  assert.equal(findConversationFocus(items, events, { eventId: "missing", runId: "run-a" }), null);
  assert.equal(findConversationFocus(items, events, { runId: "run" }), null);
});

test("usage is counted once per complete assistant message, never per persisted part", () => {
  const base = { event_type: "assistant/message", session_id: "s", run_id: "r", turn_id: "turn", created_at: "2026-09-09T00:00:01Z", status: "completed" };
  const events = [0, 1].map((index) => ({ ...base, event_id: `part-${index}`, sequence: 2 + index, payload: { source: { message_ordinal: 1, part_index: index, part_key: `${index}` }, message: { kind: "response", model_name: "historical-model", usage: { input_tokens: 80, output_tokens: 20 }, parts: [{ part_kind: index ? "text" : "thinking", content: index ? "answer" : "thinking" }] } } }));
  const facts = savedRunFacts(events, "r");
  assert.deepEqual(facts.usage, { input_tokens: 80, output_tokens: 20, total_tokens: 100 });
  assert.equal(facts.model_name, "historical-model");
  assert.deepEqual(savedRunFacts(events, "not-this-run").usage, {});
  assert.deepEqual(savedRunFacts([]).usage, {});
});

test("diagnostic inspector uses injected saved identity, historical model and unknown values", () => {
  const html = renderSessionInspector({ diagnostic: true, state: { sessionId: "session-1", agentId: "agent-1" }, agentName: "Agent", identity: { user: "platform-owner" }, activeSession: { appId: "saved-app", spaceId: "saved-space" }, model: { name: "historical-model" }, statusText: "未确认" });
  assert.match(html, /platform-owner/);
  assert.match(html, /saved-app/);
  assert.match(html, /historical-model/);
  assert.match(html, /Language[\s\S]*未记录/);
  assert.doesNotMatch(html, /继承|未认证/);
  assert.doesNotMatch(html, /data-diagnostic-context|平台用户|diagnostic-detail-avatar/);
  assert.match(html, /上下文预算/);
  assert.match(html, /上下文压缩/);
  const run = renderSessionInspector({ diagnostic: true, activeTab: "run", run: { run_id: "run-1", duration_ms: null } });
  assert.match(run, /本次使用/);
  assert.doesNotMatch(run, />0 ms</);
  assert.doesNotMatch(run, /data-diagnostic-run-logs|查看此轮日志/);
});

test("lazy trace does not erase saved per-run facts or borrow another run's model or usage", () => {
  const session = { run_id: "pending", model_name: "last-response-model", usage: { total_tokens: 900 }, runs: [
    { run_id: "earlier", model_name: "earlier-model", usage: { input_tokens: 3, output_tokens: 2, total_tokens: 5 } },
    { run_id: "pending", status: "running" },
  ] };
  const current = diagnosticRunPresentation(session);
  assert.equal(current.model.name, "last-response-model");
  assert.equal(current.runModel, null);
  assert.deepEqual(current.usage, {});
  const prior = diagnosticRunPresentation(session, [], "earlier");
  assert.equal(prior.runModel.name, "earlier-model");
  assert.equal(prior.usage.total_tokens, 5);
  const html = renderSessionInspector({ diagnostic: true, activeTab: "run", ...current });
  assert.match(html, /未记录/);
  assert.doesNotMatch(html, /last-response-model/);
});

// Tiny structural DOM for the actual presenter (not a mocked presenter).
// Selectors here are deliberately restricted to the subset used in these tests.
class Element {
  constructor(tag = "div") { this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null; this.attributes = {}; this.dataset = {}; this.listeners = new Map(); this._text = ""; }
  get className() { return this.attributes.class || ""; }
  set className(value) { this.attributes.class = value; }
  get classList() { const el = this; return { contains: name => el.className.split(/\s+/).includes(name), add(...names) { el.className = [...new Set([...el.className.split(/\s+/), ...names])].join(" ").trim(); }, remove(...names) { el.className = el.className.split(/\s+/).filter(name => !names.includes(name)).join(" "); }, toggle(name, value) { const next = value ?? !this.contains(name); next ? this.add(name) : this.remove(name); return next; } }; }
  setAttribute(key, value) { this.attributes[key] = String(value); if (key.startsWith("data-")) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  addEventListener(name, fn) { this.listeners.set(name, [...(this.listeners.get(name) || []), fn]); }
  appendChild(node) { node.remove(); node.parentNode = this; this.children.push(node); return node; }
  append(...nodes) { nodes.forEach(node => this.appendChild(node)); }
  insertBefore(node, sibling) { node.remove(); node.parentNode = this; this.children.splice(sibling ? this.children.indexOf(sibling) : this.children.length, 0, node); }
  remove() { if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); this.parentNode = null; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set textContent(value) { this.children = []; this._text = String(value); }
  set innerHTML(html) {
    this.children = []; this._text = ""; this._html = html;
    const stack = [this];
    for (const match of String(html).matchAll(/<\/?([\w-]+)([^>]*)>|([^<]+)/g)) {
      if (match[3]) { stack.at(-1)._text += match[3]; continue; }
      if (match[0].startsWith("</")) { if (stack.length > 1) stack.pop(); continue; }
      const child = new Element(match[1]);
      for (const attr of match[2].matchAll(/([\w-]+)="([^"]*)"/g)) child.setAttribute(attr[1], attr[2]);
      stack.at(-1).appendChild(child); if (!["input", "img", "br"].includes(match[1])) stack.push(child);
    }
  }
  get innerHTML() { return this._html || ""; }
  querySelectorAll(selector) {
    const matches = el => selector.split(",").some(part => {
      const simple = part.trim(); if (simple.startsWith(".")) return simple.slice(1).split(".").every(name => el.classList.contains(name));
      if (simple.startsWith("[")) { const [, name, value] = simple.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/) || []; return name && (el.getAttribute(name) !== null || name.startsWith("data-") && el.dataset[name.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] != null) && (value == null || el.getAttribute(name) === value); }
      return el.tagName.toLowerCase() === simple;
    });
    const found = []; const visit = el => { for (const child of el.children) { if (matches(child)) found.push(child); visit(child); } }; visit(this); return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

test("readonly pending tool renders saved request without initializing widgets, timers or callbacks", () => {
  const host = new Element(); let mutations = 0; let intervals = 0;
  const presenter = createConversationPresenter({ messages: host, readOnly: true,
    document: { createElement: tag => new Element(tag) },
    window: { requestAnimationFrame: fn => fn(), setInterval: () => { intervals += 1; }, clearInterval() {} },
    isUiToolCall: () => true, maybeAttachHitlWidget: () => { mutations += 1; }, renderHitlWidget: () => { mutations += 1; }, resumeDeferred: () => { mutations += 1; },
  });
  const assistant = presenter.createAssistantMessage("2026-09-09T00:00:00Z", 2);
  const actions = assistantActionsFromHistoryItems([{ kind: "tool", item_id: "tool:1", tool_name: "builtin__confirm", call_id: "1", input: { message: "Confirm?" }, widget: "confirm", status: "awaiting_input" }]);
  applyAssistantPresentationActions(assistant, actions);
  assistant.resumeProcess();
  assert.equal(mutations, 0);
  assert.equal(intervals, 0);
  assert.equal(host.querySelectorAll(".hitl-widget").length, 0);
  assert.match(host.textContent, /等待操作 · 只读/);
  assert.equal(host.querySelectorAll(".tool-block").length, 1);
  assert.match(host.querySelector(".args").textContent, /Confirm\?/);
  assistant.dispose();
});

test("missing saved timestamps and corrupt metadata remain unknown, not current time or zero", () => {
  const host = new Element();
  const presenter = createConversationPresenter({ messages: host, readOnly: true,
    document: { createElement: tag => new Element(tag) }, window: { requestAnimationFrame: fn => fn() },
  });
  const row = presenter.addMessage("user", "Saved input", null, 1);
  assert.equal(row.querySelector("time").textContent, "—");
  assert.equal(row.querySelector("time").dateTime, "");
  const html = renderSessionInspector({ diagnostic: true, activeSession: { turnCount: null, messageCount: null, attachmentCount: null } });
  assert.match(html, /title="提问数"[^]*?未记录/);
  assert.match(html, /title="消息事件数"[^]*?未记录/);
  assert.match(html, /title="附件数"[^]*?未记录/);
});


test("session inspector shows persisted scope, never current test request headers", () => {
  const html = renderSessionInspector({
    state: { sessionId: "history" },
    activeSession: { appId: "original001", spaceId: "original" },
    context: { app: "current002", space: "current" }, identity: { user: "test-user" },
  });
  assert.match(html, /模型[\s\S]*App \/ Space[\s\S]*original001[\s\S]*original[\s\S]*会话统计/);
  assert.doesNotMatch(html, /current002|test-user|请求 Header/);
  const missing = renderSessionInspector({ context: { app: "current002" } });
  assert.doesNotMatch(missing, /current002/);
  assert.match(missing, /App \/ Space[\s\S]*—/);
});


test("empty fields retain their original labels and styles while values and zero remain intact", () => {
  const empty = renderSessionInspector({ state: { agentId: "internal-agent-id" }, activeSession: { appId: null, spaceId: "" } });
  assert.match(empty, /inspector-scope-value"><code>—<\/code>/);
  assert.doesNotMatch(empty, /internal-agent-id|console-empty-value|>No<|估算方式|estimator_source/);
  const filled = renderSessionInspector({ diagnostic: true, agentName: "Agent name", state: { agentId: "hidden-id" }, activeSession: { appId: "<app>", spaceId: "space", turnCount: 0 }, contextStatus: { estimator_source: "conservative_fallback" } });
  assert.match(filled, /&lt;app&gt;/);
  assert.match(filled, /<code>space<\/code>/);
  assert.match(filled, /title="提问数"><i[^]*?<\/i>0<\/span>/);
  assert.doesNotMatch(filled, /hidden-id|conservative_fallback/);
});
