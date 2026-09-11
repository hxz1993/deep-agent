import { createDiagnosticsExport, logExportSummary } from "./diagnostics-export.js?v=20260912-21";
import { createDiagnosticsQuery } from "./diagnostics-query.js?v=20260912-21";
import { escapeHtml as escape, icon, icons, localInput, diagnosticRequest, bindDiagnosticResizer, bindDiagnosticSelects, selectHtml } from "./diagnostics-ui.js?v=20260912-21";

const SOURCES = { api: "API", gateway: "网关" };
const OPTIONS = {
  period: [["24", "最近 24 小时"], ["1", "最近 1 小时"], ["6", "最近 6 小时"], ["168", "最近 7 天"], ["custom", "自定义时间"]],
  source: [["", "全部来源"], ["api", "API"], ["gateway", "网关"]],
  level: [["", "全部等级"], ["CRITICAL", "CRITICAL · 严重"], ["ERROR", "ERROR · 错误"], ["WARNING", "WARNING · 警告"], ["INFO", "INFO · 信息"], ["DEBUG", "DEBUG · 调试"]],
};
const DETAIL_SCALARS = new Set(("timestamp summary message raw request_id event level source schema_version record_id boot_id instance_id process_id " +
  "session_id run_id agent_id conversation_id channel_run_id account_id tool_call_id tool model_id model_name provider_name phase status " +
  "error_type error_code command mode finish_reason compaction reason duration_ms request_index attempt queue_depth input_tokens output_tokens " +
  "total_tokens requests tool_calls tool_search_calls cache_read_tokens cache_write_tokens spill_count spill_failures byte_size spilled_bytes " +
  "model_api api_path http_method http_status api_request_id tool_manifest_status tool_count unique_tool_count tools_omitted tool_names_hash").split(" "));
