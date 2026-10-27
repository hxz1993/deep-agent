import { bindChannelResizer } from "./channel-resizer.js?v=20260915-release-audit";
import { DIAGNOSTIC_PERIODS, diagnosticRangeHelpHtml, bindDiagnosticRangeDismiss } from "./diagnostic-range-control.js?v=20261019-release0138";
import { diagnosticTime } from "./diagnostics.js?v=20261019-release0138";
import { escapeHtml as e, icon, icons, selectHtml, bindDiagnosticSelects, diagnosticRequest, diagnosticRequestOptions } from "./diagnostics-ui.js?v=20261019-release0138";
import { diagnosticTarget, sameDiagnosticFilters } from "./diagnostics-navigation.js?v=20261019-release0138";

const PERIODS = DIAGNOSTIC_PERIODS.map(([value, name]) => [value === "custom" ? value : String(Number(value) / 24), name]);
const DIMENSIONS = [["model", "按模型"], ["user", "按用户"], ["agent", "按 Agent"]];
const METRICS = [["total_tokens", "Token", "layers-3"], ["requests", "请求", "activity"]];
const PURPOSES = { chat: "聊天", title: "标题", compaction: "压缩" };
const COLORS = ["var(--diag-accent)", "var(--usage-orange)", "var(--usage-green)", "var(--usage-rose)", "var(--usage-other)"];
const seriesKeys = (report, group) => report?.series_order?.[group] || [...new Set((report?.trend?.[group] || []).map(row => row.key))];
const seriesColor = (report, group, key) => COLORS[Math.max(0, seriesKeys(report, group).indexOf(key)) % COLORS.length];
const modelName = model => model?.name?.startsWith("function:") ? "测试模型" : model?.name || model?.model_name || "未记录模型";
const modelBadge = (model, key) => model?.deleted ? "已删除" : key?.startsWith("legacy:") && !model?.name?.startsWith("function:") ? "历史" : "";
const seriesName = (report, group, key) => key === "__other__" ? "其他" : group === "purpose" ? PURPOSES[key] || key : group === "agent" ? report?.agents?.[key] || key : group === "model" ? modelName(report?.models?.[key] || report?.model_snapshots?.[key]) : group === "user" ? report?.users?.[key]?.username || key : key;
const fmt = value => Number(value || 0).toLocaleString("zh-CN");
const short = value => value >= 1e6 ? `${(value / 1e6).toFixed(2)}M` : value >= 1e3 ? `${(value / 1e3).toFixed(1)}K` : fmt(value);

export function usageAveragePresentation(row = {}) {
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const requests = row.requests || 0, missing = row.missing_requests || 0;
  if (!requests) return { value: "—", partial: false, hint: "当前范围没有模型请求。" };
  if (!missing && count(requests) && count(row.total_tokens)) {
    return { value: short(row.total_tokens / requests), partial: false, hint: "合计 Token / 模型请求次数。" };
  }
  let complete = row.complete_requests, tokens = row.complete_tokens;
  // Older services can prove this fallback only when every reported input and
  // output belongs to the same fully measured requests; partial calls must not
  // contribute their known half to this average.
  if (complete === undefined && tokens === undefined && count(requests) && count(missing)
      && missing <= requests && row.input_reported_requests === requests - missing
      && row.output_reported_requests === requests - missing) {
    complete = requests - missing;
    tokens = row.total_tokens;
  }
  if (count(requests) && count(missing) && count(complete) && complete > 0
      && complete === requests - missing && count(tokens)) {
    return { value: short(tokens / complete), partial: true,
      hint: `按完整记录 ${fmt(complete)}/${fmt(requests)} 次计算，${fmt(missing)} 次用量缺失未计入。` };
  }
  return { value: "—", partial: false, hint: missing === requests
    ? `共 ${fmt(requests)} 次请求，均缺少完整 Token 数据，暂无法计算单次平均。`
    : "请求用量不完整，当前数据无法确定完整记录的平均值。" };
}

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

