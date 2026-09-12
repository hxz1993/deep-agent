import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import {
  parseDiagnosticRoute, diagnosticHash, sessionRange, sessionDrilldown,
  createDiagnosticNavigation, overviewFilters,
} from "../../../console/diagnostics-navigation.js";
import {
  buildSessionQuery, sessionQueryError, createSessionReader,
} from "../../../console/diagnostics-session-query.js";
import { escapeHtml, icon, localInput, selectHtml } from "../../../console/diagnostics-ui.js";
import { COLUMN_BOUNDS } from "../../../console/diagnostic-columns.js";

const source = await readFile(new URL("../../../console/diagnostics-sessions.js", import.meta.url), "utf8");
const uiSource = await readFile(new URL("../../../console/diagnostics-ui.js", import.meta.url), "utf8");
const centerSource = await readFile(new URL("../../../console/diagnostics-center.js", import.meta.url), "utf8");
const turn = () => new Promise(setImmediate);
const settle = async () => { await turn(); await turn(); };
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const filters = {
  period: "custom", start: "2026-09-01T00:00:00Z", end: "2026-09-08T00:00:00Z",
  timezone: "Asia/Shanghai", agent_id: "agent-a", user_id: "platform-owner", source: "channel",
};
const overview = () => ({
  metrics: { session_count: 5, question_count: 9, active_user_count: 2, error_session_count: 1 },
  trend: [{ date: "2026-09-03", start: "2026-09-02T16:00:00Z", end: "2026-09-03T16:00:00Z", question_count: 3 }],
  agents: [{ agent_id: "agent-a", agent_name: "Agent A", question_count: 9 }],
  active_users: [{ user_id: "platform-owner", question_count: 9 }],
  recent_questions: [{ session_id: "session-full-id", run_id: "run-question", event_id: "event-question", user_id: "platform-owner", agent_id: "agent-a", summary: "实际问题", created_at: "2026-09-03T01:00:00Z" }],
  recent_errors: [{ session_id: "session-full-id", run_id: "run-error", event_id: "event-error", agent_id: "agent-a", message: "工具连接不可用", created_at: "2026-09-03T01:01:00Z" }],
  users: { "platform-owner": { display_name: "平台账号", email: "owner@example.test" } },
});
const session = () => ({
  session_id: "session-full-id", user_id: "platform-owner", title: "真实会话", latest_status: "completed",
  created_at: "2026-09-03T01:00:00Z", last_activity_at: "2026-09-03T02:00:00Z",
  runs: [{ run_id: "run-question", started_at: "2026-09-03T01:00:00Z", finished_at: "2026-09-03T01:01:00Z", status: "completed" }],
});

function eventTarget(dataset = {}, extra = {}) {
  return { dataset, classList: { add() {}, remove() {} }, setAttribute() {}, closest() { return this; }, matches() { return false; }, ...extra };
}

function sessionHarness(request = async () => overview()) {
  const listeners = new Map(), documentListeners = new Map(), requests = [], navigations = [], replacements = [], views = [];
  const scroll = { scrollTop: 0 }, tableScroll = { scrollTop: 0 }, questionsScroll = { scrollTop: 0 }, activeUsers = { scrollTop: 0, scrolled: false, focused: false, scrollIntoView() { this.scrolled = true; }, focus() { this.focused = true; } };
  const root = {
    innerHTML: "", contains: () => true, attributes: {},
    addEventListener(name, handler) { const items = listeners.get(name) || []; items.push(handler); listeners.set(name, items); },
    querySelector(selector) { if (selector === ".diagnostic-session-scroll") return scroll; if (selector === ".diagnostic-session-table-scroll") return tableScroll; if (selector === ".diagnostic-question-list") return questionsScroll; if (selector === "#diagnosticActiveUsers") return activeUsers; return null; },
    setAttribute(name, value) { this.attributes[name] = value; },
    replaceChildren() { this.innerHTML = ""; },
  };
  let selectCallback;
  const copied = [], toasts = [];
  const context = vm.createContext({
    URL, URLSearchParams, Date, Intl, AbortController, setTimeout, clearTimeout,
    document: { baseURI: "https://console.example/console/", addEventListener(name, handler) { documentListeners.set(name, handler); } },
    window: { location: { href: "https://console.example/console/" }, DeepAgentConsole: { toast: (...args) => toasts.push(args) } },
    navigator: { clipboard: { writeText: async value => copied.push(value) } },
    FormData: class { constructor(form) { this.values = form.values; } [Symbol.iterator]() { return Object.entries(this.values)[Symbol.iterator](); } },
    e: escapeHtml, icon, shortId: value => value, icons() {}, localInput, selectHtml,
    bindDiagnosticSelects(_root, callback) { selectCallback = callback; return { dispose() {}, close() {} }; },
    diagnosticTime: value => value || "未记录", diagnosticHash, sessionRange, sessionDrilldown, overviewFilters,
    COLUMN_BOUNDS, bindDiagnosticColumns: () => () => {},
    buildSessionQuery, sessionQueryError, createSessionReader,
    diagnosticRequest: (path, signal) => { requests.push({ path, signal }); return request(path, signal); },
    createReadonlySessionView(options) {
      const view = { options, loads: [], errors: [], loading: [], destroyed: false,
        load(data) { this.loads.push(plain(data)); }, setLoading(value) { this.loading.push(value); },
        setError(value) { this.errors.push(value); }, destroy() { this.destroyed = true; },
        snapshot: () => ({ scroll: 44 }), restore(value) { this.restored = value; } };
      views.push(view); return view;
    },
  });
  vm.runInContext(source.replace(/^import .*;\n/gm, "").replace(/^export /gm, ""), context);
  const controller = context.initDiagnosticSessions({ root, navigate: hash => navigations.push(hash), replace: hash => replacements.push(hash), onBack() {} });
  return {
    controller, context, root, scroll, tableScroll, questionsScroll, activeUsers, requests, navigations, replacements, views, copied, toasts,
    activate(view = "overview", f = filters, saved = null) { controller.activate({ view, id: view === "detail" ? "session-full-id" : null, filters: { ...f } }, saved); },
    click(dataset) { const event = { target: eventTarget(dataset), stopPropagation() { this.stopped = true; }, preventDefault() {} }; return Promise.all((listeners.get("click") || []).map(fn => fn(event))); },
    submit(values, formName = "filters") { const target = eventTarget({ sessionForm: formName }, { values }); for (const fn of listeners.get("submit") || []) fn({ target, preventDefault() {} }); },
    select(key, value) { selectCallback(key, value); },
  };
}

