import { contextOccupancyPresentation, tokenValue } from "./context-budget-presentation.js?v=20260923-c02";
import { renderUserIdentity } from "./user-identity.js?v=20260917-release01010";
import { composerProviderIcon } from "./provider-icons.js?v=20260929-model-provider";
// Pure shared inspector markup. Hosts supply identity, historical data and tab state.
const escapeHtml = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

export function formatTokens(value) {
  if (value === null || value === undefined || value === "") return "—";
  return Number.isFinite(Number(value)) ? Number(value).toLocaleString("zh-CN") : "—";
}

export function contextBudgetPercent(value, total) {
  const amount = tokenValue(value);
  const capacity = tokenValue(total);
  if (amount == null || capacity == null || capacity <= 0) return null;
  return Math.max(0, Math.min(100, (amount / capacity) * 100));
}

export function renderContextBudgetOverview(contextStatus = {}) {
  const total = tokenValue(contextStatus.context_window_tokens);
  const occupancy = contextOccupancyPresentation(contextStatus);
  const output = tokenValue(contextStatus.predicted_output_tokens);
  const safety = tokenValue(contextStatus.safety_margin_tokens);
  const complete = total != null && occupancy.tokens != null && output != null && safety != null;
  const free = complete ? Math.max(total - occupancy.tokens - output - safety, 0) : null;
  const segments = [
    ["history", occupancy.tokens, occupancy.label],
    ["output", output, "输出预留"],
    ["safety", safety, "安全余量"],
    ["free", free, "剩余"],
  ];
  let availableWidth = 100;
  const segmentHtml = segments.map(([tone, value, label]) => {
    // Clamp only painted widths, never the original values in labels/tooltips.
    const width = complete ? Math.min(availableWidth, contextBudgetPercent(value, total) || 0) : 0;
    availableWidth -= width;
    return `<span class="context-budget-segment tone-${tone}" style="width:${width.toFixed(2)}%" title="${escapeHtml(label)} ${escapeHtml(formatTokens(value))} Token"></span>`;
  }).join("");
  const markers = [
    ["trigger", contextStatus.trigger_tokens, "触发线"],
  ].map(([tone, value, label]) => {
    const position = contextBudgetPercent(value, total);
    if (position === null) return "";
    return `<span class="context-budget-marker tone-${tone}" style="left:${position.toFixed(2)}%"><span>${escapeHtml(label)}</span></span>`;
  }).join("");
  return `<div class="context-budget-overview">
    <div class="context-budget-summary">
      <span title="${escapeHtml(occupancy.detail)}"><small>${occupancy.label}</small><strong>${escapeHtml(formatTokens(occupancy.tokens))}</strong></span>
      <span title="可用输入 ${escapeHtml(formatTokens(contextStatus.usable_request_tokens))} Token"><small>剩余预算</small><strong>${escapeHtml(formatTokens(free))}</strong></span>
      <span><small>上下文窗口</small><strong>${escapeHtml(formatTokens(contextStatus.context_window_tokens))}</strong></span>
    </div>
    <div class="context-budget-composite" role="img" aria-label="上下文预算分段与压缩阈值">
      <div class="context-budget-segments">${segmentHtml}</div>
      ${markers}
    </div>
    <div class="context-budget-legend">
      <span class="tone-history" title="${escapeHtml(occupancy.detail)}">${occupancy.label}</span><span class="tone-output">输出预留</span><span class="tone-safety">安全余量</span><span class="tone-free">剩余</span>
    </div>
  </div>`;
}

export function inspectorFact(label, value, options = {}) {
  const classes = [options.mono ? "mono" : "", options.wide ? "wide" : ""].filter(Boolean).join(" ");
  const text = escapeHtml(value ?? "—");
  const displayText = escapeHtml(options.displayValue ?? value ?? "—");
  const copy = options.copy && value ? `<button type="button" class="inspector-copy-btn" data-inspector-copy="${text}" title="复制 ${escapeHtml(options.copyLabel || label)}" aria-label="复制 ${escapeHtml(options.copyLabel || label)}"><i data-lucide="copy" aria-hidden="true"></i></button>` : "";
  return `<div class="${classes}"><span>${escapeHtml(label)}</span><strong${copy ? ' class="inspector-copy-value"' : ""}>${copy ? `<span class="inspector-id-text" title="${text}">${displayText}</span>${copy}` : text}</strong></div>`;
}

export function inspectorFactHtml(label, valueHtml, options = {}) {
  const classes = [options.mono ? "mono" : "", options.wide ? "wide" : ""].filter(Boolean).join(" ");
  return `<div class="${classes}"><span>${escapeHtml(label)}</span><strong>${valueHtml}</strong></div>`;
}

