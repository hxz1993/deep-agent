import { DIAGNOSTIC_PERIODS, diagnosticRangeHelpHtml, bindDiagnosticRangeDismiss } from "./diagnostic-range-control.js?v=20260923-c04-8";
import { diagnosticTime } from "./diagnostics.js?v=20260915-ui-polish";
import { renderUserIdentity } from "./user-identity.js?v=20260917-release01010";
import { escapeHtml as e, icon, icons, selectHtml, bindDiagnosticSelects, diagnosticRequest } from "./diagnostics-ui.js?v=20260915-release-audit";
import { diagnosticHash } from "./diagnostics-navigation.js?v=20260923-c04-1";

const PERIODS = DIAGNOSTIC_PERIODS.map(([value, name]) => [value === "custom" ? value : String(Number(value) / 24), name]);
const DIMENSIONS = [["model", "按模型"], ["user", "按用户"], ["agent", "按 Agent"], ["date", "按日期"]];
const CHART_GROUPS = [["agent", "Agent", "bot"], ["model", "模型", "boxes"], ["user", "用户", "user-round"]];
const METRICS = [["total_tokens", "Token", "layers-3"], ["requests", "请求", "activity"]];
const PURPOSES = { chat: "聊天", title: "标题", compaction: "压缩" };
const COLORS = ["var(--diag-accent)", "var(--usage-orange)", "var(--usage-green)", "var(--usage-rose)", "var(--usage-other)"];
const seriesKeys = (report, group) => report?.series_order?.[group] || [...new Set((report?.trend?.[group] || []).map(row => row.key))];
const seriesColor = (report, group, key) => COLORS[Math.max(0, seriesKeys(report, group).indexOf(key)) % COLORS.length];
const seriesName = (report, group, key) => key === "__other__" ? "其他" : group === "purpose" ? PURPOSES[key] || key : group === "agent" ? report?.agents?.[key] || key : group === "model" ? report?.models?.[key]?.name || report?.model_snapshots?.[key]?.name || key : group === "user" ? report?.users?.[key]?.username || key : key;
const fmt = value => Number(value || 0).toLocaleString("zh-CN");
const short = value => value >= 1e6 ? `${(value / 1e6).toFixed(2)}M` : value >= 1e3 ? `${(value / 1e3).toFixed(1)}K` : fmt(value);

export function usageRange(period, now = new Date(), custom = {}) {
  if (period === "custom") {
    const start = new Date(custom.start), end = new Date(custom.end);
    if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || start >= end || end - start > 31 * 86400000) throw new Error("自定义时间必须在 31 天以内，且结束时间晚于开始时间");
    return { start: start.toISOString(), end: end.toISOString() };
  }
  const end = new Date(now), start = new Date(now);
  if (/^\d+$/.test(period)) {
    const days = Number(period);
    if (days === 1) start.setDate(start.getDate() - 1);
    else { start.setHours(0, 0, 0, 0); start.setDate(start.getDate() - days + 1); }
  }
  else {
    start.setHours(0, 0, 0, 0);
    if (period === "yesterday") { start.setDate(start.getDate() - 1); end.setTime(start.getTime()); end.setDate(end.getDate() + 1); }
    else if (period === "week") start.setDate(start.getDate() - (start.getDay() + 6) % 7);
    else if (period === "month") start.setDate(1);
    else if (period === "last_month") { start.setDate(1); start.setMonth(start.getMonth() - 1); end.setTime(start.getTime()); end.setMonth(end.getMonth() + 1); }
  }
  if (end - start > 31 * 86400000) throw new Error("查询最多支持 31 天");
  return { start: start.toISOString(), end: end.toISOString() };
}