function navigationHarness(initialHash = diagnosticHash("list", filters), initialState = null) {
  const entries = [{ hash: initialHash, state: initialState }]; let index = 0, snapshot = { scroll: 100 }, restored;
  const history = {
    get state() { return entries[index].state; },
    replaceState(state, _title, hash) { entries[index] = { state, hash }; },
    pushState(state, _title, hash) { entries.splice(index + 1); entries.push({ state, hash }); index += 1; },
    back() { if (index > 0) index -= 1; },
    forward() { if (index + 1 < entries.length) index += 1; },
  };
  const nav = createDiagnosticNavigation({ getHash: () => entries[index].hash, history,
    getSnapshot: () => snapshot, onNavigate() { restored = nav.enter(); } });
  return { nav, history, entries, get hash() { return entries[index].hash; }, get restored() { return restored; },
    set snapshot(value) { snapshot = value; }, pop() { restored = nav.enter(); return restored; } };
}

function centerHarness(initialHash = diagnosticHash("logs", { q: "original", level: "ERROR" })) {
  const navigation = navigationHarness(initialHash), handlers = new Map(), navCalls = [];
  const node = () => ({ classList: { toggle() {} }, setAttribute() {}, removeAttribute() {} });
  const logs = { activated: [], saved: { scroll: 88, selectedId: "log-record" }, snapshot() { return this.saved; }, activate(...args) { this.activated.push(args); }, deactivate() {}, clear() { this.saved = null; } };
  const sessions = { activated: [], saved: { scroll: 321, filters: { ...filters, offset: 50 } }, snapshot() { return this.saved; }, activate(...args) { this.activated.push(args); }, deactivate() {}, clear() { this.saved = null; } };
  const context = vm.createContext({
    location: { get hash() { return navigation.hash; } }, history: navigation.history,
    document: { getElementById: () => node(), querySelectorAll: () => [] },
    window: { addEventListener(name, callback) { handlers.set(name, callback); }, dispatchEvent() {}, DeepAgentConsole: { showPage(page, options) { navCalls.push({ page, options }); } } },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    createDiagnosticNavigation, diagnosticHash, parseDiagnosticRoute,
    initDiagnosticLogs(options) { logs.options = options; return logs; },
    initDiagnosticSessions(options) { sessions.options = options; return sessions; },
  });
  vm.runInContext(centerSource.replace(/^import .*;\n/gm, "").replace(/^export /gm, ""), context);
  context.initDiagnostics();
  return { context, logs, sessions, navigation, handlers, navCalls };
}

test("diagnostic routes accept only internal views and preserve complete IDs without content or credentials", () => {
  assert.equal(parseDiagnosticRoute("#/diagnostics").view, "logs");
  assert.equal(parseDiagnosticRoute("#/diagnostics/sessions").view, "overview");
  for (const hash of ["https://evil.example", "#/workspace", "#/diagnostics/logs/extra", "#/diagnostics/sessions/%2fetc", "#/diagnostics/sessions/%00", "#/diagnostics/sessions/%ZZ"]) assert.equal(parseDiagnosticRoute(hash), null);
  const route = parseDiagnosticRoute(diagnosticHash("detail", { view: "trajectory", event_id: "e&1", run_id: "r+1", token: "secret", prompt: "private", return: "https://evil.example" }, "session:exact-ID"));
  assert.equal(route.id, "session:exact-ID");
  assert.deepEqual(route.filters, { view: "trajectory", event_id: "e&1", run_id: "r+1" });
});

test("session range defaults to seven days, freezes supplied UTC range, enforces 31 days and half-open order", () => {
  const now = new Date("2026-09-09T08:00:00Z"), first = sessionRange({}, now);
  assert.equal(first.start, "2026-09-02T08:00:00.000Z");
  assert.equal(first.end, now.toISOString());
  assert.deepEqual(sessionRange(first, new Date("2026-09-10T08:00:00Z")), first);
  assert.equal(sessionRange({ ...filters, end: "2026-10-02T00:00:00Z" }).end, "2026-10-02T00:00:00.000Z");
  assert.throws(() => sessionRange({ ...filters, end: "2026-10-02T00:00:01Z" }), /31 天/);
  for (const values of [{ start: "bad" }, { start: first.end, end: first.start }, { start: first.end, end: first.end }]) assert.throws(() => sessionRange(values), /早于/);
});

