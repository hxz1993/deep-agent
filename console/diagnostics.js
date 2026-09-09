import { createDiagnosticsQuery } from "./diagnostics-query.js?v=20260909-1";

const SOURCE_LABELS = { api: "API", gateway: "企微网关" };
const FILTER_OPTIONS = {
  period: [["24", "最近 24 小时"], ["1", "最近 1 小时"], ["6", "最近 6 小时"], ["168", "最近 7 天"], ["custom", "自定义时间"]],
  source: [["", "全部来源"], ["api", "API"], ["gateway", "企微网关"]],
  level: [["", "全部等级"], ["ERROR", "ERROR · 错误"], ["WARNING", "WARNING · 警告"], ["INFO", "INFO · 信息"], ["DEBUG", "DEBUG · 调试"]],
};
const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");

export function diagnosticScalars(record) {
  return Object.fromEntries(Object.entries(record || {}).filter(([, value]) => value === null || ["string", "number", "boolean"].includes(typeof value)));
}

export function diagnosticTime(timestamp) {
  const date = new Date(timestamp);
  if (!Number.isFinite(date.getTime())) return String(timestamp || "—");
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
}

export function diagnosticRowsHtml(items) {
  return items.map((item, index) => {
    const level = String(item.level || "INFO").toUpperCase();
    const tone = ["ERROR", "WARNING", "INFO", "DEBUG"].includes(level) ? level.toLowerCase() : "info";
    const fields = Object.entries(diagnosticScalars(item)).map(([key, value]) => `<div><dt>${escape(key)}</dt><dd><code>${escape(value ?? "—")}</code>${["session_id", "run_id"].includes(key) && value ? `<button type="button" class="diagnostics-inline-action" data-filter-id="${key}" title="按此 ${escape(key)} 查询"><i data-lucide="filter" aria-hidden="true"></i><span>筛选</span></button><button type="button" class="diagnostics-inline-action" data-copy-id="${key}" title="复制 ${escape(key)}"><i data-lucide="copy" aria-hidden="true"></i><span class="diagnostics-sr-only">复制</span></button>` : ""}</dd></div>`).join("");
    return `<details class="diagnostics-record" data-record-index="${index}">
      <summary class="diagnostics-row"><span class="diagnostics-chevron"><i data-lucide="chevron-right" aria-hidden="true"></i></span><time title="${escape(item.timestamp)}">${escape(diagnosticTime(item.timestamp))}</time><span class="diagnostics-level ${tone}">${escape(level)}</span><span class="diagnostics-source">${escape(SOURCE_LABELS[item.source] || item.source || "—")}</span><span class="diagnostics-summary"><span>${escape(item.summary || item.event || "—")}</span><code>${escape(item.event || "")}</code></span>${Number.isFinite(item.duration_ms) ? `<span class="diagnostics-duration">${escape((item.duration_ms / 1000).toFixed(1))}s</span>` : ""}</summary>
      <div class="diagnostics-detail"><div class="diagnostics-detail-heading"><strong>结构化记录</strong><button type="button" class="diagnostics-inline-action" data-copy-record><i data-lucide="copy" aria-hidden="true"></i><span>复制记录</span></button></div><dl>${fields}</dl></div>
    </details>`;
  }).join("");
}

export function diagnosticFilters(values, now = new Date()) {
  let start;
  let end;
  if (values.period === "custom") {
    if (!values.start || !values.end) throw new Error("请选择开始和结束时间。");
    start = new Date(values.start);
    end = new Date(values.end);
  } else {
    end = new Date(now);
    start = new Date(end.getTime() - Number(values.period || 24) * 3600000);
  }
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || start >= end) throw new Error("开始时间必须早于结束时间。");
  return { start: start.toISOString(), end: end.toISOString(), source: values.source || "", level: values.level || "", session_id: values.session_id || "", run_id: values.run_id || "", q: values.q || "" };
}

let diagnostics = null;
export function setDiagnosticsActive(active) {
  if (!diagnostics || diagnostics.state.active === active) return;
  diagnostics.setActive(active);
  if (active) diagnostics.search();
  else diagnostics.closeMenus();
}

