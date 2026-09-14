import { escapeHtml as e, icon, icons, localInput, selectHtml, bindDiagnosticSelects, diagnosticRequest } from "./diagnostics-ui.js?v=20260912-21";
import { diagnosticTime } from "./diagnostics.js?v=20260914-user-lookup-logs";
import { diagnosticHash, sessionRange, sessionDrilldown, overviewFilters } from "./diagnostics-navigation.js?v=20260912-21";
import { bindDiagnosticColumns, COLUMN_BOUNDS } from "./diagnostic-columns.js?v=20260912-21";
import { buildSessionQuery, sessionQueryError, createSessionReader } from "./diagnostics-session-query.js?v=20260912-21";
import { createReadonlySessionView } from "./diagnostic-detail.js?v=20260912-21";

export const SESSION_STATUSES = { running: "进行中", pending: "待操作", waiting: "待操作", completed: "已完成", stopped: "已停止", error: "异常", failed: "异常", unknown: "未确认", not_started: "未开始" };
const PERIODS = [["24", "最近 24 小时"], ["168", "最近 7 天"], ["720", "最近 30 天"], ["custom", "自定义时间"]];
const PAGE_SIZE = 50;
const SOURCE_OPTIONS = [["", "全部来源"], ["direct", "普通会话"], ["channel", "渠道会话"]];
const button = (label, action, extra = "", kind = "") => `<button type="button" class="btn diagnostics-button ${kind}" data-session-action="${action}" ${extra}>${label}</button>`;
const empty = message => `<div class="diagnostics-empty">${e(message)}</div>`;
export function userCardHtml(id, profiles = {}) {
  const user = profiles[id] || {}, name = user.display_name || id || "未记录";
  return `<span class="diagnostic-user diagnostic-username" title="${e(id)}">${e(name)}</span>`;
}
export function shortQuestionTime(value) {
  const full = diagnosticTime(value);
  return /^\d{4}-\d{2}-\d{2} /.test(full) ? full.slice(5, 16) : full;
}
export function questionTrendHtml(bins = [], availableWidth = 800) {
  if (!bins.length) return empty("当前范围暂无提问趋势");
  const peak = Math.max(1, ...bins.map(bin => Number(bin.question_count) || 0));
  const unit = 10 ** Math.floor(Math.log10(Math.max(1, peak / 4)));
  const max = Math.max(4, Math.ceil(peak / 4 / unit) * unit * 4);
  const width = Math.max(120, Math.round(Number(availableWidth) || 800)), left = 40, right = width - 24, bottom = 154, top = 18;
  const points = bins.map((bin, index) => [left + (bins.length === 1 ? (right - left) / 2 : index * (right - left) / (bins.length - 1)), bottom - (Number(bin.question_count) || 0) / max * (bottom - top)]);
  const labelStep = Math.max(1, Math.ceil(bins.length / Math.max(2, Math.floor((right - left) / 72))));
  const grid = Array.from({ length: 5 }, (_, i) => { const y = bottom - i * (bottom - top) / 4; return `<line x1="${left}" x2="${right}" y1="${y}" y2="${y}" class="${i ? "diagnostic-chart-grid" : "diagnostic-chart-axis"}"/><text x="${left - 10}" y="${y + 4}" text-anchor="end">${max * i / 4}</text>`; }).join("");
  return `<svg class="diagnostic-trend" viewBox="0 0 ${width} 184" role="group" aria-label="按本地日期统计提问数">${grid}<line x1="${left}" x2="${left}" y1="${top}" y2="${bottom}" class="diagnostic-chart-axis"/><path d="M${points[0][0]},${bottom} L${points.map(point => point.join(",")).join(" L")} L${points.at(-1)[0]},${bottom} Z" class="diagnostic-chart-area"/><path d="M${points.map(point => point.join(",")).join(" L")}" class="diagnostic-chart-line"/>${points.map(([x, y], index) => { const bin = bins[index], showLabel = index % labelStep === 0 || index === bins.length - 1; return `<g role="button" tabindex="0" data-day-index="${index}" aria-label="${e(bin.date)} ${Number(bin.question_count) || 0} 条提问"><title>${e(bin.date)} · ${Number(bin.question_count) || 0} 条提问</title><circle cx="${x}" cy="${y}" r="13" class="diagnostic-chart-hit"/><circle cx="${x}" cy="${y}" r="4.5" class="diagnostic-chart-point"/>${showLabel ? `<text x="${x}" y="178" text-anchor="middle">${e(bin.date?.slice(5))}</text><text x="${x}" y="${y - 10}" text-anchor="middle">${Number(bin.question_count) || 0}</text>` : ""}</g>`; }).join("")}</svg>`;
}