test("overview requests aggregate only common filters; list filters are encoded literally", () => {
  const f = { ...filters, query: "name_%<>&", has_questions: true, has_execution_error: true, latest_status: "error", limit: 50, offset: 100, token: "secret", path: "/etc/passwd" };
  const q = new URLSearchParams(buildSessionQuery(f)), overviewQuery = new URLSearchParams(buildSessionQuery(f, true));
  assert.equal(q.get("query"), "name_%<>&");
  assert.equal(q.get("user_id"), "platform-owner");
  assert.equal(q.get("offset"), "100");
  for (const key of ["query", "has_questions", "has_execution_error", "latest_status", "limit", "offset", "period", "token", "path"]) assert.equal(overviewQuery.has(key), false);
  for (const key of ["period", "token", "path"]) assert.equal(q.has(key), false);
});

test("drilldowns preserve public filters but remove unrelated list search/status/pagination", () => {
  const f = { ...filters, query: "previous", latest_status: "error", offset: 150, limit: 100 };
  assert.deepEqual(sessionDrilldown(f, { has_questions: true }), { ...filters, has_questions: true, offset: 0 });
  assert.equal(sessionDrilldown(f, { agent_id: "other-agent" }).agent_id, "other-agent");
  assert.equal(sessionDrilldown(f, { user_id: "other-platform-user" }).user_id, "other-platform-user");
});

test("back and forward restore original filters, page and scroll using memory-only snapshots", () => {
  const h = navigationHarness(); h.nav.enter();
  h.snapshot = { filters: { ...filters, offset: 100 }, scroll: 321 };
  h.nav.navigate(diagnosticHash("detail", { run_id: "run-question" }, "session-full-id"));
  h.snapshot = { detailSnapshot: { scroll: 44 } };
  h.nav.back(diagnosticHash("overview")); h.pop();
  assert.equal(h.restored.scroll, 321); assert.equal(h.restored.filters.offset, 100);
  assert.equal(h.hash, diagnosticHash("list", filters));
  h.history.forward(); h.pop(); assert.equal(h.restored.detailSnapshot.scroll, 44);
  assert.equal(JSON.stringify(h.entries).includes("321"), false, "scroll and response bodies are not browser history state");
});

test("navigation does not collide with a history key preserved across reload", () => {
  const h = navigationHarness(diagnosticHash("list", filters), { diagnosticsKey: "diagnostics-1" }); h.nav.enter();
  h.snapshot = { scroll: 321 };
  h.nav.navigate(diagnosticHash("detail", {}, "session-full-id"));
  assert.notEqual(h.entries[0].state.diagnosticsKey, h.entries[1].state.diagnosticsKey);
  h.snapshot = { scroll: 44 }; h.nav.back(diagnosticHash("overview")); h.pop();
  assert.equal(h.restored.scroll, 321);
});

test("external return addresses are rejected and direct detail fallback remains inside Console", () => {
  const h = navigationHarness(diagnosticHash("detail", {}, "session-full-id"), { diagnosticsFrom: "https://evil.example" }); h.nav.enter();
  assert.equal(h.nav.navigate("https://evil.example"), false);
  h.nav.back(diagnosticHash("overview"));
  assert.equal(parseDiagnosticRoute(h.hash).view, "overview");
});

test("center log-to-session link carries only Session ID and Back restores selected log plus scroll", () => {
  const h = centerHarness();
  assert.equal(h.logs.activated.length, 0, "initialization does not prefetch inactive diagnostics");
  h.context.setDiagnosticsActive(true);
  h.logs.options.onOpenSession("session-exact-id", "run-exact-id");
  let route = h.sessions.activated.at(-1)[0];
  assert.equal(route.id, "session-exact-id"); assert.equal(route.filters.run_id, undefined);
  assert.equal(route.filters.view, "conversation");
  h.sessions.options.onBack(); h.context.setDiagnosticsActive(true);
  const [, saved] = h.logs.activated.at(-1);
  assert.equal(saved.selectedId, "log-record"); assert.equal(saved.scroll, 88);
  route = parseDiagnosticRoute(h.navigation.hash); assert.equal(route.filters.q, "original"); assert.equal(route.filters.level, "ERROR");
});

test("center session-to-log and browser Back preserve the session-list page and filters", () => {
  const h = centerHarness(diagnosticHash("list", { ...filters, offset: 50 })); h.context.setDiagnosticsActive(true);
  h.sessions.options.navigate(diagnosticHash("detail", { view: "trajectory", event_id: "event-error" }, "session-full-id"));
  h.sessions.saved = { detailSnapshot: { view: "trajectory", scroll: 55 } };
  h.sessions.options.navigate(diagnosticHash("logs", { session_id: "session-full-id", run_id: "run-question" }));
  assert.equal(h.logs.activated.at(-1)[0].run_id, undefined);
  assert.equal(h.logs.activated.at(-1)[0].q, undefined, "cross-link drops unrelated prior log keywords");
  h.logs.options.onBack(); h.context.setDiagnosticsActive(true);
  assert.equal(h.sessions.activated.at(-1)[1].detailSnapshot.scroll, 55);
  h.navigation.history.back(); h.context.setDiagnosticsActive(true);
  assert.equal(h.sessions.activated.at(-1)[0].view, "list");
  assert.equal(h.sessions.activated.at(-1)[1].filters.offset, 50);
  assert.equal(h.sessions.activated.at(-1)[1].scroll, 321);
});

test("center keeps last session view for menu entry and routes via existing unsaved-editor gate", () => {
  const h = centerHarness(diagnosticHash("list", { ...filters, query: "name", offset: 50 })); h.context.setDiagnosticsActive(true);
  h.sessions.options.navigate(diagnosticHash("logs", {}));
  h.context.openDiagnostics("sessions");
  assert.equal(h.navCalls.at(-1).page, "diagnostics");
  const route = parseDiagnosticRoute(h.navCalls.at(-1).options.diagnosticTarget);
  assert.equal(route.view, "list"); assert.equal(route.filters.query, "name"); assert.equal(route.filters.offset, "50");
});

