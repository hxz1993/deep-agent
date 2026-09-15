import { peerPickerHtml, bindPeerPicker } from "./channel-peer-picker.js?v=20260915-release-audit";
import { escapeHtml as e, icon, icons, selectHtml, bindDiagnosticSelects, diagnosticRequest } from "./diagnostics-ui.js?v=20260915-release-audit";
import { diagnosticHash, sessionRange, channelEntryFilters } from "./diagnostics-navigation.js?v=20260915-release-audit";
import { createSessionReader, sessionQueryError } from "./diagnostics-session-query.js?v=20260915-release-audit";
import { bindChannelResizer } from "./channel-resizer.js?v=20260915-release-audit";
import { shortQuestionTime } from "./diagnostics-sessions.js?v=20260915-app-filter";

const PERIODS = [["24", "最近 1 天"], ["168", "最近 7 天"], ["720", "最近 30 天"]];
const PAGE_SIZE = 25;
const empty = text => `<div class="diagnostics-empty">${e(text)}</div>`;
const count = value => Number(value || 0).toLocaleString();
const logo = () => '<img src="./assets/channel-logos/wecom.png" width="20" height="20" alt="企业微信" />';
const copy = (id, separator = false) => id ? `<button type="button" class="channel-copy${separator ? " diagnostic-copy-separator" : ""}" data-channel-copy="${e(id)}" aria-label="复制会话 ID ${e(id)}" title="复制会话 ID">${icon("copy")}</button>` : "";
export const channelPeerName = peer => peer?.name || (peer?.kind === "group" ? "群聊" : "用户");
export function channelQuery(filters, extra = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({ start: filters.start, end: filters.end, timezone: filters.timezone, ...extra })) {
    if (value !== "" && value != null) params.set(key, String(value));
  }
  return params.toString();
}
export function channelSessionHtml(session) {
  const id = session.session_id || "";
  return `<article class="channel-session-item">
    <button type="button" class="channel-last-question" data-channel-open="${e(id)}">${e(session.latest_question || "暂无提问")}</button>
    <div class="diagnostic-session-id channel-session-meta"><span class="channel-session-time"><time>${e(shortQuestionTime(session.last_activity))}</time><span aria-hidden="true">·</span>${session.is_question ? icon("message-circle-more") : `<span class="channel-question-count" title="提问数" aria-label="${count(session.question_count)} 条提问">${icon("message-circle-more")}${count(session.question_count)}</span>`}</span><span class="channel-session-stats">${session.is_latest ? '<span class="channel-latest-label">最近</span>' : ""}<span class="channel-session-reference">${copy(id)}<code>${e(id.slice(0, 8))}</code></span></span></div>
  </article>`;
}

