const WORKSPACE_ASSET_VERSION = "20260815-2";

let initialized = false;
let lastRenderKey = "";
let contextTestCatalog = null;
let contextTestCatalogAgentId = "";
let contextTestRequestAgentId = "";
let selectedContextReference = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function fetchLegacyPage() {
  const url = new URL("./workspace-template.html", import.meta.url);
  url.searchParams.set("v", WORKSPACE_ASSET_VERSION);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`无法加载工作台结构：HTTP ${response.status}`);
  return new DOMParser().parseFromString(await response.text(), "text/html");
}

function installLegacyDom(source) {
  const legacyApp = source.querySelector(".app");
  const sidebar = legacyApp?.querySelector("#sidebar");
  const chat = legacyApp?.querySelector(".chat");
  const menu = source.querySelector("#newChatMenu");
  if (!legacyApp || !sidebar || !chat || !menu) {
    throw new Error("工作台页面结构不完整");
  }

  sidebar.querySelector(".sidebar-footer")?.remove();
  const sidebarHeader = sidebar.querySelector(".sidebar-header");
  const agentSwitcher = document.createElement("div");
  agentSwitcher.className = "agent-switcher";
  agentSwitcher.innerHTML = `
    <label class="agent-select-field">
      <i data-lucide="bot" aria-hidden="true"></i>
      <select id="workspaceAgentSelect" aria-label="选择测试 Agent">
        <option value="">正在加载 Agent…</option>
      </select>
    </label>
    <button type="button" class="icon-action pin-agent" id="pinAgentPaneBtn" title="固定 Agent 列表到左侧" aria-label="固定 Agent 列表到左侧" aria-pressed="false">
      <i data-lucide="pin"></i>
    </button>`;
  sidebar.insertBefore(agentSwitcher, sidebarHeader);
  sidebarHeader?.remove();
  sidebar.querySelector("#newChatBtn")?.remove();
  sidebar.querySelector("#newChatBtnWide")?.remove();
  sidebar.querySelector(".sidebar-section.grow")?.classList.add("capabilities-section");

  const sessionSection = sidebar.querySelector(".sidebar-section:not(.grow)");
  if (sessionSection) {
    sessionSection.querySelector(".section-title")?.remove();
    const toolbar = document.createElement("div");
    toolbar.className = "session-toolbar";
    const search = document.createElement("label");
    search.className = "compact-search session-search";
    search.innerHTML = '<i data-lucide="search" aria-hidden="true"></i><input type="search" id="sessionSearchInput" placeholder="搜索当前 Agent 会话" />';
    const testChatButton = document.createElement("button");
    testChatButton.type = "button";
    testChatButton.id = "contextTestChatBtn";
    testChatButton.className = "icon-action session-test-action hidden";
    testChatButton.title = "新建测试会话";
    testChatButton.setAttribute("aria-label", "新建测试会话");
    testChatButton.innerHTML = '<i data-lucide="flask-conical"></i>';
    const newChatButton = document.createElement("button");
    newChatButton.type = "button";
    newChatButton.id = "newChatBtn";
    newChatButton.className = "icon-action session-new-action";
    newChatButton.dataset.consoleDirectNewChat = "true";
    newChatButton.title = "为当前 Agent 新建对话";
    newChatButton.setAttribute("aria-label", "为当前 Agent 新建对话");
    newChatButton.innerHTML = '<i data-lucide="square-pen"></i>';
    toolbar.append(search, testChatButton, newChatButton);
    sessionSection.insertBefore(toolbar, sessionSection.querySelector("#sessionList"));
  }

  const host = document.getElementById("workspaceLegacyHost");
  host.replaceChildren(sidebar, chat);
  installContextTestDialog();

  document.getElementById("sessionSearchInput")?.addEventListener("input", filterSessions);
}