const TOOL_LIST_FIELDS = new Set(["redeclared_tool_names", "duplicate_tool_names", "added_tool_names", "history_tool_names", "tool_names"]);
export function diagnosticScalars(record) {
  const result = {};
  for (const [key, value] of Object.entries(record || {})) {
    if (DETAIL_SCALARS.has(key) && value !== null && ["string", "number", "boolean"].includes(typeof value)) result[key] = value;
  }
  let budget = 3600;
  for (const key of TOOL_LIST_FIELDS) {
    if (!Array.isArray(record?.[key])) continue;
    const names = [];
    for (const name of record[key].slice(0, 256)) {
      if (typeof name !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(name) || name.length + 3 > budget) continue;
      names.push(name); budget -= name.length + 3;
    }
    result[key] = names.join(", ");
  }
  return result;
}
export function diagnosticTime(timestamp) {
  const date = new Date(timestamp);
  if (!Number.isFinite(date.getTime())) return String(timestamp || "—");
  const pad = value => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
export function highlightedDiagnostic(text, query = "") {
  const value = String(text || ""), term = query.trim();
  if (!term) return escape(value);
  const lower = value.toLowerCase(), needle = term.toLowerCase();
  let start = 0, found, result = "";
  while ((found = lower.indexOf(needle, start)) >= 0) {
    result += escape(value.slice(start, found)) + `<mark>${escape(value.slice(found, found + term.length))}</mark>`;
    start = found + term.length;
  }
  return result + escape(value.slice(start));
}
const recordKey = item => `${item.boot_id}:${item.record_id}`;
const levelTone = value => ["ERROR", "CRITICAL"].includes(value) ? "error" : value === "WARNING" ? "warning" : value === "DEBUG" ? "debug" : "info";
export function diagnosticRowsHtml(items, { selected = "", keyword = "" } = {}) {
  return items.map((item, index) => {
    const level = String(item.level || "—").toUpperCase();
    const message = item.message || item.summary || item.raw || item.event || "—";
    return `<div class="diagnostics-row${recordKey(item) === selected ? " selected" : ""}" role="button" tabindex="0" aria-label="${escape(message)}" aria-pressed="${recordKey(item) === selected}" data-record-index="${index}"><span class="diagnostics-sequence" aria-label="当前查询第 ${index + 1} 条">${index + 1}</span><time title="${escape(item.timestamp)}">${escape(diagnosticTime(item.timestamp))}</time><span class="diagnostics-level ${levelTone(level)}">${escape(level)}</span><span class="diagnostics-source">${escape(SOURCES[item.source] || item.source || "—")}</span><span class="diagnostics-summary" title="${escape(message)}">${highlightedDiagnostic(message, keyword)}</span><span class="diagnostics-session">${item.session_id ? `<button type="button" data-open-session="${escape(item.session_id)}" title="打开会话 ${escape(item.session_id)}">${escape(String(item.session_id).slice(0, 8))}</button>` : ""}</span></div>`;
  }).join("");
}
export function diagnosticDetailHtml(record) {
  if (!record) return "";
  const fields = diagnosticScalars(record);
  const body = Object.entries(fields).filter(([key]) => key !== "raw").map(([key, value]) => {
    const jump = key === "session_id" ? `<button type="button" class="diagnostics-inline-action" data-open-session="${escape(value)}" aria-label="打开关联会话">${icon("external-link")}</button>` : "";
    return `<div class="diagnostics-property" data-field="${escape(key)}"><dt>${escape(key)}</dt><dd><code>${escape(String(value))}</code><span class="diagnostics-property-actions"><button type="button" class="diagnostics-inline-action" data-copy-id="${escape(key)}" aria-label="复制 ${escape(key)}">${icon("copy")}</button>${jump}</span></dd></div>`;
  }).join("");
  return `<header class="diagnostics-drawer-head"><h2>日志详情</h2><button type="button" class="diagnostics-inline-action" data-close-detail aria-label="关闭日志详情">${icon("x")}</button></header><div class="diagnostics-drawer-body"><div class="diagnostics-raw-head"><span>raw</span><button type="button" class="diagnostics-inline-action" data-copy-id="raw" aria-label="复制原始日志">${icon("copy")}</button></div><pre class="diagnostics-raw-record">${escape(record.raw || "")}</pre><section class="diagnostics-field-group"><dl>${body}</dl></section></div>`;
}
export function diagnosticFilters(values, now = new Date()) {
  let start, end;
  if (values.period === "custom") {
    if (!values.start || !values.end) throw new Error("请选择开始和结束时间。");
    start = new Date(values.start); end = new Date(values.end);
  } else { end = new Date(now); start = new Date(end.getTime() - Number(values.period || 24) * 3600000); }
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || start >= end) throw new Error("开始时间必须早于结束时间。");
  return { start: start.toISOString(), end: end.toISOString(), source: values.source || "", level: values.level || "", session_id: values.session_id || "", q: values.q || "" };
}