test("permission loss clears both page snapshots and disables further activation until gate succeeds", () => {
  const h = centerHarness(); h.context.setDiagnosticsActive(true);
  h.handlers.get("deep-agent-diagnostics-denied")({ detail: { status: 403 } });
  assert.equal(h.logs.saved, null); assert.equal(h.sessions.saved, null);
});

test("legacy log Run ID URLs are ignored, while actual session event定位 remains available", () => {
  const route = parseDiagnosticRoute("#/diagnostics/logs?session_id=full-id&run_id=old-hidden-filter");
  assert.deepEqual(route.filters, { session_id: "full-id" });
  assert.doesNotMatch(diagnosticHash("logs", { session_id: "full-id", run_id: "hidden" }), /run_id/);
});

test("reader cancels old generation and never accepts its late event results", async () => {
  const gate = deferred(), requests = [], reader = createSessionReader((path, signal) => { requests.push({ path, signal }); return gate.promise; });
  const first = reader.begin(), pending = reader.readEvents(first, "old-session");
  const second = reader.begin();
  assert.equal(first.signal.aborted, true); assert.equal(second.current(), true);
  gate.resolve({ items: [{ event_id: "late", sequence: 1 }], next_after_sequence: null });
  assert.equal(await pending, null); reader.cancel(); assert.equal(second.current(), false);
});

test("events load all pages at 500 with progress, deduplicate IDs and retain order", async () => {
  const pages = [{ items: [{ event_id: "a", sequence: 1 }], next_after_sequence: 1 }, { items: [{ event_id: "a", sequence: 1 }, { event_id: "b", sequence: 2 }], next_after_sequence: null }], paths = [], progress = [];
  const reader = createSessionReader(async path => { paths.push(path); return pages.shift(); });
  const items = await reader.readEvents(reader.begin(), "session:full&ID", (...args) => progress.push(args));
  assert.deepEqual(items.map(item => item.event_id), ["a", "b"]);
  assert.deepEqual(progress, [[1, false], [2, true]]);
  assert.match(paths[0], /session%3Afull%26ID\/events\?limit=500$/);
  assert.match(paths[1], /after_sequence=1/);
});

test("invalid or stationary event cursor fails visibly instead of looping or claiming completion", async () => {
  for (const pages of [ [{ items: null }], [{ items: [], next_after_sequence: 1 }, { items: [], next_after_sequence: 1 }] ]) {
    const reader = createSessionReader(async () => pages.shift());
    await assert.rejects(reader.readEvents(reader.begin(), "session"), /Invalid events|cursor did not advance/);
  }
});

test("query errors distinguish login, permission, deleted object, history, input and availability", () => {
  const messages = [401, 403, 404, 409, 422, 503, 0].map(status => sessionQueryError({ status }));
  assert.equal(new Set(messages).size, 7);
  assert.match(messages[2], /不存在或已删除/); assert.match(messages[3], /轨迹/);
});

test("overview session, question and exception metrics drill to correctly labelled session filters", async () => {
  const h = sessionHarness(); h.activate(); await settle();
  for (const [key, expected] of [["session_count", {}], ["question_count", { has_questions: "true" }], ["error_session_count", { has_execution_error: "true" }]]) {
    await h.click({ stat: key }); const route = parseDiagnosticRoute(h.navigations.at(-1));
    assert.equal(route.view, "list"); assert.equal(route.filters.user_id, undefined); assert.equal(route.filters.source, "channel");
    for (const [name, value] of Object.entries(expected)) assert.equal(route.filters[name], value);
  }
});

test("active-user metric expands and focuses this page instead of navigating", async () => {
  const h = sessionHarness(); h.activate(); await settle(); await h.click({ stat: "active_user_count" });
  assert.equal(h.navigations.length, 0); assert.equal(h.controller.snapshot().showUsers, true);
  assert.equal(h.activeUsers.scrolled, true); assert.equal(h.activeUsers.focused, true);
});

test("trend, Agent and platform-account drilldowns preserve other conditions", async () => {
  const h = sessionHarness(); h.activate(); await settle();
  await h.click({ dayIndex: "0" }); let route = parseDiagnosticRoute(h.navigations.at(-1));
  assert.equal(route.filters.start, overview().trend[0].start); assert.equal(route.filters.end, overview().trend[0].end);
  assert.equal(route.filters.has_questions, "true"); assert.equal(route.filters.agent_id, "agent-a");
  await h.click({ agentDrill: "agent-b" }); route = parseDiagnosticRoute(h.navigations.at(-1));
  assert.equal(route.filters.agent_id, "agent-b"); assert.equal(route.filters.user_id, undefined);
  await h.click({ userDrill: "platform-other" }); route = parseDiagnosticRoute(h.navigations.at(-1));
  assert.equal(route.filters.user_id, "platform-other"); assert.equal(route.filters.agent_id, "agent-a");
});

test("recent questions View all opens filtered list while exact question/error opens exact detail event", async () => {
  const h = sessionHarness(); h.activate(); await settle();
  await h.click({ sessionAction: "all-questions" }); let route = parseDiagnosticRoute(h.navigations.at(-1));
  assert.equal(route.view, "list"); assert.equal(route.filters.has_questions, "true");
  await h.click({ questionIndex: "0" }); route = parseDiagnosticRoute(h.navigations.at(-1));
  assert.equal(route.id, "session-full-id"); assert.deepEqual(route.filters, { view: "conversation", event_id: "event-question", run_id: "run-question" });
  await h.click({ errorIndex: "0" }); route = parseDiagnosticRoute(h.navigations.at(-1));
  assert.deepEqual(route.filters, { view: "trajectory", event_id: "event-error", run_id: "run-error" });
});

