// Pure shared inspector markup. Hosts supply identity, historical data and tab state.
const escapeHtml = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

export function formatTokens(value) {
  if (value === null || value === undefined || value === "") return "—";
  return Number.isFinite(Number(value)) ? Number(value).toLocaleString("zh-CN") : "—";
}

export function contextBudgetPercent(value, total) {
  const amount = Number(value);
  const capacity = Number(total);
  if (!Number.isFinite(amount) || !Number.isFinite(capacity) || capacity <= 0) return null;
  return Math.max(0, Math.min(100, (amount / capacity) * 100));
}

export function finiteTokenValue(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : 0;
}

export function renderContextBudgetOverview(contextStatus) {
  const total = finiteTokenValue(contextStatus.context_window_tokens);
  const history = finiteTokenValue(contextStatus.estimated_history_tokens);
  const output = finiteTokenValue(contextStatus.predicted_output_tokens);
  const safety = finiteTokenValue(contextStatus.safety_margin_tokens);
  const occupied = Math.min(total, history);
  const outputWidth = Math.min(Math.max(total - occupied, 0), output);
  const safetyWidth = Math.min(Math.max(total - occupied - outputWidth, 0), safety);
  const free = Math.max(total - occupied - outputWidth - safetyWidth, 0);
  const segments = [
    ["history", occupied, "历史"],
    ["output", outputWidth, "输出预留"],
    ["safety", safetyWidth, "安全余量"],
    ["free", free, "剩余"],
  ];
  const segmentHtml = segments.map(([tone, value, label]) => {
    const width = contextBudgetPercent(value, total) || 0;
    return `<span class="context-budget-segment tone-${tone}" style="width:${width.toFixed(2)}%" title="${escapeHtml(label)} ${escapeHtml(formatTokens(value))} Token"></span>`;
  }).join("");
  const markers = [
    ["target", contextStatus.target_tokens, "目标线"],
    ["trigger", contextStatus.trigger_tokens, "触发线"],
  ].map(([tone, value, label]) => {
    const position = contextBudgetPercent(value, total);
    if (position === null) return "";
    return `<span class="context-budget-marker tone-${tone}" style="left:${position.toFixed(2)}%"><span>${escapeHtml(label)}</span></span>`;
  }).join("");
  return `<div class="context-budget-overview">
    <div class="context-budget-summary">
      <span><small>历史占用</small><strong>${escapeHtml(formatTokens(contextStatus.estimated_history_tokens))}</strong></span>
      <span><small>可用输入</small><strong>${escapeHtml(formatTokens(contextStatus.usable_request_tokens))}</strong></span>
      <span><small>上下文窗口</small><strong>${escapeHtml(formatTokens(contextStatus.context_window_tokens))}</strong></span>
    </div>
    <div class="context-budget-composite" role="img" aria-label="上下文预算分段与压缩阈值">
      <div class="context-budget-segments">${segmentHtml}</div>
      ${markers}
    </div>
    <div class="context-budget-legend">
      <span class="tone-history">历史</span><span class="tone-output">输出预留</span><span class="tone-safety">安全余量</span><span class="tone-free">剩余</span>
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

export function renderRunUsageOverview(run, usage, contextStatus) {
  const input = finiteTokenValue(run.input_tokens ?? usage.input_tokens);
  const output = finiteTokenValue(run.output_tokens ?? usage.output_tokens);
  const total = Math.max(finiteTokenValue(run.total_tokens ?? usage.total_tokens), input + output);
  const contextWindow = finiteTokenValue(contextStatus?.context_window_tokens);
  const capacity = Math.max(contextWindow, total);
  const remaining = contextWindow > 0 ? Math.max(capacity - total, 0) : 0;
  const inputWidth = contextBudgetPercent(input, capacity) || 0;
  const outputWidth = contextBudgetPercent(output, capacity) || 0;
  const remainingWidth = contextBudgetPercent(remaining, capacity) || 0;
  return `<div class="run-usage-overview">
    <div class="run-usage-summary">
      <span><small>本次使用</small><strong>${escapeHtml(formatTokens(run.total_tokens ?? usage.total_tokens))}</strong></span>
      <span><small>预计剩余</small><strong>${escapeHtml(formatTokens(contextWindow > 0 ? remaining : null))}</strong></span>
    </div>
    <div class="run-usage-track" role="img" aria-label="本轮输入、输出与上下文剩余 Token">
      <span class="input" style="width:${inputWidth.toFixed(2)}%" title="输入 ${escapeHtml(formatTokens(input))} Token"></span><span class="output" style="width:${outputWidth.toFixed(2)}%" title="输出 ${escapeHtml(formatTokens(output))} Token"></span><span class="remaining" style="width:${remainingWidth.toFixed(2)}%" title="上下文剩余 ${escapeHtml(formatTokens(remaining))} Token"></span>
    </div>
    <div class="run-usage-legend"><span class="input">输入</span><span class="output">输出</span><span class="remaining">上下文剩余</span></div>
  </div>`;
}

export function renderSessionInspector({
  state = {}, activeSession = {}, model = null, context = {}, identity = {}, contextStatus = {},
  usage = {}, run = {}, runModel = null, agentName = "—", channelOrigin = null, agentChannels = [],
  activeTab = "session", contextContent = "", statusText = "", statusClass = "idle", diagnostic = false,
} = {}) {
  const compaction = contextStatus.last_compaction || {};
  const strategyLabels = {
    none: "未触发",
    tiered_deterministic: "确定性压缩",
    tiered_summary: "分层摘要",
    sliding_window_fallback: "滑动窗口兜底",
  };
  const compactionState = contextStatus.compaction_event_id
    ? "已完成"
    : compaction.triggered
    ? strategyLabels[compaction.strategy] || compaction.strategy
    : contextStatus.compaction_enabled
      ? "未触发"
      : "未启用";
  const compactionContent = `<section class="inspector-section">
    <header><strong>上下文压缩</strong><span>${escapeHtml(compactionState)}</span></header>
    <div class="compaction-comparison">
      <span><small>压缩前</small><strong>${escapeHtml(formatTokens(compaction.estimated_tokens_before))}</strong></span>
      <i data-lucide="arrow-right"></i>
      <span><small>压缩后</small><strong>${escapeHtml(formatTokens(compaction.estimated_tokens_after ?? contextStatus.compaction_estimated_tokens))}</strong></span>
    </div>
    <div class="inspector-facts compact">
      ${inspectorFact("压缩事件", contextStatus.compaction_event_id || "暂无", { mono: true, wide: true })}
      ${inspectorFact("来源截止", contextStatus.compaction_source_through_sequence ?? "—")}
      ${inspectorFact("摘要模型", contextStatus.summary_model_id || "—")}
      ${inspectorFact("更新时间", contextStatus.compaction_updated_at || "—")}
    </div>
  </section>`;
  const diagnosticCount = (value) => value == null ? "未记录" : String(value);
  const sessionCounts = `<span class="inspector-inline-metrics">
    <span title="提问数"><i data-lucide="message-circle-more"></i>${escapeHtml(diagnostic ? diagnosticCount(activeSession.turnCount) : String(activeSession.turnCount || 0))}</span>
    <span title="消息事件数"><i data-lucide="repeat-2"></i>${escapeHtml(diagnostic ? diagnosticCount(activeSession.messageCount) : String(activeSession.messageCount ?? contextStatus.raw_message_count ?? state.modelMessageCount ?? 0))}</span>
    <span title="附件数"><i data-lucide="paperclip"></i>${escapeHtml(diagnostic ? diagnosticCount(activeSession.attachmentCount) : String(activeSession.attachmentCount || 0))}</span>
  </span>`;
  const sessionScope = `<span class="inspector-scope-value"><code>${escapeHtml(activeSession.appId || "—")}</code><span>/</span><code>${escapeHtml(activeSession.spaceId || "—")}</code></span>`;
  const hasChannelTab = Boolean(channelOrigin || agentChannels.length);
  if (!hasChannelTab && activeTab === "channel") activeTab = "session";
  const channelSource = channelOrigin ? `<section class="inspector-section">
    <div class="inspector-facts compact">
      ${inspectorFactHtml("渠道名称", `<span class="inspector-channel-name">${escapeHtml(channelOrigin.accountName)}<span class="inspector-channel-badge">${escapeHtml(channelOrigin.platform)}</span></span>`, { wide: true })}
      ${channelOrigin.displayName ? inspectorFact("会话对象", channelOrigin.peerName, { wide: true }) : ""}
      ${inspectorFact(channelOrigin.conversationType, channelOrigin.peerId, { mono: true, wide: true, copy: true, copyLabel: channelOrigin.peerKey })}
      ${channelOrigin.peerKey === "User ID" && channelOrigin.chatId ? inspectorFact("Chat ID", channelOrigin.chatId, { mono: true, wide: true, copy: true }) : ""}
      ${inspectorFact("会话 ID", state.sessionId, { mono: true, wide: true, copy: true, displayValue: state.sessionId?.slice(0, 8) })}
    </div></section>` : `<section class="inspector-section">
      <p class="inspector-channel-note">${state.sessionId ? "当前会话不是由渠道发起的。" : "尚未选择渠道会话。"}</p>
      <header><strong>Agent 关联渠道</strong></header>
      <div class="inspector-facts compact">${agentChannels.map((account) => inspectorFactHtml("渠道名称", `<span class="inspector-channel-name">${escapeHtml(account.name)}<span class="inspector-channel-badge">${escapeHtml(account.channel_type === "wecom" ? "企微" : account.channel_type)}</span></span>`)).join("")}</div>
    </section>`;
  const sessionContent = `<section class="inspector-section inspector-session-group">
    <div class="inspector-facts">
      ${inspectorFact("会话 ID", state.sessionId || "尚未创建", { mono: true, wide: true, copy: Boolean(state.sessionId), displayValue: state.sessionId?.slice(0, 8) })}
      ${inspectorFact("Agent", agentName, { wide: true })}
      ${inspectorFact("模型", model?.name || model?.model || (diagnostic ? "未记录" : "—"))}
      ${inspectorFactHtml("App / Space", sessionScope, { wide: true })}
      ${inspectorFactHtml("会话统计", sessionCounts)}
      ${diagnostic ? inspectorFact("Language", context.language || "未记录") + inspectorFact("User ID", identity?.user || "未认证", { mono: true, wide: true }) : ""}
    </div>
  </section>
  <section class="inspector-section inspector-budget-section">
    <header${diagnostic ? ` title="${escapeHtml(contextStatus.unavailable_reason || "按当前配置估算，非历史快照")}"` : ""}><strong>上下文预算</strong></header>
    ${renderContextBudgetOverview(contextStatus)}
  </section>
  ${compactionContent}`;

  const runContent = `<section class="inspector-section">
    ${renderRunUsageOverview(run, usage, contextStatus)}
    <div class="inspector-facts compact run-facts">
      ${inspectorFact("终态", run.status || "—")}
      ${inspectorFact("请求", run.requests ?? usage.requests ?? "—")}
      ${inspectorFact("工具调用", run.tool_calls ?? usage.tool_calls ?? "—")}
      ${inspectorFact("Tool Search", run.tool_search_calls ?? "—")}
      ${inspectorFact("缓存读取", formatTokens(run.cache_read_tokens ?? (diagnostic ? usage.cache_read_tokens : null)))}
      ${inspectorFact("缓存写入", formatTokens(run.cache_write_tokens ?? (diagnostic ? usage.cache_write_tokens : null)))}
      ${inspectorFact("耗时", run.duration_ms != null && Number.isFinite(Number(run.duration_ms)) ? `${Number(run.duration_ms).toLocaleString("zh-CN")} ms` : "—")}
      ${inspectorFact("压缩", run.compaction || "—")}
      ${inspectorFact("Conv. ID", (diagnostic ? run.conversation_id : state.conversationId || run.conversation_id) || "—", { mono: true, wide: true })}
      ${inspectorFact("Run ID", run.run_id || "—", { mono: true, wide: true, copy: Boolean(run.run_id) })}
      ${inspectorFact("模型", runModel?.name || runModel?.model || (!diagnostic && (model?.name || model?.model)) || run.model_id || (diagnostic ? "未记录" : "—"), { wide: true })}
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