export function initDiagnostics() {
  const page = document.getElementById("page-diagnostics");
  if (!page || diagnostics) return;
  const byId = (id) => page.querySelector(`#${id}`);
  const form = byId("diagnosticsFilters");
  const list = byId("diagnosticsRecords");
  const notice = byId("diagnosticsNotice");
  const empty = byId("diagnosticsEmpty");
  const more = byId("diagnosticsMore");
  const meta = byId("diagnosticsMeta");
  const values = { period: "24", source: "", level: "" };
  let renderedItems = null;

  async function request(path, signal) {
    const transport = new AbortController();
    const abort = () => transport.abort();
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
    const timeout = window.setTimeout(abort, 15000);
    try {
      const response = await window.daFetch(window.serviceUrl(path), { signal: transport.signal, includeContext: false, reportStatus: false, cache: "no-store" });
      if (!response.ok) throw Object.assign(new Error(`HTTP ${response.status}`), { status: response.status });
      return await response.json();
    } finally {
      window.clearTimeout(timeout);
      signal.removeEventListener("abort", abort);
    }
  }

  function render(state) {
    byId("diagnosticsQuery").disabled = state.busy;
    byId("diagnosticsRefresh").disabled = state.busy;
    page.setAttribute("aria-busy", String(state.busy));
    const instance = state.instanceId ? `当前实例 ${state.instanceId}` : "当前部署实例";
    byId("diagnosticsInstance").textContent = `${instance} · API / 企微网关`;
    const fingerprint = state.items.map((item) => `${item.boot_id}:${item.record_id}`).join("|");
    if (renderedItems !== fingerprint) {
      renderedItems = fingerprint;
      const opened = new Set([...list.querySelectorAll("details[open]")].map((row) => row.dataset.recordIndex));
      list.innerHTML = diagnosticRowsHtml(state.items);
      for (const row of list.querySelectorAll("details")) row.open = opened.has(row.dataset.recordIndex);
      window.lucide?.createIcons();
    }
    notice.classList.toggle("hidden", !state.notice);
    notice.dataset.kind = state.notice?.kind || "info";
    notice.textContent = state.notice ? `${state.notice.title}：${state.notice.message}` : "";
    if (state.status?.issues?.length) {
      const issues = state.status.issues.map((issue) => `${SOURCE_LABELS[issue.source] || issue.source || "日志"}: ${issue.code}${Number.isFinite(issue.dropped_records) ? ` (${issue.dropped_records})` : ""}`).join("；");
      notice.textContent += ` ${issues}`;
    }
    empty.classList.toggle("hidden", Boolean(state.items.length));
    empty.textContent = state.busy ? "正在查询日志…" : state.notice ? state.notice.title : "没有匹配的日志，可调整时间范围或筛选条件。";
    meta.textContent = state.busy ? "正在查询…" : `已展示 ${state.items.length} 条${state.range ? ` · ${diagnosticTime(state.range.start)} — ${diagnosticTime(state.range.end)}` : ""}`;
    const partial = byId("diagnosticsPartial");
    partial.classList.toggle("hidden", !state.partial && !state.capped);
    partial.textContent = state.capped ? "已达到页面 5,000 条展示上限，请缩小查询范围。" : state.nextCursor ? "本次仅扫描了部分范围，可继续查询或缩小筛选范围。" : "本次仅扫描了部分范围，请缩小来源或查询范围，并检查日志状态。";
    more.classList.toggle("hidden", !state.nextCursor);
    more.disabled = state.busy;
    more.textContent = state.busy ? "查询中…" : state.partial ? "继续查询" : "加载更多";
  }

  const query = createDiagnosticsQuery({ request, onChange: render });
  const search = () => {
    try {
      const filters = diagnosticFilters({ ...values, start: byId("diagnosticsStart").value, end: byId("diagnosticsEnd").value, session_id: byId("diagnosticsSession").value, run_id: byId("diagnosticsRun").value, q: byId("diagnosticsKeyword").value });
      closeMenus();
      return query.query(filters);
    } catch (error) {
      query.changeFilters();
      notice.classList.remove("hidden");
      notice.dataset.kind = "error";
      notice.textContent = error.message;
    }
  };
  function closeMenus() {
    for (const menu of page.querySelectorAll(".console-select-menu")) menu.classList.add("hidden");
    for (const trigger of page.querySelectorAll(".console-select-trigger")) trigger.setAttribute("aria-expanded", "false");
  }
  function updateSelect(name) {
    const host = page.querySelector(`[data-diagnostic-select="${name}"]`);
    const options = FILTER_OPTIONS[name];
    const selected = options.find(([value]) => value === values[name]);
    host.querySelector(".console-select-trigger span").textContent = selected[1];
    host.querySelector(".console-select-menu").innerHTML = options.map(([value, label]) => `<button class="console-select-option${value === values[name] ? " selected" : ""}" type="button" role="option" aria-selected="${value === values[name]}" data-value="${escape(value)}"><span>${escape(label)}</span></button>`).join("");
  }
  for (const host of page.querySelectorAll("[data-diagnostic-select]")) {
    const name = host.dataset.diagnosticSelect;
    const trigger = host.querySelector(".console-select-trigger");
    const menu = host.querySelector(".console-select-menu");
    const open = () => {
      closeMenus();
      menu.classList.remove("open-up");
      menu.classList.remove("hidden");
      trigger.setAttribute("aria-expanded", "true");
      const bounds = trigger.getBoundingClientRect();
      if (menu.getBoundingClientRect().height > window.innerHeight - bounds.bottom - 8 && bounds.top > window.innerHeight - bounds.bottom) menu.classList.add("open-up");
      menu.querySelector('[aria-selected="true"]')?.focus();
    };
    trigger.addEventListener("click", () => menu.classList.contains("hidden") ? open() : closeMenus());
    trigger.addEventListener("keydown", (event) => { if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); open(); } });
    menu.addEventListener("keydown", (event) => {
      const options = [...menu.querySelectorAll("[role=option]")];
      let index = options.indexOf(document.activeElement);
      if (event.key === "ArrowDown") index = (index + 1) % options.length;
      else if (event.key === "ArrowUp") index = (index - 1 + options.length) % options.length;
      else if (event.key === "Home") index = 0;
      else if (event.key === "End") index = options.length - 1;
      else if (event.key === "Escape") { closeMenus(); trigger.focus(); }
      else if (event.key === "Tab") closeMenus();
      else return;
      if (event.key !== "Tab") event.preventDefault();
      if (!["Escape", "Tab"].includes(event.key)) options[index]?.focus();
    });
    menu.addEventListener("click", (event) => {
      const option = event.target.closest("[data-value]");
      if (!option) return;
      values[name] = option.dataset.value;
      updateSelect(name);
      byId("diagnosticsCustomTime").classList.toggle("hidden", values.period !== "custom");
      closeMenus();
      trigger.focus();
      query.changeFilters();
    });
    updateSelect(name);
  }
  document.addEventListener("click", (event) => { if (!event.target.closest("#page-diagnostics .console-select")) closeMenus(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMenus(); });
  window.addEventListener("resize", closeMenus);
  window.addEventListener("pagehide", () => { query.setActive(false); closeMenus(); });
  form.addEventListener("input", () => query.changeFilters());
  form.addEventListener("submit", (event) => { event.preventDefault(); void search(); });
  byId("diagnosticsRefresh").addEventListener("click", () => void search());
  byId("diagnosticsReset").addEventListener("click", () => {
    form.reset();
    Object.assign(values, { period: "24", source: "", level: "" });
    Object.keys(FILTER_OPTIONS).forEach(updateSelect);
    byId("diagnosticsCustomTime").classList.add("hidden");
    void search();
  });
  more.addEventListener("click", () => void query.query(undefined, { more: true }));
  list.addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    const row = button?.closest("[data-record-index]");
    const item = query.state.items[Number(row?.dataset.recordIndex)];
    if (!button || !item) return;
    const key = button.dataset.filterId || button.dataset.copyId;
    if (button.dataset.filterId && ["session_id", "run_id"].includes(key)) {
      byId(key === "session_id" ? "diagnosticsSession" : "diagnosticsRun").value = item[key];
      void search();
    } else if (button.hasAttribute("data-copy-record") || ["session_id", "run_id"].includes(key)) {
      try {
        await navigator.clipboard.writeText(button.hasAttribute("data-copy-record") ? JSON.stringify(diagnosticScalars(item), null, 2) : String(item[key]));
        byId("diagnosticsCopyStatus").textContent = "已复制";
      } catch { byId("diagnosticsCopyStatus").textContent = "复制失败，请选中记录手动复制。"; }
    }
  });
  diagnostics = { ...query, search, closeMenus };
  render(query.state);
}