function installContextTestDialog() {
  document.getElementById("contextTestDialog")?.remove();
  const dialog = document.createElement("dialog");
  dialog.id = "contextTestDialog";
  dialog.className = "context-test-dialog";
  dialog.innerHTML = `
    <form method="dialog">
      <header>
        <div><strong>新建测试会话</strong><span>快速初始化一段可触发上下文压缩的合法对话历史</span></div>
        <button type="button" class="icon-action" data-context-test-close aria-label="关闭"><i data-lucide="x"></i></button>
      </header>
      <div class="context-test-body">
        <div class="context-test-budget" id="contextTestBudget"></div>
        <div class="context-test-scenarios" id="contextTestScenarios"></div>
        <div class="context-test-error hidden" id="contextTestError"></div>
      </div>
      <footer>
        <button type="button" data-context-test-close>取消</button>
        <button type="button" class="primary" id="createContextTestBtn">创建并打开</button>
      </footer>
    </form>`;
  document.body.appendChild(dialog);
  dialog.querySelectorAll("[data-context-test-close]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });
}

function formatTokens(value) {
  if (value === null || value === undefined || value === "") return "—";
  return Number.isFinite(Number(value)) ? Number(value).toLocaleString("zh-CN") : "—";
}

function renderContextTestDialog() {
  if (!contextTestCatalog) return;
  const budget = document.getElementById("contextTestBudget");
  const scenarios = document.getElementById("contextTestScenarios");
  if (!budget || !scenarios) return;
  budget.innerHTML = `
    <span>模型 <strong>${escapeHtml(contextTestCatalog.model_name)}</strong></span>
    <span>窗口 <strong>${escapeHtml(formatTokens(contextTestCatalog.context_window_tokens))}</strong></span>
    <span>触发线 <strong>${escapeHtml(formatTokens(contextTestCatalog.trigger_tokens))}</strong></span>
    <span>目标线 <strong>${escapeHtml(formatTokens(contextTestCatalog.target_tokens))}</strong></span>`;
  scenarios.innerHTML = contextTestCatalog.scenarios
    .map(
      (scenario, index) => `
        <label class="context-test-scenario">
          <input type="radio" name="contextTestScenario" value="${escapeHtml(scenario.key)}" ${index === 0 ? "checked" : ""} />
          <span class="context-test-scenario-copy">
            <strong>${escapeHtml(scenario.title)}</strong>
            <span>${escapeHtml(scenario.description)}</span>
            <small>${escapeHtml(scenario.expected_behavior)}</small>
          </span>
        </label>`,
    )
    .join("");
}

async function loadContextTestCatalog(agentId) {
  const button = document.getElementById("contextTestChatBtn");
  if (!agentId) {
    button?.classList.add("hidden");
    return null;
  }
  if (contextTestCatalogAgentId === agentId && contextTestCatalog) {
    button?.classList.remove("hidden");
    return contextTestCatalog;
  }
  if (contextTestRequestAgentId === agentId) return null;
  button?.classList.add("hidden");
  contextTestRequestAgentId = agentId;
  try {
    const response = await daFetch(
      serviceUrl(`api/v1/context-test-scenarios?agent_id=${encodeURIComponent(agentId)}`),
    );
    if (!response.ok) {
      if (contextTestRequestAgentId === agentId) button?.classList.add("hidden");
      return null;
    }
    const payload = await response.json();
    if (contextTestRequestAgentId !== agentId) return null;
    contextTestCatalog = payload;
    contextTestCatalogAgentId = agentId;
    button?.classList.remove("hidden");
    return payload;
  } catch {
    if (contextTestRequestAgentId === agentId) button?.classList.add("hidden");
    return null;
  } finally {
    if (contextTestRequestAgentId === agentId) contextTestRequestAgentId = "";
  }
}

async function openContextTestDialog() {
  const api = window.DeepAgentConsoleChat;
  const dialog = document.getElementById("contextTestDialog");
  if (!api?.state.agentId || !dialog) return;
  const catalog = await loadContextTestCatalog(api.state.agentId);
  if (!catalog) return;
  document.getElementById("contextTestError")?.classList.add("hidden");
  renderContextTestDialog();
  window.lucide?.createIcons();
  dialog.showModal();
}

async function createContextTestSession() {
  const api = window.DeepAgentConsoleChat;
  const dialog = document.getElementById("contextTestDialog");
  const createButton = document.getElementById("createContextTestBtn");
  const error = document.getElementById("contextTestError");
  const selected = dialog?.querySelector('input[name="contextTestScenario"]:checked');
  if (!api?.state.agentId || !selected || !createButton || !error) return;
  createButton.disabled = true;
  createButton.textContent = "正在创建…";
  error.classList.add("hidden");
  try {
    const response = await daFetch(
      serviceUrl(`api/v1/context-test-scenarios/${encodeURIComponent(selected.value)}`),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: api.state.agentId }),
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        typeof payload.detail === "string"
          ? payload.detail
          : `创建测试会话失败：HTTP ${response.status}`,
      );
    }
    dialog.close();
    await api.syncSessionsFromBackend();
    await api.openSession({
      id: payload.session.session_id,
      agentId: payload.session.agent_id,
      agentName: payload.session.agent_name,
      title: payload.session.title,
      messageCount: payload.session.message_count,
    });
    api.setComposerText(payload.recommended_prompt);
  } catch (cause) {
    error.textContent = cause?.message || "创建测试会话失败";
    error.classList.remove("hidden");
  } finally {
    createButton.disabled = false;
    createButton.textContent = "创建并打开";
  }
}