test("overview ignores list filters, list restores them; copying a full Session ID never navigates", async () => {
  const h = sessionHarness(); h.activate("list", { ...filters, query: "literal_%", offset: 100 }); await settle();
  await h.click({ sessionView: "overview" }); const route = parseDiagnosticRoute(h.navigations.at(-1));
  assert.equal(route.view, "overview"); assert.equal(route.filters.user_id, undefined); assert.equal(route.filters.query, undefined);
  h.activate("overview", route.filters); await settle();
  assert.doesNotMatch(h.root.innerHTML, /name="user_id"|name="query"/);
  await h.click({ sessionView: "list" });
  const restored = parseDiagnosticRoute(h.navigations.at(-1));
  assert.equal(restored.filters.user_id, "platform-owner"); assert.equal(restored.filters.query, "literal_%");
  const count = h.navigations.length; await h.click({ copySession: "full-untruncated-session-id" });
  assert.deepEqual(h.copied, ["full-untruncated-session-id"]); assert.equal(h.navigations.length, count);
  assert.equal(h.toasts.at(-1)[0], "会话 ID 已复制");
  await h.click({ sessionId: "full-untruncated-session-id" });
  assert.equal(parseDiagnosticRoute(h.navigations.at(-1)).id, "full-untruncated-session-id");
});

test("list pagination fixes UTC range and changing query/filter resets offset before server query", async () => {
  const h = sessionHarness(async () => ({ items: [], total: 150 })); h.activate("list"); await settle();
  const first = new URLSearchParams(h.requests[0].path.split("?")[1]);
  await h.click({ sessionAction: "next" }); await settle();
  let query = new URLSearchParams(h.requests.at(-1).path.split("?")[1]);
  assert.equal(query.get("offset"), "50"); assert.equal(query.get("start"), first.get("start")); assert.equal(query.get("end"), first.get("end"));
  h.submit({ query: "name_%" }, "search"); await settle();
  query = new URLSearchParams(h.requests.at(-1).path.split("?")[1]); assert.equal(query.get("offset"), "0"); assert.equal(query.get("query"), "name_%");
  h.select("agent_id", "agent-b"); await settle(); query = new URLSearchParams(h.requests.at(-1).path.split("?")[1]);
  assert.equal(query.get("agent_id"), "agent-b"); assert.equal(query.get("query"), "name_%"); assert.equal(query.get("offset"), "0");
});

test("removing a drilldown chip performs a new full query rather than filtering loaded subset", async () => {
  const h = sessionHarness(); h.activate("list", { ...filters, has_questions: "true", has_execution_error: "true", offset: 100 }); await settle();
  await h.click({ clearSession: "has_execution_error" }); await settle();
  const query = new URLSearchParams(h.requests.at(-1).path.split("?")[1]);
  assert.equal(h.requests.length, 2); assert.equal(query.has("has_execution_error"), false); assert.equal(query.get("has_questions"), "true"); assert.equal(query.get("offset"), "0");
});

test("invalid time shows a local error and never runs an aggregate request", async () => {
  const h = sessionHarness(); h.activate("overview", { ...filters, end: "2026-11-01T00:00:00Z" }); await settle();
  assert.equal(h.requests.length, 0); assert.match(h.root.innerHTML, /31 天/);
});

test("saved list snapshot restores page, results and scroll without a new request", async () => {
  const h = sessionHarness(); const saved = { filters: { ...filters, offset: 100, limit: 50 }, data: { items: [], total: 150 }, showUsers: false, showAgents: false, scroll: 444, tableScroll: 300 };
  h.activate("list", filters, saved); await settle();
  assert.equal(h.requests.length, 0); assert.equal(h.scroll.scrollTop, 444); assert.equal(h.controller.snapshot().filters.offset, 100);
  assert.equal(h.tableScroll.scrollTop, 300); assert.equal(h.controller.snapshot().tableScroll, 300);
});

test("inner dashboard scroll survives local rerenders, while a new query resets all scroll regions", async () => {
  const h = sessionHarness(); h.activate(); await settle();
  h.questionsScroll.scrollTop = 210; h.activeUsers.scrollTop = 60;
  await h.click({ sessionAction: "users" });
  assert.equal(h.controller.snapshot().questionsScroll, 210);
  assert.equal(h.controller.snapshot().usersScroll, 60);
  h.tableScroll.scrollTop = 300;
  h.select("agent_id", "agent-b"); await settle();
  assert.equal(h.controller.snapshot().questionsScroll, 0);
  assert.equal(h.controller.snapshot().tableScroll, 0);
  assert.equal(h.controller.snapshot().usersScroll, 0);
});

test("loading status lives in header, outside the list controls and rows", async () => {
  const gate = deferred(), h = sessionHarness(() => gate.promise);
  h.activate("list");
  assert.match(h.root.innerHTML, /diagnostic-filter-secondary[\s\S]*class="diagnostic-loading"[^>]*>正在加载…<\/span>[\s\S]*<\/header>/);
  assert.doesNotMatch(h.root.innerHTML.split('</header>')[1], /diagnostic-loading/);
  gate.resolve({ total: 0, items: [] }); await settle();
  assert.doesNotMatch(h.root.innerHTML, /正在加载…/);
});