export function renderRunUsageOverview(run = {}, usage = {}) {
  const reported = [run.input_tokens ?? usage.input_tokens, run.output_tokens ?? usage.output_tokens, run.total_tokens ?? usage.total_tokens];
  const hasUsage = reported.some(value => (tokenValue(value) ?? 0) > 0);
  const input = hasUsage ? tokenValue(reported[0]) : null;
  const output = hasUsage ? tokenValue(reported[1]) : null;
  const total = (hasUsage ? tokenValue(reported[2]) : null)
    ?? (input != null && output != null ? input + output : null);
  const partial = run.usage_partial === true || usage.usage_partial === true;
  const usageTitle = partial
    ? "仅累计 Provider 已报告的 Usage，部分响应缺失；不代表完整消耗或当前上下文占用"
    : "本轮各模型响应的 Provider Usage 累计消耗，不代表当前上下文占用";
  // This bar is the Run's cumulative input/output composition, not a model window.
  const capacity = input != null && output != null ? input + output : null;
  const inputWidth = contextBudgetPercent(input, capacity) || 0;
  const outputWidth = contextBudgetPercent(output, capacity) || 0;
  return `<div class="run-usage-overview">
    <div class="run-usage-summary">
      <span title="${usageTitle}"><small>${partial ? "累计使用（部分）" : "累计使用"}</small><strong>${escapeHtml(formatTokens(total))}</strong></span>
      <span><small>输入 / 输出</small><strong>${escapeHtml(formatTokens(input))} / ${escapeHtml(formatTokens(output))}</strong></span>
    </div>
    <div class="run-usage-track" role="img" aria-label="本轮累计输入与输出 Token 构成">
      <span class="input" style="width:${inputWidth.toFixed(2)}%" title="输入 ${escapeHtml(formatTokens(input))} Token"></span><span class="output" style="width:${outputWidth.toFixed(2)}%" title="输出 ${escapeHtml(formatTokens(output))} Token"></span>
    </div>
    <div class="run-usage-legend"><span class="input">输入</span><span class="output">输出</span></div>
  </div>`;
}

export function formatCacheHitRatio(summary = {}) {
  const ratio = summary.read_complete === true && typeof summary.hit_ratio === "number"
    && Number.isFinite(summary.hit_ratio) && summary.hit_ratio >= 0 && summary.hit_ratio <= 1
    ? summary.hit_ratio : null;
  return ratio == null ? "—" : `${(ratio * 100).toFixed(1)}%`;
}

function cacheFact(label, value, complete, scope, stacked = false) {
  const amount = tokenValue(value);
  const partial = amount != null && complete !== true;
  const title = amount == null ? "供应商未报告或旧记录无法确认"
    : partial ? `${scope}仅累计已报告数据，部分请求缺失` : `${scope}供应商报告的 Token`;
  const text = `${escapeHtml(formatTokens(amount))}${partial ? "（部分）" : ""}`;
  if (stacked) return `<span title="${escapeHtml(title)}"><small>${label}</small><strong>${text}</strong></span>`;
  return inspectorFactHtml(label, `<span title="${escapeHtml(title)}">${text}</span>`);
}

export function renderCacheFacts(summary = {}, { scope = "会话累计", ratioLabel = "累计命中率", stacked = false } = {}) {
  const title = summary.response_count > 0 && summary.read_complete !== true
    ? "缓存读取数据不完整，无法计算完整命中率"
    : `${scope}缓存读取 Token ÷ 输入 Token；缓存读取已包含在输入中`;
  if (stacked) return `<span title="${escapeHtml(title)}"><small>${ratioLabel}</small><strong>${formatCacheHitRatio(summary)}</strong></span>
    ${cacheFact("缓存读取", summary.cache_read_tokens, summary.read_complete, scope, true)}
    ${cacheFact("缓存写入", summary.cache_write_tokens, summary.write_complete, scope, true)}`;
  return `${inspectorFactHtml(ratioLabel, `<span title="${escapeHtml(title)}">${formatCacheHitRatio(summary)}</span>`)}
    ${cacheFact("缓存读取", summary.cache_read_tokens, summary.read_complete, scope)}
    ${cacheFact("缓存写入", summary.cache_write_tokens, summary.write_complete, scope)}`;
}