function filterSessions(event) {
  const query = String(event?.target?.value || "").trim().toLowerCase();
  document.querySelectorAll("#sessionList > li").forEach((row) => {
    row.classList.toggle("console-filtered", !!query && !row.textContent.toLowerCase().includes(query));
  });
}

function renderAgents() {
  const api = window.DeepAgentConsoleChat;
  if (!api) return;
  const query = document.getElementById("agentSearchInput")?.value.trim().toLowerCase() || "";
  const agents = api.state.agents.filter((agent) => {
    const text = `${agent.name || ""} ${agent.id || ""} ${agent.description || ""}`.toLowerCase();
    return !query || text.includes(query);
  });
  renderAgentSwitcher(api.state);
  const host = document.getElementById("consoleAgentList");
  if (api.state.agentId && api.state.agentId !== contextTestCatalogAgentId) {
    void loadContextTestCatalog(api.state.agentId);
  }
  document.getElementById("agentCount").textContent = `${agents.length} 个`;
  if (!agents.length) {
    host.innerHTML = '<div class="list-empty">没有匹配的 Agent</div>';
    return;
  }
  host.innerHTML = agents
    .map((agent) => {
      const active = agent.id === api.state.agentId;
      const model = agent.model?.name || agent.model?.model || "未配置模型";
      const capabilityCount = Array.isArray(agent.capabilities) ? agent.capabilities.length : "默认";
      return `<button type="button" class="agent-item ${active ? "active" : ""}" data-agent-id="${escapeHtml(agent.id)}">
        <span class="agent-avatar">${escapeHtml((agent.name || "A").slice(0, 1).toUpperCase())}</span>
        <span class="agent-copy"><strong>${escapeHtml(agent.name)}</strong><small>${escapeHtml(model)} · ${escapeHtml(capabilityCount)} 能力</small></span>
        <span class="agent-state" title="可测试"></span>
      </button>`;
    })
    .join("");

  host.querySelectorAll("[data-agent-id]").forEach((button) => {
    button.addEventListener("click", () => {
      selectWorkspaceAgent(button.dataset.agentId);
    });
  });
}

function renderAgentSwitcher(state) {
  const select = document.getElementById("workspaceAgentSelect");
  if (!select) return;
  const optionKey = state.agents.map((agent) => `${agent.id}:${agent.name}`).join("|");
  if (select.dataset.optionKey !== optionKey) {
    select.dataset.optionKey = optionKey;
    select.replaceChildren();
    if (!state.agents.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "无可用 Agent";
      select.appendChild(option);
    } else {
      state.agents.forEach((agent) => {
        const option = document.createElement("option");
        option.value = agent.id;
        option.textContent = agent.name;
        select.appendChild(option);
      });
    }
  }
  select.value = state.agentId || "";
  select.disabled = !state.agents.length;
  select.title = currentAgentName(state);
}

function setAgentPanePinned(pinned) {
  const grid = document.getElementById("workspaceGrid");
  const pinButton = document.getElementById("pinAgentPaneBtn");
  const unpinButton = document.getElementById("unpinAgentPaneBtn");
  if (!grid || !pinButton || !unpinButton) return;
  grid.classList.toggle("agent-pinned", pinned);
  pinButton.classList.toggle("active", pinned);
  pinButton.setAttribute("aria-pressed", String(pinned));
  unpinButton.setAttribute("aria-pressed", String(pinned));
  closeWorkspaceDrawers();
}