test("new session query wins over late response and deactivation ignores all late data", async () => {
  const gates = [deferred(), deferred()]; let index = 0;
  const h = sessionHarness(() => gates[index++].promise); h.activate("overview");
  h.activate("list", { ...filters, user_id: "new-owner" });
  assert.equal(h.requests[0].signal.aborted, true);
  gates[1].resolve({ items: [], total: 2 }); await settle(); gates[0].resolve(overview()); await settle();
  assert.equal(h.controller.snapshot().data.total, 2);
  const late = deferred(), h2 = sessionHarness(() => late.promise); h2.activate(); h2.controller.deactivate();
  assert.equal(h2.requests[0].signal.aborted, true); late.resolve(overview()); await settle();
  assert.equal(h2.controller.snapshot().data, null);
});

test("detail loads admin metadata/history/events only and passes exact event/run focus", async () => {
  const h = sessionHarness(async path => path.includes("/history") ? { items: [{ id: "question" }] } : path.includes("/events") ? { items: [{ event_id: "event-question", sequence: 1 }], next_after_sequence: null } : session());
  h.activate("detail", { view: "trajectory", event_id: "event-question", run_id: "run-question" }); await settle();
  assert.equal(h.requests.length, 4); assert.ok(h.requests.every(call => call.path.startsWith("api/v1/diagnostics/sessions/session-full-id")));
  const data = h.views[0].loads.at(-1); assert.deepEqual(data.focus, { view: "trajectory", eventId: "event-question", runId: "run-question" });
  assert.equal(data.events[0].event_id, "event-question"); assert.equal(data.history.items[0].id, "question");
});

test("readonly detail does not receive a new linked-log action", async () => {
  const h = sessionHarness(async path => path.includes("/history") ? { items: [] } : path.includes("/events") ? { items: [], next_after_sequence: null } : session());
  h.activate("detail", {}); await settle();
  assert.equal(h.views[0].options.onOpenLogs, undefined);
  assert.equal(h.navigations.length, 0);
});

test("history projection failure preserves successfully loaded trajectory and explicit error", async () => {
  const h = sessionHarness(async path => { if (path.includes("/history")) throw { status: 409 }; return path.includes("/events") ? { items: [{ event_id: "error-event" }], next_after_sequence: null } : session(); });
  h.activate("detail", { view: "trajectory" }); await settle();
  assert.equal(h.views[0].loads.at(-1).events[0].event_id, "error-event");
  assert.match(JSON.stringify(h.views[0].errors), /轨迹/);
});

test("context estimation waits for history and does not cancel still-loading events", async () => {
  const historyGate = deferred(), eventsGate = deferred();
  const h = sessionHarness(async path => path.includes("/history") ? historyGate.promise : path.includes("/events") ? eventsGate.promise : session());
  h.activate("detail", {}); await settle();
  h.views[0].options.onViewChange("trajectory"); await settle();
  assert.equal(h.requests.some(call => call.path.includes("include_context")), false);
  assert.equal(h.requests.find(call => call.path.includes("/history")).signal.aborted, false);
  historyGate.resolve({ items: [{ id: "loaded-history" }] }); await settle();
  assert.equal(h.requests.filter(call => call.path.includes("include_context")).length, 1);
  assert.equal(h.requests.find(call => call.path.includes("/events")).signal.aborted, false);
  eventsGate.resolve({ items: [{ event_id: "loaded-event" }], next_after_sequence: null }); await settle();
  assert.equal(h.views[0].loads.at(-1).history.items[0].id, "loaded-history");
});

test("ordinary detail defers trajectory until selected, then reuses fully loaded events", async () => {
  const h = sessionHarness(async path => path.includes("/history") ? { items: [] } : path.includes("/events") ? { items: [{ event_id: "saved-event" }], next_after_sequence: null } : session());
  h.activate("detail", {}); await settle();
  assert.equal(h.requests.length, 3); assert.ok(h.requests.every(call => !call.path.includes("/events")));
  assert.equal(h.requests.filter(call => call.path.includes("include_context=true")).length, 1);
  h.views[0].options.onViewChange("trajectory"); await settle();
  assert.equal(h.requests.length, 4); assert.equal(h.views[0].loads.at(-1).events[0].event_id, "saved-event");
  h.views[0].options.onViewChange("conversation"); h.views[0].options.onViewChange("trajectory"); await settle();
  assert.equal(h.requests.length, 4);
});

test("leaving or refreshing detail aborts its pending context estimate", async () => {
  for (const action of ["deactivate", "clear", "refresh", "reactivate"]) {
    const gate = deferred(); let contextReads = 0;
    const h = sessionHarness(async path => path.includes("include_context") ? (++contextReads === 1 ? gate.promise : session()) : path.includes("/history") ? { items: [] } : path.includes("/events") ? { items: [], next_after_sequence: null } : session());
    h.activate("detail", {}); await settle();
    if (action === "refresh") h.views[0].options.onRefresh();
    else if (action === "reactivate") h.activate("detail", {});
    else h.controller[action]();
    const request = h.requests.find(call => call.path.includes("include_context"));
    assert.equal(request.signal.aborted, true, action);
    gate.resolve({ ...session(), title: "stale context must not win" }); await settle();
    if (["refresh", "reactivate"].includes(action)) assert.notEqual(h.views.at(-1).loads.at(-1)?.session.title, "stale context must not win");
  }
});

test("changing detail view while history loads is retained when the response arrives", async () => {
  const gate = deferred();
  const h = sessionHarness(async path => path.includes("/history") ? gate.promise : path.includes("/events") ? { items: [], next_after_sequence: null } : session());
  h.activate("detail", { view: "conversation" }); await settle();
  h.views[0].options.onViewChange("trajectory");
  gate.resolve({ items: [] }); await settle();
  assert.equal(h.views[0].loads.at(-1).focus.view, "trajectory");
});

