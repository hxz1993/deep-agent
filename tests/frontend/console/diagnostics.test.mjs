import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { buildDiagnosticsQuery, createDiagnosticsQuery, diagnosticsError, diagnosticsStatusNotice } from "../../../console/diagnostics-query.js";
import { diagnosticFilters, diagnosticRowsHtml, diagnosticScalars } from "../../../console/diagnostics.js";

const root = new URL("../../../console/", import.meta.url);
const html = await readFile(new URL("index.html", root), "utf8");
const css = await readFile(new URL("diagnostics.css", root), "utf8");
const source = await readFile(new URL("diagnostics.js", root), "utf8");
const main = await readFile(new URL("main.js", root), "utf8");
const status = { enabled: true, available: true, instance_id: "pod-a", sources: ["api", "gateway"], issues: [] };
const record = (id, fields = {}) => ({ timestamp: "2026-09-09T08:00:00Z", level: "INFO", source: "api", event: "tool.started", summary: "工具开始", boot_id: "boot-a", record_id: id, ...fields });
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

test("diagnostics retains its navigation entry and replaces all placeholder capabilities", () => {
  const section = html.match(/<section[^>]+id="page-diagnostics"[\s\S]*?<\/section>/)[0];
  assert.match(html, /data-page="diagnostics" title="诊断中心"/);
  assert.doesNotMatch(section, /Phase 1|能力规划中|暂未开放|主动探测|P95|下载|<select/);
  for (const id of ["diagnosticsFilters", "diagnosticsSession", "diagnosticsRun", "diagnosticsKeyword", "diagnosticsQuery", "diagnosticsMore", "diagnosticsNotice", "diagnosticsRecords"]) assert.ok(section.includes(`id="${id}"`));
  assert.match(section, /role="listbox"/);
  assert.match(main, /setDiagnosticsActive\(consoleAllowed && next === "diagnostics"\)/);
  assert.ok(main.indexOf("if (current === \"configuration\"") < main.indexOf("setDiagnosticsActive(consoleAllowed"), "dirty-editor navigation gates still precede diagnostic activation");
});

test("query uses central same-origin helper without App context, no polling or download", () => {
  assert.match(source, /window\.daFetch\(window\.serviceUrl\(path\)/);
  assert.match(source, /includeContext: false, reportStatus: false/);
  assert.doesNotMatch(source, /setInterval|EventSource|WebSocket|createObjectURL|\/download|localStorage/);
  assert.match(source, /window\.addEventListener\("pagehide"/);
});

test("filters use literal encoded values, UTC range and no unrelated user/App/path fields", () => {
  const params = new URLSearchParams(buildDiagnosticsQuery({ session_id: " s/1 ", run_id: "r&2", q: "x.*<&", app: "app", user: "u", path: "/etc/passwd", source: "gateway" }, "a+/="));
  assert.equal(params.get("session_id"), "s/1");
  assert.equal(params.get("run_id"), "r&2");
  assert.equal(params.get("q"), "x.*<&");
  assert.equal(params.get("cursor"), "a+/=");
  assert.equal(params.get("limit"), "200");
  for (const key of ["app", "user", "path"]) assert.equal(params.has(key), false);
  const filters = diagnosticFilters({}, new Date("2026-09-09T08:00:00Z"));
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
  await h.query.query({ start: "fixed-start", end: "fixed-end", run_id: "run-a" });
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
  const first = h.query.query({ run_id: "old" });
  await turn();
  await h.query.query({ run_id: "new" });
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
  const output = diagnosticRowsHtml([row]);
  assert.doesNotMatch(output, /<img|<iframe|<script>|onclick="bad/);
  assert.match(output, /&lt;img/);
  assert.match(output, /data-filter-id="session_id"/);
  assert.match(output, /data-copy-id="run_id"/);
  assert.match(output, /结构化记录/);
  assert.match(output, /1\.2s/);
  assert.doesNotMatch(output, /not-rendered/);
  assert.equal("metadata" in diagnosticScalars(row), false);
});

test("diagnostics styles share theme tokens and retain actions and detail layout on narrow screens", () => {
  assert.doesNotMatch(css, /#[a-f0-9]{3,8}\b|rgba?\(/i);
  assert.match(css, /var\(--accent\)/);
  assert.match(css, /var\(--danger\)/);
  assert.match(css, /@media \(max-width: 760px\)/);
  assert.match(css, /\.diagnostics-filter-actions \{ grid-column: 1 \/ -1;/);
  assert.match(css, /overflow-wrap: anywhere/);
  assert.match(css, /\.diagnostics-page \.console-select-menu[^}]*z-index: 35/);
  assert.match(source, /ArrowDown/);
  assert.match(source, /Escape/);
});