function inspectorUsesDrawer() {
  return matchMedia("(max-width: 1260px)").matches;
}

function syncInspectorToggle() {
  const grid = document.getElementById("workspaceGrid");
  const inspector = document.getElementById("inspectorPane");
  const button = document.getElementById("toggleInspectorPaneBtn");
  if (!grid || !inspector || !button) return;
  const visible = inspectorUsesDrawer()
    ? inspector.classList.contains("mobile-open")
    : !grid.classList.contains("inspector-hidden");
  const sessionDetailsActive = visible && !selectedContextReference;
  button.classList.toggle("active", sessionDetailsActive);
  button.setAttribute("aria-pressed", String(sessionDetailsActive));
  button.title = sessionDetailsActive ? "隐藏会话详情" : "显示会话详情";
}

function toggleInspectorPane() {
  const grid = document.getElementById("workspaceGrid");
  const inspector = document.getElementById("inspectorPane");
  if (!grid || !inspector) return;
  if (selectedContextReference) {
    selectedContextReference = null;
    lastRenderKey = "";
    syncContextReferenceButtons();
    openInspectorPane();
    renderInspector();
    return;
  }
  if (inspectorUsesDrawer()) {
    const shouldOpen = !inspector.classList.contains("mobile-open");
    closeWorkspaceDrawers();
    if (shouldOpen) {
      inspector.classList.add("mobile-open");
      document.getElementById("workspaceDrawerBackdrop")?.classList.remove("hidden");
    }
  } else {
    grid.classList.toggle("inspector-hidden");
  }
  syncInspectorToggle();
}

function selectWorkspaceAgent(agentId) {
  const api = window.DeepAgentConsoleChat;
  if (!api || !agentId || agentId === api.state.agentId) return;
  api.selectAgent(agentId);
  lastRenderKey = "";
  renderAgents();
  renderInspector();
}

function currentAgentName(state) {
  return state.agents.find((agent) => agent.id === state.agentId)?.name || state.agentId || "—";
}

function contextReferenceSelectionId(reference, branchPoint) {
  return [branchPoint ?? "live", reference?.key || "", reference?.type || ""].join(":");
}

function isContextReferenceSelected(reference, branchPoint) {
  return selectedContextReference?.id === contextReferenceSelectionId(reference, branchPoint);
}

function syncContextReferenceButtons() {
  document.querySelectorAll(".context-ref-info").forEach((button) => {
    button.setAttribute(
      "aria-pressed",
      button.dataset.contextRefId === selectedContextReference?.id ? "true" : "false",
    );
  });
}

function openInspectorPane() {
  const grid = document.getElementById("workspaceGrid");
  const inspector = document.getElementById("inspectorPane");
  if (!grid || !inspector) return;
  if (inspectorUsesDrawer()) {
    closeWorkspaceDrawers();
    inspector.classList.add("mobile-open");
    document.getElementById("workspaceDrawerBackdrop")?.classList.remove("hidden");
  } else {
    grid.classList.remove("inspector-hidden");
  }
  syncInspectorToggle();
}

function toggleContextReferenceInspector(reference, branchPoint) {
  const id = contextReferenceSelectionId(reference, branchPoint);
  if (selectedContextReference?.id !== id) {
    selectedContextReference = {
      id,
      sessionId: window.DeepAgentConsoleChat?.state.sessionId || null,
      reference: JSON.parse(JSON.stringify(reference)),
    };
  }
  openInspectorPane();
  lastRenderKey = "";
  syncContextReferenceButtons();
  renderInspector();
}