test("context response retains current tab even after switching away from a loaded trajectory", async () => {
  const gate = deferred();
  const h = sessionHarness(async path => path.includes("include_context") ? gate.promise : path.includes("/history") ? { items: [] } : path.includes("/events") ? { items: [], next_after_sequence: null } : session());
  h.activate("detail", { view: "trajectory" }); await settle();
  h.views[0].options.onViewChange("conversation");
  gate.resolve(session()); await settle();
  assert.equal(h.views[0].loads.at(-1).focus.view, "conversation");
});

test("switching detail session ignores old metadata and never cancels actual Agent execution", async () => {
  const old = deferred();
  const h = sessionHarness(path => path.includes("session-full-id") ? old.promise : Promise.resolve(path.includes("/events") ? { items: [], next_after_sequence: null } : path.includes("/history") ? { items: [] } : { ...session(), session_id: "new-session" }));
  h.activate("detail", {}); h.controller.activate({ view: "detail", id: "new-session", filters: {} });
  old.resolve(session()); await settle();
  assert.equal(h.views[0].destroyed, true); assert.equal(h.views[0].loads.length, 0);
  assert.equal(h.views[1].loads.at(-1).session.session_id, "new-session");
  assert.ok(h.requests.every(call => !/\/cancel|\/chat|\/live/.test(call.path)));
});