export function renderSessionInspector({
  state = {}, activeSession = {}, model = null, modelProvider = null, context = {}, identity = {}, contextStatus = {},
  usage = {}, run = {}, sessionCache = {}, runCache = {}, runModel = null, agentName = "—", channelOrigin = null, agentChannels = [],
  activeTab = "session", contextContent = "", statusText = "", statusClass = "idle",
} = {}) {
  // Every Run emits telemetry, including no-op estimates. Only a successful
  // triggered compaction may replace the last persisted comparison.
  const currentCompaction = contextStatus.last_compaction;
  const compaction = currentCompaction?.triggered === true
    && currentCompaction.summary_applied === true && !currentCompaction.summary_failed
    && !currentCompaction.failure && currentCompaction.compaction_event_saved === true
    ? currentCompaction : {};
  const strategyLabels = {
    none: "未触发",
    tiered_deterministic: "确定性压缩",
    tiered_summary: "上下文摘要",
    sliding_window_fallback: "滑动窗口兜底",
  };
  const compactionState = contextStatus.compaction_event_id
    ? "已完成"
    : compaction.triggered
    ? strategyLabels[compaction.strategy] || compaction.strategy
    : contextStatus.compaction_enabled
      ? "未触发"
      : "未启用";
  const compactionScope = compaction.estimated_tokens_before != null
    ? (compaction.estimate_scope || contextStatus.estimate_scope)
    : contextStatus.compaction_estimate_scope;
  const compactionHint = compactionScope === "full_model_request"
    ? "压缩前后均为同次完整请求的预计占用"
    : "压缩前后为历史内容估算，不含完整请求参数";
  const compactionContent = `<section class="inspector-section">
    <header><strong>上下文压缩</strong><span>${escapeHtml(compactionState)}</span></header>
    <div class="compaction-comparison" title="${compactionHint}">
      <span><small>压缩前</small><strong>${escapeHtml(formatTokens(compaction.estimated_tokens_before ?? contextStatus.compaction_estimated_tokens_before))}</strong></span>
      <i data-lucide="arrow-right"></i>
      <span><small>压缩后</small><strong>${escapeHtml(formatTokens(compaction.estimated_tokens_after ?? contextStatus.compaction_estimated_tokens_after ?? contextStatus.compaction_estimated_tokens))}</strong></span>
    </div>
    <div class="inspector-facts compact">
      ${inspectorFact("压缩事件", contextStatus.compaction_event_id || "暂无", { mono: true, wide: true })}
      ${inspectorFact("来源截止", contextStatus.compaction_source_through_sequence ?? "—")}
      ${inspectorFact("摘要模型", contextStatus.summary_model_id || "—")}
      ${inspectorFact("更新时间", contextStatus.compaction_updated_at || "—")}
    </div>
  </section>`;
  const savedCount = (value) => value == null ? "未记录" : String(value);
  const sessionCounts = `<span class="inspector-inline-metrics">
    <span title="提问数"><i data-lucide="message-circle-more"></i>${escapeHtml(savedCount(activeSession.turnCount))}</span>
    <span title="消息事件数"><i data-lucide="repeat-2"></i>${escapeHtml(savedCount(activeSession.messageCount))}</span>
    <span title="附件数"><i data-lucide="paperclip"></i>${escapeHtml(savedCount(activeSession.attachmentCount))}</span>
  </span>`;
  const sessionScope = `<span class="inspector-scope-value"><code>${escapeHtml(activeSession.appId || "—")}</code><span>/</span><code>${escapeHtml(activeSession.spaceId || "—")}</code></span>`;
  const hasChannelTab = Boolean(channelOrigin || agentChannels.length);
  if (!hasChannelTab && activeTab === "channel") activeTab = "session";
  const channelSource = channelOrigin ? `<section class="inspector-section">
    <div class="inspector-facts compact">
      ${inspectorFactHtml("渠道", `<span class="inspector-channel-name"><img class="session-source-logo" src="${channelOrigin.logo}" alt="" />${escapeHtml(channelOrigin.platform)}</span>`)}
      ${inspectorFact("来源 ID", channelOrigin.sourceId, { mono: true, wide: true, copy: true })}
      ${inspectorFactHtml("渠道名称", `<span class="inspector-channel-name">${escapeHtml(channelOrigin.accountName)}</span>`, { wide: true })}
      ${inspectorFact("会话对象", channelOrigin.displayName || "未记录", { wide: true })}
      ${inspectorFact(channelOrigin.conversationType, channelOrigin.peerId, { mono: true, wide: true, copy: true, copyLabel: channelOrigin.peerKey })}
      ${channelOrigin.peerKey === "User ID" && channelOrigin.chatId ? inspectorFact("Chat ID", channelOrigin.chatId, { mono: true, wide: true, copy: true }) : ""}
      ${inspectorFact("会话 ID", state.sessionId, { mono: true, wide: true, copy: true, displayValue: state.sessionId?.slice(0, 8) })}
    </div></section>` : `<section class="inspector-section">
      <p class="inspector-channel-note">${state.sessionId ? "当前会话不是由渠道发起的。" : "尚未选择渠道会话。"}</p>
      <header><strong>Agent 关联渠道</strong></header>
      <div class="inspector-facts compact">${agentChannels.map((account) => inspectorFactHtml("渠道名称", `<span class="inspector-channel-name">${escapeHtml(account.name)}<span class="inspector-channel-badge">${escapeHtml(account.channel_type === "wecom" ? "企微" : account.channel_type === "feishu" ? "飞书" : account.channel_type === "dingtalk" ? "钉钉" : account.channel_type)}</span></span>`)).join("")}</div>
    </section>`;
  const sessionContent = `<section class="inspector-section inspector-session-group">
    <div class="inspector-facts">
      ${inspectorFact("会话 ID", state.sessionId || "尚未创建", { mono: true, wide: true, copy: Boolean(state.sessionId), displayValue: state.sessionId?.slice(0, 8) })}
      ${inspectorFact("Agent", agentName, { wide: true })}
      ${inspectorFactHtml("模型", `<span class="inspector-channel-name">${modelProvider ? `<span class="composer-provider-icon">${composerProviderIcon(modelProvider)}</span>` : ""}${escapeHtml(model?.name || model?.model || "未记录")}</span>`)}
      ${inspectorFactHtml("会话来源", channelOrigin ? `<span class="inspector-channel-name"><img class="session-source-logo" src="${channelOrigin.logo}" alt="" />${escapeHtml(channelOrigin.platform)}</span>` : `<span class="inspector-channel-name"><img class="session-source-logo" src="./favicon.svg?v=20260922-d-rounded" alt="" />DeepFOS</span>`)}
      ${inspectorFactHtml("App / Space", sessionScope, { wide: true })}
      ${inspectorFactHtml("会话统计", sessionCounts)}
      ${inspectorFact("Language", context.language || "未记录")}
      <div class="wide inspector-user-fact"><span>用户</span>${renderUserIdentity(identity?.profile || {}, {userId: identity?.user || "", copyable: true})}</div>
    </div>
  </section>
  <section class="inspector-section inspector-cache-section">
    <header title="当前会话各模型响应的缓存统计"><strong>缓存统计</strong></header>
    <div class="context-budget-summary">${renderCacheFacts(sessionCache, { stacked: true })}</div>
  </section>
  <section class="inspector-section inspector-budget-section">
    <header title="${escapeHtml(contextOccupancyPresentation(contextStatus).detail)}"><strong>上下文预算</strong></header>
    ${renderContextBudgetOverview(contextStatus)}
  </section>
  ${compactionContent}`;

  const runContent = `<section class="inspector-section">
    ${renderRunUsageOverview(run, usage)}
    <div class="inspector-facts compact run-facts">
      ${inspectorFact("终态", run.status || "—")}
      ${inspectorFact("请求", run.requests ?? usage.requests ?? "—")}
      ${inspectorFact("工具调用", run.tool_calls ?? usage.tool_calls ?? "—")}
      ${inspectorFact("Tool Search", run.tool_search_calls ?? "—")}
      ${renderCacheFacts(runCache.response_count > 0 ? runCache : {}, { scope: "本轮累计", ratioLabel: "本轮命中率" })}
      ${inspectorFact("耗时", run.duration_ms != null && Number.isFinite(Number(run.duration_ms)) ? `${Number(run.duration_ms).toLocaleString("zh-CN")} ms` : "—")}
      ${inspectorFact("压缩", run.compaction || "—")}
      ${inspectorFact("Conv. ID", run.conversation_id || "—", { mono: true, wide: true })}
      ${inspectorFact("Run ID", run.run_id || "—", { mono: true, wide: true, copy: Boolean(run.run_id) })}
      ${inspectorFact("模型", runModel?.name || runModel?.model || run.model_id || "未记录", { wide: true })}
    </div>
  </section>`;

  const tabContent = activeTab === "run"
    ? runContent
    : activeTab === "channel"
      ? channelSource
    : activeTab === "context"
      ? contextContent
      : sessionContent;
  return `<nav class="inspector-tabs" role="tablist" aria-label="会话详情">
      <span class="inspector-tab-buttons">
        <button type="button" role="tab" data-inspector-tab="session" aria-selected="${activeTab === "session"}">概览</button>
        ${hasChannelTab ? `<button type="button" role="tab" data-inspector-tab="channel" aria-selected="${activeTab === "channel"}">渠道</button>` : ""}
        <button type="button" role="tab" data-inspector-tab="run" aria-selected="${activeTab === "run"}">当前</button>
        <button type="button" role="tab" data-inspector-tab="context" aria-selected="${activeTab === "context"}">上下文</button>
      </span>
      <span class="status-badge inspector-tab-status ${statusClass}">${statusText}</span>
    </nav>
    <div class="inspector-tab-panel">${tabContent}</div>`;

}