function renderContextReferenceInspector(host, state) {
  if (!selectedContextReference) return false;
  if (selectedContextReference.sessionId !== (state.sessionId || null)) {
    selectedContextReference = null;
    syncContextReferenceButtons();
    return false;
  }
  const reference = selectedContextReference.reference;
  const rawJson = JSON.stringify(reference, null, 2);
  host.innerHTML = `
    <section class="inspector-section context-reference-inspector">
      <header><strong>上下文引用</strong><span>只读 JSON</span></header>
      <dl class="inspector-grid">
        <dt>名称</dt><dd>${escapeHtml(reference.label || "—")}</dd>
        <dt>Key</dt><dd class="mono">${escapeHtml(reference.key || "—")}</dd>
        <dt>类型</dt><dd class="mono">${escapeHtml(reference.type || "—")}</dd>
        <dt>版本</dt><dd>${escapeHtml(reference.schema_version || "—")}</dd>
      </dl>
      <pre class="inspector-json" aria-label="上下文引用 JSON">${escapeHtml(rawJson)}</pre>
      <p class="inspector-help">这是会话历史保存的完整上下文引用。metadata 只用于系统和前端，不会进入模型输入。点击对话标题栏的“会话详情”可切回会话信息。</p>
    </section>`;
  return true;
}