export function initDiagnosticLogs({ page, onQuery = () => {}, onOpenSession = () => {}, onBack = () => {} }) {
  const byId = id => page.querySelector(`#${id}`);
  const form = byId("diagnosticsFilters"), list = byId("diagnosticsRecords"), notice = byId("diagnosticsNotice"), empty = byId("diagnosticsEmpty"), more = byId("diagnosticsMore"), meta = byId("diagnosticsMeta"), drawer = byId("diagnosticsDrawer"), split = byId("diagnosticsSplit");
  const values = { period: "24", source: "", level: "" };
  let rendered = "", selected = "", fixedRange = null, view = "list", active = false;
  const query = createDiagnosticsQuery({ request: diagnosticRequest, onChange: render });
  const exportDialog = byId("diagnosticsExportDialog");
  const exporter = createDiagnosticsExport({ request: diagnosticRequest, onChange: state => {
    byId("diagnosticsExportStatus").textContent = logExportSummary(state);
    byId("diagnosticsExportConfirm").disabled = state.phase !== "ready" || !state.items.length;
    byId("diagnosticsExportConfirm").textContent = state.reason ? "导出已准备日志" : "导出日志";
  } });
  const rawReader = createDiagnosticsExport({ request: diagnosticRequest, onChange: renderRaw });
  function renderRaw(state) {
    const text = state.items.map(item => item.raw).join("\n") + (state.items.length ? "\n" : "");
    const area = byId("diagnosticsRawText");
    if (area.value !== text) { const top = area.scrollTop, left = area.scrollLeft; area.value = text; area.scrollTop = top; area.scrollLeft = left; }
    byId("diagnosticsRawStatus").textContent = logExportSummary(state).replace("预计导出", "已加载").replace("正在统计匹配日志，已准备", "正在加载，已读取");
    if (view === "raw") {
      byId("diagnosticsQuery").disabled = state.phase === "loading";
      page.setAttribute("aria-busy", String(state.phase === "loading"));
    }
  }
  function setView(next) {
    view = next;
    for (const name of ["list", "raw"]) {
      const button = byId(name === "list" ? "diagnosticsListView" : "diagnosticsRawView");
      button.classList.toggle("active", view === name);
      button.setAttribute("aria-pressed", String(view === name));
    }
    byId("diagnosticsListPane").hidden = view !== "list";
    byId("diagnosticsRawPane").hidden = view !== "raw";
    split.classList.toggle("raw-view", view === "raw");
    if (active) {
      if (view === "raw") query.cancel();
      void search();
    }
  }
  byId("diagnosticsListView").addEventListener("click", () => setView("list"));
  byId("diagnosticsRawView").addEventListener("click", () => setView("raw"));
  function invalidate() { rawReader.cancel(); closeExport(); query.changeFilters(); }
  function closeExport() { exporter.cancel(); if (exportDialog.open) exportDialog.close(); }
  byId("diagnosticsExport").addEventListener("click", () => {
    try {
      const filters = fixedRange ? diagnosticFilters({ ...inputs(), period: "custom", ...fixedRange }) : diagnosticFilters(inputs());
      menus.close();
      byId("diagnosticsExportRange").textContent = `查询时间：${diagnosticTime(filters.start)} ～ ${diagnosticTime(filters.end)}`;
      byId("diagnosticsExportFilters").textContent = `${SOURCES[filters.source] || "全部来源"} · ${filters.level || "全部等级"}${filters.session_id ? ` · 会话 ${filters.session_id}` : ""}${filters.q ? ` · 关键词 ${filters.q}` : ""}`;
      exportDialog.showModal();
      void exporter.prepare(filters);
    } catch (error) { notice.classList.remove("hidden"); notice.dataset.kind = "error"; notice.textContent = error.message; }
  });
  ["diagnosticsExportClose", "diagnosticsExportCancel"].forEach(id => byId(id).addEventListener("click", closeExport));
  exportDialog.addEventListener("cancel", event => { event.preventDefault(); closeExport(); });
  exportDialog.addEventListener("close", () => exporter.cancel());
  byId("diagnosticsExportConfirm").addEventListener("click", () => {
    const payload = exporter.payload();
    if (!payload) return;
    const url = URL.createObjectURL(new Blob([payload], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url; link.download = `deep-agent-logs-${new Date().toISOString().replace(/[:.]/g, "-")}.log`;
    document.body.append(link);
    try { link.click(); closeExport(); }
    finally { link.remove(); setTimeout(() => URL.revokeObjectURL(url), 0); }
  });
  function render(state) {
    byId("diagnosticsQuery").disabled = state.busy;
    page.setAttribute("aria-busy", String(state.busy));
    byId("diagnosticsInstance").textContent = "查看会话执行过程，排查运行问题。";
    if (!state.items.some(item => recordKey(item) === selected)) selected = "";
    const fingerprint = `${state.items.map(recordKey).join("|")}:${selected}:${state.filters.q || ""}`;
    if (rendered !== fingerprint) { rendered = fingerprint; list.innerHTML = diagnosticRowsHtml(state.items, { selected, keyword: state.filters.q || "" }); renderDetail(); }
    notice.classList.toggle("hidden", !state.notice); notice.dataset.kind = state.notice?.kind || "info";
    notice.textContent = state.notice ? `${state.notice.title}：${state.notice.message}` : "";
    if (state.status?.issues?.length) notice.textContent += ` ${state.status.issues.map(issue => `${SOURCES[issue.source] || "日志"}: ${issue.code}${Number.isFinite(issue.dropped_records) ? ` (${issue.dropped_records})` : ""}`).join("；")}`;
    empty.classList.toggle("hidden", Boolean(state.items.length));
    empty.textContent = state.busy ? "正在查询日志…" : state.notice ? state.notice.title : state.partial ? "当前扫描范围未匹配，可继续查询。" : "没有匹配的日志，可调整条件；会话历史可能早于日志保留范围。";
    meta.textContent = `已加载 ${state.items.length} 条${state.busy ? " · 正在查询…" : ""}`;
    byId("diagnosticsRangeSeparator").hidden = !state.filters.start;
    byId("diagnosticsQueryRange").textContent = state.filters.start ? `查询时间：${diagnosticTime(state.filters.start)} ～ ${diagnosticTime(state.filters.end)}` : "";
    const partial = byId("diagnosticsPartial"); partial.classList.toggle("hidden", !state.partial && !state.capped);
    partial.textContent = state.capped ? "已达到页面 5,000 条展示上限，请缩小查询范围。" : state.nextCursor ? "本次仅扫描部分范围，可继续查询。" : "本次仅扫描部分范围，请缩小条件并检查日志状态。";
    const retention = Number(state.status?.retention_days);
    meta.title = Number.isFinite(retention) && retention > 0 ? `日志保留策略：最近 ${retention} 天；更早的会话可能没有对应日志。` : "";
    more.classList.toggle("hidden", !state.nextCursor); more.disabled = state.busy;
    more.innerHTML = `${state.busy ? "查询中…" : state.partial ? "继续查询" : "加载更多"}${icon("arrow-down")}`;
    icons();
  }
  function renderDetail() { const item = query.state.items.find(record => recordKey(record) === selected); drawer.innerHTML = diagnosticDetailHtml(item); split.classList.toggle("drawer-open", Boolean(item)); icons(); }
  const inputs = () => ({ ...values, start: byId("diagnosticsStart").value, end: byId("diagnosticsEnd").value, session_id: byId("diagnosticsSession").value.trim(), q: byId("diagnosticsKeyword").value.trim() });
  function search({ refresh = false } = {}) {
    try {
      const filters = fixedRange && (!refresh || values.period === "custom") ? diagnosticFilters({ ...inputs(), period: "custom", ...fixedRange }) : diagnosticFilters(inputs());
      fixedRange = { start: filters.start, end: filters.end }; menus.close(); onQuery({ ...filters, period: values.period });
      rawReader.cancel(); closeExport();
      if (view === "raw") { query.cancel(); return rawReader.prepare(filters); }
      return query.query(filters);
    } catch (error) { invalidate(); notice.classList.remove("hidden"); notice.dataset.kind = "error"; notice.textContent = error.message; if (view === "raw") byId("diagnosticsRawStatus").textContent = error.message; }
  }
  function updateSelect(name) { const host = page.querySelector(`[data-diagnostic-select="${name}"]`); host.innerHTML = selectHtml(name, { period: "时间范围", source: "来源", level: "等级" }[name], values[name], OPTIONS[name]); host.querySelector(".console-select-trigger").insertAdjacentHTML("afterbegin", icon({ period: "clock-3", source: "database", level: "triangle-alert" }[name])); }
  const menus = bindDiagnosticSelects(page, (name, value) => { values[name] = value; if (name === "period") fixedRange = null; updateSelect(name); byId("diagnosticsCustomTime").classList.toggle("hidden", values.period !== "custom"); if (name === "period" && value === "custom") byId("diagnosticsStart").focus(); invalidate(); icons(); });
  Object.keys(OPTIONS).forEach(updateSelect);
  window.addEventListener("pagehide", () => { active = false; rawReader.cancel(); closeExport(); query.setActive(false); menus.close(); });
  form.addEventListener("input", event => { if (["diagnosticsStart", "diagnosticsEnd"].includes(event.target.id)) fixedRange = null; invalidate(); });
  form.addEventListener("submit", event => { event.preventDefault(); void search({ refresh: true }); });
  byId("diagnosticsBack").addEventListener("click", onBack);
  byId("diagnosticsReset").addEventListener("click", () => { form.reset(); Object.assign(values, { period: "24", source: "", level: "" }); fixedRange = null; Object.keys(OPTIONS).forEach(updateSelect); byId("diagnosticsCustomTime").classList.add("hidden"); void search(); });
  more.addEventListener("click", () => void query.query(undefined, { more: true }));
  page.addEventListener("click", async event => {
    const button = event.target.closest("button"), row = event.target.closest("[data-record-index]");
    const item = row ? query.state.items[Number(row.dataset.recordIndex)] : query.state.items.find(record => recordKey(record) === selected);
    if (button?.hasAttribute("data-close-detail")) { selected = ""; render(query.state); return; }
    if (!item) return;
    if (button?.hasAttribute("data-open-session")) { event.stopPropagation(); onOpenSession(item.session_id); return; }
    if (button?.dataset.copyId && Object.hasOwn(diagnosticScalars(item), button.dataset.copyId)) { try { await navigator.clipboard.writeText(String(diagnosticScalars(item)[button.dataset.copyId])); byId("diagnosticsCopyStatus").textContent = "已复制"; } catch { byId("diagnosticsCopyStatus").textContent = "复制失败，请手动选择。"; } return; }
    if (row) { selected = recordKey(item); render(query.state); }
  });
  list.addEventListener("keydown", event => { if ((event.key === "Enter" || event.key === " ") && event.target.matches("[data-record-index]")) { event.preventDefault(); event.target.click(); } });
  bindDiagnosticResizer(byId("diagnosticsResizer"), split, "--diagnostic-drawer-width", 436);
  function activate(filters = {}, snapshot = null, hasBack = false) {
    active = true; rawReader.cancel();
    query.setActive(true);
    Object.assign(values, { period: filters.period || (filters.start ? "custom" : "24"), source: filters.source || "", level: filters.level || "" }, snapshot?.values || {});
    const source = snapshot?.inputs || filters;
    byId("diagnosticsSession").value = source.session_id || ""; byId("diagnosticsKeyword").value = source.q || "";
    byId("diagnosticsStart").value = localInput(source.start); byId("diagnosticsEnd").value = localInput(source.end);
    Object.keys(OPTIONS).forEach(updateSelect); byId("diagnosticsCustomTime").classList.toggle("hidden", values.period !== "custom"); byId("diagnosticsBack").classList.toggle("hidden", !hasBack);
    fixedRange = source.start && source.end ? { start: source.start, end: source.end } : null; selected = snapshot?.selected || "";
    if (snapshot?.query && view === "list") { query.restore(snapshot.query); page.querySelector(".diagnostics-log-view").scrollTop = snapshot.scroll || 0; }
    else void search();
  }
  return { activate, deactivate: () => { active = false; rawReader.cancel(); closeExport(); query.setActive(false); menus.close(); }, snapshot: () => ({ values: { ...values }, inputs: { ...inputs(), ...fixedRange }, selected, query: { ...query.state, items: [...query.state.items] }, scroll: page.querySelector(".diagnostics-log-view").scrollTop }), clear: () => { active = false; rawReader.cancel(); closeExport(); selected = ""; query.changeFilters(); query.setActive(false); } };
}