test("username and chart labels escape content without rendering avatar or email", () => {
  const h = sessionHarness(); const html = h.context.userCardHtml("owner", { owner: { display_name: '<img onerror="bad">', email: "<script>", avatar_url: "https://avatars.example/a.png" } });
  assert.doesNotMatch(html, /<script>|<img |<small|<svg/); assert.match(html, /&lt;img onerror/);
  assert.doesNotMatch(h.context.userCardHtml("owner", { owner: { avatar_url: "javascript:alert(1)" } }), /<img /);
  const chart = h.context.questionTrendHtml([{ date: "<svg>", question_count: 4 }]); assert.match(chart, /&lt;svg&gt;/); assert.doesNotMatch(chart, /aria-label="<svg>/);
});

test("user display contains only resolved username, with real ID fallback", () => {
  const h = sessionHarness(); const html = h.context.userCardHtml("owner", { owner: { avatar_url: "/avatars/owner.png", display_name: "Owner", email: "owner@example.test" } });
  assert.match(html, />Owner<\/span>/);
  assert.doesNotMatch(html, /<img |avatars|example.test|diagnostic-avatar/);
  assert.match(h.context.userCardHtml("real-user-id"), />real-user-id<\/span>/);
});

test("overview matches image workbench: separate metrics, question feed, username-only metadata and chart grid", async () => {
  const h = sessionHarness(); h.activate(); await settle();
  assert.match(h.root.innerHTML, /diagnostic-question-list/);
  assert.match(h.root.innerHTML, /diagnostic-question-icon/);
  assert.match(h.root.innerHTML, /diagnostic-question-meta/);
  assert.equal((h.root.innerHTML.match(/data-stat=/g) || []).length, 4);
  assert.match(h.root.innerHTML, /<h2>活跃用户<\/h2>/);
  assert.doesNotMatch(h.root.innerHTML, /diagnostic-avatar|owner@example.test|diagnostic-question-columns/);
  assert.doesNotMatch(h.root.innerHTML, /diagnostic-eyebrow|DIAGNOSTICS/);
  const chart = h.context.questionTrendHtml([{ date: "2026-09-01", question_count: 1 }]);
  assert.equal((chart.match(/class="diagnostic-chart-grid"/g) || []).length, 4);
  assert.match(chart, /diagnostic-chart-axis/); assert.match(chart, /diagnostic-chart-area/);
});

test("trend recomputes its horizontal geometry without scaling point radius or vertical coordinates", () => {
  const h = sessionHarness(), bins = [{ date: "2026-09-01", question_count: 6 }];
  for (const width of [400, 800, 1200]) {
    const chart = h.context.questionTrendHtml(bins, width);
    assert.match(chart, new RegExp(`viewBox="0 0 ${width} 184"`));
    assert.match(chart, new RegExp(`cx="${width / 2 + 8}" cy="52" r="4.5"`));
    assert.doesNotMatch(chart, /preserveAspectRatio="none"/);
  }
});

test("session Query refreshes relative windows but preserves unchanged custom timestamp precision", async () => {
  const h = sessionHarness(); h.activate("list", { ...filters, period: "168" }); await settle();
  h.submit({ user_id: "platform-owner" }); await settle();
  const query = new URL(h.requests.at(-1).path, "http://local").searchParams;
  assert.notEqual(query.get("end"), filters.end);
  const exact = { ...filters, start: "2026-09-01T00:00:12.345Z", end: "2026-09-08T00:00:34.567Z" };
  h.activate("overview", exact); await settle();
  h.submit({ start: localInput(exact.start), end: localInput(exact.end) }); await settle();
  const custom = new URL(h.requests.at(-1).path, "http://local").searchParams;
  assert.equal(custom.get("start"), exact.start); assert.equal(custom.get("end"), exact.end);
  assert.match(h.root.innerHTML, /diagnostics-custom-time[\s\S]*>应用<\/button>[\s\S]*agent-view-switch/);
  assert.match(h.root.innerHTML, /form="diagnosticSessionFilters" type="datetime-local"/);
  assert.match(h.root.innerHTML, /data-session-action="reset"[^>]*>重置<\/button>/);
  assert.doesNotMatch(h.root.innerHTML, /重置筛选/);
});

test("compact pagination keeps fixed filters and rejects out-of-range pages", async () => {
  const h = sessionHarness(async () => ({ total: 128, items: [] })); h.activate("list", { ...filters, offset: 0, limit: 50 }); await settle();
  assert.match(h.root.innerHTML, /第 1 页，共 3 页/);
  assert.doesNotMatch(h.root.innerHTML, /diagnostic-page-range|diagnostic-page-numbers/);
  await h.click({ sessionPage: "2" }); await settle();
  let params = new URL(h.requests.at(-1).path, "http://local").searchParams;
  assert.equal(params.get("offset"), "50"); assert.equal(params.get("user_id"), "platform-owner");
  const count = h.requests.length;
  await h.click({ sessionPage: "9" }); await h.click({ sessionPage: "NaN" });
  assert.equal(h.requests.length, count);
});

test("pagination retains the server total while loading either direction and ignores a superseded page", async () => {
  const next = deferred(), previous = deferred(), stale = deferred();
  const replies = [{ total: 128, items: [] }, next.promise, previous.promise, stale.promise, { total: 8, items: [] }];
  const h = sessionHarness(() => replies.shift()); h.activate("list"); await settle();
  assert.match(h.root.innerHTML, /第 1 \/ 3 页 · 共 128 项/);
  await h.click({ sessionAction: "next" });
  assert.match(h.root.innerHTML, /第 2 \/ 3 页 · 共 128 项/);
  assert.equal(h.root.attributes["aria-busy"], "true");
  const requestCount = h.requests.length;
  await h.click({ sessionAction: "next" });
  assert.equal(h.requests.length, requestCount, "repeated clicks while loading do not skip a page");
  next.resolve({ total: 128, items: [] }); await settle();
  await h.click({ sessionAction: "previous" });
  assert.match(h.root.innerHTML, /第 1 \/ 3 页 · 共 128 项/);
  previous.resolve({ total: 128, items: [] }); await settle();
  await h.click({ sessionAction: "next" });
  h.select("agent_id", "new-agent"); await settle();
  assert.equal(h.requests.at(-2).signal.aborted, true);
  assert.match(h.root.innerHTML, /第 1 \/ 1 页 · 共 8 项/);
  stale.resolve({ total: 999, items: [] }); await settle();
  assert.match(h.root.innerHTML, /第 1 \/ 1 页 · 共 8 项/);
  assert.equal(h.controller.snapshot().data.total, 8);
});

test("session list uses fixed fifty-row pages and retains original missing-field labels", async () => {
  const h = sessionHarness(async () => ({ total: 1, items: [{ session_id: "empty-fields", title: "", agent_name: "", agent_id: "", user_id: "", latest_status: "completed", updated_at: "2026-09-11T01:00:00Z" }] }));
  h.activate("list", { ...filters, limit: 100 }); await settle();
  assert.equal(new URL(h.requests[0].path, "http://local").searchParams.get("limit"), "50");
  assert.doesNotMatch(h.root.innerHTML, /每页条数|20 条 \/ 页|100 条 \/ 页|diagnostic-missing-value|>No</);
  assert.match(h.root.innerHTML, /未命名会话[\s\S]*当前范围暂无提问[\s\S]*未记录/);
  assert.match(h.root.innerHTML, /data-session-action="previous"[^>]*>上一页<\/button>/);
  const count = h.requests.length;
  await h.click({ sessionAction: "previous" }); await h.click({ sessionAction: "next" });
  assert.equal(h.requests.length, count, "first and last page boundaries do not issue requests");
});

test("resolved username in picker submits the original platform ID, never its display name", async () => {
  const h = sessionHarness(); h.activate("list"); await settle();
  assert.match(h.root.innerHTML, /name="user_id" value="平台账号"/);
  h.submit({ user_id: "平台账号" }); await settle();
  assert.equal(new URL(h.requests.at(-1).path, "http://local").searchParams.get("user_id"), "platform-owner");
});

test("dropdown markup uses Console controls, escaped option values and selected ARIA state", () => {
  const html = selectHtml("source", "来源", "channel", [["", "全部"], ["channel", "渠道"], ['x" onclick="evil', "<script>"]]);
  assert.doesNotMatch(html, /<select|<script>/); assert.match(html, /role="listbox"/); assert.match(html, /aria-selected="true" data-value="channel"/);
  assert.match(html, /data-value="x&quot; onclick=&quot;evil"/);
});

test("production session controller does not import live workspace state or write-capable workflows", () => {
  assert.doesNotMatch(source, /from ["']\.\/(?:chat|workspace)\.js|localStorage|EventSource|WebSocket|setInterval|\/cancel|\/chat\/stream|\/compact|\/uploads/);
  assert.doesNotMatch(source, /window\.DeepAgentConsoleChat\.state|window\.DeepAgentConsoleContext/);
});

test("diagnostic request uses same-origin API helper, no App, no cache and propagates permission loss", async () => {
  const calls = [], events = [];
  const context = vm.createContext({ AbortController, setTimeout, clearTimeout,
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    window: { serviceUrl: path => `/prefix/${path}`, daFetch: async (path, options) => { calls.push({ path, options }); return { ok: false, status: 403, json: async () => ({ detail: "denied" }) }; }, dispatchEvent: event => events.push(event) },
  });
  vm.runInContext(uiSource.replace(/^export /gm, ""), context);
  await assert.rejects(context.diagnosticRequest("api/v1/diagnostics/sessions"), error => error.status === 403);
  assert.equal(calls[0].path, "/prefix/api/v1/diagnostics/sessions");
  assert.equal(calls[0].options.includeContext, false); assert.equal(calls[0].options.cache, "no-store"); assert.equal(calls[0].options.reportStatus, false);
  assert.equal(events[0].type, "deep-agent-diagnostics-denied"); assert.equal(events[0].detail.status, 403);
});
