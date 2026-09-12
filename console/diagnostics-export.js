import { buildDiagnosticsQuery, diagnosticsError, diagnosticsStatusNotice } from "./diagnostics-query.js?v=20260912-21";

export const LOG_EXPORT_LIMITS = Object.freeze({ records: 5000, bytes: 10 * 1024 * 1024, pages: 100, milliseconds: 60000 });
const REASONS = { records: "达到 5,000 条上限", bytes: "达到 10 MB 大小上限", pages: "达到扫描上限", time: "达到 60 秒读取上限", partial: "部分日志未能完整读取" };
export function logExportSummary(state) {
  if (state.phase === "loading") return `正在统计匹配日志，已准备 ${state.items.length.toLocaleString()} 条…`;
  if (state.phase === "error") return state.error;
  if (state.phase !== "ready") return "";
  const count = state.items.length.toLocaleString();
  return `预计导出 ${count} 条日志${state.reason ? `；${REASONS[state.reason]}，结果已截断。` : "。"}${state.issues.length ? "日志源存在异常，结果可能不完整。" : ""}`;
}

// Preflight buffers a bounded snapshot; confirmation downloads exactly that snapshot.
export function createDiagnosticsExport({ request, projectRecord = item => item, onChange = () => {}, limits = LOG_EXPORT_LIMITS }) {
  let generation = 0, controller = null;
  const state = { phase: "idle", items: [], filters: {}, reason: "", issues: [], instanceId: "", error: "" };
  const notify = () => onChange(state);
  function cancel() {
    generation += 1; controller?.abort(); controller = null;
    Object.assign(state, { phase: "idle", items: [], reason: "", error: "" });
    notify();
  }
  async function prepare(filters) {
    cancel();
    const own = generation;
    controller = new AbortController();
    const ownController = controller;
    const signal = ownController.signal;
    const current = () => own === generation;
    Object.assign(state, { phase: "loading", filters: Object.fromEntries(new URLSearchParams(buildDiagnosticsQuery(filters))), issues: [], instanceId: "" });
    delete state.filters.limit;
    notify();
    let timer, timedOut = false, bytes = 0;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => { timedOut = true; ownController.abort(); reject(new Error("timeout")); }, limits.milliseconds);
    });
    const aborted = new Promise((_, reject) => signal.addEventListener("abort", () => reject(new Error("cancelled")), { once: true }));
    const get = path => Promise.race([request(path, signal), timeout, aborted]);
    try {
      const status = await get("api/v1/diagnostics/logs/status");
      if (!current()) return;
      const notice = diagnosticsStatusNotice(status);
      if (!status?.enabled || !status?.available) throw new Error(notice.message);
      state.instanceId = status.instance_id || "";
      state.issues = (status.issues || []).map(issue => ({ source: issue.source, code: issue.code }));
      const seen = new Set(), cursors = new Set();
      let cursor = null;
      for (let page = 0; page < limits.pages; page += 1) {
        const result = await get(`api/v1/diagnostics/logs?${buildDiagnosticsQuery(state.filters, cursor)}`);
        if (!current()) return;
        if (!Array.isArray(result?.items)) throw new Error("日志响应格式异常，请重新导出。");
        if (state.instanceId && state.instanceId !== result.instance_id) throw Object.assign(new Error("Instance changed"), { status: 409 });
        state.instanceId = result.instance_id || state.instanceId;
        for (const item of result.items) {
          const key = `${item.boot_id}:${item.record_id}`;
          if (seen.has(key)) continue;
          if (state.items.length >= limits.records) { state.reason = "records"; break; }
          const projected = projectRecord(item);
          if (typeof projected.raw !== "string") throw new Error("Missing raw log text");
          const size = new TextEncoder().encode(projected.raw).byteLength + 1;
          if (bytes + size > limits.bytes) { state.reason = "bytes"; break; }
          bytes += size; seen.add(key); state.items.push(projected);
        }
        if (!state.reason && state.items.length >= limits.records && result.next_cursor) state.reason = "records";
        notify();
        if (state.reason) break;
        if (!result.next_cursor) { if (result.partial) state.reason = "partial"; break; }
        if (cursors.has(result.next_cursor) || page + 1 >= limits.pages) { state.reason = "pages"; break; }
        cursors.add(result.next_cursor); cursor = result.next_cursor;
      }
      if (current()) state.phase = "ready";
    } catch (error) {
      if (!current()) return;
      if (timedOut && state.items.length) { state.reason = "time"; state.phase = "ready"; }
      else {
        state.phase = "error"; state.items = [];
        state.error = timedOut ? "读取超时，请缩小查询范围后重试。" : error.status ? diagnosticsError(error).message : "日志准备失败，请检查日志状态并重试。";
      }
    } finally {
      clearTimeout(timer);
      if (current()) { controller = null; notify(); }
    }
  }
  function payload() {
    if (state.phase !== "ready" || !state.items.length) return null;
    return state.items.map(item => item.raw).join("\n") + "\n";
  }
  return { state, prepare, cancel, payload };
}