export function initDiagnosticSessions({ root, navigate, replace, onBack }) {
  const reader = createSessionReader(diagnosticRequest), contextReader = createSessionReader(diagnosticRequest), eventsReader = createSessionReader(diagnosticRequest);
  const state = { active: false, route: null, filters: {}, data: null, listTotal: null, busy: false, error: "", users: {}, agents: new Map(), showUsers: false, showAgents: false };
  let detail = null, detailData = null, selectBinding = null, scrollToUsers = false, eventsLoading = false, trendObserver = null;
  let releaseColumns = () => {}, columnWidths = null, listFilters = {};
  const disconnectTrend = () => { trendObserver?.disconnect(); trendObserver = null; };
  function observeTrend() {
    const host = root.querySelector(".diagnostic-trend-host");
    if (!host || !state.data?.trend?.length) return;
    let lastWidth = 0;
    const resize = () => {
      if (!state.active || state.route.view !== "overview" || root.querySelector(".diagnostic-trend-host") !== host) return;
      const width = Math.round(host.getBoundingClientRect().width);
      if (width <= 0 || width === lastWidth) return;
      lastWidth = width;
      const focused = host.contains(document.activeElement) ? document.activeElement?.closest("[data-day-index]")?.dataset.dayIndex : undefined;
      host.innerHTML = questionTrendHtml(state.data.trend, width);
      if (focused !== undefined) host.querySelector(`[data-day-index="${focused}"]`)?.focus({ preventScroll: true });
    };
    resize();
    if (typeof ResizeObserver !== "undefined") { trendObserver = new ResizeObserver(resize); trendObserver.observe(host); }
  }
  const cancelReaders = () => { releaseColumns(); disconnectTrend(); reader.cancel(); contextReader.cancel(); eventsReader.cancel(); eventsLoading = false; };
  const publishFilters = () => replace(diagnosticHash(state.route.view, state.filters));
  const scrollRegions = { scroll: ".diagnostic-session-scroll", tableScroll: ".diagnostic-session-table-scroll", questionsScroll: ".diagnostic-question-list", errorsScroll: ".diagnostic-dashboard-side > section:first-child", usersScroll: "#diagnosticActiveUsers", agentsScroll: ".diagnostic-agent-bars" };
  const captureScroll = () => Object.fromEntries(Object.entries(scrollRegions).map(([key, selector]) => [key, root.querySelector(selector)?.scrollTop || 0]));
  function restoreScroll(saved = {}) {
    for (const [key, selector] of Object.entries(scrollRegions)) {
      const region = root.querySelector(selector);
      if (region) region.scrollTop = Math.max(0, Number(saved[key]) || 0);
    }
  }
  function rememberProfiles(data) {
    Object.assign(state.users, data?.users || {});
    // Bounded visited-account options, not an enterprise directory cache.
    const keys = Object.keys(state.users); for (const key of keys.slice(0, Math.max(0, keys.length - 300))) delete state.users[key];
    for (const item of [...(data?.agent_options || []), ...(data?.agents || []), ...(data?.items || []), ...(data?.recent_questions || [])]) if (item.agent_id) state.agents.set(item.agent_id, item.agent_name || item.agent_id);
  }
  function field(label, html, glyph = "") { return `<div class="diagnostics-field${glyph ? " diagnostic-control-with-icon" : ""}" aria-label="${e(label)}">${glyph ? icon(glyph) : ""}${html}</div>`; }
  function viewSwitchHtml() {
    return `<div class="agent-view-switch diagnostic-view-switch" role="group" aria-label="会话视图">${["overview", "list"].map((view, index) => `<button type="button" data-session-view="${view}" class="${view === state.route.view ? "active" : ""}" aria-pressed="${view === state.route.view}" title="${index ? "列表" : "概览"}">${icon(index ? "list" : "layout-grid")}<span>${index ? "列表" : "概览"}</span></button>`).join("")}</div>`;
  }
  function userFilterHtml() {
    const f = state.filters;
    return `<div class="diagnostics-field diagnostic-user-filter"><div class="diagnostic-user-combobox">
        ${icon("user-round")}<input name="user_id" value="${e(state.users[f.user_id]?.display_name || f.user_id || "")}" placeholder="全部用户" aria-label="平台用户" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="diagnosticLoadedUsers" title="可选择已加载用户，或输入完整用户 ID 后按回车查询" spellcheck="false"/>
        <button type="button" data-session-action="user-picker" class="diagnostic-user-picker" aria-label="选择平台用户">${icon("chevron-down")}</button>
        <div class="diagnostic-user-options hidden" id="diagnosticLoadedUsers" role="listbox" aria-label="已加载平台账号"></div>
      </div></div>`;
  }
  function filtersHtml() {
    const f = state.filters;
    return `<form id="diagnosticSessionFilters" class="diagnostic-session-filters" data-session-form="filters" autocomplete="off">
      ${field("Agent", selectHtml("agent_id", "全部 Agent", f.agent_id || "", [["", "全部 Agent"], ...state.agents]), "bot")}
      ${field("时间范围", selectHtml("period", "时间范围", f.period, PERIODS), "clock-3")}
      ${field("来源", selectHtml("source", "全部来源", f.source || "", SOURCE_OPTIONS), "database")}
      <button type="submit" class="btn diagnostics-button primary" ${state.busy ? "disabled" : ""}>${icon("search")}查询</button>
      ${button("重置", "reset", "", "text")}
    </form>`;
  }
  function filterFeedbackHtml() {
    const f = state.filters, range = `${diagnosticTime(f.start)} — ${diagnosticTime(f.end)} · ${f.timezone}`;
    return `<div class="diagnostic-filter-secondary"><span class="diagnostic-loading" role="status" aria-live="polite">${state.busy ? "正在加载…" : ""}</span><div class="diagnostic-filter-feedback"><span class="diagnostic-filter-meta" title="${e(range)}">${e(range)}</span><div class="diagnostic-filter-chips">${chipsHtml()}</div></div><div class="diagnostic-secondary-actions">
      ${f.period === "custom" ? `<div class="diagnostics-custom-time"><label class="diagnostics-field"><input form="diagnosticSessionFilters" type="datetime-local" name="start" aria-label="开始时间" title="开始时间" value="${localInput(f.start)}" required/></label><span>至</span><label class="diagnostics-field"><input form="diagnosticSessionFilters" type="datetime-local" name="end" aria-label="结束时间" title="结束时间（不含）" value="${localInput(f.end)}" required/></label><button form="diagnosticSessionFilters" class="diagnostics-button primary" type="submit" ${state.busy ? "disabled" : ""}>应用</button></div>` : ""}
      ${viewSwitchHtml()}</div></div>`;
  }
  function chipsHtml() { return [state.filters.has_questions === "true" || state.filters.has_questions === true ? ["has_questions", "范围内有提问"] : null, state.filters.has_execution_error === "true" || state.filters.has_execution_error === true ? ["has_execution_error", "范围内有执行异常"] : null].filter(Boolean).map(([key, label]) => `<button type="button" class="diagnostic-filter-chip" data-clear-session="${key}">${label}${icon("x")}</button>`).join(""); }
  function overviewHtml(data) {
    const metrics = data?.metrics || {}, questions = data?.recent_questions || [], errors = data?.recent_errors || [], users = data?.active_users || [], agents = data?.agents || [];
    const max = Math.max(1, ...agents.map(agent => agent.question_count));
    const stats = [["session_count", "会话数", "messages-square", "blue"], ["question_count", "提问数", "message-circle-more", "purple"], ["active_user_count", "活跃用户", "user-round", "green"], ["error_session_count", "含异常会话", "triangle-alert", "red"]];
    return `<div class="diagnostic-stats">${stats.map(([key, label, glyph, tone]) => `
      <button type="button" data-stat="${key}" ${!data ? "disabled" : ""}>
        <span class="diagnostic-stat-icon ${tone}">${icon(glyph)}</span>
        <span><small>${label}</small><strong>${metrics[key] == null ? "—" : Number(metrics[key]).toLocaleString()}</strong></span>
        <span class="diagnostic-stat-arrow">${icon("arrow-up-right")}</span>
      </button>`).join("")}</div>
      <div class="diagnostic-dashboard">
        <section class="diagnostic-chart-section"><header><h2>提问趋势</h2><span title="${e(state.filters.timezone)}">提问数 · ${e(state.filters.timezone)}</span></header><div class="diagnostic-trend-host">${questionTrendHtml(data?.trend)}</div></section>
        <section class="diagnostic-agent-section"><header><h2>Agent 分布</h2><span>提问数</span></header>
          <div class="diagnostic-agent-bars">${agents.slice(0, state.showAgents ? agents.length : 8).map((agent, index) => `<button type="button" data-agent-drill="${e(agent.agent_id)}" data-tone="${index % 3}"><span title="${e(agent.agent_name || agent.agent_id)}">${e(agent.agent_name || agent.agent_id)}</span><span class="diagnostic-bar-track"><i style="width:${Math.max(0, Math.min(100, agent.question_count / max * 100))}%"></i></span><b>${Number(agent.question_count)}</b></button>`).join("") || empty("暂无提问")}</div>
          ${agents.length > 8 ? button(state.showAgents ? "收起" : `展开全部 ${agents.length} 个 Agent`, "agents", "", "text") : ""}
        </section>
        <section class="diagnostic-recent"><header><h2>最近提问</h2>${button(`查看全部${icon("arrow-right")}`, "all-questions", "", "text")}</header>
          <div class="diagnostic-question-list">${questions.map((question, index) => `<button type="button" class="diagnostic-question" data-question-index="${index}">
            <span class="diagnostic-question-icon">${icon("message-circle-more")}</span>
            <span class="diagnostic-question-copy"><strong title="${e(question.summary)}">${e(question.summary || "附件 / 上下文输入")}</strong><span class="diagnostic-question-meta"><span title="${e(question.agent_name || question.agent_id)}">${e(question.agent_name || question.agent_id)}</span><span aria-hidden="true">·</span>${userCardHtml(question.user_id, state.users)}<span aria-hidden="true">·</span><time title="${e(diagnosticTime(question.created_at))}">${e(shortQuestionTime(question.created_at))}</time></span></span>
            <span class="diagnostic-question-arrow">${icon("arrow-right")}</span>
          </button>`).join("") || empty("当前范围暂无提问")}</div>
        </section>
        <div class="diagnostic-dashboard-side">
          <section><header><h2>最近异常</h2></header>${errors.map((error, index) => `<button type="button" class="diagnostic-error-item" data-error-index="${index}"><span class="diagnostic-error-icon">${icon("triangle-alert")}</span><span class="diagnostic-error-copy"><strong>${e(error.message || error.summary || error.code || "执行异常")}</strong><small>${e(error.agent_name || error.agent_id)} · ${e(diagnosticTime(error.created_at))}</small></span></button>`).join("") || empty("当前范围没有执行异常")}</section>
          <section id="diagnosticActiveUsers" tabindex="-1"><header><h2>活跃用户</h2>${users.length > 5 ? button(state.showUsers ? "收起" : `展开${icon("arrow-right")}`, "users", "", "text") : ""}</header>${users.slice(0, state.showUsers ? 20 : 5).map((user, index) => `<button type="button" class="diagnostic-active-user" data-user-drill="${e(user.user_id)}"><span class="diagnostic-user-rank">${index + 1}</span>${userCardHtml(user.user_id, state.users)}<span class="diagnostic-user-count" title="范围内提问数">${Number(user.question_count)}</span></button>`).join("") || empty("暂无活跃账号")}</section>
        </div>
      </div>`;
  }
  function listHtml(data) {
    const f = state.filters, total = Number(data?.total ?? state.listTotal ?? 0), offset = Number(f.offset || 0), limit = PAGE_SIZE, items = data?.items || [];
    const currentPage = Math.floor(offset / limit) + 1, pageCount = Math.max(1, Math.ceil(total / limit));
    return `<section class="diagnostic-list-workbench" aria-label="会话列表">
      <form class="diagnostic-list-toolbar" data-session-form="search"><div class="diagnostic-list-search"><label class="diagnostics-field"><button class="diagnostic-search-submit" type="submit" aria-label="查询会话">${icon("search")}</button><input type="search" name="query" value="${e(f.query || "")}" placeholder="搜索会话名称或 ID" aria-label="搜索会话名称或 ID" maxlength="200"/></label></div>
        <div class="diagnostic-list-options">${userFilterHtml()}${selectHtml("latest_status", "全部状态", f.latest_status || "", [["", "全部状态"], ...Object.entries(SESSION_STATUSES).filter(([key]) => !["pending", "failed"].includes(key))])}
        </div>
      </form>
      <div class="diagnostic-session-table-scroll"><div class="diagnostic-session-table" role="table" aria-label="会话查询列表">
        <div class="diagnostic-session-row diagnostic-session-columns" role="row">${["会话", "Agent", "用户", "来源", "状态", "最近活动"].map((label, i) => `<span role="columnheader">${label}${i === 5 ? icon("arrow-down") : `<span class="diagnostic-column-resizer" role="separator" tabindex="0" aria-orientation="vertical" aria-label="调整${label}列宽" aria-valuemin="${COLUMN_BOUNDS[i][0]}" aria-valuemax="${COLUMN_BOUNDS[i][1]}" data-resize-column="${i}"></span>`}</span>`).join("")}</div>
        ${items.map(item => `<div class="diagnostic-session-row" role="row" tabindex="0" data-session-id="${e(item.session_id)}">
          <div class="diagnostic-session-name" role="cell"><div class="diagnostic-session-title"><strong title="${e(item.title || "未命名会话")}">${e(item.title || "未命名会话")}</strong></div><div class="diagnostic-session-id"><button type="button" data-copy-session="${e(item.session_id)}" aria-label="复制 Session ID" title="复制 Session ID：${e(item.session_id)}">${icon("copy")}</button><code title="${e(item.session_id)}">${e(String(item.session_id).slice(0, 8))}</code><span aria-hidden="true">·</span><p title="${e(item.recent_question?.summary || "当前范围暂无提问")}">${e(item.recent_question?.summary || "当前范围暂无提问")}</p></div></div>
          <span class="diagnostic-agent-name" role="cell"><span class="diagnostic-agent-icon">${icon("bot")}</span><span title="${e(item.agent_name || item.agent_id)}">${e(item.agent_name || item.agent_id || "未记录")}</span></span>
          <span role="cell">${userCardHtml(item.user_id, state.users)}</span>
          <span role="cell"><span class="diagnostic-source-pill">${item.source === "channel" ? "渠道" : "普通"}</span></span>
          <span role="cell"><span class="diagnostic-status ${e(item.latest_status)}">${e(SESSION_STATUSES[item.latest_status] || "未确认")}</span></span>
          <time role="cell">${e(diagnosticTime(item.last_activity_at || item.updated_at))}</time>
        </div>`).join("")}
      </div>
      ${!items.length && !state.busy ? empty("没有匹配会话，可重置时间、Agent 或用户条件后再查询。") : ""}
      </div>
      <footer class="diagnostic-list-footer"><nav class="diagnostic-pagination" aria-label="会话分页" title="共 ${total.toLocaleString()} 个会话">${button("上一页", "previous", `aria-label="上一页" ${offset <= 0 || state.busy ? "disabled" : ""}`, "btn-sm")}<span class="diagnostic-page-current models-page-info" aria-live="polite" aria-label="第 ${currentPage} 页，共 ${pageCount} 页">第 ${currentPage} / ${pageCount} 页 · 共 ${total.toLocaleString()} 项</span>${button("下一页", "next", `aria-label="下一页" ${offset + limit >= total || state.busy ? "disabled" : ""}`, "btn-sm")}</nav></footer>
    </section>`;
  }
  function render() {
    if (!state.active || state.route.view === "detail") return;
    const scroll = captureScroll();
    selectBinding?.dispose(); disconnectTrend(); releaseColumns();
    root.innerHTML = `<div class="diagnostics-page diagnostic-sessions-page" data-diagnostic-view="${state.route.view}">
      <header class="diagnostics-head"><div class="diagnostic-page-heading"><h1>会话</h1><p>了解用户提问与使用情况，查看完整会话。</p></div>
        <div class="diagnostic-head-actions">${filtersHtml()}</div>
        ${filterFeedbackHtml()}
      </header>
      <div class="diagnostic-session-scroll">${state.error ? `<div class="diagnostics-notice" data-kind="error" role="alert">${e(state.error)}</div>` : ""}${state.route.view === "overview" ? overviewHtml(state.data) : listHtml(state.data)}</div>
    </div>`;
    restoreScroll(scroll);
    root.setAttribute("aria-busy", String(state.busy));
    selectBinding = bindDiagnosticSelects(root, (key, value) => {
      state.filters[key] = value; state.filters.offset = 0;
      if (key === "period") { delete state.filters.start; delete state.filters.end; if (value === "custom") { Object.assign(state.filters, sessionRange({ ...state.filters, period: "168" }), { period: "custom" }); render(); return; } }
      void loadCurrent();
    });
    releaseColumns = bindDiagnosticColumns(root.querySelector(".diagnostic-session-table"), columnWidths, values => { columnWidths = values; });
    icons(); observeTrend();
    if (scrollToUsers) { scrollToUsers = false; root.querySelector("#diagnosticActiveUsers")?.scrollIntoView({ block: "center" }); root.querySelector("#diagnosticActiveUsers")?.focus({ preventScroll: true }); }
  }
  async function loadCurrent({ pageChange = false } = {}) {
    if (state.route.view === "overview") state.filters = overviewFilters(state.filters);
    else state.filters.limit = PAGE_SIZE;
    state.listTotal = pageChange ? state.data?.total ?? state.listTotal : null;
    try { state.filters = sessionRange(state.filters); } catch (error) { reader.cancel(); state.busy = false; state.data = null; state.error = error.message; render(); return; }
    publishFilters(); const scope = reader.begin(); state.busy = true; state.error = ""; state.data = null; restoreScroll(); render();
    try {
      const suffix = state.route.view === "overview" ? "/overview" : "";
      const data = await scope.get(`api/v1/diagnostics/sessions${suffix}?${buildSessionQuery(state.filters, state.route.view === "overview")}`);
      if (!scope.current() || !state.active) return;
      state.data = data; rememberProfiles(data);
    } catch (error) { if (scope.current() && state.active) state.error = sessionQueryError(error); }
    finally { if (scope.current() && state.active) { state.busy = false; render(); } }
  }
  const openSession = (id, focus = {}) => navigate(diagnosticHash("detail", focus, id));
  const drill = extra => navigate(diagnosticHash("list", sessionDrilldown(state.filters, extra)));
  function mountDetail(saved) {
    detail?.destroy(); selectBinding?.dispose(); detailData = saved?.detailData || null;
    detail = createReadonlySessionView({ root, onBack, onRefresh: () => void loadDetail(), onViewChange: view => { state.route.filters.view = view; replace(diagnosticHash("detail", state.route.filters, state.route.id)); if (view === "trajectory") void loadEvents(); } });
    if (detailData) { detailData.focus.view = state.route.filters.view || "conversation"; detail.load(detailData); detail.restore?.(saved.detailSnapshot); if (state.route.filters.view === "trajectory") void loadEvents(); }
    else void loadDetail();
  }
  async function loadDetail() {
    contextReader.cancel(); eventsReader.cancel(); eventsLoading = false;
    const scope = reader.begin(), id = state.route.id, focus = { view: state.route.filters.view || "conversation", eventId: state.route.filters.event_id, runId: state.route.filters.run_id };
    detailData = null; detail.setLoading?.(true);
    try {
      const session = await scope.get(`api/v1/diagnostics/sessions/${encodeURIComponent(id)}`);
      if (!scope.current() || state.route.id !== id || !state.active) return;
      rememberProfiles(session);
      detailData = { session, history: null, events: [], eventsComplete: false, users: session.users || {}, focus };
      detail.load(detailData);
      const historyTask = scope.get(`api/v1/diagnostics/sessions/${encodeURIComponent(id)}/history`).then(history => ({ history })).catch(error => ({ historyError: sessionQueryError(error) }));
      const results = await Promise.all([historyTask]);
      if (!scope.current() || state.route.id !== id || !state.active) return;
      Object.assign(detailData, ...results); detailData.focus.view = state.route.filters.view || "conversation"; detail.load(detailData);
      if (detailData.historyError || detailData.eventsError) detail.setError?.({ history: detailData.historyError, events: detailData.eventsError });
      if (state.route.filters.view === "trajectory" || (focus.runId && !focus.eventId)) void loadEvents();
      if (!detailData.historyError) void loadContext();
    } catch (error) { if (scope.current() && state.active) detail.setError?.(sessionQueryError(error)); }
    finally { if (scope.current() && state.active) detail.setLoading?.(false); }
  }
  async function loadEvents() {
    if (!detailData || eventsLoading || detailData.eventsComplete) return;
    const id = state.route.id, scope = eventsReader.begin(); eventsLoading = true;
    detail.setLoading?.("正在读取已保存轨迹…", { section: "events" });
    try {
      const events = await eventsReader.readEvents(scope, id, (count, complete) => { if (scope.current() && !complete) detail.setLoading?.(`已读取 ${count} 条轨迹，继续读取…`, { section: "events" }); });
      if (!scope.current() || !state.active || state.route.id !== id || !detailData) return;
      Object.assign(detailData, { events, eventsComplete: true, eventsError: null }); detailData.focus.view = state.route.filters.view || "conversation"; detail.load(detailData);
    } catch (error) { if (scope.current() && state.active && detailData) { detailData.eventsError = sessionQueryError(error); detail.setError?.(detailData.eventsError, { section: "events" }); } }
    finally { if (scope.current()) eventsLoading = false; }
  }
  async function loadContext() {
    if (!detailData) return;
    const id = state.route.id, scope = contextReader.begin();
    try { const session = await scope.get(`api/v1/diagnostics/sessions/${encodeURIComponent(id)}?include_context=true`); if (!scope.current() || state.route.id !== id || !state.active) return; detailData.session = session; detailData.focus.view = state.route.filters.view || "conversation"; detail.load(detailData); }
    catch (error) { if (scope.current()) detail.setError?.({ context: sessionQueryError(error) }); }
  }
  function submit(event) {
    const form = event.target.closest("[data-session-form]"); if (!form || !root.contains(form)) return;
    event.preventDefault(); const values = Object.fromEntries(new FormData(form));
    if (form.dataset.sessionForm === "filters" && state.filters.period !== "custom") { delete state.filters.start; delete state.filters.end; }
    if (values.user_id && values.user_id === state.users[state.filters.user_id]?.display_name) values.user_id = state.filters.user_id;
    for (const key of ["start", "end"]) if (values[key]) values[key] = values[key] === localInput(state.filters[key]) ? state.filters[key] : new Date(values[key]).toISOString();
    Object.assign(state.filters, values, { offset: 0 }); void loadCurrent();
  }
  async function click(event) {
    const target = event.target.closest("button,[data-session-id],[data-day-index]"); if (!target || !root.contains(target) || state.route?.view === "detail") return;
    if (target.dataset.copySession) {
      event.stopPropagation();
      if (target.disabled) return;
      target.disabled = true;
      try {
        await navigator.clipboard.writeText(target.dataset.copySession);
        target.classList.add("copied");
        target.innerHTML = icon("check");
        target.title = "会话 ID 已复制";
        target.setAttribute("aria-label", "会话 ID 已复制");
        icons();
        window.DeepAgentConsole?.toast("会话 ID 已复制");
        setTimeout(() => {
          if (!target.isConnected) return;
          target.classList.remove("copied");
          target.innerHTML = icon("copy");
          target.title = `复制 Session ID：${target.dataset.copySession}`;
          target.setAttribute("aria-label", "复制 Session ID");
          target.disabled = false;
          icons();
        }, 1500);
      } catch {
        target.disabled = false;
        window.DeepAgentConsole?.toast("复制失败，请手动选择", true);
      }
      return;
    }
    if (target.dataset.loadedUser !== undefined) { state.filters.user_id = target.dataset.loadedUser; state.filters.offset = 0; void loadCurrent(); return; }
    if (target.dataset.sessionView) {
      if (state.route.view === "list") listFilters = Object.fromEntries(Object.entries(state.filters).filter(([key]) => ["user_id", "query", "latest_status", "has_questions", "has_execution_error", "limit"].includes(key)));
      const common = overviewFilters(state.filters);
      navigate(diagnosticHash(target.dataset.sessionView, target.dataset.sessionView === "overview" ? common : { ...listFilters, ...common, offset: 0 })); return;
    }
    if (target.dataset.sessionPage) {
      const page = Number(target.dataset.sessionPage), limit = PAGE_SIZE, pages = Math.ceil(Number(state.data?.total || 0) / limit);
      if (Number.isInteger(page) && page >= 1 && page <= pages && !state.busy) { state.filters.offset = (page - 1) * limit; void loadCurrent({ pageChange: true }); }
      return;
    }
    if (target.dataset.openSession || target.dataset.sessionId) return openSession(target.dataset.openSession || target.dataset.sessionId);
    if (target.dataset.questionIndex !== undefined) { const q = state.data.recent_questions[Number(target.dataset.questionIndex)]; return openSession(q.session_id, { view: "conversation", event_id: q.event_id, run_id: q.run_id }); }
    if (target.dataset.errorIndex !== undefined) { const q = state.data.recent_errors[Number(target.dataset.errorIndex)]; return openSession(q.session_id, { view: "trajectory", event_id: q.event_id, run_id: q.run_id }); }
    if (target.dataset.dayIndex !== undefined) { const bin = state.data.trend[Number(target.dataset.dayIndex)]; return drill({ start: bin.start, end: bin.end, period: "custom", has_questions: true }); }
    if (target.dataset.agentDrill !== undefined) return drill({ agent_id: target.dataset.agentDrill });
    if (target.dataset.userDrill !== undefined) return drill({ user_id: target.dataset.userDrill });
    if (target.dataset.clearSession) { delete state.filters[target.dataset.clearSession]; state.filters.offset = 0; void loadCurrent(); return; }
    if (target.dataset.stat) { if (target.dataset.stat === "active_user_count") { state.showUsers = true; scrollToUsers = true; render(); } else drill(target.dataset.stat === "question_count" ? { has_questions: true } : target.dataset.stat === "error_session_count" ? { has_execution_error: true } : {}); return; }
    switch (target.dataset.sessionAction) {
      case "all-questions": drill({ has_questions: true }); break;
      case "users": state.showUsers = !state.showUsers; render(); break;
      case "user-picker": root.querySelector('input[name="user_id"]')?.focus(); break;
      case "agents": state.showAgents = !state.showAgents; render(); break;
      case "reset": state.filters = { period: "168", limit: 50, offset: 0 }; void loadCurrent(); break;
      case "previous":
        if (state.busy || Number(state.filters.offset || 0) <= 0) break;
        state.filters.offset = Math.max(0, Number(state.filters.offset || 0) - PAGE_SIZE); void loadCurrent({ pageChange: true }); break;
      case "next":
        if (state.busy || Number(state.filters.offset || 0) + PAGE_SIZE >= Number(state.data?.total || 0)) break;
        state.filters.offset = Number(state.filters.offset || 0) + PAGE_SIZE; void loadCurrent({ pageChange: true }); break;
    }
  }
  function userOptions(event) {
    if (!event.target.matches('input[name="user_id"]')) return;
    const menu = root.querySelector("#diagnosticLoadedUsers"), q = event.target.value.toLowerCase();
    const entries = Object.entries(state.users).filter(([id, user]) => `${id} ${user.display_name || ""}`.toLowerCase().includes(q)).slice(0, 30);
    menu.innerHTML = `<button type="button" role="option" data-loaded-user="">全部用户</button>${entries.map(([id]) => `<button type="button" role="option" data-loaded-user="${e(id)}">${userCardHtml(id, state.users)}</button>`).join("")}<p>仅查找已加载账号；其他账号请直接输入 ID 后查询。</p>`;
    menu.classList.remove("hidden"); event.target.setAttribute("aria-expanded", "true");
  }
  root.addEventListener("submit", submit); root.addEventListener("click", click); root.addEventListener("input", userOptions); root.addEventListener("focusin", userOptions);

  root.addEventListener("keydown", event => {
    if ((event.key === "Enter" || event.key === " ") && event.target.matches("[data-session-id],[data-day-index]")) { event.preventDefault(); event.target.dispatchEvent(new MouseEvent("click", { bubbles: true })); }
    const input = root.querySelector('input[name="user_id"]'), menu = root.querySelector("#diagnosticLoadedUsers");
    if (["ArrowDown", "ArrowUp"].includes(event.key) && event.target.closest(".diagnostic-user-combobox")) {
      event.preventDefault(); if (menu.classList.contains("hidden")) userOptions({ target: input });
      const options = [...menu.querySelectorAll("[data-loaded-user]")], index = options.indexOf(event.target);
      options[(index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length]?.focus();
    }
    if (event.key === "Escape") { menu?.classList.add("hidden"); input?.setAttribute("aria-expanded", "false"); }
  });
  document.addEventListener("click", event => { if (!event.target.closest(".diagnostic-user-combobox")) { root.querySelector("#diagnosticLoadedUsers")?.classList.add("hidden"); root.querySelector('input[name="user_id"]')?.setAttribute("aria-expanded", "false"); } });
  return {
    activate(route, saved = null) {
      cancelReaders(); state.active = true; state.route = route; detail?.destroy(); detail = null; state.busy = false; state.error = "";
      if (route.view === "detail") return mountDetail(saved);
      state.filters = { offset: 0, ...route.filters, limit: PAGE_SIZE }; state.data = saved?.data || null; state.listTotal = null;
      if (saved) {
        Object.assign(state, { filters: { ...saved.filters, limit: PAGE_SIZE }, showUsers: saved.showUsers, showAgents: saved.showAgents });
        if (Number(saved.filters?.limit || PAGE_SIZE) !== PAGE_SIZE) state.data = null;
      }
      state.filters.offset = Math.max(0, Math.floor(Number(state.filters.offset || 0) / PAGE_SIZE)) * PAGE_SIZE;
      if (route.view === "overview") state.filters = overviewFilters(state.filters);
      try { state.filters = sessionRange(state.filters); } catch (error) { state.error = error.message; render(); return; }
      if (state.data) { rememberProfiles(state.data); render(); restoreScroll(saved); } else void loadCurrent();
    },
    deactivate() { state.active = false; cancelReaders(); selectBinding?.close(); detail?.destroy(); detail = null; },
    snapshot() { return state.route?.view === "detail" ? { detailData, detailSnapshot: detail?.snapshot?.() } : { filters: { ...state.filters }, data: state.data, showUsers: state.showUsers, showAgents: state.showAgents, ...captureScroll() }; },
    clear() { state.active = false; cancelReaders(); detail?.destroy(); detail = null; detailData = null; state.data = null; state.users = {}; listFilters = {}; columnWidths = null; state.agents.clear(); root.replaceChildren(); },
  };
}