function renderInspector() {
  const api = window.DeepAgentConsoleChat;
  const host = document.getElementById("inspectorContent");
  if (!api || !host) return;
  const state = api.state;
  if (renderContextReferenceInspector(host, state)) return;

  const model = state.llmModels.find((item) => item.id === state.llmModelId);
  const context = window.DeepAgentConsoleContext?.effectiveContext || {};
  const identity = window.DeepAgentConsoleContext?.identity || {};
  const contextStatus = state.contextStatus || {};
  const compaction = contextStatus.last_compaction || {};
  const strategyLabels = {
    none: "未触发",
    tiered_deterministic: "确定性压缩",
    tiered_summary: "分层摘要",
    sliding_window_fallback: "滑动窗口兜底",
  };
  const statusText = state.streaming ? "生成中" : state.awaitingHitl ? "等待用户" : "空闲";
  const statusClass = state.streaming ? "running" : state.awaitingHitl ? "waiting" : "idle";
  const usage = state.lastRunUsage || {};
  const run = state.lastRunMetrics || {};
  const compactionState = compaction.triggered
    ? strategyLabels[compaction.strategy] || compaction.strategy
    : contextStatus.compaction_enabled
      ? "未触发"
      : "未启用";
  const triggerTokens = Number(contextStatus.trigger_tokens);
  const historyTokens = Number(contextStatus.estimated_history_tokens || 0);
  const budgetPercent = triggerTokens > 0
    ? Math.min(100, Math.round((historyTokens / triggerTokens) * 100))
    : 0;

  host.innerHTML = `
    <section class="inspector-section inspector-session-group">
      <header><strong>当前会话</strong><span class="status-badge ${statusClass}">${statusText}</span></header>
      <dl class="inspector-grid">
        <dt>会话 ID</dt><dd class="mono">${escapeHtml(state.sessionId || "尚未创建")}</dd>
        <dt>Agent</dt><dd>${escapeHtml(currentAgentName(state))}</dd>
        <dt>Agent ID</dt><dd class="mono">${escapeHtml(state.agentId || "—")}</dd>
        <dt>模型</dt><dd>${escapeHtml(model?.name || model?.model || "—")}</dd>
        <dt>原始消息</dt><dd>${escapeHtml(contextStatus.raw_message_count ?? state.modelMessageCount ?? 0)} 条</dd>
        <dt>待发附件</dt><dd>${state.pendingAttachments.length} 个</dd>
      </dl>
      <header class="inspector-subheading"><strong>请求Header</strong><span>自动推导</span></header>
      <dl class="inspector-grid">
        <dt>App</dt><dd class="mono">${escapeHtml(context.app || "继承")}</dd>
        <dt>Space</dt><dd class="mono">${escapeHtml(context.space || "继承")}</dd>
        <dt>Language</dt><dd>${escapeHtml(context.language || "继承")}</dd>
        <dt>User ID</dt><dd class="mono">${escapeHtml(identity.user || "未认证")}</dd>
      </dl>
    </section>
    <section class="inspector-section inspector-budget-section">
        <div class="budget-meter" title="原始历史估算占压缩触发线的比例"><span style="width:${budgetPercent}%"></span></div>
        <header><strong>上下文预算</strong><span>历史快照</span></header>
        <dl class="inspector-grid">
          <dt>当前历史估算</dt><dd>${escapeHtml(formatTokens(contextStatus.estimated_history_tokens))} Token</dd>
          <dt>上下文窗口</dt><dd>${escapeHtml(formatTokens(contextStatus.context_window_tokens))} Token</dd>
          <dt>动态输出预估</dt><dd>${escapeHtml(formatTokens(contextStatus.predicted_output_tokens))} Token</dd>
          <dt>安全余量</dt><dd>${escapeHtml(formatTokens(contextStatus.safety_margin_tokens))} Token</dd>
          <dt>可用输入预算</dt><dd>${escapeHtml(formatTokens(contextStatus.usable_request_tokens))} Token</dd>
          <dt>压缩触发线</dt><dd>${escapeHtml(formatTokens(contextStatus.trigger_tokens))} Token</dd>
          <dt>压缩目标线</dt><dd>${escapeHtml(formatTokens(contextStatus.target_tokens))} Token</dd>
        </dl>
        <p class="inspector-help">预算值全部由后端统一计算。这里是完整原始会话历史的只读快照，不等同于某次 Run 的最终模型请求载荷。</p>
      </section>
      <section class="inspector-section">
        <header><strong>当前 Run</strong><span>${run.detailed === false ? "详细指标请查看运行日志" : "当前页面最近一次"}</span></header>
        <dl class="inspector-grid">
          <dt>Conversation ID</dt><dd class="mono">${escapeHtml(state.conversationId || run.conversation_id || "—")}</dd>
          <dt>Run ID</dt><dd class="mono">${escapeHtml(run.run_id || "—")}</dd>
          <dt>终态</dt><dd>${escapeHtml(run.status || "—")}</dd>
          <dt>模型</dt><dd>${escapeHtml(run.model_id || model?.name || model?.model || "—")}</dd>
          <dt>请求次数</dt><dd>${escapeHtml(run.requests ?? usage.requests ?? "—")}</dd>
          <dt>输入 Token</dt><dd>${escapeHtml(formatTokens(run.input_tokens ?? usage.input_tokens))}</dd>
          <dt>输出 Token</dt><dd>${escapeHtml(formatTokens(run.output_tokens ?? usage.output_tokens))}</dd>
          <dt>合计 Token</dt><dd>${escapeHtml(formatTokens(run.total_tokens ?? usage.total_tokens))}</dd>
          <dt>缓存读取</dt><dd>${escapeHtml(formatTokens(run.cache_read_tokens))}</dd>
          <dt>缓存写入</dt><dd>${escapeHtml(formatTokens(run.cache_write_tokens))}</dd>
          <dt>工具调用</dt><dd>${escapeHtml(run.tool_calls ?? usage.tool_calls ?? "—")}</dd>
          <dt>Tool Search</dt><dd>${escapeHtml(run.tool_search_calls ?? "—")}</dd>
          <dt>耗时</dt><dd>${Number.isFinite(Number(run.duration_ms)) ? `${Number(run.duration_ms).toLocaleString("zh-CN")} ms` : "—"}</dd>
          <dt>压缩</dt><dd>${escapeHtml(run.compaction || "—")}</dd>
        </dl>
      </section>
      <section class="inspector-section">
        <header><strong>压缩与 Checkpoint</strong><span>${escapeHtml(compactionState)}</span></header>
        <dl class="inspector-grid">
          <dt>本轮压缩前/后</dt><dd>${escapeHtml(formatTokens(compaction.estimated_tokens_before))} / ${escapeHtml(formatTokens(compaction.estimated_tokens_after))}</dd>
          <dt>Checkpoint</dt><dd>${contextStatus.checkpoint_exists ? "已生成" : "暂无"}</dd>
          <dt>Checkpoint Token</dt><dd>${escapeHtml(formatTokens(contextStatus.checkpoint_estimated_tokens))}</dd>
          <dt>估算范围</dt><dd>${escapeHtml(contextStatus.estimate_scope || "—")}</dd>
          <dt>估算器</dt><dd>${escapeHtml(contextStatus.estimator_source || "—")}</dd>
        </dl>
    </section>`;
}