export function usageChartSvg(report, group = "agent", metric = "total_tokens", chartType = "line", hidden = new Set(), width = 800, height = 300) {
  const dates = report?.dates || [], rows = report?.trend?.[group] || [];
  if (!dates.length) return `<p class="diagnostics-empty">暂无趋势数据</p>`;
  const keys = seriesKeys(report, group).filter(key => !hidden.has(key));
  const byDate = new Map(rows.map(row => [`${row.date}:${row.key}`, row]));
  const valueAt = (date, key) => Number(byDate.get(`${date}:${key}`)?.[metric] || 0);
  const values = chartType === "stack" ? dates.map(date => keys.reduce((sum,key) => sum + valueAt(date,key),0)) : dates.flatMap(date => keys.map(key => valueAt(date,key)));
  const peak = Math.max(1,...values), base = 10 ** Math.floor(Math.log10(peak / 4));
  const step = [1,2,5,10].map(n => n * base).find(n => n * 4 >= peak) || 10 * base;
  const max = Math.max(metric === "requests" ? 4 : 1, Math.ceil(peak / step) * step);
  const left = 54, right = width - 16, top = 18, bottom = height - 32, plot = bottom - top;
  const slot = (right-left)/dates.length;
  const xAt = index => chartType === "stack" ? left + slot * (index+.5) : left + (right-left) * index / Math.max(1,dates.length-1);
  const yAt = value => bottom - value/max*plot;
  const unit = metric === "requests" ? "次请求" : "Token";
  const grid = Array.from({length:5},(_,i) => {
    const value = max*i/4, y = yAt(value);
    return `<g class="usage-chart-grid"><line x1="${left}" y1="${y}" x2="${right}" y2="${y}"/><text x="${left-9}" y="${y+4}" text-anchor="end">${short(Math.round(value))}</text></g>`;
  }).join("");
  const labels = dates.map((date,i) => i % Math.max(1,Math.ceil(dates.length/7)) === 0 || i === dates.length-1 ? `<text x="${xAt(i)}" y="${bottom+22}" text-anchor="${i === 0 ? "start" : i === dates.length-1 ? "end" : "middle"}">${e(date.slice(5))}</text>` : "").join("");
  const marks = chartType === "stack" ? dates.map((date,i) => {
    let sum = 0; const bar = Math.max(4,Math.min(44,slot*.56));
    return keys.map(key => { const value = valueAt(date,key); sum += value; return value ? `<rect x="${xAt(i)-bar/2}" y="${yAt(sum)}" width="${bar}" height="${value/max*plot}" fill="${seriesColor(report,group,key)}"><title>${e(date)} · ${e(seriesName(report,group,key))} · ${fmt(value)} ${unit}</title></rect>` : ""; }).join("");
  }).join("") : keys.map(key => `<path class="usage-series-line" d="${dates.map((date,i) => `${i ? "L" : "M"}${xAt(i)} ${yAt(valueAt(date,key))}`).join(" ")}" stroke="${seriesColor(report,group,key)}"><title>${e(seriesName(report,group,key))}</title></path>`).join("");
  const hits = chartType === "line" ? dates.map((date,i) => `<rect x="${Math.max(left,xAt(i)-slot/2)}" y="${top}" width="${Math.min(slot,right-Math.max(left,xAt(i)-slot/2))}" height="${plot}" fill="transparent"><title>${e(date)}${keys.map(key => `&#10;${e(seriesName(report,group,key))}：${fmt(valueAt(date,key))} ${unit}`).join("")}</title></rect>`).join("") : "";
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="按日期查看${metric === "requests" ? "请求次数" : "总 Token"}${chartType === "line" ? "折线趋势" : "堆叠柱状图"}">${grid}${labels}${marks}${hits}</svg>`;
}

export function initDiagnosticUsage({ root, navigate, replace }) {
  const state = { active: false, filters: null, draft: null, report: null, details: null, dimension: "agent", group: "agent", metric: "total_tokens", chartType: "line",
    hidden: new Set(), sort: "total_tokens", descending: true, offset: 0, contributionOffset: 0, busy: false, error: "", contributing: null, contributions: null };
  let reportAbort = null, detailAbort = null, contributionAbort = null;
  const chartResize = new ResizeObserver(() => drawChart());
  function drawChart() {
    if (!state.active) return;
    const host = root.querySelector(".usage-chart");
    if (!host || !state.report) return;
    const width = Math.max(320, Math.round(host.clientWidth)), height = Math.max(220, Math.round(host.clientHeight));
    host.innerHTML = usageChartSvg(state.report, state.group, state.metric, state.chartType, state.hidden, width, height);
  }
  const queryString = values => new URLSearchParams(Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== "")).toString();
  const params = () => Object.fromEntries(["start", "end", "timezone", "agent_id"].map(key => [key, state.filters?.[key]]));
  const label = (key, dimension = state.group) => key === "__other__" ? "其他" : dimension === "purpose" ? PURPOSES[key] || key : dimension === "model" ? state.report?.models?.[key]?.name || state.report?.model_snapshots?.[key]?.name || key : dimension === "agent" ? state.report?.agents?.[key] || key : dimension === "user" ? state.report?.users?.[key]?.username || key : key;
  const title = value => e(String(value || ""));
  const measured = (row, field) => {
    const source = field === "total_tokens" ? (row.input_reported_requests || row.output_reported_requests) : row[field.replace("_tokens", "_reported_requests")];
    return source ? fmt(row[field]) : "—";
  };
  const chartLabels = () => seriesKeys(state.report, state.group);
  const stat = (name, value, glyph, tone, detail = "") => `<div class="usage-stat"><span class="usage-stat-icon ${tone}">${icon(glyph)}</span><span><small>${name}</small><strong>${value}</strong>${detail ? `<span class="usage-stat-detail">${detail}</span>` : ""}</span></div>`;
  const rangeHelpHtml = range => diagnosticRangeHelpHtml(range, { formId:"usageFilters", busy:state.busy, formatTime:value => { const full = diagnosticTime(value); return /^\d{4}-\d{2}-\d{2} /.test(full) ? full.slice(5,16) : full; } });
  const legendHtml = report => {
    const rows = report?.trend?.[state.group] || [], totals = new Map();
    rows.forEach(row => totals.set(row.key, (totals.get(row.key) || 0) + Number(row[state.metric] || 0)));
    const all = [...totals.values()].reduce((sum, value) => sum + value, 0);
    return chartLabels().map(key => `<button type="button" data-usage-series="${title(key)}" class="usage-legend-item ${state.hidden.has(key) ? "is-hidden" : ""}" aria-pressed="${!state.hidden.has(key)}" title="${title(label(key))}"><i style="background:${seriesColor(report, state.group, key)}"></i><span class="usage-legend-copy"><span class="usage-legend-name">${title(label(key))}</span><span class="usage-legend-value"><strong>${all ? (100 * (totals.get(key) || 0) / all).toFixed(1) : "0.0"}%</strong><small>${short(totals.get(key) || 0)}</small></span></span></button>`).join("");
  };
  const dimensionSwitchHtml = () => `<div class="agent-view-switch diagnostic-view-switch usage-dimension-switch" role="group" aria-label="用量明细维度">${[["agent","Agent","bot"],["model","模型","boxes"],["user","用户","user-round"],["date","日期","calendar-days"]].map(([key,name,glyph]) => `<button type="button" data-usage-dimension="${key}" class="${state.dimension === key ? "active" : ""}" aria-pressed="${state.dimension === key}">${icon(glyph)}<span>${name}</span></button>`).join("")}</div>`;
  const peakUsage = data => {
    const daily = new Map();
    (data?.trend?.agent || []).forEach(row => daily.set(row.date,(daily.get(row.date)||0)+Number(row.total_tokens||0)));
    return [...daily].sort((a,b) => b[1]-a[1])[0];
  };
  const insightsHtml = data => {
    const summary = data?.summary || {};
    return `<section class="usage-surface usage-purpose"><div class="usage-panel-head"><h2>用量统计</h2></div><div class="usage-purpose-values">${(data?.distribution || []).map(row => `<div><small>${title(PURPOSES[row.key] || row.key)}</small><strong>${short(row.total_tokens)}</strong><span>${summary.total_tokens ? (row.total_tokens/summary.total_tokens*100).toFixed(1) : "0"}%</span></div>`).join("")}<div><small>输入</small><strong>${measured(summary,"input_tokens")}</strong></div><div><small>输出</small><strong>${measured(summary,"output_tokens")}</strong></div><div><small>模型请求</small><strong>${fmt(summary.requests)}</strong></div></div></section>`;
  };
  const chartSwitchHtml = (field, name, options) => `<div class="agent-view-switch diagnostic-view-switch" role="group" aria-label="${name}">${options.map(([key,text,glyph]) => `<button type="button" data-usage-chart-field="${field}" data-usage-chart-value="${key}" class="${state[field] === key ? "active" : ""}" aria-pressed="${state[field] === key}">${icon(glyph)}<span>${text}</span></button>`).join("")}</div>`;
  const exportDialog = () => `<dialog class="modal usage-export-dialog" id="usageExportDialog" aria-labelledby="usageExportTitle"><div class="modal-header"><h3 id="usageExportTitle">导出用量明细</h3><button type="button" class="icon-action" data-usage-action="close-export" aria-label="关闭">${icon("x")}</button></div><div class="modal-body"><p>时间：<span id="usageExportRange"></span></p><p>Agent：<span id="usageExportAgent"></span> · 维度：<span id="usageExportDimension"></span></p><label><input type="radio" name="usage_export_scope" value="all" checked> 全部匹配明细</label><label><input type="radio" name="usage_export_scope" value="page"> 当前页（最多 10 条）</label><p class="usage-export-status" role="status"></p></div><div class="modal-footer"><button type="button" class="btn" data-usage-action="close-export">取消</button><button type="button" class="btn primary" data-usage-action="confirm-export">导出 CSV</button></div></dialog>`;

  function render() {
    if (!state.active) return;
    const range = state.draft || {}, data = state.report, summary = data?.summary || {};
    const agents = [["", "全部 Agent"], ...Object.entries(data?.agents || {}).map(([id, name]) => [id, name])];
    root.innerHTML = `<div class="diagnostic-usage-page diagnostic-sessions-page">
      <header class="usage-header"><div class="usage-title"><h1>用量</h1><p>统计聊天、标题与压缩用量，暂不纳入网络搜索和图片生成。</p></div><form id="usageFilters" class="diagnostic-session-filters" autocomplete="off">
        <div class="diagnostics-field diagnostic-control-with-icon diagnostic-time-filter">${icon("clock-3")}${selectHtml("period", "时间范围", range.period || "7", PERIODS).replace(icon("chevron-down"), "")}${rangeHelpHtml(range)}</div>
        <div class="diagnostics-field diagnostic-control-with-icon usage-agent">${icon("bot")}${selectHtml("agent_id", "Agent", range.agent_id || "", agents)}</div>
        <button class="btn diagnostics-button primary" type="submit" ${state.busy ? "disabled" : ""}>${icon("search")}查询</button><button class="btn diagnostics-button text" type="button" data-usage-action="reset">重置</button>
      </form></header>
      ${state.error ? `<p class="usage-error" role="alert">${title(state.error)}</p>` : ""}
      <div class="usage-overview">
        <section class="usage-surface usage-trend"><div class="usage-panel-head"><div><h2>用量趋势</h2><small>按日期查看 Token 与模型请求次数</small></div><div class="usage-chart-tools">${chartSwitchHtml("metric", "统计指标", METRICS)}${chartSwitchHtml("group", "趋势分组", CHART_GROUPS)}<div class="agent-view-switch diagnostic-view-switch usage-chart-types" role="group" aria-label="图表类型">${[["line","折线图","chart-line"],["stack","堆叠图","chart-column-stacked"]].map(([key,name,glyph]) => `<button type="button" class="${state.chartType === key ? "active" : ""}" data-usage-chart-field="chartType" data-usage-chart-value="${key}" aria-label="${name}" title="${name}" aria-pressed="${state.chartType === key}">${icon(glyph)}</button>`).join("")}</div></div></div><div class="usage-chart-body"><div class="usage-chart">${usageChartSvg(data, state.group, state.metric, state.chartType, state.hidden)}</div><div class="usage-legend">${legendHtml(data)}</div></div></section>
        <div class="usage-overview-cards"><div class="usage-stats">${stat("累计 Token", measured(summary, "total_tokens"), "layers-3", "purple")}${stat("日均 Token", (summary.input_reported_requests || summary.output_reported_requests) && data?.dates?.length ? short(summary.total_tokens/data.dates.length) : "—", "calendar-days", "blue")}${stat("单次平均 Token", summary.requests && !summary.missing_requests ? short(summary.total_tokens/summary.requests) : "—", "chart-no-axes-combined", "green")}${stat("峰值日期", peakUsage(data)?.[1] ? `${title(peakUsage(data)[0].slice(5))} <span class="usage-peak-secondary">${short(peakUsage(data)[1])}</span>` : "—", "calendar-check", "orange")}</div>
        ${insightsHtml(data)}
        <section class="usage-surface usage-users"><div class="usage-panel-head"><div><h2>用户用量</h2></div><button type="button" class="usage-link" data-usage-action="view-users">查看明细 ${icon("arrow-right")}</button></div><div class="usage-user-ranking">${(data?.top_users || []).slice(0,5).map(row => `<div class="usage-user-rank"><span class="diagnostic-identity-icon">${icon("user-round")}</span><span class="usage-mini-user" title="${title(label(row.key,"user"))}">${title(label(row.key,"user"))}</span><span class="usage-rank-track"><i style="width:${Math.max(1,100 * row.total_tokens / Math.max(1,data.top_users[0]?.total_tokens || 0))}%"></i></span><strong>${short(row.total_tokens)}</strong></div>`).join("") || '<p class="diagnostics-empty">暂无数据</p>'}</div></section></div>
      </div>
      <section class="usage-details"><div class="usage-detail-tools">${dimensionSwitchHtml()}<div class="usage-detail-actions"><button type="button" class="diagnostics-button" data-usage-action="export">${icon("download")}导出</button></div></div><div class="usage-workbench"><div class="usage-table-area"><div class="usage-table-frame"><div id="usageDetailRows"></div></div><div id="usageDetailFooter"></div></div><aside class="usage-contributions diagnostic-channels-page" aria-label="贡献会话"><div id="usageContributionRows"></div><div id="usageContributionFooter"></div></aside></div></section>
      ${summary.missing_requests ? `<p class="usage-coverage" role="status">${fmt(summary.missing_requests)} 次请求的用量不完整，Token 仅统计已报告值。</p>` : ""}${state.busy ? '<p class="usage-loading" role="status">正在查询用量…</p>' : ""}${exportDialog()}</div>`;
    renderDetailRows(); icons(); chartResize.disconnect();
    const chart = root.querySelector(".usage-chart");
    if (chart) { chartResize.observe(chart); drawChart(); }
  }

  function renderDetailRows() {
    const host = root.querySelector("#usageDetailRows"), footer = root.querySelector("#usageDetailFooter"); if (!host || !footer) return;
    const pagination = (total, offset, previous, next, unit = "项") => `<footer class="diagnostic-list-footer"><nav class="diagnostic-pagination" aria-label="用量明细分页"><button type="button" class="diagnostics-button" data-usage-action="${previous}" ${offset ? "" : "disabled"}>上一页</button><span class="diagnostic-page-current models-page-info">第 ${Math.floor(offset / 10) + 1} / ${Math.max(1, Math.ceil(total / 10))} 页 · 共 ${fmt(total)} ${unit}</span><button type="button" class="diagnostics-button" data-usage-action="${next}" ${offset + 10 < total ? "" : "disabled"}>下一页</button></nav></footer>`;
    const contributionHost = root.querySelector("#usageContributionRows"), contributionFooter = root.querySelector("#usageContributionFooter");
    if (state.contributing) {
      const rows = state.contributions?.items || [], total = state.contributions?.total || 0;
      const selected = state.details?.items?.find(row => row.key === state.contributing.key), dimension = state.contributing.dimension;
      const glyph = {agent:"bot",model:"boxes",user:"user-round",date:"calendar-days"}[dimension];
      const description = dimension === "model" ? selected?.model?.provider_name || "模型用量" : dimension === "user" ? state.details?.users?.[state.contributing.key]?.email || "用户用量" : dimension === "agent" ? `Agent · ${state.contributing.key}` : "当日贡献会话";
      const name = dimension === "user" ? state.details?.users?.[state.contributing.key]?.username || state.contributing.name : selected?.model?.name || selected?.name || state.contributing.name;
      contributionHost.innerHTML = `<div class="usage-panel-head"><span class="usage-object-icon">${icon(glyph)}</span><div class="usage-object-copy"><h2>${title(name)}</h2><small>${title(description)}</small></div><span class="usage-session-count" title="${total} 个会话">${icon("messages-square")}${fmt(total)}</span></div>${rows.map(row => `<article class="usage-session-entry"><div class="usage-session-content"><div class="usage-session-heading"><button type="button" class="channel-last-question" data-usage-session="${title(row.session_id)}">${title(row.title || "未命名会话")}</button><strong class="usage-token-count" title="Token">${measured(row,"total_tokens")}</strong></div><div class="diagnostic-session-id channel-session-meta"><span class="channel-session-time"><code title="${title(row.session_id)}">${title(row.session_id.slice(0,8))}</code><button type="button" class="diagnostic-copy-separator" data-usage-copy="${title(row.session_id)}" aria-label="复制 Session ID" title="复制 Session ID：${title(row.session_id)}">${icon("copy")}</button><time>${title(diagnosticTime(row.last_activity))}</time></span><span class="usage-session-counts"><span title="模型请求次数">${icon("activity")}${fmt(row.requests)}</span></span></div></div></article>`).join("") || `<p class="diagnostics-empty">${state.contributions ? "暂无贡献会话" : "正在加载贡献会话…"}</p>`}`;
      contributionFooter.innerHTML = rows.length < total ? `<button type="button" class="diagnostics-button text channel-history-more" data-usage-action="contribution-more">加载更多会话（${rows.length} / ${total}）</button>` : "";
    } else {
      contributionHost.innerHTML = `<div class="usage-panel-head"><h2>贡献会话</h2></div><p class="diagnostics-empty">选择左侧条目，查看贡献会话</p>`;
      contributionFooter.innerHTML = "";
    }
    const rows = state.details?.items || [], total = state.details?.total || 0;
    const headers = `<div class="usage-detail-row usage-detail-head"><span>${title(DIMENSIONS.find(([key]) => key === state.dimension)?.[1] || "明细")}</span><button data-usage-sort="requests">请求</button><button data-usage-sort="input_tokens">输入</button><button data-usage-sort="output_tokens">输出</button><button data-usage-sort="total_tokens">合计</button><span title="合计 Token / 模型请求次数">单次平均</span><span title="占当前筛选范围总 Token 的比例">用量占比</span></div>`;
    const names = row => {
      if (state.dimension === "user") return renderUserIdentity(state.details?.users?.[row.key] || {}, {userId:row.key, copyable:false});
      if (state.dimension === "model") { const model = row.model || {}; return `<span class="usage-model-name">${model.deleted ? '<small class="usage-deleted">已删除</small>' : ""}${title(model.name || model.model_name || row.key)}<small>${title(model.provider_name || "")}${model.deleted ? ` · ${title(row.key)}` : ""}</small></span>`; }
      return title(row.name || row.key);
    };
    host.innerHTML = `${headers}${rows.map(row => `<button type="button" class="usage-detail-row ${state.contributing?.key === row.key && state.contributing?.dimension === state.dimension ? "is-selected" : ""}" data-usage-key="${title(row.key)}"><span class="usage-detail-name">${names(row)}</span><span>${fmt(row.requests)}</span><span>${measured(row, "input_tokens")}</span><span>${measured(row, "output_tokens")}</span><strong>${measured(row, "total_tokens")}</strong><span>${row.requests && !row.missing_requests ? short(row.total_tokens/row.requests) : "—"}</span><span>${state.details?.summary?.total_tokens ? (row.total_tokens/state.details.summary.total_tokens*100).toFixed(1)+"%" : "—"}</span></button>`).join("") || `<p class="diagnostics-empty">${state.busy ? "正在加载…" : "当前条件暂无用量"}</p>`}`;
    footer.innerHTML = (total > 10 ? pagination(total, state.offset, "previous", "next") : "") + (state.details?.summary?.missing_requests ? `<span class="usage-detail-incomplete">${fmt(state.details.summary.missing_requests)} 次用量不完整</span>` : "");
  }

  async function loadDetails() {
    detailAbort?.abort(); detailAbort = new AbortController();
    const signal = detailAbort.signal;
    try {
      const details = await diagnosticRequest(`/api/v1/diagnostics/usage/details?${queryString({...params(), group_by:state.dimension, sort:state.sort, descending:state.descending, offset:state.offset, limit:10})}`, signal);
      if (!signal.aborted && state.active) { state.details = details;
        const selected = details.items?.find(row => row.key === state.contributing?.key && state.dimension === state.contributing?.dimension);
        if (!selected) { state.contributing = null; state.contributions = null; contributionAbort?.abort(); }
        renderDetailRows(); icons();
        if (!selected && details.items?.length) root.querySelector("[data-usage-key]")?.click(); }
    } catch (error) { if (!signal.aborted && state.active) { state.error = error.message; render(); } }
  }
  async function loadContributions() {
    contributionAbort?.abort();
    if (!state.contributing) return;
    contributionAbort = new AbortController();
    const signal = contributionAbort.signal, selected = state.contributing;
    try {
      const result = await diagnosticRequest(`/api/v1/diagnostics/usage/sessions?${queryString({...params(), group_by:selected.dimension, key:selected.key, offset:state.contributionOffset})}`, signal);
      if (!signal.aborted && state.active && state.contributing === selected) { state.contributions = {...result, items:state.contributionOffset ? [...(state.contributions?.items || []), ...result.items] : result.items}; renderDetailRows(); icons(); }
    } catch (error) { if (!signal.aborted && state.active && state.contributing === selected) { state.error = error.message; render(); } }
  }
  async function load() {
    reportAbort?.abort(); detailAbort?.abort(); contributionAbort?.abort(); reportAbort = new AbortController();
    state.contributing = null; state.contributions = null;
    const signal = reportAbort.signal; state.busy = true; state.error = ""; render();
    try {
      const data = await diagnosticRequest(`/api/v1/diagnostics/usage?${queryString(params())}`, signal);
      if (signal.aborted || !state.active) return;
      state.report = data; state.busy = false; state.hidden.clear(); render(); await loadDetails();
    } catch (error) { if (!signal.aborted && state.active) { state.busy = false; state.error = error.message; render(); } }
  }
  function applyFilters() {
    try {
      const period = state.draft.period || "7";
      const custom = period === "custom" ? {start:root.querySelector('[name="start"]')?.value || state.draft.start, end:root.querySelector('[name="end"]')?.value || state.draft.end} : {};
      const bounds = usageRange(period, new Date(), custom);
      state.filters = {...bounds, period, timezone:state.filters?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", agent_id:state.draft.agent_id || ""};
      state.draft = {...state.filters}; state.offset = 0; state.details = null; state.contributing = null;
      replace(diagnosticHash("usage", state.filters)); load();
    } catch (error) { state.error = error.message; render(); }
  }
  function setSelect(name, value) {
    if (!["period", "agent_id"].includes(name)) return;
    const start = root.querySelector('[name="start"]')?.value, end = root.querySelector('[name="end"]')?.value;
    state.draft = {...state.draft, ...(start && end ? {start:new Date(start).toISOString(), end:new Date(end).toISOString()} : {}), [name]:value};
    if (name === "period" && value === "custom") {
      render(); const settings = root.querySelector(".diagnostic-range-settings");
      if (settings) { settings.open = true; settings.querySelector("input")?.focus(); }
      return;
    }
    applyFilters();
  }
  bindDiagnosticSelects(root, setSelect);
  bindDiagnosticRangeDismiss(root);
  root.addEventListener("submit", event => {
    if (event.target.id !== "usageFilters") return;
    event.preventDefault(); applyFilters();
  });
  root.addEventListener("click", async event => {
    const copy = event.target.closest("[data-usage-copy]");
    if (copy) {
      if (copy.disabled) return;
      copy.disabled = true;
      try {
        await navigator.clipboard.writeText(copy.dataset.usageCopy);
        copy.classList.add("copied"); copy.innerHTML = icon("check");
        copy.title = "会话 ID 已复制"; copy.setAttribute("aria-label", "会话 ID 已复制"); icons();
        window.DeepAgentConsole?.toast("会话 ID 已复制");
        setTimeout(() => {
          if (!copy.isConnected) return;
          copy.classList.remove("copied"); copy.innerHTML = icon("copy");
          copy.title = `复制 Session ID：${copy.dataset.usageCopy}`;
          copy.setAttribute("aria-label", "复制 Session ID"); copy.disabled = false; icons();
        }, 1500);
      } catch { copy.disabled = false; window.DeepAgentConsole?.toast("复制失败，请手动选择", true); }
      return;
    }
    const chartControl = event.target.closest("[data-usage-chart-field]");
    if (chartControl) { state[chartControl.dataset.usageChartField] = chartControl.dataset.usageChartValue; state.hidden.clear(); render(); return; }
    const series = event.target.closest("[data-usage-series]");
    if (series) { const key = series.dataset.usageSeries; state.hidden.has(key) ? state.hidden.delete(key) : state.hidden.add(key); render(); return; }
    const dimension = event.target.closest("[data-usage-dimension]");
    if (dimension) { state.dimension = dimension.dataset.usageDimension; state.offset = 0; state.contributing = null; state.details = null; render(); loadDetails(); return; }
    const sort = event.target.closest("[data-usage-sort]");
    if (sort) { state.descending = state.sort === sort.dataset.usageSort ? !state.descending : true; state.sort = sort.dataset.usageSort; state.offset = 0; loadDetails(); return; }
    const session = event.target.closest("[data-usage-session]");
    if (session) { navigate(diagnosticHash("detail", {view:"conversation"}, session.dataset.usageSession)); return; }
    const row = event.target.closest("[data-usage-key]");
    if (row) {
      const name = row.querySelector(".usage-detail-name")?.innerText?.trim().split(/\n+/).filter(Boolean).join(" · ") || row.dataset.usageKey;
      state.contributing = {dimension:state.dimension, key:row.dataset.usageKey, name}; state.contributions = null; state.contributionOffset = 0; render();
      await loadContributions();
      return;
    }
    const action = event.target.closest("[data-usage-action]")?.dataset.usageAction;
    if (!action) return;
    if (action === "reset") { const range = usageRange("7"); state.filters = {...range, period:"7", timezone:Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", agent_id:""}; state.draft = {...state.filters}; state.offset = 0; replace(diagnosticHash("usage", state.filters)); load(); }
    else if (action === "view-users") { state.dimension = "user"; state.offset = 0; state.contributing = null; render(); loadDetails(); }
    else if (action === "previous" || action === "next") { state.offset = Math.max(0, state.offset + (action === "next" ? 10 : -10)); loadDetails(); }
    else if (action === "contribution-more") { state.contributionOffset = state.contributions?.items?.length || 0; await loadContributions(); }
    else if (action === "export") { const dialog = root.querySelector("#usageExportDialog"); dialog.querySelector("#usageExportRange").textContent = `${new Date(state.filters.start).toLocaleString()} — ${new Date(state.filters.end).toLocaleString()}`; dialog.querySelector("#usageExportAgent").textContent = state.report?.agents?.[state.filters.agent_id] || "全部 Agent"; dialog.querySelector("#usageExportDimension").textContent = DIMENSIONS.find(([key]) => key === state.dimension)?.[1] || ""; dialog.showModal(); }
    else if (action === "close-export") root.querySelector("#usageExportDialog")?.close();
    else if (action === "confirm-export") {
      const dialog = root.querySelector("#usageExportDialog"), status = dialog.querySelector(".usage-export-status"), button = dialog.querySelector('[data-usage-action="confirm-export"]');
      button.disabled = true; status.textContent = "正在生成导出文件…";
      try {
        const scope = dialog.querySelector('input[name="usage_export_scope"]:checked').value;
        const path = `/api/v1/diagnostics/usage/export?${queryString({...params(), group_by:state.dimension, sort:state.sort, descending:state.descending, scope, offset:state.offset})}`;
        const response = await window.daFetch(window.serviceUrl(path), {includeContext:false, reportStatus:false, cache:"no-store"});
        if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || `HTTP ${response.status}`);
        const url = URL.createObjectURL(await response.blob()), link = document.createElement("a"); link.href = url; link.download = "deepagent-usage.csv"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); dialog.close();
      } catch (error) { status.textContent = error.message; } finally { button.disabled = false; }
    }
  });
  return {
    activate(route, snapshot) {
      state.active = true;
      if (snapshot?.filters && diagnosticHash("usage", snapshot.filters) === diagnosticHash("usage", route.filters) && snapshot.report) Object.assign(state, {...snapshot, active:true, hidden:new Set(snapshot.hidden || [])});
      else {
        const period = route.filters.period || "7", bounds = route.filters.start && route.filters.end ? route.filters : {...usageRange(period), ...route.filters};
        state.filters = {...bounds, timezone:route.filters.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", period, agent_id:route.filters.agent_id || ""};
        state.draft = {...state.filters}; state.report = null; state.details = null;
      }
      render(); if (!state.report) load(); else loadDetails();
    },
    deactivate() { state.active = false; reportAbort?.abort(); detailAbort?.abort(); contributionAbort?.abort(); chartResize.disconnect(); },
    snapshot() { return {...state, hidden:[...state.hidden], active:false, busy:false}; },
    clear() { reportAbort?.abort(); detailAbort?.abort(); contributionAbort?.abort(); chartResize.disconnect(); state.active = false; state.report = null; state.details = null; },
  };
}