export function initDiagnosticChannels({ root, navigate, replace, onBack }) {
  const accountsReader = createSessionReader(diagnosticRequest), overviewReader = createSessionReader(diagnosticRequest), peersReader = createSessionReader(diagnosticRequest), historyReader = createSessionReader(diagnosticRequest);
  const state = { active: false, accounts: [], filters: {}, overview: null, peers: null, sessions: null, selected: null, loading: false, peersLoading: false, historyLoading: false, error: "", historyError: "", userChoice: null, groupChoice: null, pickerQueries: {user:"", group:""} };
  let selectBinding, disposeResizer, disposePicker;
  let inspectorWidth = 0;
  const cancel = () => { accountsReader.cancel(); overviewReader.cancel(); peersReader.cancel(); historyReader.cancel(); };
  const publish = () => replace(diagnosticHash("channels", state.filters));
  const base = () => `api/v1/diagnostics/channels/${encodeURIComponent(state.filters.account_id)}`;
  const account = () => state.accounts.find(item => item.id === state.filters.account_id);
  function accountHtml() {
    const a = account();
    if (!a) return empty(state.loading ? "正在加载渠道…" : "暂无渠道账户");
    const app = a.application;
    return `<section class="channel-account-card" aria-label="渠道账户信息"><div class="channel-account-column"><div class="channel-account-heading">${logo()}<strong>${e(a.name)}</strong><span class="channel-enabled ${a.enabled ? "enabled" : "disabled"}">${a.enabled ? "已启用" : "已停用"}</span></div><dl class="channel-account-fields"><dt>平台类型</dt><dd>企微（WS）</dd><dt>绑定 Agent</dt><dd>${e(a.agent?.name || "未绑定")}</dd><dt>会话用户</dt><dd title="${e(a.user_id)}">${e(a.user_name || a.user_id || "未设置")}</dd></dl></div><div class="channel-application-column"><div class="channel-app-heading">${icon("link-2")}<strong>${e(app?.name || "未关联企微应用")}</strong></div><dl class="channel-app-fields"><dt>企微 Agent ID</dt><dd>${e(app?.agent_id || "—")}</dd><dt>企业 ID</dt><dd title="${e(app?.corp_id || "")}">${e(app?.corp_id || "—")}</dd><dt>用户名查询</dt><dd>${a.user_details_enabled ? "已开启" : "未开启"}</dd></dl></div></section>`;
  }
  function metricsHtml() {
    const summary = state.overview?.summary;
    return `<div class="channel-metrics">${[["active_users", "活跃用户", "users-round", "purple"], ["session_count", "会话数", "messages-square", ""], ["question_count", "提问数", "message-circle-more", "green"]].map(([key, label, glyph, tone]) => `<div class="channel-metric"><span class="diagnostic-stat-icon ${tone}">${icon(glyph)}</span><span class="channel-metric-copy"><small>${label}</small><strong>${summary ? count(summary[key]) : "—"}</strong></span></div>`).join("")}</div>`;
  }
  function rankingHtml() {
    const users = state.overview?.active_users || [];
    return `<section class="channel-active-users" aria-label="活跃用户"><h2>活跃用户</h2>${users.length ? users.slice(0, 5).map((user, i) => `<button type="button" data-channel-user="${e(user.user_id)}" aria-pressed="${state.filters.channel_user_id === user.user_id}" class="channel-active-user diagnostic-active-user" title="${e(user.name || user.user_id)}"><span class="diagnostic-user-rank">${i + 1}</span><span class="diagnostic-ranking-identity"><span class="diagnostic-identity-icon">${icon("user-round")}</span><span class="channel-ranking-name">${e(user.name || user.user_id)}</span></span><span class="diagnostic-user-count">${count(user.question_count)}</span></button>`).join("") : empty(state.loading ? "正在加载…" : "暂无活跃用户")}</section>`;
  }
  function pickerChoice(kind) {
    const id = state.filters[kind === "user" ? "channel_user_id" : "channel_group_id"];
    if (!id) return null;
    const cached = state[kind === "user" ? "userChoice" : "groupChoice"];
    return cached?.peer_id === id ? cached : {peer_id:id};
  }
  function selectFilter(kind, peer) {
    state[kind === "user" ? "userChoice" : "groupChoice"] = peer;
    state.filters = {...state.filters, [kind === "user" ? "channel_user_id" : "channel_group_id"]:peer?.peer_id, peer_id:undefined, query:"", offset:0};
    state.error = ""; void loadPeers();
  }
  function toolbarHtml() {
    const f = state.filters;
    return `<div class="diagnostic-list-toolbar"><div class="agent-view-switch diagnostic-view-switch" role="group" aria-label="渠道对象类型">${[["user", "用户", "user-round"], ["group", "群聊", "users-round"]].map(([kind, text, glyph]) => `<button type="button" class="${f.kind === kind ? "active" : ""}" data-channel-kind="${kind}" aria-pressed="${f.kind === kind}">${icon(glyph)}${text}</button>`).join("")}</div><div class="channel-peer-filters">${f.kind === "group" ? peerPickerHtml("group", pickerChoice("group")) : ""}${peerPickerHtml("user", pickerChoice("user"))}</div></div>`;
  }
  function tableHtml() {
    const rows = state.peers?.items || [], f = state.filters;
    return `<div class="channel-table-area"><div class="channel-table-scroll diagnostic-session-table-scroll"><div class="channel-peer-table diagnostic-session-table" role="table" aria-label="渠道会话"><div class="diagnostic-session-row diagnostic-session-columns" role="row"><span role="columnheader">${f.kind === "group" ? "群聊" : "渠道用户"}</span><span role="columnheader">会话</span><span role="columnheader">提问</span><span role="columnheader">最近活跃</span><span role="columnheader"><span class="sr-only">操作</span></span></div>${rows.map(peer => {
      const latest = peer.latest_session || {}, id = latest.session_id || "";
      return `<div role="row" data-channel-peer="${e(peer.peer_id)}" tabindex="0" aria-selected="${state.selected?.peer_id === peer.peer_id}" class="diagnostic-session-row ${state.selected?.peer_id === peer.peer_id ? "selected" : ""}"><div role="cell"><div class="channel-identity"><div class="channel-identity-copy"><div class="channel-peer-name diagnostic-session-title" title="${e(channelPeerName(peer))} · ${e(peer.peer_id)}"><span class="diagnostic-identity-icon ${peer.kind === "group" ? "group" : ""}">${icon(peer.kind === "group" ? "users-round" : "user-round")}</span><strong>${e(channelPeerName(peer))}</strong><span>·</span><span>${e(peer.peer_id)}</span></div><div class="diagnostic-session-id"><code>${e(id.slice(0, 8))}</code>${copy(id, true)}<p title="${e(latest.latest_question)}">${e(latest.latest_question || "暂无提问")}</p></div></div></div></div><div role="cell">${count(peer.session_count)}</div><div role="cell">${count(peer.question_count)}</div><div role="cell"><time>${e(shortQuestionTime(peer.last_activity))}</time></div><div role="cell"><button type="button" class="channel-open channel-row-open" data-channel-open="${e(id)}" aria-label="打开最新会话 ${e(channelPeerName(peer))}" title="打开最新会话" ${id ? "" : "disabled"}>${icon("arrow-up-right")}<span>最新会话</span></button></div></div>`;
    }).join("")}</div>${rows.length ? "" : empty(state.peersLoading ? "正在加载…" : "当前条件下暂无记录")}</div></div>`;
  }
  function paginationHtml() {
    const f = state.filters;
    return `<footer class="diagnostic-list-footer"><nav class="diagnostic-pagination" aria-label="渠道列表分页"><button type="button" class="diagnostics-button" data-channel-action="previous" ${state.peersLoading || !Number(f.offset) ? "disabled" : ""}>上一页</button><span>${Math.floor(Number(f.offset || 0) / PAGE_SIZE) + 1} / ${Math.max(1, Math.ceil(Number(state.peers?.total || 0) / PAGE_SIZE))} · 共 ${count(state.peers?.total)} 项</span><button type="button" class="diagnostics-button" data-channel-action="next" ${state.peersLoading || Number(f.offset || 0) + PAGE_SIZE >= Number(state.peers?.total || 0) ? "disabled" : ""}>下一页</button></nav></footer>`;
  }
  function historyHtml() {
    const peer = state.selected;
    if (!peer) return empty("选择用户或群聊查看会话");
    const items = state.sessions?.items || [];
    const matching = state.filters.kind === "group" && state.filters.channel_user_id;
    return `<header class="channel-inspector-heading"><span class="channel-avatar ${peer.kind === "group" ? "group" : ""}">${icon(peer.kind === "group" ? "users-round" : "user-round")}</span><div class="channel-inspector-identity"><strong>${e(channelPeerName(peer))}</strong><span title="${e(peer.peer_id)}">${e(peer.peer_id)}</span></div></header><div class="channel-history-heading"><h2>${matching ? "匹配提问" : "历史会话"}</h2><span>${count(state.sessions?.total ?? (matching ? peer.question_count : peer.session_count))}</span></div>${state.historyError ? `<div class="diagnostics-notice" data-kind="error">${e(state.historyError)}<button type="button" class="diagnostics-button text" data-channel-action="retry-history">重试</button></div>` : ""}<div class="channel-session-list">${items.map(channelSessionHtml).join("")}${!items.length ? empty(state.historyLoading ? "正在加载会话…" : "当前范围暂无会话") : ""}${items.length < Number(state.sessions?.total || 0) ? `<button type="button" class="diagnostics-button text channel-history-more" data-channel-action="more-history" ${state.historyLoading ? "disabled" : ""}>${state.historyLoading ? "正在加载…" : matching ? "加载更多提问" : "加载更多会话"}</button>` : ""}</div>`;
  }
  function render() {
    if (!state.active) return;
    const f = state.filters;
    disposeResizer?.(); disposePicker?.();
    root.innerHTML = `<div class="diagnostics-page diagnostic-sessions-page diagnostic-channels-page"><header class="diagnostics-head"><div class="diagnostic-page-heading"><div class="channel-page-title"><button type="button" class="diagnostics-button text" data-channel-action="back">${icon("arrow-left")}返回平台会话</button><h1>渠道详情</h1></div><p>按渠道账户查看用户、群聊与使用情况。</p></div><div class="diagnostic-head-actions"><form class="diagnostic-session-filters" data-channel-form="period"><label class="diagnostics-field diagnostic-control-with-icon">${icon("clock-3")}${selectHtml("period", "时间范围", f.period, PERIODS)}</label><label class="diagnostics-field diagnostic-control-with-icon channel-account-select">${logo()}${selectHtml("account_id", "渠道账户", f.account_id, state.accounts.map(a => [a.id, a.name])).replace(/(class="console-select-option[^>]+>)/g, "$1" + logo())}</label><button type="submit" class="btn diagnostics-button primary" ${state.loading ? "disabled" : ""}>${icon("search")}查询</button><button type="button" class="btn diagnostics-button text" data-channel-action="reset">重置</button></form></div></header><div class="channel-content">${state.error ? `<div class="diagnostics-notice" data-kind="error">${e(state.error)}</div>` : ""}<div class="channel-top"><div class="channel-summary-stack">${accountHtml()}${metricsHtml()}</div><section class="channel-analytics-card" aria-label="活跃用户排行">${rankingHtml()}</section></div>${toolbarHtml()}<div class="channel-workbench">${tableHtml()}<div class="channel-resizer" role="separator" aria-label="调整会话详情宽度" aria-orientation="vertical" tabindex="0"></div><aside class="channel-session-inspector" aria-label="关联会话">${historyHtml()}</aside></div>${paginationHtml()}</div><span class="channel-read-status" role="status" aria-live="polite">${state.loading || state.peersLoading ? "正在加载…" : ""}</span></div>`;
    selectBinding?.dispose();
    selectBinding = bindDiagnosticSelects(root, (name, value) => {
      if (name === "account_id") state.filters = { ...state.filters, account_id: value, peer_id: undefined, channel_user_id: undefined, channel_group_id: undefined, filter_peer_id: undefined, query: "", offset: 0 };
      else state.filters = { ...state.filters, period: value, start: undefined, end: undefined, offset: 0 };
      void loadAccount();
    });
    disposeResizer = bindChannelResizer(root.querySelector(".channel-workbench"), inspectorWidth, width => { inspectorWidth = width; });
    const pickers = ["user", ...(f.kind === "group" ? ["group"] : [])].map(kind => bindPeerPicker(root, {
      kind, selectedId: f[kind === "user" ? "channel_user_id" : "channel_group_id"],
      initialQuery: state.pickerQueries[kind], onQuery: query => { state.pickerQueries[kind] = query; },
      fetchPage: (query, offset, signal) => diagnosticRequest(`${base()}/peers?${channelQuery(f, {kind, query, offset, limit:25, options_only:true, include_group_senders:kind === "user", sender_id:kind === "group" ? f.channel_user_id : undefined})}`, signal),
      onSelect: peer => selectFilter(kind, peer),
    }));
    disposePicker = () => pickers.forEach(dispose => dispose());
    icons();
  }
  function renderHistory(preserveScroll = false) {
    if (!state.active) return;
    const host = root.querySelector(".channel-session-inspector");
    if (host) { const scroll = preserveScroll ? host.querySelector(".channel-session-list")?.scrollTop || 0 : 0; host.innerHTML = historyHtml(); const list = host.querySelector(".channel-session-list"); if (list) list.scrollTop = scroll; icons(); }
  }
  async function loadHistory(more = false) {
    if (!state.selected) return;
    const scope = historyReader.begin(), offset = more ? state.sessions?.items.length || 0 : 0;
    state.historyLoading = true; state.historyError = ""; if (!more) state.sessions = null; renderHistory(more);
    try {
      const data = await scope.get(`${base()}/sessions?${channelQuery(state.filters, { kind: state.filters.kind, peer_id: state.selected.peer_id, sender_id: state.filters.kind === "group" ? state.filters.channel_user_id : undefined, limit: 20, offset })}`);
      if (!scope.current() || !state.active) return;
      state.sessions = { ...data, items: more ? [...(state.sessions?.items || []), ...data.items] : data.items };
    } catch (error) { if (scope.current() && state.active) state.historyError = sessionQueryError(error); }
    finally { if (scope.current() && state.active) { state.historyLoading = false; renderHistory(more); } }
  }
  async function loadPeers() {
    if (!account()) return;
    const scope = peersReader.begin(); historyReader.cancel(); state.historyLoading = false; state.sessions = null; state.selected = null; state.historyError = ""; state.peersLoading = true; state.peers = null; publish(); render();
    try {
      const data = await scope.get(`${base()}/peers?${channelQuery(state.filters, { kind: state.filters.kind, query: state.filters.query, peer_id: state.filters.kind === "group" ? state.filters.channel_group_id : state.filters.channel_user_id, sender_id: state.filters.kind === "group" ? state.filters.channel_user_id : undefined, limit: PAGE_SIZE, offset: state.filters.offset || 0 })}`);
      if (!scope.current() || !state.active) return;
      state.peers = data;
      const filterId = state.filters.kind === "group" ? state.filters.channel_group_id : state.filters.channel_user_id;
      const named = data.items.find(item => item.peer_id === filterId);
      if (named) state[state.filters.kind === "group" ? "groupChoice" : "userChoice"] = named;
      state.selected = data.items.find(item => item.peer_id === state.filters.peer_id) || data.items[0] || null; state.filters.peer_id = state.selected?.peer_id; publish();
    } catch (error) { if (scope.current() && state.active) state.error = sessionQueryError(error); }
    finally { if (scope.current() && state.active) { state.peersLoading = false; render(); if (state.selected) void loadHistory(); } }
  }
  async function loadAccount() {
    overviewReader.cancel(); peersReader.cancel(); historyReader.cancel(); state.overview = null; state.peers = null; state.sessions = null; state.selected = null; state.error = "";
    try { state.filters = sessionRange(state.filters); } catch (error) { state.error = error.message; render(); return; }
    if (!account()) { state.loading = false; render(); return; }
    const scope = overviewReader.begin(); state.loading = true; publish(); render();
    const peers = loadPeers();
    try {
      const data = await scope.get(`${base()}/overview?${channelQuery(state.filters)}`);
      if (!scope.current() || !state.active) return;
      state.overview = data;
    } catch (error) { if (scope.current() && state.active) state.error = sessionQueryError(error); }
    finally { if (scope.current() && state.active) { state.loading = false; render(); } }
    await peers;
  }
  async function loadAccounts() {
    const scope = accountsReader.begin(); state.loading = true; state.error = ""; render();
    try {
      const data = await scope.get("api/v1/diagnostics/channels/accounts");
      if (!scope.current() || !state.active) return;
      state.accounts = data.items;
      if (!account()) state.filters.account_id = state.accounts[0]?.id;
      await loadAccount();
    } catch (error) { if (scope.current() && state.active) { state.loading = false; state.error = sessionQueryError(error); render(); } }
  }
  root.addEventListener("submit", event => {
    const form = event.target.closest("[data-channel-form]"); if (!form) return; event.preventDefault();
    if (form.dataset.channelForm === "search") { state.filters.query = new FormData(form).get("query").trim(); state.filters.offset = 0; state.error = ""; void loadPeers(); }
    else { state.filters.start = undefined; state.filters.end = undefined; state.filters.offset = 0; void loadAccounts(); }
  });
  root.addEventListener("click", async event => {
    const target = event.target.closest("button,[data-channel-peer]"); if (!target || !state.active) return;
    if (target.dataset.channelCopy) {
      try { await navigator.clipboard.writeText(target.dataset.channelCopy); if (target.isConnected) { target.innerHTML = icon("check"); target.classList.add("copied"); target.title = "已复制"; icons(); setTimeout(() => { if (target.isConnected) { target.innerHTML = icon("copy"); target.classList.remove("copied"); target.title = "复制会话 ID"; icons(); } }, 1500); } } catch { target.title = "复制失败，请手动复制 ID"; }
      return;
    }
    if (target.dataset.channelUser) {
      const user = state.overview?.active_users?.find(item => item.user_id === target.dataset.channelUser);
      if (user) selectFilter("user", {peer_id:user.user_id, name:user.name, kind:"user"});
      return;
    }
    if (target.dataset.channelOpen) { navigate(diagnosticHash("detail", { view: "conversation" }, target.dataset.channelOpen)); return; }
    if (target.dataset.channelAccount) { state.filters = { ...state.filters, account_id: target.dataset.channelAccount, peer_id: undefined, channel_user_id: undefined, channel_group_id: undefined, filter_peer_id: undefined, query: "", offset: 0 }; void loadAccount(); return; }
    if (target.dataset.channelKind) { state.filters = { ...state.filters, kind: target.dataset.channelKind, peer_id: undefined, query: "", offset: 0 }; state.error = ""; void loadPeers(); return; }
    if (target.dataset.channelPeer) { state.selected = state.peers.items.find(item => item.peer_id === target.dataset.channelPeer); state.filters.peer_id = state.selected?.peer_id; state.sessions = null; state.historyError = ""; publish(); render(); void loadHistory(); return; }
    switch (target.dataset.channelAction) {
      case "back": (onBack || navigate)(diagnosticHash("overview", channelEntryFilters(state.filters))); break;
      case "reset": state.filters = { period: "168", account_id: state.filters.account_id, kind: "user", offset: 0 }; void loadAccount(); break;
      case "previous": if (!state.peersLoading && Number(state.filters.offset) > 0) { state.filters.offset -= PAGE_SIZE; void loadPeers(); } break;
      case "next": if (!state.peersLoading && Number(state.filters.offset || 0) + PAGE_SIZE < state.peers?.total) { state.filters.offset = Number(state.filters.offset || 0) + PAGE_SIZE; void loadPeers(); } break;
      case "retry-history": void loadHistory(); break;
      case "more-history": if (!state.historyLoading) void loadHistory(true); break;
    }
  });
  root.addEventListener("keydown", event => { if (["Enter", " "].includes(event.key) && event.target.matches("[data-channel-peer]")) { event.preventDefault(); event.target.click(); } });
  return {
    activate(route, saved) {
      cancel(); state.active = true; state.loading = false; state.peersLoading = false; state.historyLoading = false; state.error = ""; state.historyError = "";
      state.filters = { ...route.filters, ...channelEntryFilters(route.filters), kind: route.filters.kind === "group" ? "group" : "user", offset: Math.max(0, Math.floor(Number(route.filters.offset || 0) / PAGE_SIZE) || 0) * PAGE_SIZE };
      if (state.filters.filter_peer_id) {
        const key = state.filters.kind === "group" ? "channel_group_id" : "channel_user_id";
        state.filters[key] ||= state.filters.filter_peer_id; delete state.filters.filter_peer_id;
      }
      if (saved?.accounts?.length && saved.peers && saved.overview) { Object.assign(state, saved, { active: true }); render(); for (const [selector, position] of Object.entries(saved.scroll || {})) { const host = root.querySelector(selector); if (host) host.scrollTop = position; } if (!state.sessions && state.selected) void loadHistory(); }
      else { state.accounts = []; state.overview = null; state.peers = null; state.selected = null; state.sessions = null; void loadAccounts(); }
    },
    deactivate() { state.active = false; cancel(); disposeResizer?.(); disposePicker?.(); selectBinding?.dispose(); selectBinding = null; },
    snapshot() { return { userChoice: state.userChoice, groupChoice: state.groupChoice, pickerQueries: {...state.pickerQueries}, accounts: state.accounts, filters: { ...state.filters }, overview: state.overview, peers: state.peers, sessions: state.sessions, selected: state.selected, scroll: Object.fromEntries([".channel-content", ".channel-table-scroll", ".channel-session-list", ".channel-account-options"].map(selector => [selector, root.querySelector(selector)?.scrollTop || 0])) }; },
    clear() { state.active = false; cancel(); disposeResizer?.(); disposePicker?.(); selectBinding?.dispose(); selectBinding = null; state.accounts = []; state.filters = {}; state.peers = null; state.sessions = null; state.overview = null; state.selected = null; state.userChoice = null; state.groupChoice = null; state.pickerQueries = { user: "", group: "" }; inspectorWidth = 0; root.replaceChildren(); },
  };
}
