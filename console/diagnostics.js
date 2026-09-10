import { createDiagnosticsQuery } from "./diagnostics-query.js?v=20260910-7";
import { escapeHtml as escape, icon, shortId, icons, localInput, diagnosticRequest, bindDiagnosticResizer, bindDiagnosticSelects, selectHtml } from "./diagnostics-ui.js?v=20260909-2";

const SOURCES = { api: "API", gateway: "网关" };
const OPTIONS = {
  period: [["24", "最近 24 小时"], ["1", "最近 1 小时"], ["6", "最近 6 小时"], ["168", "最近 7 天"], ["custom", "自定义时间"]],
  source: [["", "全部来源"], ["api", "API"], ["gateway", "网关"]],
  level: [["", "全部等级"], ["CRITICAL", "CRITICAL · 严重"], ["ERROR", "ERROR · 错误"], ["WARNING", "WARNING · 警告"], ["INFO", "INFO · 信息"], ["DEBUG", "DEBUG · 调试"]],
};
const DETAIL_SCALARS = new Set(("timestamp summary event level source schema_version record_id boot_id instance_id process_id " +
  "session_id run_id agent_id conversation_id channel_run_id account_id tool_call_id tool model_id model_name provider_name phase status " +
  "error_type error_code command mode finish_reason compaction reason duration_ms request_index attempt queue_depth input_tokens output_tokens " +
  "total_tokens requests tool_calls tool_search_calls cache_read_tokens cache_write_tokens spill_count spill_failures byte_size spilled_bytes " +
  "model_api api_path http_method http_status api_request_id tool_manifest_status tool_count unique_tool_count tools_omitted tool_names_hash").split(" "));
