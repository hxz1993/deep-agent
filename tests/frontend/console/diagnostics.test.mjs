import { createDiagnosticsExport, logExportSummary } from "../../../console/diagnostics-export.js";
import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import { buildDiagnosticsQuery, createDiagnosticsQuery, diagnosticsError, diagnosticsStatusNotice } from "../../../console/diagnostics-query.js";
import { diagnosticFilters, diagnosticRowsHtml, diagnosticDetailHtml, diagnosticScalars, diagnosticFieldLabel, diagnosticTime, highlightedDiagnostic } from "../../../console/diagnostics.js";

const root = new URL("../../../console/", import.meta.url);
const html = await readFile(new URL("index.html", root), "utf8");
const css = await readFile(new URL("diagnostics.css", root), "utf8");
const source = await readFile(new URL("diagnostics.js", root), "utf8");
const main = await readFile(new URL("main.js", root), "utf8");
const ui = await readFile(new URL("diagnostics-ui.js", root), "utf8");
const status = { enabled: true, available: true, instance_id: "pod-a", sources: ["api", "gateway"], issues: [] };
const record = (id, fields = {}) => ({ timestamp: "2026-09-09T08:00:00Z", level: "INFO", source: "api", event: "tool.started", summary: "工具开始", raw: "2026-09-09 | INFO | 工具开始", boot_id: "boot-a", record_id: id, ...fields });
const page = (items, fields = {}) => ({ instance_id: "pod-a", items, next_cursor: null, partial: false, range: { start: "2026-09-08T08:00:00Z", end: "2026-09-09T08:00:00Z" }, ...fields });
const deferred = () => { let resolve; let reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const turn = () => new Promise(setImmediate);
function harness(responses) {
  const calls = [];
  const changes = [];
  const query = createDiagnosticsQuery({
    request: async (path, signal) => {
      calls.push({ path, signal });
      const response = responses.shift();
      if (response instanceof Error) throw response;
      return response;
    },
    onChange: (state) => changes.push(state),
  });
  return { query, calls, changes };
}

function logControllerHarness(responses = []) {
  const nodes = new Map(), calls = [];
  let now = "2026-09-10T08:00:00.000Z", selectCallback;
  function node(key) {
    if (!nodes.has(key)) {
      const classes = new Set(), attributes = new Map(), handlers = new Map();
      nodes.set(key, { value: "", innerHTML: "", dataset: {}, handlers, scrollTop: 0,
        classList: { add: (...names) => names.forEach(name => classes.add(name)), remove: (...names) => names.forEach(name => classes.delete(name)), contains: name => classes.has(name), toggle(name, active) { active ? classes.add(name) : classes.delete(name); } },
        setAttribute: (name, value) => attributes.set(name, value), getAttribute: name => attributes.get(name),
        addEventListener(name, fn) { const list = handlers.get(name) || []; list.push(fn); handlers.set(name, list); },
        querySelector: selector => node(`${key} ${selector}`), insertAdjacentHTML() {}, focus() {},
        showModal() { this.open = true; }, close() { this.open = false; },
        reset() { for (const [id, target] of nodes) if (/^#diagnostics(?:Session|Keyword|Start|End)$/.test(id)) target.value = ""; },
      });
    }
    return nodes.get(key);
  }
  const root = node("page"); root.querySelector = node;
  const context = vm.createContext({
    Date: class extends Date { constructor(...args) { super(...(args.length ? args : [now])); } },
    createDiagnosticsQuery, createDiagnosticsExport, logExportSummary, escape: value => String(value ?? ""), icon: () => "", shortId: value => value,
    icons() {}, localInput: value => value || "", selectHtml: () => "", bindDiagnosticResizer() {},
    bindDiagnosticSelects(_root, callback) { selectCallback = callback; return { close() {} }; },
    window: { addEventListener() {} },
    diagnosticRequest: async path => { calls.push(path); const result = responses.length ? responses.shift() : path.endsWith("/status") ? status : page([]); if (result instanceof Error) throw result; return result; },
  });
  vm.runInContext(source.replace(/^import .*;\n/gm, "").replace(/^export /gm, ""), context);
  const controller = context.initDiagnosticLogs({ page: root });
  return { controller, node, calls, setNow(value) { now = value; }, select(name, value) { selectCallback(name, value); },
    fire(id, name, extra = {}) { for (const fn of node(id).handlers.get(name) || []) fn({ preventDefault() {}, target: node(id), ...extra }); },
    lastFilters() { return new URLSearchParams(calls.at(-1).split("?")[1]); } };
}

test("one query button refreshes relative time while restored and custom ranges remain exact", async () => {
  const h = logControllerHarness();
  h.controller.activate({ period: "24", start: "2026-09-08T08:00:00.123Z", end: "2026-09-09T08:00:00.123Z" });
  await turn();
  assert.equal(h.lastFilters().get("end"), "2026-09-09T08:00:00.123Z", "route restoration retains the supplied query window");
  h.fire("#diagnosticsFilters", "submit"); await turn();
  assert.equal(h.lastFilters().get("end"), "2026-09-10T08:00:00.000Z");
  h.setNow("2026-09-10T09:00:00.000Z"); h.fire("#diagnosticsFilters", "submit"); await turn();
  assert.equal(h.lastFilters().get("start"), "2026-09-09T09:00:00.000Z");
  h.controller.activate({ period: "custom", start: "2026-09-08T08:00:00.123Z", end: "2026-09-09T08:00:00.456Z" }); await turn();
  h.fire("#diagnosticsFilters", "submit"); await turn();
  assert.equal(h.lastFilters().get("end"), "2026-09-09T08:00:00.456Z", "custom re-query retains seconds and milliseconds");
});

test("fixed log search fields restore their values and reset together without Run filters", async () => {
  const h = logControllerHarness(); h.controller.activate(); await turn();
  h.node("#diagnosticsSession").value = "session-a";
  h.node("#diagnosticsKeyword").value = "查找内容";
  h.fire("#diagnosticsFilters", "submit"); await turn();
  assert.equal(h.lastFilters().get("session_id"), "session-a");
  assert.equal(h.lastFilters().get("q"), "查找内容");
  assert.equal(h.lastFilters().has("run_id"), false);
  const saved = h.controller.snapshot(); h.controller.deactivate(); h.controller.activate({}, saved);
  assert.equal(h.node("#diagnosticsSession").value, "session-a");
  assert.equal(h.node("#diagnosticsKeyword").value, "查找内容");
  assert.equal("advancedOpen" in saved, false);
  h.fire("#diagnosticsReset", "click"); await turn();
  assert.equal(h.lastFilters().has("session_id"), false);
  assert.equal(h.lastFilters().has("q"), false);
  assert.equal(h.lastFilters().has("run_id"), false);
  assert.equal(h.lastFilters().get("start"), "2026-09-09T08:00:00.000Z");
});

test("diagnostics retains its navigation entry and replaces all placeholder capabilities", () => {
  const section = html.match(/<section[^>]+id="page-diagnostics"[\s\S]*?<\/section>/)[0];
  assert.match(html, /data-diagnostics-nav="logs" title="日志"/);
  assert.match(html, /data-diagnostics-nav="sessions" title="会话"/);
  assert.doesNotMatch(section, /Phase 1|能力规划中|暂未开放|主动探测|P95|下载|<select/);
  for (const id of ["diagnosticsFilters", "diagnosticsSession", "diagnosticsKeyword", "diagnosticsQuery", "diagnosticsMore", "diagnosticsNotice", "diagnosticsRecords"]) assert.ok(section.includes(`id="${id}"`));
  assert.doesNotMatch(section, /id="diagnosticsRun"|id="diagnosticsAdvanced/);
  assert.match(ui, /role="listbox"/);
  assert.match(main, /setDiagnosticsActive\(consoleAllowed && next === "diagnostics"\)/);
  assert.ok(main.indexOf("if (current === \"configuration\"") < main.indexOf("setDiagnosticsActive(consoleAllowed"), "dirty-editor navigation gates still precede diagnostic activation");
});

test("query uses central same-origin helper without App context, no polling or server-side download", () => {
  assert.match(ui, /window\.daFetch\(window\.serviceUrl\(path\)/);
  assert.match(ui, /includeContext: false, reportStatus: false/);
  assert.doesNotMatch(source, /setInterval|EventSource|WebSocket|\/download|localStorage/);
  assert.match(source, /window\.addEventListener\("pagehide"/);
});

test("filters use literal encoded values, UTC range and no unrelated user/App/path fields", () => {
  const params = new URLSearchParams(buildDiagnosticsQuery({ session_id: " s/1 ", run_id: "r&2", q: "x.*<&", app: "app", user: "u", path: "/etc/passwd", source: "gateway" }, "a+/="));
  assert.equal(params.get("session_id"), "s/1");
  assert.equal(params.has("run_id"), false);
  assert.equal(params.get("q"), "x.*<&");
  assert.equal(params.get("cursor"), "a+/=");
  assert.equal(params.get("limit"), "200");
  for (const key of ["app", "user", "path"]) assert.equal(params.has(key), false);
  const filters = diagnosticFilters({ run_id: "legacy-run" }, new Date("2026-09-09T08:00:00Z"));
  assert.equal("run_id" in filters, false);
  assert.equal(filters.start, "2026-09-08T08:00:00.000Z");
  assert.equal(filters.end, "2026-09-09T08:00:00.000Z");
  assert.throws(() => diagnosticFilters({ period: "custom", start: "", end: "" }), /开始和结束/);
  assert.throws(() => diagnosticFilters({ period: "custom", start: "2026-09-09T08:00", end: "2026-09-08T08:00" }), /早于/);
});

test("inactive diagnostic page never fetches; status and global results load only on query", async () => {
  const h = harness([status, page([record("1")])]);
  await h.query.query();
  assert.equal(h.calls.length, 0);
  h.query.setActive(true);
  await h.query.query({ q: "工具" });
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[0].path, "api/v1/diagnostics/logs/status");
  assert.equal(new URLSearchParams(h.calls[1].path.split("?")[1]).get("q"), "工具");
  assert.equal(h.query.state.items.length, 1);
  assert.equal(h.query.state.busy, false);
  assert.equal(h.query.state.notice, null);
});

test("pagination holds original time scope, preserves order and deduplicates records", async () => {
  const h = harness([status, page([record("1")], { next_cursor: "next", partial: true }), page([record("1"), record("2")])]);
  h.query.setActive(true);
  await h.query.query({ start: "fixed-start", end: "fixed-end", session_id: "session-a" });
  assert.equal(h.query.state.partial, true);
  await h.query.query(undefined, { more: true });
  assert.deepEqual(h.query.state.items.map((r) => r.record_id), ["1", "2"]);
  const params = new URLSearchParams(h.calls[2].path.split("?")[1]);
  assert.equal(params.get("start"), "fixed-start");
  assert.equal(params.get("end"), "fixed-end");
  assert.equal(params.get("cursor"), "next");
  assert.equal(h.query.state.nextCursor, null);
  assert.equal(h.query.state.partial, false);
  await h.query.query(undefined, { more: true });
  assert.equal(h.calls.length, 3);
});

test("an empty partial scan still exposes its continuation and does not claim no more results", async () => {
  const h = harness([status, page([], { next_cursor: "scan-frontier", partial: true }), page([record("found")])]);
  h.query.setActive(true);
  await h.query.query({ q: "rare" });
  assert.equal(h.query.state.items.length, 0);
  assert.equal(h.query.state.partial, true);
  assert.equal(h.query.state.nextCursor, "scan-frontier");
  await h.query.query(undefined, { more: true });
  assert.equal(h.query.state.items[0].record_id, "found");
});

test("leaving page aborts status and ignores a transport that resolves after cancellation", async () => {
  const gate = deferred();
  const h = harness([gate.promise, page([record("late")])]);
  h.query.setActive(true);
  const pending = h.query.query();
  h.query.setActive(false);
  assert.equal(h.calls[0].signal.aborted, true);
  gate.resolve(status);
  await pending;
  assert.equal(h.calls.length, 1);
  assert.equal(h.query.state.items.length, 0);
  assert.equal(h.query.state.busy, false);
});

test("editing conditions immediately cancels pending results and clears the pagination scope", async () => {
  const gate = deferred();
  const h = harness([status, gate.promise]);
  h.query.setActive(true);
  const pending = h.query.query({ session_id: "old" });
  await turn();
  h.query.changeFilters();
  assert.equal(h.calls[1].signal.aborted, true);
  gate.resolve(page([record("old")], { next_cursor: "old-cursor" }));
  await pending;
  assert.equal(h.query.state.items.length, 0);
  assert.equal(h.query.state.nextCursor, null);
  assert.match(h.query.state.notice.title, /已更改/);
});

test("a newer query wins even if the old query resolves last", async () => {
  const gate = deferred();
  const h = harness([status, gate.promise, status, page([record("new")])]);
  h.query.setActive(true);
  const first = h.query.query({ session_id: "old" });
  await turn();
  await h.query.query({ session_id: "new" });
  gate.resolve(page([record("old")]));
  await first;
  assert.deepEqual(h.query.state.items.map((r) => r.record_id), ["new"]);
});

test("disabled, unavailable and incomplete sources are distinct from zero matches", async () => {
  for (const variant of [{ enabled: false }, { available: false }]) {
    const h = harness([{ ...status, ...variant }]);
    h.query.setActive(true);
    await h.query.query();
    assert.equal(h.calls.length, 1);
    assert.equal(h.query.state.notice.kind, "warning");
    assert.equal(h.query.state.busy, false);
  }
  const issues = [{ source: "gateway", code: "queue_full", dropped_records: 3 }];
  const h = harness([{ ...status, issues }, page([])]);
  h.query.setActive(true);
  await h.query.query();
  assert.match(h.query.state.notice.title, /不完整/);
  assert.equal(diagnosticsStatusNotice(status), null);
});

test("403/401/503/409 use safe distinct messages, do not render raw errors", () => {
  const titles = new Set();
  for (const code of [401, 403, 409, 422, 503]) {
    const result = diagnosticsError({ status: code, message: "secret123<script>" });
    titles.add(result.title);
    assert.doesNotMatch(JSON.stringify(result), /secret123|script/);
  }
  assert.equal(titles.size, 5);
});

test("expired cursor or changed instance clears partial results and requires explicit requery", async () => {
  for (const result of [Object.assign(new Error("expired"), { status: 409 }), page([record("new-pod")], { instance_id: "pod-b" })]) {
    const h = harness([status, page([record("1")], { next_cursor: "stale" }), result]);
    h.query.setActive(true);
    await h.query.query();
    await h.query.query(undefined, { more: true });
    assert.equal(h.query.state.items.length, 0);
    assert.equal(h.query.state.nextCursor, null);
    assert.match(h.query.state.notice.title, /已失效/);
    assert.equal(h.calls.length, 3, "no automatic restart scan");
  }
});

test("permission loss clears already loaded records; ordinary network failure keeps retryable page", async () => {
  for (const code of [403, 0]) {
    const h = harness([status, page([record("1")], { next_cursor: "next" }), Object.assign(new Error("network"), { status: code })]);
    h.query.setActive(true);
    await h.query.query();
    await h.query.query(undefined, { more: true });
    assert.equal(h.query.state.items.length, code === 403 ? 0 : 1);
    assert.equal(h.query.state.busy, false);
  }
});

test("query has a bounded browser result set without silently claiming complete", async () => {
  const items = Array.from({ length: 5100 }, (_, i) => record(String(i)));
  const h = harness([status, page(items, { next_cursor: "next" })]);
  h.query.setActive(true);
  await h.query.query();
  assert.equal(h.query.state.items.length, 5000);
  assert.equal(h.query.state.capped, true);
  assert.equal(h.query.state.nextCursor, null);
  assert.match(source, /展示上限/);
});

test("records escape summaries, events, field names and values; only scalar details are rendered", () => {
  const row = record("1", { summary: '<img src=x onerror="bad()">', event: "tool.<script>", session_id: 's" onclick="bad', run_id: "run-a", level: 'ERROR" onclick="bad', "<script>": "<iframe>", metadata: { private: "not-rendered" }, duration_ms: 1234 });
  const output = diagnosticRowsHtml([row]) + diagnosticDetailHtml(row);
  assert.doesNotMatch(output, /<img|<iframe|<script>|onclick="bad/);
  assert.match(output, /&lt;img/);
  assert.match(output, /data-open-session=/);
  assert.doesNotMatch(output, /data-filter-id/);
  assert.match(output, /data-field="run_id"/);
  assert.doesNotMatch(output, /<h3>会话<\/h3>/);
  assert.match(output, /<code>1234<\/code>/);
  assert.doesNotMatch(output, /结构化记录|规范字段|diagnostics-chevron|diagnostics-duration/);
  assert.match(output, /<details class="diagnostics-raw-details">/);
  assert.doesNotMatch(output, /<h3>服务信息<\/h3>/);
  assert.doesNotMatch(output, /not-rendered/);
  assert.equal("metadata" in diagnosticScalars(row), false);
});

test("diagnostics styles share theme tokens and retain actions and detail layout on narrow screens", () => {
  assert.doesNotMatch(css, /#[a-f0-9]{3,8}\b|rgba?\(/i);
  assert.match(css, /var\(--accent\)/);
  assert.match(css, /var\(--danger\)/);
  assert.match(css, /@media \(max-width:\s?760px\)/);
  assert.match(css, /\.diagnostics-filter-actions \{ grid-column: 1 \/ -1;/);
  assert.match(css, /overflow-wrap:\s?anywhere/);
  assert.match(css, /\.diagnostics-page \.console-select-menu[^}]*z-index:\s?35/);
  assert.match(ui, /ArrowDown/);
  assert.match(ui, /Escape/);
});

test("model API details show safe tool inventories in the existing property layout", () => {
  const data = record("api", { event: "model_api_request", model_api: "https://models.example.test",
    api_path: "/chat/completions", http_method: "POST", http_status: 400,
    api_request_id: "a".repeat(32), tool_manifest_status: "complete", tool_count: 3, unique_tool_count: 2,
    tool_names: ["read_file", "docs_search", "docs_search"], duplicate_tool_names: ["docs_search"],
    added_tool_names: ["docs_search"], history_tool_names: ["docs_search"], redeclared_tool_names: ["docs_search"],
    tools_omitted: 0, tool_names_hash: "b".repeat(64),
    headers: "NEVER_LOG_HEADERS", body: "NEVER_LOG_BODY", request_body: { prompt: "NEVER_LOG_PROMPT" },
    other_list: ["NEVER_LOG_OTHER"] });
  const output = diagnosticDetailHtml(data);
  assert.doesNotMatch(output, /<h3>模型 API<\/h3>/);
  assert.match(output, /<dt title="http_status">HTTP 状态<\/dt><dd><code>400<\/code>/);
  assert.match(output, /<dt title="tool_manifest_status">工具清单状态<\/dt><dd><code>complete<\/code>/);
  assert.match(output, /<dt title="tool_names">工具清单<\/dt><dd><code>read_file, docs_search, docs_search<\/code>/);
  assert.match(output, /<dt title="duplicate_tool_names">重复工具<\/dt><dd><code>docs_search<\/code>/);
  assert.match(output, /<dt title="history_tool_names">历史工具<\/dt><dd><code>docs_search<\/code>/);
  assert.match(output, /<dt title="redeclared_tool_names">重新声明工具<\/dt><dd><code>docs_search<\/code>/);
  assert.match(output, /<dt title="tools_omitted">省略工具数<\/dt><dd><code>0<\/code>/);
  assert.equal((output.match(/<dt title="model_api">模型 API<\/dt>/g) || []).length, 1);
  assert.doesNotMatch(output, /NEVER_LOG|<a href/);
  assert.equal(diagnosticScalars({ duplicate_tool_names: [] }).duplicate_tool_names, "");
});

test("model API tool arrays reject arbitrary structures and have a shared display bound", () => {
  const fields = diagnosticScalars({ tool_names: ["safe_name", "<img src=x>", { token: "SECRET" }, 12],
    duplicate_tool_names: "SECRET", added_tool_names: ["https://SECRET", "with space"],
    history_tool_names: ["docs_search", "<script>", { token: "SECRET" }], redeclared_tool_names: "SECRET",
    metadata: { token: "SECRET" } });
  assert.deepEqual(fields, { tool_names: "safe_name", added_tool_names: "", history_tool_names: "docs_search" });
  const names = Array.from({ length: 1000 }, (_, i) => `tool_${i}_${"x".repeat(100)}`);
  const bounded = diagnosticScalars({ tool_names: names, duplicate_tool_names: names, added_tool_names: names,
    history_tool_names: names, redeclared_tool_names: ["important_redeclaration"] });
  assert.equal(bounded.redeclared_tool_names, "important_redeclaration");
  assert.ok(Object.values(bounded).reduce((size, text) => size + text.length, 0) < 3700);
});

test("dense log rows highlight literal text safely and details never duplicate facts", () => {
  assert.equal(highlightedDiagnostic('<b>x.*</b>', 'x.*'), '&lt;b&gt;<mark>x.*</mark>&lt;/b&gt;');
  const data = record("1", { session_id: "complete-session", run_id: "complete-run", channel_run_id: "channel-only", duration_ms: 50 });
  const row = diagnosticRowsHtml([data]);
  assert.doesNotMatch(row, /50|duration|data-filter-id|<details/);
  assert.match(row, /data-open-session="complete-session"/);
  assert.match(row, />complete<\/button>/);
  assert.match(row, /class="diagnostics-sequence" aria-label="当前查询第 1 条">1<\/span>/);
  const detail = diagnosticDetailHtml(data);
  assert.equal((detail.match(/<dt title="session_id">会话 ID<\/dt>/g) || []).length, 1);
  assert.match(detail, /channel_run_id/);
  assert.doesNotMatch(diagnosticRowsHtml([record("service")]), /data-open-session/);
});

test("log details translate labels and limit copy and navigation actions to sessions", () => {
  const detail = diagnosticDetailHtml(record("a", { session_id: "session-a", run_id: "run-a", process_id: 42, instance_id: "node", tool_call_id: "call", agent_id: "agent" }));
  assert.deepEqual([...detail.matchAll(/data-copy-id="([^"]+)"/g)].map(match => match[1]), ["session_id"]);
  assert.match(detail, /<details class="diagnostics-raw-details"><summary>/);
  assert.doesNotMatch(detail, /<details[^>]* open/);
  assert.doesNotMatch(detail, /<h3>会话|<h3>服务信息/);
  assert.match(detail, /data-open-session="session-a"/);
  assert.match(detail, /data-field="run_id"><dt title="run_id">执行 ID<\/dt><dd><code>run-a<\/code>/);
  assert.doesNotMatch(detail, /data-filter-id/);
  assert.match(source, /onOpenSession\(item\.session_id\)/);
  assert.doesNotMatch(source, /onOpenSession\(item\.session_id,|filterId|diagnosticsRun/);
});

test("logs place session_id with dropdowns above the aligned search and actions row", () => {
  const header = html.match(/<header class="diagnostics-head diagnostics-log-header">([\s\S]*?)<\/header>/)[1];
  assert.match(header, /id="diagnosticsFilters"/);
  assert.match(header, /placeholder="搜索日志内容"/);
  assert.match(header, /class="diagnostics-search-row"/);
  assert.doesNotMatch(header, /<details|<summary|diagnosticsRefresh|diagnosticsAdvanced|diagnosticsRun|应用筛选/);
  const secondRow = header.slice(header.indexOf('class="diagnostics-search-row"'));
  assert.ok(secondRow.includes('id="diagnosticsKeyword"'));
  const firstRow = header.slice(header.indexOf('class="diagnostics-primary-row"'), header.indexOf('class="diagnostics-search-row"'));
  assert.match(firstRow, /<label[^>]*><i data-lucide="messages-square"[^>]*><\/i><input[^>]*id="diagnosticsSession"/);
  assert.doesNotMatch(secondRow, /id="diagnosticsSession"/);
  assert.equal((header.match(/type="submit"/g) || []).length, 1);
  assert.match(header, /class="diagnostics-button text" id="diagnosticsReset"[^>]*>重置<\/button>/);
  assert.match(css, /\.diagnostics-search-row \{ flex:0 0 100%[^}]*display:flex/);
  assert.doesNotMatch(css, /\.diagnostics-search-row \{[^}]*(?:position:absolute|position:fixed|box-shadow)/);
  assert.doesNotMatch(header, /<span>关键词<\/span>|<span>session_id<\/span>|当前部署实例/);
  assert.match(header, /aria-label="会话 ID"/);
});

test("compact Console log composition places its header above the split workbench without a redundant result heading", () => {
  const section = html.match(/<section[^>]+id="page-diagnostics"[\s\S]*?<\/section>/)[0];
  const headerEnd = section.indexOf("</header>");
  assert.ok(headerEnd < section.indexOf('id="diagnosticsSplit"'), "the filter toolbar must not shrink when opening details");
  assert.doesNotMatch(section, /DIAGNOSTICS|diagnostics-eyebrow/);
  assert.doesNotMatch(section, /<h2>查询结果<\/h2>/);
  assert.match(section, /id="diagnosticsQueryRange"/);
  assert.doesNotMatch(section, /aria-label="更多筛选"/);
  assert.match(css, /#diagnosticsLogPage \.diagnostics-head h1[^}]*font-size:16px/);
  assert.match(css, /#diagnosticsLogPage \.diagnostics-row[^}]*height:24px/);
  assert.match(css, /#diagnosticsLogPage \.diagnostics-row[^}]*grid-template-columns:30px 150px 52px 48px minmax\(120px,1fr\) 72px; gap:6px/);
  assert.match(css, /#diagnosticsLogPage \.diagnostics-summary[^}]*font-size:12px/);
  assert.doesNotMatch(section, /diagnostics-footer|id="diagnosticsRange"/);
  assert.match(section, /diagnostics-result-title[\s\S]*diagnosticsMeta[\s\S]*diagnosticsMore/);
  assert.match(css, /#diagnosticsLogPage \.diagnostics-split[^}]*border:0; border-radius:0/);
  assert.match(css, /#diagnosticsLogPage \.diagnostics-filters \.console-select-trigger[^}]*height:32px/);
  assert.match(css, /#diagnosticsLogPage \.diagnostics-split[^}]*--diagnostic-drawer-width:436px/);
  assert.match(source, /bindDiagnosticResizer\(byId\("diagnosticsResizer"\), split, "--diagnostic-drawer-width", 436\)/);
});

test("log details retain exact numeric values and keep compact label columns", () => {
  const detail = diagnosticDetailHtml(record("detail", { model_name: "model-a", duration_ms: 30000, attempt: 2, reason: "请求超时", session_id: "session-a", run_id: "run-a" }));
  assert.match(detail, /<code>30000<\/code>/);
  assert.ok(detail.indexOf('data-field="model_name"') < detail.indexOf('data-field="duration_ms"'));
  assert.ok(detail.indexOf('data-field="duration_ms"') < detail.indexOf('data-field="reason"'));
  assert.equal((detail.match(/<dt title="source">来源<\/dt>/g) || []).length, 1);
  assert.match(detail, /diagnostics-property-actions/);
  assert.match(css, /#diagnosticsLogPage \.diagnostics-property \{ display:grid; grid-column:1\/-1; grid-template-columns:subgrid; align-items:start; column-gap:8px/);
  assert.match(diagnosticTime("2026-09-09T08:00:00Z"), /^2026-09-09 \d{2}:\d{2}:\d{2}$/);
  assert.equal(diagnosticTime("not-a-date"), "not-a-date");
});

test("restored log snapshot retains cursor and fixed range without an automatic query", async () => {
  const h = harness([status, page([record("1")], { next_cursor: "saved-cursor" })]);
  h.query.setActive(true); await h.query.query({ start: "fixed", end: "fixed-end" });
  const saved = { ...h.query.state, items: [...h.query.state.items] };
  h.query.setActive(false); h.query.setActive(true); h.query.restore(saved);
  assert.equal(h.calls.length, 2);
  assert.equal(h.query.state.nextCursor, "saved-cursor");
  assert.equal(h.query.state.filters.start, "fixed");
  assert.equal(h.query.state.busy, false);
});


test("日志导出按当前输入和固定查询时间预检，导航离开关闭弹框", async () => {
  const h = logControllerHarness();
  h.controller.activate({ period: "24", start: "2026-09-08T08:00:00.123Z", end: "2026-09-09T08:00:00.456Z" });
  await turn();
  h.node("#diagnosticsKeyword").value = "new filter";
  h.node("#diagnosticsSession").value = "session-2";
  h.setNow("2026-09-11T09:00:00.000Z");
  h.fire("#diagnosticsExport", "click");
  assert.equal(h.node("#diagnosticsExportDialog").open, true);
  await turn();
  assert.equal(h.lastFilters().get("q"), "new filter");
  assert.equal(h.lastFilters().get("session_id"), "session-2");
  assert.equal(h.lastFilters().get("end"), "2026-09-09T08:00:00.456Z");
  assert.equal(h.node("#diagnosticsExportConfirm").disabled, true, "empty results cannot download");
  h.controller.deactivate();
  assert.equal(h.node("#diagnosticsExportDialog").open, false);
});

test("原始视图自动分页并逐字展示，切换查询时取消旧加载", async () => {
  const pending = deferred();
  const first = record("one", { raw: "时间 | INFO | message | key=a\\|b" });
  const second = record("two", { raw: "<script>literal</script>" });
  const h = logControllerHarness([status, page([]), status, page([first], { next_cursor: "next" }), page([second])]);
  h.controller.activate({ period: "24" }); await turn();
  h.fire("#diagnosticsRawView", "click"); await turn();
  assert.equal(h.node("#diagnosticsRawText").value, first.raw + "\n" + second.raw + "\n");
  assert.equal(h.node("#diagnosticsRawPane").hidden, false);
  assert.match(h.node("#diagnosticsRawStatus").textContent, /已加载 2 条/);
  assert.equal(h.node("#diagnosticsRawText").innerHTML, "");
  h.fire("#diagnosticsKeyword", "input");
  h.fire("#diagnosticsFilters", "input");
  assert.equal(h.node("#diagnosticsRawText").value, "");
  h.controller.deactivate();
  const late = logControllerHarness([status, page([]), status, pending.promise]);
  late.controller.activate({ period: "24" }); await turn();
  late.fire("#diagnosticsRawView", "click"); await turn(); late.controller.deactivate();
  pending.resolve(page([second])); await turn();
  assert.equal(late.node("#diagnosticsRawText").value, "");
});
test("列表使用写入时的消息，未解析行不伪造 INFO 或时间", () => {
  const item = record("one", { message: "工具 search_tools 完成，耗时 320ms", duration_ms: 320, agent_id: "hidden-list-agent", raw: "raw actual | agent_id=hidden-list-agent" });
  assert.match(diagnosticRowsHtml([item]), /工具 search_tools 完成，耗时 320ms/);
  assert.doesNotMatch(diagnosticRowsHtml([item]), /duration_ms|agent_id|hidden-list-agent|raw actual/);
  const unknown = diagnosticRowsHtml([{ boot_id: "file", record_id: "offset", raw: "old JSON line" }]);
  assert.match(unknown, /old JSON line/); assert.doesNotMatch(unknown, />INFO</);
  const detail = diagnosticDetailHtml(item);
  assert.match(detail, /raw actual/); assert.match(detail, /<dt title="message">正文<\/dt>/);
});


test("detail fields use Chinese labels with unknown scalar fallback and no duplicate message", () => {
  const detail = diagnosticDetailHtml(record("labels", { agent_name: "日志展示验收", future_fact: "unknown_value", summary: "工具完成", message: "工具完成", source: "api" }));
  assert.equal(diagnosticFieldLabel("future_fact"), "future_fact");
  assert.match(detail, /<dt title="agent_name">Agent 名称<\/dt><dd><code>日志展示验收<\/code>/);
  assert.match(detail, /<dt title="future_fact">future_fact<\/dt><dd><code>unknown_value<\/code>/);
  assert.match(detail, /<dt title="source">来源<\/dt><dd><code>api<\/code>/);
  assert.doesNotMatch(detail, /data-field="summary"|data-copy-id/);
  assert.match(detail, /<dt title="message">正文<\/dt>/);
});

test("session preview preserves exactly eight characters and raw detail is collapsed on every render", () => {
  const row = diagnosticRowsHtml([record("preview", { session_id: "b662a978abcdefghijklmnop" })]);
  assert.match(row, /title="打开会话 b662a978abcdefghijklmnop">b662a978<\/button>/);
  assert.match(css, /\.diagnostics-session button[^}]*text-overflow:clip/);
  assert.match(css, /#diagnosticsLogPage \.diagnostics-session button[^}]*width:8ch; padding:0/);
  for (const id of ["one", "two"]) {
    const detail = diagnosticDetailHtml(record(id, { raw: "literal <markup> | value=a\\|b" }));
    assert.match(detail, /<details class="diagnostics-raw-details"><summary>/);
    assert.doesNotMatch(detail, /<details[^>]* open|data-copy-id="raw"/);
    assert.match(detail, /literal &lt;markup&gt; \| value=a\\\|b/);
  }
  assert.match(css, /\.diagnostics-raw-record[^}]*border:1px solid var\(--border\)/);
});
