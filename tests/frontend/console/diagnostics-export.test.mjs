import test from "node:test";
import assert from "node:assert/strict";
import { createDiagnosticsExport, LOG_EXPORT_LIMITS, logExportSummary } from "../../../console/diagnostics-export.js";
import { diagnosticScalars } from "../../../console/diagnostics.js";
const status = { enabled: true, available: true, instance_id: "one", issues: [] };
const record = id => ({ boot_id: "b", record_id: id, summary: `record ${id}`, raw: `record ${id}` });
const page = (items, next_cursor = null, extra = {}) => ({ items, next_cursor, instance_id: "one", ...extra });
const turn = () => new Promise(setImmediate);
function harness(responses, limits = LOG_EXPORT_LIMITS) {
  const calls = [];
  const exporter = createDiagnosticsExport({ limits, projectRecord: diagnosticScalars, request: async (path, signal) => {
    calls.push({ path, signal }); const response = responses.shift();
    if (response instanceof Error) throw response;
    return response;
  } });
  return { ...exporter, calls };
}
test("导出预检沿用所有筛选、继续游标并去重，确认下载同一快照", async () => {
  const h = harness([status, page([record(1)], "cursor1", { partial: true }), page([record(1), { ...record(2), secret: "never-export" }])]);
  const filters = { start: "2026-09-10T00:00:00Z", end: "2026-09-11T00:00:00Z", source: "api", level: "ERROR", session_id: "session", q: "keyword" };
  const pending = h.prepare(filters);
  filters.q = "changed";
  assert.equal(h.payload(), null);
  await pending;
  assert.equal(h.state.phase, "ready"); assert.equal(h.state.items.length, 2);
  assert.equal((!h.state.reason && !h.state.issues.length), true); assert.equal(Boolean(h.state.reason), false);
  for (const call of h.calls.slice(1)) {
    const params = new URLSearchParams(call.path.split("?")[1]);
    assert.equal(params.get("q"), "keyword"); assert.equal(params.get("session_id"), "session");
    assert.equal(params.get("level"), "ERROR"); assert.equal(params.get("source"), "api");
    assert.equal(params.get("start"), filters.start); assert.equal(params.get("end"), filters.end);
  }
  assert.match(h.calls[2].path, /cursor=cursor1/);
  assert.doesNotMatch(JSON.stringify(h.payload()), /never-export/);
  assert.match(logExportSummary(h.state), /预计导出 2 条/);
});
test("恰好 5000 条完整匹配不标记截断，有更多结果才截断", async () => {
  for (const next of [null, "more"]) {
    const h = harness([status, page(Array.from({ length: 5000 }, (_, i) => record(i)), next)]);
    await h.prepare({});
    assert.equal(h.state.items.length, 5000); assert.equal(Boolean(h.state.reason), Boolean(next));
    assert.equal(h.calls.length, 2);
  }
});
test("超过上限丢弃多出条目，空结果不提供下载", async () => {
  const h = harness([status, page(Array.from({ length: 5001 }, (_, i) => record(i)))]);
  await h.prepare({}); assert.equal(h.state.items.length, 5000); assert.equal(h.state.reason, "records");
  const empty = harness([status, page([])]); await empty.prepare({});
  assert.equal(empty.state.phase, "ready"); assert.equal(empty.payload(), null);
  assert.match(logExportSummary(empty.state), /预计导出 0 条/);
});
test("文件字节预算、扫描页数及无进展游标都有兜底", async () => {
  const bytes = harness([status, page([record(1), { ...record(2), raw: "x".repeat(500) }])], { ...LOG_EXPORT_LIMITS, bytes: 100 });
  await bytes.prepare({}); assert.equal(bytes.state.reason, "bytes"); assert.equal(bytes.state.items.length, 1);
  assert.ok(new TextEncoder().encode(bytes.payload()).length <= 100);
  const pages = harness([status, page([record(1)], "more")], { ...LOG_EXPORT_LIMITS, pages: 1 });
  await pages.prepare({}); assert.equal(pages.state.reason, "pages");
  const repeat = harness([status, page([], "same", { partial: true }), page([], "same", { partial: true })]);
  await repeat.prepare({}); assert.equal(repeat.state.reason, "pages"); assert.equal(repeat.calls.length, 3);
});
test("取消请求及时清理并忽略迟到结果，下一次准备不受旧任务影响", async () => {
  let release;
  const pendingPage = new Promise(resolve => { release = resolve; });
  const h = harness([status, pendingPage, status, page([record(2)])]);
  const pending = h.prepare({ q: "old" }); await turn();
  h.cancel(); await pending;
  assert.equal(h.calls[1].signal.aborted, true); assert.equal(h.payload(), null);
  await h.prepare({ q: "new" });
  release(page([record(1)])); await turn();
  assert.equal(h.state.items[0].record_id, 2); assert.equal(h.state.filters.q, "new");
});
test("超时有记录时明确截断，无记录时禁止空下载", async () => {
  const limits = { ...LOG_EXPORT_LIMITS, milliseconds: 15 };
  const h = harness([status, page([record(1)], "more"), new Promise(() => {})], limits);
  await h.prepare({}); assert.equal(h.state.reason, "time"); assert.equal((!h.state.reason && !h.state.issues.length), false);
  assert.equal(h.calls.at(-1).signal.aborted, true);
  const empty = harness([status, new Promise(() => {})], limits);
  await empty.prepare({}); assert.equal(empty.state.phase, "error"); assert.equal(empty.payload(), null);
});
test("权限、游标、实例变化及请求失败不输出半份文件", async () => {
  for (const statusCode of [401, 403, 409, 503, 500]) {
    const h = harness([status, page([record(1)], "more"), Object.assign(Error("failure"), { status: statusCode })]);
    await h.prepare({}); assert.equal(h.state.phase, "error"); assert.equal(h.payload(), null);
  }
  const changed = harness([status, page([record(1)], null, { instance_id: "two" })]);
  await changed.prepare({}); assert.equal(changed.state.phase, "error");
});
test("日志源异常与最终部分扫描在页面提示，文件只包含原文", async () => {
  const h = harness([{ ...status, issues: [{ source: "api", code: "dropped" }] }, page([record(1)], null, { partial: true })]);
  await h.prepare({}); assert.equal((!h.state.reason && !h.state.issues.length), false);
  assert.equal(h.state.reason, "partial"); assert.equal(h.state.issues[0].code, "dropped");
});

test("原始文本逐字导出，保留管道符和转义，不掺入页面说明", async () => {
  const raw = "2026-09-12 | INFO | API - 中文字段 | key=a\\|b";
  const h = harness([status, page([{ ...record(1), raw }])]);
  await h.prepare({}); assert.equal(h.payload(), raw + "\n");
});