const TOOL_LIST_FIELDS = new Set(["redeclared_tool_names", "duplicate_tool_names", "added_tool_names", "history_tool_names", "tool_names"]);
export function diagnosticScalars(record) {
  const result = {};
  let toolBudget = 3600;
  for (const [key, value] of Object.entries(record || {})) {
    if (DETAIL_SCALARS.has(key) && value !== null && ["string", "number", "boolean"].includes(typeof value)) result[key] = value;
  }
  for (const key of TOOL_LIST_FIELDS) {
    const value = record?.[key];
    if (Array.isArray(value)) {
      const names = [];
      for (const name of value.slice(0, 256)) {
        if (typeof name !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(name) || name.length + 3 > toolBudget) continue;
        names.push(name); toolBudget -= name.length + 3;
      }
      result[key] = names.length ? names.join(" · ") : value.length ? "工具名已省略" : "无";
    }
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
    const level = String(item.level || "INFO").toUpperCase();
    return `<div class="diagnostics-row${recordKey(item) === selected ? " selected" : ""}" role="button" tabindex="0" aria-label="${escape(item.summary || item.event)}" aria-pressed="${recordKey(item) === selected}" data-record-index="${index}"><span class="diagnostics-sequence" aria-label="当前查询第 ${index + 1} 条">${index + 1}</span><time title="${escape(item.timestamp)}">${escape(diagnosticTime(item.timestamp))}</time><span class="diagnostics-level ${levelTone(level)}">${escape(level)}</span><span class="diagnostics-source">${escape(SOURCES[item.source] || item.source || "—")}</span><span class="diagnostics-summary" title="${escape(item.summary || item.event)}">${highlightedDiagnostic(item.summary || item.event || "—", keyword)}</span><span class="diagnostics-session">${item.session_id ? `<button type="button" data-open-session="${escape(item.session_id)}" title="打开会话 ${escape(item.session_id)}">${escape(String(item.session_id).slice(0, 8))}</button>` : ""}</span></div>`;
  }).join("");
}
const LABELS = { event: "事件", phase: "阶段", tool: "工具", model_name: "模型", model_id: "模型 ID", provider_name: "Provider", duration_ms: "耗时", attempt: "尝试次数", request_index: "请求序号", error_type: "错误分类", error_code: "错误代码", reason: "原因", status: "状态", agent_id: "Agent ID", session_id: "Session ID", run_id: "Run ID", tool_call_id: "Tool Call ID", conversation_id: "Conversation ID", channel_run_id: "渠道处理 ID", account_id: "渠道账号 ID", instance_id: "实例", process_id: "进程", boot_id: "启动标识", source: "来源", finish_reason: "结束原因", queue_depth: "队列长度", input_tokens: "输入 Token", output_tokens: "输出 Token", total_tokens: "总 Token", tool_calls: "工具调用数" };
Object.assign(LABELS, { model_api: "API 地址", api_path: "API 路径", http_method: "HTTP 方法", http_status: "HTTP 状态", api_request_id: "API 请求 ID", tool_manifest_status: "工具清单状态", tool_count: "工具数", unique_tool_count: "唯一工具数", tool_names: "请求工具名", duplicate_tool_names: "重复工具名", added_tool_names: "新增工具名", history_tool_names: "历史搜索工具名", redeclared_tool_names: "顶层与历史重声明", tools_omitted: "省略工具名条目", tool_names_hash: "工具清单 SHA256" });
const MANIFEST_LABELS = { complete: "已读取", unavailable: "无法读取", body_too_large: "请求超出检查上限", unsupported: "不支持的工具格式" };
export function diagnosticDetailHtml(record) {
  if (!record) return "";
  const fields = diagnosticScalars(record), used = new Set(["timestamp", "summary", "level", "schema_version", "record_id"]);
  const field = key => {
    if (!(key in fields) || fields[key] === "" || used.has(key)) return "";
    used.add(key);
    const value = key === "duration_ms" ? (typeof fields[key] === "number" && fields[key] >= 1000 ? `${(fields[key] / 1000).toFixed(1)} s` : `${fields[key]} ms`) : key === "source" ? SOURCES[fields[key]] || fields[key] : key === "tool_manifest_status" ? MANIFEST_LABELS[fields[key]] || fields[key] : fields[key];
    const copy = key === "session_id" ? `<button type="button" class="diagnostics-inline-action" data-copy-id="${escape(key)}" aria-label="复制 ${escape(LABELS[key] || key)}">${icon("copy")}</button>` : "";
    const jump = key === "session_id" ? `<button type="button" class="diagnostics-inline-action" data-open-session="${escape(value)}" aria-label="打开关联会话">${icon("external-link")}</button>` : "";
    return `<div class="diagnostics-property" data-field="${escape(key)}"><dt>${escape(LABELS[key] || key)}</dt><dd><code>${escape(value)}</code>${copy || jump ? `<span class="diagnostics-property-actions">${copy}${jump}</span>` : ""}</dd></div>`;
  };
  const group = (label, keys) => { const body = keys.map(field).join(""); return body ? `<section class="diagnostics-field-group">${label ? `<h3>${label}</h3>` : ""}<dl>${body}</dl></section>` : ""; };
  const relations = group("会话", ["session_id", "run_id"]);
  const execution = group("执行结果", ["tool", "model_name", "provider_name", "duration_ms", "attempt", "status", "phase", "finish_reason", "reason", "error_type", "error_code"]);
  const modelApi = group("模型 API", ["model_api", "api_path", "http_method", "http_status", "api_request_id", "tool_manifest_status", "tool_count", "unique_tool_count", "redeclared_tool_names", "duplicate_tool_names", "added_tool_names", "history_tool_names", "tool_names", "tools_omitted", "tool_names_hash"]);
  const service = group("服务信息", ["source", "event", "model_id", "request_index", "tool_call_id", "conversation_id", "channel_run_id", "account_id", "agent_id", "instance_id", "process_id", "boot_id"]);
  const extra = group("其他信息", Object.keys(fields).filter(key => !used.has(key)));
  const level = String(record.level || "INFO").toUpperCase();
  return `<header class="diagnostics-drawer-head"><h2>日志详情</h2><button type="button" class="diagnostics-inline-action" data-close-detail aria-label="关闭日志详情">${icon("x")}</button></header><div class="diagnostics-drawer-body"><div class="diagnostics-facts"><span class="diagnostics-level ${levelTone(level)}">${escape(level)}</span><time title="${escape(record.timestamp)}">${escape(diagnosticTime(record.timestamp))}</time></div><h3 class="diagnostics-full-summary">${escape(record.summary || record.event)}</h3>${relations}${execution}${modelApi}${service}${extra}</div>`;
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
  let rendered = "", selected = "", fixedRange = null;
  const query = createDiagnosticsQuery({ request: diagnosticRequest, onChange: render });
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
      return query.query(filters);
    } catch (error) { query.changeFilters(); notice.classList.remove("hidden"); notice.dataset.kind = "error"; notice.textContent = error.message; }
  }
  function updateSelect(name) { const host = page.querySelector(`[data-diagnostic-select="${name}"]`); host.innerHTML = selectHtml(name, { period: "时间范围", source: "来源", level: "等级" }[name], values[name], OPTIONS[name]); host.querySelector(".console-select-trigger").insertAdjacentHTML("afterbegin", icon({ period: "clock-3", source: "database", level: "triangle-alert" }[name])); }
  const menus = bindDiagnosticSelects(page, (name, value) => { values[name] = value; if (name === "period") fixedRange = null; updateSelect(name); byId("diagnosticsCustomTime").classList.toggle("hidden", values.period !== "custom"); if (name === "period" && value === "custom") byId("diagnosticsStart").focus(); query.changeFilters(); icons(); });
  Object.keys(OPTIONS).forEach(updateSelect);
  window.addEventListener("pagehide", () => { query.setActive(false); menus.close(); });
  form.addEventListener("input", event => { if (["diagnosticsStart", "diagnosticsEnd"].includes(event.target.id)) fixedRange = null; query.changeFilters(); });
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
    if (button?.dataset.copyId === "session_id") { try { await navigator.clipboard.writeText(String(item.session_id)); byId("diagnosticsCopyStatus").textContent = "已复制"; } catch { byId("diagnosticsCopyStatus").textContent = "复制失败，请手动选择。"; } return; }
    if (row) { selected = recordKey(item); render(query.state); }
  });
  list.addEventListener("keydown", event => { if ((event.key === "Enter" || event.key === " ") && event.target.matches("[data-record-index]")) { event.preventDefault(); event.target.click(); } });
  bindDiagnosticResizer(byId("diagnosticsResizer"), split, "--diagnostic-drawer-width", 436);
  function activate(filters = {}, snapshot = null, hasBack = false) {
    query.setActive(true);
    Object.assign(values, { period: filters.period || (filters.start ? "custom" : "24"), source: filters.source || "", level: filters.level || "" }, snapshot?.values || {});
    const source = snapshot?.inputs || filters;
    byId("diagnosticsSession").value = source.session_id || ""; byId("diagnosticsKeyword").value = source.q || "";
    byId("diagnosticsStart").value = localInput(source.start); byId("diagnosticsEnd").value = localInput(source.end);
    Object.keys(OPTIONS).forEach(updateSelect); byId("diagnosticsCustomTime").classList.toggle("hidden", values.period !== "custom"); byId("diagnosticsBack").classList.toggle("hidden", !hasBack);
    fixedRange = source.start && source.end ? { start: source.start, end: source.end } : null; selected = snapshot?.selected || "";
    if (snapshot?.query) { query.restore(snapshot.query); page.querySelector(".diagnostics-log-view").scrollTop = snapshot.scroll || 0; }
    else void search();
  }
  return { activate, deactivate: () => { query.setActive(false); menus.close(); }, snapshot: () => ({ values: { ...values }, inputs: { ...inputs(), ...fixedRange }, selected, query: { ...query.state, items: [...query.state.items] }, scroll: page.querySelector(".diagnostics-log-view").scrollTop }), clear: () => { selected = ""; query.changeFilters(); query.setActive(false); } };
}