function startStateMonitor() {
  window.setInterval(() => {
    const api = window.DeepAgentConsoleChat;
    if (!api) return;
    const key = JSON.stringify({
      agents: Array.isArray(api.state.agents) ? api.state.agents.map((a) => [a.id, a.name]) : [],
      agentId: api.state.agentId,
      sessionId: api.state.sessionId,
      llmModelId: api.state.llmModelId,
      streaming: api.state.streaming,
      awaitingHitl: api.state.awaitingHitl,
      attachments: api.state.pendingAttachments.length,
      contextStatus: api.state.contextStatus,
      lastRunUsage: api.state.lastRunUsage,
      lastRunMetrics: api.state.lastRunMetrics,
      conversationId: api.state.conversationId,
    });
    if (key === lastRenderKey) return;
    lastRenderKey = key;
    renderAgents();
    renderInspector();
  }, 350);
}

function bindWorkspaceControls() {
  document.getElementById("workspaceAgentSelect")?.addEventListener("change", (event) => {
    selectWorkspaceAgent(event.target.value);
  });
  document.getElementById("pinAgentPaneBtn")?.addEventListener("click", () => {
    const pinned = document.getElementById("workspaceGrid")?.classList.contains("agent-pinned");
    setAgentPanePinned(!pinned);
  });
  document.getElementById("unpinAgentPaneBtn")?.addEventListener("click", () => {
    setAgentPanePinned(false);
  });
  document.getElementById("agentSearchInput")?.addEventListener("input", renderAgents);
  document.getElementById("refreshAgentsBtn")?.addEventListener("click", async () => {
    await window.DeepAgentConsoleChat?.loadAgents();
    renderAgents();
  });
  document.getElementById("contextTestChatBtn")?.addEventListener("click", openContextTestDialog);
  document.getElementById("createContextTestBtn")?.addEventListener("click", createContextTestSession);
  document.getElementById("toggleInspectorPaneBtn")?.addEventListener("click", toggleInspectorPane);
  document.querySelectorAll("[data-workspace-drawer]").forEach((button) => {
    button.addEventListener("click", () => toggleWorkspaceDrawer(button.dataset.workspaceDrawer));
  });
  document.getElementById("workspaceDrawerBackdrop")?.addEventListener("click", closeWorkspaceDrawers);
  document.getElementById("sessionList")?.addEventListener("click", () => {
    if (matchMedia("(max-width: 620px)").matches) closeWorkspaceDrawers();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeWorkspaceDrawers();
  });
}

function closeWorkspaceDrawers() {
  document.querySelector(".agent-pane")?.classList.remove("mobile-open");
  document.querySelector("#workspaceLegacyHost > .sidebar")?.classList.remove("mobile-open");
  document.getElementById("inspectorPane")?.classList.remove("mobile-open");
  document.getElementById("workspaceDrawerBackdrop")?.classList.add("hidden");
  syncInspectorToggle();
}

function toggleWorkspaceDrawer(name) {
  const targets = {
    agents: document.querySelector(".agent-pane"),
    sessions: document.querySelector("#workspaceLegacyHost > .sidebar"),
    inspector: document.getElementById("inspectorPane"),
  };
  const target = targets[name];
  const shouldOpen = target && !target.classList.contains("mobile-open");
  closeWorkspaceDrawers();
  if (!shouldOpen) return;
  target.classList.add("mobile-open");
  document.getElementById("workspaceDrawerBackdrop")?.classList.remove("hidden");
  syncInspectorToggle();
}

window.DeepAgentConsoleWorkspace = {
  isContextReferenceSelected,
  toggleContextReferenceInspector,
};

export async function initWorkspace() {
  if (initialized) return;
  initialized = true;
  const source = await fetchLegacyPage();
  installLegacyDom(source);
  await import(`./chat.js?v=${WORKSPACE_ASSET_VERSION}`);
  bindWorkspaceControls();
  setAgentPanePinned(false);
  syncInspectorToggle();
  window.addEventListener("resize", syncInspectorToggle);
  renderAgents();
  renderInspector();
  startStateMonitor();
  document.getElementById("workspaceLoading")?.classList.add("hidden");
  document.getElementById("workspaceGrid")?.classList.remove("hidden");
  document.getElementById("workspaceMobileBar")?.classList.remove("hidden");
  window.lucide?.createIcons();
}