export function usageChartSvg(report, group = "agent", metric = "total_tokens", chartType = "line", focused = null, width = 800, height = 300) {
  const dates = report?.dates || [], rows = report?.trend?.[group] || [];
  if (!dates.length) return `<p class="diagnostics-empty">暂无趋势数据</p>`;
  const keys = seriesKeys(report, group);
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
    return keys.map(key => { const value = valueAt(date,key); sum += value; return value ? `<rect data-chart-series="${e(key)}" opacity="${focused && focused !== key ? .25 : 1}" x="${xAt(i)-bar/2}" y="${yAt(sum)}" width="${bar}" height="${value/max*plot}" fill="${seriesColor(report,group,key)}"><title>${e(date)} · ${e(seriesName(report,group,key))} · ${fmt(value)} ${unit}</title></rect>` : ""; }).join("");
  }).join("") : keys.map(key => `<path data-chart-series="${e(key)}" opacity="${focused && focused !== key ? .25 : 1}" class="usage-series-line" d="${dates.map((date,i) => `${i ? "L" : "M"}${xAt(i)} ${yAt(valueAt(date,key))}`).join(" ")}" stroke="${seriesColor(report,group,key)}"><title>${e(seriesName(report,group,key))}</title></path>`).join("");
  const hits = chartType === "line" ? dates.map((date,i) => `<rect x="${Math.max(left,xAt(i)-slot/2)}" y="${top}" width="${Math.min(slot,right-Math.max(left,xAt(i)-slot/2))}" height="${plot}" fill="transparent"><title>${e(date)}${keys.map(key => `&#10;${e(seriesName(report,group,key))}：${fmt(valueAt(date,key))} ${unit}`).join("")}</title></rect>`).join("") : "";
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="按日期查看${metric === "requests" ? "请求次数" : "总 Token"}${chartType === "line" ? "折线趋势" : "堆叠柱状图"}">${grid}${labels}${marks}${hits}</svg>`;
}

export function initDiagnosticUsage({ root, navigate, replace }) {
  const state = { active: false, filters: null, draft: null, report: null, details: null, dimension: "agent", metric: "total_tokens", chartType: "line",
    focused: null, sort: "total_tokens", descending: true, offset: 0, contributionOffset: 0, detailBusy: false, contributionBusy: false, busy: false, error: "", contributing: null, contributions: null };
  let reportAbort = null, detailAbort = null, contributionAbort = null;
  let releaseResizer = () => {};
  const chartResize = new ResizeObserver(() => drawChart());
  function drawChart() {
    if (!state.active) return;
    const host = root.querySelector(".usage-chart");
    if (!host || !state.report) return;
    const width = Math.max(320, Math.round(host.clientWidth)), height = Math.max(220, Math.round(host.clientHeight));
    host.innerHTML = usageChartSvg(state.report, state.dimension, state.metric, state.chartType, state.focused, width, height);
  }
  const queryString = values => new URLSearchParams(Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== "")).toString();
  const params = () => Object.fromEntries(["start", "end", "timezone", "agent_id"].map(key => [key, state.filters?.[key]]));
  const label = (key, dimension = state.dimension) => key === "__other__" ? "其他" : dimension === "purpose" ? PURPOSES[key] || key : dimension === "model" ? modelName(state.report?.models?.[key] || state.report?.model_snapshots?.[key]) : dimension === "agent" ? state.report?.agents?.[key] || key : dimension === "user" ? state.report?.users?.[key]?.username || key : key;
  const title = value => e(String(value || ""));
  const averageHtml = row => {
    const average = usageAveragePresentation(row);
    return `<span title="${title(average.hint)}" aria-label="${title(`${average.value}${average.partial ? "，" : "。"}${average.hint}`)}">${average.value}${average.partial ? "*" : ""}</span>`;
  };
  const measured = (row, field) => {
    const source = field === "total_tokens" ? (row.input_reported_requests || row.output_reported_requests) : row[field.replace("_tokens", "_reported_requests")];
    return source ? fmt(row[field]) : "—";
  };
  const chartLabels = () => seriesKeys(state.report, state.dimension);
  const stat = (name, value, glyph, tone, detail = "", hint = "") => `<div class="usage-stat"><span class="usage-stat-icon ${tone}">${icon(glyph)}</span><span><small class="${hint ? "usage-stat-label-with-hint" : ""}">${name}${hint ? `<span class="usage-info" tabindex="0" aria-label="${title(hint)}">${icon("info")}</span><span class="usage-info-tooltip" role="tooltip">${title(hint)}</span>` : ""}</small><strong>${value}</strong>${detail ? `<span class="usage-stat-detail">${detail}</span>` : ""}</span></div>`;
  const rangeHelpHtml = range => diagnosticRangeHelpHtml(range, { formId:"usageFilters", busy:state.busy, formatTime:value => { const full = diagnosticTime(value); return /^\d{4}-\d{2}-\d{2} /.test(full) ? full.slice(5,16) : full; } });
  const legendHtml = report => {
    const rows = report?.trend?.[state.dimension] || [], totals = new Map();
    rows.forEach(row => totals.set(row.key, (totals.get(row.key) || 0) + Number(row[state.metric] || 0)));
    const all = [...totals.values()].reduce((sum, value) => sum + value, 0);
    return chartLabels().map(key => `<button type="button" data-usage-series="${title(key)}" class="usage-legend-item ${state.focused && state.focused !== key ? "is-dimmed" : ""}" aria-pressed="${state.focused === key}" title="${title(label(key))}"><i style="background:${seriesColor(report, state.dimension, key)}"></i><span class="usage-legend-copy"><span class="usage-legend-name">${title(label(key))}</span><span class="usage-legend-value"><strong>${all ? (100 * (totals.get(key) || 0) / all).toFixed(1) : "0.0"}%</strong><small>${short(totals.get(key) || 0)}</small></span></span></button>`).join("");
  };
  const dimensionSwitchHtml = () => `<div class="agent-view-switch diagnostic-view-switch usage-dimension-switch" role="group" aria-label="用量明细维度">${[["agent","Agent","bot"],["model","模型","sparkles"],["user","用户","user-round"]].map(([key,name,glyph]) => `<button type="button" data-usage-dimension="${key}" class="${state.dimension === key ? "active" : ""}" aria-pressed="${state.dimension === key}">${icon(glyph)}<span>${name}</span></button>`).join("")}</div>`;
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
  const exportDialog = () => `<dialog class="modal usage-export-dialog" id="usageExportDialog" aria-labelledby="usageExportTitle"><div class="modal-header"><h3 id="usageExportTitle">导出用量汇总</h3><button type="button" class="icon-action" data-usage-action="close-export" aria-label="关闭">${icon("x")}</button></div><div class="modal-body"><p>时间：<span id="usageExportRange"></span></p><p>Agent：<span id="usageExportAgent"></span> · 维度：<span id="usageExportDimension"></span></p><label><input type="radio" name="usage_export_scope" value="all" checked> 全部匹配汇总</label><label><input type="radio" name="usage_export_scope" value="page"> 当前页（最多 10 条）</label><p class="usage-export-status" role="status"></p></div><div class="modal-footer"><button type="button" class="btn" data-usage-action="close-export">取消</button><button type="button" class="btn primary" data-usage-action="confirm-export">导出 CSV</button></div></dialog>`;

  const scrollHosts = [".usage-table-frame", "#usageContributionRows"];
  const readScroll = () => scrollHosts.map(selector => { const host = root.querySelector(selector); return {top:host?.scrollTop || 0, left:host?.scrollLeft || 0}; });
  const restoreScroll = values => scrollHosts.forEach((selector, index) => { const host = root.querySelector(selector); if (host) { host.scrollTop = values[index]?.top || 0; host.scrollLeft = values[index]?.left || 0; } });

  function render(scroll = readScroll()) {
    if (!state.active) return;
    const range = state.draft || {}, data = state.report, summary = data?.summary || {};
    const agents = [["", "全部 Agent"], ...Object.entries(data?.agents || {}).map(([id, name]) => [id, name])];
    releaseResizer();
    root.innerHTML = `<div class="diagnostic-usage-page diagnostic-sessions-page">
      <header class="usage-header"><div class="usage-title"><h1>用量</h1><p>统计聊天、标题与压缩用量，暂不纳入网络搜索和图片生成。</p></div><form id="usageFilters" class="diagnostic-session-filters" autocomplete="off">
        <div class="diagnostics-field diagnostic-control-with-icon diagnostic-time-filter">${icon("clock-3")}${selectHtml("period", "时间范围", range.period || "7", PERIODS).replace(icon("chevron-down"), "")}${rangeHelpHtml(range)}</div>
        <div class="diagnostics-field diagnostic-control-with-icon usage-agent">${icon("bot")}${selectHtml("agent_id", "Agent", range.agent_id || "", agents)}</div>
        <button class="btn diagnostics-button primary" type="submit" ${state.busy ? "disabled" : ""}>${icon("search")}查询</button><button class="btn diagnostics-button text" type="button" data-usage-action="reset">重置</button>
      </form></header>
      ${state.error ? `<p class="usage-error" role="alert">${title(state.error)}</p>` : ""}
      <div class="usage-view-toolbar">${dimensionSwitchHtml()}<button type="button" class="diagnostics-button" data-usage-action="export">${icon("download")}导出</button></div>
      <div class="usage-overview">
        <section class="usage-surface usage-trend"><div class="usage-panel-head"><div><h2>用量趋势</h2><small>按日期查看 Token 与模型请求次数</small></div><div class="usage-chart-tools">${chartSwitchHtml("metric", "统计指标", METRICS)}<div class="agent-view-switch diagnostic-view-switch usage-chart-types" role="group" aria-label="图表类型">${[["line","折线图","chart-line"],["stack","堆叠图","chart-column-stacked"]].map(([key,name,glyph]) => `<button type="button" class="${state.chartType === key ? "active" : ""}" data-usage-chart-field="chartType" data-usage-chart-value="${key}" aria-label="${name}" title="${name}" aria-pressed="${state.chartType === key}">${icon(glyph)}</button>`).join("")}</div></div></div><div class="usage-chart-body"><div class="usage-chart">${usageChartSvg(data, state.dimension, state.metric, state.chartType, state.focused)}</div><div class="usage-legend">${legendHtml(data)}</div></div></section>
        <div class="usage-summary"><div class="usage-stats">${stat("累计 Token", measured(summary, "total_tokens"), "layers-3", "purple", "", summary.missing_requests ? `有 ${fmt(summary.missing_requests)} 次请求缺少 Token 数据，总量仅包含已记录部分。` : "")}${stat("日均 Token", (summary.input_reported_requests || summary.output_reported_requests) && data?.dates?.length ? short(summary.total_tokens/data.dates.length) : "—", "calendar-days", "blue")}${stat("单次平均 Token", averageHtml(summary), "chart-no-axes-combined", "green", "", usageAveragePresentation(summary).hint)}${stat("峰值日期", peakUsage(data)?.[1] ? `${title(peakUsage(data)[0].slice(5))} <span class="usage-peak-secondary">${short(peakUsage(data)[1])}</span>` : "—", "calendar-check", "orange")}</div>${insightsHtml(data)}</div>
      </div>
      <section class="usage-details"><div class="usage-workbench"><div class="usage-table-area"><div class="usage-table-frame"><div id="usageDetailRows"></div></div><div id="usageDetailFooter"></div></div><div class="channel-resizer usage-resizer" role="separator" aria-label="调整贡献会话宽度" aria-orientation="vertical" tabindex="0"></div><aside class="usage-contributions diagnostic-channels-page" aria-label="贡献会话"><div id="usageContributionRows"></div><div id="usageContributionFooter"></div></aside></div></section>
      ${state.busy ? '<p class="usage-loading" role="status">正在查询用量…</p>' : ""}${exportDialog()}</div>`;
    const workbench = root.querySelector(".usage-workbench");
    releaseResizer = bindChannelResizer(workbench, state.inspectorWidth || workbench.clientWidth * .4, width => { state.inspectorWidth = width; });
    renderDetailRows(); icons(); chartResize.disconnect();
    const chart = root.querySelector(".usage-chart");
    if (chart) { chartResize.observe(chart); drawChart(); }
    restoreScroll(scroll);
  }

    const rowIcon = row => {
      if (state.dimension === "model") {
        const brands = {deepseek:"deepseek.ico",moonshotai:"kimi.ico",alibaba:"qwen.png",zai:"zai.svg",openrouter:"openrouter.png",openai:"openai.svg?v=20260926-centered-2",openai_codex:"openai.svg?v=20260926-centered-2",anthropic:"anthropic.png",anthropic_compatible:"anthropic.png",minimax:"minimax.ico",stepfun:"stepfun.svg",google:"google-gemini.png"};
        const providerType = row.model?.provider_type || row.model?.provider_name?.toLowerCase() || "openai_compatible";
        return `<img class="usage-identity-logo" data-provider-type="${e(providerType)}" src="./assets/provider-logos/${brands[providerType] || "openai-compatible.svg"}" alt="" />`;
      }
      if (state.dimension === "user") return icon("user-round");
      return (row.channel_types || []).length ? row.channel_types.map(kind => kind === "wecom_app" ? '<img class="usage-identity-logo" src="./assets/channel-logos/wecom-app.svg" alt="企微应用渠道" title="企微应用渠道" />' : kind === "wecom" ? '<img class="usage-identity-logo" src="./assets/channel-logos/wecom.png" alt="企业微信渠道" title="企业微信渠道" />' : kind === "feishu" ? '<img class="usage-identity-logo" src="./assets/channel-logos/feishu.ico" alt="飞书渠道" title="飞书渠道" />' : kind === "dingtalk" ? '<img class="usage-identity-logo" src="./assets/channel-logos/dingtalk.svg" alt="钉钉渠道" title="钉钉渠道" />' : `<span title="${title(kind)} 渠道">${icon("rss")}</span>`).join("") : icon("bot");
    };

  function contributionIcon(row) {
    const kinds = [...new Set(row.channel_types || [])];
    if (state.dimension !== "agent" || kinds.length < 2) return `<span class="usage-object-icon">${rowIcon(row)}</span>`;
    return `<button type="button" class="usage-object-icon usage-icon-stack" data-usage-icon-cycle aria-label="切换渠道图标" title="点击切换渠道图标">${kinds.map((kind, index) => `<span class="usage-icon-layer" data-depth="${index}" title="${title(kind === "wecom_app" ? "企微应用" : kind === "wecom" ? "企业微信" : kind === "feishu" ? "飞书" : kind)}">${rowIcon({channel_types:[kind]})}</span>`).join("")}</button>`;
  }

  function renderDetailRows() {
    const scroll = readScroll();
    const host = root.querySelector("#usageDetailRows"), footer = root.querySelector("#usageDetailFooter"); if (!host || !footer) return;
    const pagination = (total, offset, previous, next, unit = "项") => `<footer class="diagnostic-list-footer"><nav class="diagnostic-pagination" aria-label="用量明细分页"><button type="button" class="diagnostics-button" data-usage-action="${previous}" ${offset && !state.detailBusy ? "" : "disabled"}>上一页</button><span class="diagnostic-page-current models-page-info">第 ${Math.floor(offset / 10) + 1} / ${Math.max(1, Math.ceil(total / 10))} 页 · 共 ${fmt(total)} ${unit}</span><button type="button" class="diagnostics-button" data-usage-action="${next}" ${offset + 10 < total && !state.detailBusy ? "" : "disabled"}>下一页</button></nav></footer>`;
    const contributionHost = root.querySelector("#usageContributionRows"), contributionFooter = root.querySelector("#usageContributionFooter");
    if (state.contributing) {
      const rows = state.contributions?.items || [], total = state.contributions?.total || 0;
      const selected = state.details?.items?.find(row => row.key === state.contributing.key), dimension = state.contributing.dimension;
      const description = dimension === "model" ? selected?.model?.provider_name || "模型用量" : dimension === "user" ? state.details?.users?.[state.contributing.key]?.email || "用户用量" : state.contributing.key;
      const name = dimension === "user" ? state.details?.users?.[state.contributing.key]?.username || state.contributing.name : dimension === "model" ? modelName(selected?.model) : selected?.name || state.contributing.name;
      contributionHost.innerHTML = `<div class="usage-panel-head">${contributionIcon(selected || {})}<div class="usage-object-copy"><h2>${title(name)}</h2><small>${dimension === "agent" ? `<span class="usage-agent-id"><code title="${title(description)}">${title(description.slice(0,8))}</code><button type="button" class="usage-agent-copy" data-usage-copy="${title(description)}" data-usage-copy-label="Agent ID" aria-label="复制 Agent ID" title="复制完整 Agent ID">${icon("copy")}</button></span>` : title(description)}</small></div><span class="usage-session-count" title="${total} 个会话">${icon("messages-square")}${fmt(total)}</span></div>${rows.map(row => `<article class="usage-session-entry"><div class="usage-session-content"><div class="usage-session-heading"><button type="button" class="channel-last-question" data-usage-session="${title(row.session_id)}">${title(row.title || "未命名会话")}</button><strong class="usage-token-count" title="Token">${measured(row,"total_tokens")}</strong></div><div class="diagnostic-session-id channel-session-meta"><span class="channel-session-time"><code title="${title(row.session_id)}">${title(row.session_id.slice(0,8))}</code><button type="button" class="diagnostic-copy-separator" data-usage-copy="${title(row.session_id)}" aria-label="复制 Session ID" title="复制 Session ID：${title(row.session_id)}">${icon("copy")}</button><time>${title(diagnosticTime(row.last_activity))}</time></span><span class="usage-session-counts"><span title="模型请求次数">${icon("activity")}${fmt(row.requests)}</span></span></div></div></article>`).join("") || `<p class="diagnostics-empty">${state.contributions ? "暂无贡献会话" : "正在加载贡献会话…"}</p>`}`;
      contributionFooter.innerHTML = rows.length < total ? `<button type="button" class="usage-load-more" data-usage-action="contribution-more" ${state.contributionBusy ? "disabled" : ""} title="已加载 ${rows.length} / ${total} 个会话">加载更多</button>` : "";
    } else {
      contributionHost.innerHTML = `<div class="usage-panel-head"><h2>贡献会话</h2></div><p class="diagnostics-empty">选择左侧条目，查看贡献会话</p>`;
      contributionFooter.innerHTML = "";
    }
    const rows = state.details?.items || [], total = state.details?.total || 0;
    const headers = `<div class="usage-detail-row usage-detail-head"><span>明细</span><button data-usage-sort="total_tokens">Token</button><button data-usage-sort="requests">请求</button><span title="完整记录的 Token / 完整记录请求数；* 表示有缺失请求未计入">单次平均</span><span title="占当前筛选范围总 Token 的比例">用量占比</span></div>`;
    const names = row => {
      if (state.dimension === "user") { const user = state.details?.users?.[row.key] || {}; return `<span title="${title(user.email || row.key)}">${title(user.username || user.display_name || row.key)}</span>`; }
      if (state.dimension === "model") { const model = row.model || {}; const badge = modelBadge(model, row.key); return `<span title="${title(model.provider_name || "")}">${title(modelName(model))}</span>${badge ? `<small class="usage-model-badge">${badge}</small>` : ""}`; }
      return title(row.name || row.key);
    };
    host.innerHTML = `${headers}${rows.map(row => `<button type="button" class="usage-detail-row ${state.contributing?.key === row.key && state.contributing?.dimension === state.dimension ? "is-selected" : ""}" data-usage-key="${title(row.key)}"><span class="usage-detail-identity">${rowIcon(row)}<span class="usage-detail-name">${names(row)}</span></span><strong>${measured(row, "total_tokens")}</strong><span>${fmt(row.requests)}</span><span>${averageHtml(row)}</span><span>${state.details?.summary?.total_tokens ? (row.total_tokens/state.details.summary.total_tokens*100).toFixed(1)+"%" : "—"}</span></button>`).join("") || `<p class="diagnostics-empty">${state.busy ? "正在加载…" : "当前条件暂无用量"}</p>`}`;
    footer.innerHTML = (total > 10 ? pagination(total, state.offset, "previous", "next") : "");
    restoreScroll(scroll);
  }

  async function loadDetails(resetScroll = false) {
    detailAbort?.abort(); detailAbort = new AbortController();
    const signal = detailAbort.signal;
    state.detailBusy = true; renderDetailRows();
    try {
      const details = await diagnosticRequest(`/api/v1/diagnostics/usage/details?${queryString({...params(), group_by:state.dimension, sort:state.sort, descending:state.descending, offset:state.offset, limit:10})}`, signal);
      if (!signal.aborted && state.active) { state.details = details; state.detailBusy = false;
        const lastOffset = Math.max(0, Math.ceil((details.total || 0) / 10) - 1) * 10;
        if (state.offset > lastOffset) { state.offset = lastOffset; return loadDetails(true); }
        const selected = details.items?.find(row => row.key === state.contributing?.key && state.dimension === state.contributing?.dimension);
        if (!selected) { state.contributing = null; state.contributions = null; contributionAbort?.abort(); }
        renderDetailRows(); icons();
        if (resetScroll) restoreScroll([]);
        if (!selected && details.items?.length) root.querySelector("[data-usage-key]")?.click();
        else if (selected && !state.contributions) { state.contributionOffset = 0; loadContributions(); } }
    } catch (error) { if (!signal.aborted && state.active) { state.detailBusy = false; state.error = error.message; render(); } }
  }
  async function loadContributions() {
    contributionAbort?.abort();
    if (!state.contributing) return;
    contributionAbort = new AbortController();
    const signal = contributionAbort.signal, selected = state.contributing, offset = state.contributionOffset;
    state.contributionBusy = true; renderDetailRows(); icons();
    try {
      const result = await diagnosticRequest(`/api/v1/diagnostics/usage/sessions?${queryString({...params(), group_by:selected.dimension, key:selected.key, offset})}`, signal);
      if (!signal.aborted && state.active && state.contributing === selected) { state.contributionBusy = false; state.contributions = {...result, items:offset ? [...(state.contributions?.items || []), ...result.items] : result.items}; renderDetailRows(); icons(); }
    } catch (error) { if (!signal.aborted && state.active && state.contributing === selected) { state.contributionBusy = false; state.error = error.message; render(); } }
  }
  async function load() {
    reportAbort?.abort(); detailAbort?.abort(); contributionAbort?.abort(); reportAbort = new AbortController();
    state.contributing = null; state.contributions = null; state.details = null; state.detailBusy = false; state.contributionBusy = false;
    const signal = reportAbort.signal; state.busy = true; state.error = ""; render([]);
    try {
      const data = await diagnosticRequest(`/api/v1/diagnostics/usage?${queryString(params())}`, signal);
      if (signal.aborted || !state.active) return;
      state.report = data; state.busy = false; state.focused = null; render(); await loadDetails();
    } catch (error) { if (!signal.aborted && state.active) { state.busy = false; state.error = error.message; render(); } }
  }
  function applyFilters() {
    try {
      const period = state.draft.period || "7";
      const custom = period === "custom" ? {start:root.querySelector('[name="start"]')?.value || state.draft.start, end:root.querySelector('[name="end"]')?.value || state.draft.end} : {};
      const bounds = usageRange(period, new Date(), custom);
      state.filters = {...bounds, period, timezone:state.filters?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", agent_id:state.draft.agent_id || ""};
      state.draft = {...state.filters}; state.offset = 0; state.details = null; state.contributing = null;
      replace(diagnosticTarget("usage", state.filters)); load();
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
    const stack = event.target.closest("[data-usage-icon-cycle]");
    if (stack) {
      const layers = [...stack.querySelectorAll(".usage-icon-layer")];
      for (const layer of layers) layer.dataset.depth = (Number(layer.dataset.depth) + layers.length - 1) % layers.length;
      const current = layers.find(layer => layer.dataset.depth === "0");
      stack.setAttribute("aria-label", `当前${current?.title || "渠道"}，点击切换渠道图标`);
      return;
    }
    const copy = event.target.closest("[data-usage-copy]");
    if (copy) {
      if (copy.disabled) return;
      copy.disabled = true;
      const copyLabel = copy.dataset.usageCopyLabel || "会话 ID";
      try {
        await navigator.clipboard.writeText(copy.dataset.usageCopy);
        copy.classList.add("copied"); copy.innerHTML = icon("check");
        copy.title = `${copyLabel} 已复制`; copy.setAttribute("aria-label", `${copyLabel} 已复制`); icons();
        window.DeepAgentConsole?.toast(`${copyLabel} 已复制`);
        setTimeout(() => {
          if (!copy.isConnected) return;
          copy.classList.remove("copied"); copy.innerHTML = icon("copy");
          copy.title = `复制 ${copyLabel}：${copy.dataset.usageCopy}`;
          copy.setAttribute("aria-label", `复制 ${copyLabel}`); copy.disabled = false; icons();
        }, 1500);
      } catch { copy.disabled = false; window.DeepAgentConsole?.toast("复制失败，请手动选择", true); }
      return;
    }
    const chartControl = event.target.closest("[data-usage-chart-field]");
    if (chartControl) { state[chartControl.dataset.usageChartField] = chartControl.dataset.usageChartValue; state.focused = null; render(); return; }
    const series = event.target.closest("[data-usage-series]");
    if (series) { const key = series.dataset.usageSeries; state.focused = state.focused === key ? null : key; render(); return; }
    const dimension = event.target.closest("[data-usage-dimension]");
    if (dimension) { state.dimension = dimension.dataset.usageDimension; state.focused = null; state.offset = 0; state.contributing = null; state.details = null; render([]); loadDetails(true); return; }
    const sort = event.target.closest("[data-usage-sort]");
    if (sort) { state.descending = state.sort === sort.dataset.usageSort ? !state.descending : true; state.sort = sort.dataset.usageSort; state.offset = 0; loadDetails(true); return; }
    const session = event.target.closest("[data-usage-session]");
    if (session) { navigate(diagnosticTarget("detail", {view:"conversation"}, session.dataset.usageSession)); return; }
    const row = event.target.closest("[data-usage-key]");
    if (row) {
      const name = row.querySelector(".usage-detail-name")?.innerText?.trim().split(/\n+/).filter(Boolean).join(" · ") || row.dataset.usageKey;
      state.contributing = {dimension:state.dimension, key:row.dataset.usageKey, name}; state.contributions = null; state.contributionOffset = 0; render([readScroll()[0], {}]);
      await loadContributions();
      return;
    }
    const action = event.target.closest("[data-usage-action]")?.dataset.usageAction;
    if (!action) return;
    if (action === "reset") { const range = usageRange("7"); state.filters = {...range, period:"7", timezone:Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", agent_id:""}; state.draft = {...state.filters}; state.offset = 0; replace(diagnosticTarget("usage", state.filters)); load(); }
    else if (action === "previous" || action === "next") { if (state.detailBusy || state.busy) return; state.offset = Math.max(0, state.offset + (action === "next" ? 10 : -10)); loadDetails(true); }
    else if (action === "contribution-more") { if (state.contributionBusy) return; state.contributionOffset = state.contributions?.items?.length || 0; await loadContributions(); }
    else if (action === "export") { const dialog = root.querySelector("#usageExportDialog"); dialog.querySelector("#usageExportRange").textContent = `${new Date(state.filters.start).toLocaleString()} — ${new Date(state.filters.end).toLocaleString()}`; dialog.querySelector("#usageExportAgent").textContent = state.report?.agents?.[state.filters.agent_id] || "全部 Agent"; dialog.querySelector("#usageExportDimension").textContent = DIMENSIONS.find(([key]) => key === state.dimension)?.[1] || ""; dialog.showModal(); }
    else if (action === "close-export") root.querySelector("#usageExportDialog")?.close();
    else if (action === "confirm-export") {
      const dialog = root.querySelector("#usageExportDialog"), status = dialog.querySelector(".usage-export-status"), button = dialog.querySelector('[data-usage-action="confirm-export"]');
      button.disabled = true; status.textContent = "正在生成导出文件…";
      try {
        const scope = dialog.querySelector('input[name="usage_export_scope"]:checked').value;
        const path = `/api/v1/diagnostics/usage/export?${queryString({...params(), group_by:state.dimension, sort:state.sort, descending:state.descending, scope, offset:state.offset})}`;
        const transport = diagnosticRequestOptions(path);
        const response = await window.daFetch(window.serviceUrl(transport.path), {...transport.options, includeContext:false, reportStatus:false, cache:"no-store"});
        if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || `HTTP ${response.status}`);
        const url = URL.createObjectURL(await response.blob()), link = document.createElement("a"); link.href = url; link.download = "deepagent-usage.csv"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); dialog.close();
      } catch (error) { status.textContent = error.message; } finally { button.disabled = false; }
    }
  });
  return {
    activate(route, snapshot) {
      state.active = true;
      const restoring = snapshot?.filters && sameDiagnosticFilters(snapshot.filters, route.filters) && snapshot.report;
      if (restoring) Object.assign(state, {...snapshot, active:true, focused:snapshot.focused || null});
      else {
        const period = route.filters.period || "7", bounds = route.filters.start && route.filters.end ? route.filters : {...usageRange(period), ...route.filters};
        state.filters = {...bounds, timezone:route.filters.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", period, agent_id:route.filters.agent_id || ""};
        state.draft = {...state.filters}; state.report = null; state.details = null; state.offset = 0; state.contributing = null; state.contributions = null;
        replace(diagnosticTarget("usage", state.filters));
      }
      render(restoring ? snapshot.scroll || [] : []); if (!state.report) load(); else loadDetails();
    },
    deactivate() { releaseResizer(); state.active = false; reportAbort?.abort(); detailAbort?.abort(); contributionAbort?.abort(); chartResize.disconnect(); },
    snapshot() { return {...state, scroll:readScroll(), active:false, busy:false, detailBusy:false, contributionBusy:false}; },
    clear() { releaseResizer(); reportAbort?.abort(); detailAbort?.abort(); contributionAbort?.abort(); chartResize.disconnect(); state.active = false; state.report = null; state.details = null; },
  };
}
