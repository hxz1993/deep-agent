const MAX_EVENTS = 80;
const WORKSPACE_ASSET_VERSION = "20260730-1";

let initialized = false;
let eventRows = [];
let activeInspectorTab = "overview";
let lastRenderKey = "";
let contextTestCatalog = null;
let contextTestCatalogAgentId = "";
let contextTestRequestAgentId = "";

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function truncate(value, length = 1200) {
  let text;
  try {
    text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  } catch {
    text = String(value);
  }
  return text.length > length ? `${text.slice(0, length)}\n…` : text;
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
  const sessionPanelTitle = sidebar.querySelector(".brand-name");
  if (sessionPanelTitle) {
    sessionPanelTitle.id = "sessionPanelTitle";
    sessionPanelTitle.textContent = "Agent 会话";
  }
  const sidebarHeader = sidebar.querySelector(".sidebar-header");
  sidebar.querySelector("#newChatBtn")?.remove();
  sidebar.querySelector("#newChatBtnWide")?.remove();
  const sessionPanelSummary = document.createElement("span");
  sessionPanelSummary.id = "sessionPanelSummary";
  sessionPanelSummary.className = "session-panel-summary";
  sessionPanelSummary.textContent = "0 个会话";
  sidebarHeader?.appendChild(sessionPanelSummary);
  sidebar.querySelector(".sidebar-section.grow")?.classList.add("capabilities-section");

  const sessionSection = sidebar.querySelector(".sidebar-section:not(.grow)");
  if (sessionSection) {
    sessionSection.querySelector(".section-title")?.remove();
    const toolbar = document.createElement("div");
    toolbar.className = "session-toolbar";
    const search = document.createElement("label");
    search.className = "compact-search session-search";
    search.innerHTML = '<span aria-hidden="true">⌕</span><input type="search" id="sessionSearchInput" placeholder="搜索当前 Agent 会话" />';
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

  const chatActions = chat.querySelector(".chat-actions");
  if (chatActions) {
    const contextButton = document.createElement("button");
    contextButton.type = "button";
    contextButton.className = "workspace-context-action";
    contextButton.dataset.openContext = "";
    contextButton.innerHTML = '<i data-lucide="waypoints"></i><span>测试上下文</span>';
    chatActions.prepend(contextButton);
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
      api.selectAgent(button.dataset.agentId);
      renderAgents();
      eventRows = [];
      renderInspector();
    });
  });
}

function currentAgentName(state) {
  return state.agents.find((agent) => agent.id === state.agentId)?.name || state.agentId || "—";
}

function eventToolRows() {
  return eventRows.filter((row) => row.event === "tool_call" || row.event === "tool_result");
}

function renderInspector() {
  const api = window.DeepAgentConsoleChat;
  const host = document.getElementById("inspectorContent");
  if (!api || !host) return;
  const state = api.state;

  if (activeInspectorTab === "events") {
    host.innerHTML = eventRows.length
      ? `<div class="event-list">${eventRows
          .slice()
          .reverse()
          .map((row) => `<details><summary><span>${escapeHtml(row.time)}</span><strong>${escapeHtml(row.event)}</strong></summary><pre>${escapeHtml(truncate(row.data))}</pre></details>`)
          .join("")}</div>`
      : '<div class="inspector-empty"><p>本次页面运行还没有 SSE 事件</p></div>';
    return;
  }

  if (activeInspectorTab === "tools") {
    const tools = eventToolRows();
    host.innerHTML = tools.length
      ? `<div class="tool-timeline">${tools
          .map((row) => {
            const name = row.data?.tool || "未命名工具";
            const id = row.data?.tool_call_id || "—";
            return `<article class="${row.event === "tool_result" ? "complete" : "running"}"><span></span><div><strong>${escapeHtml(name)}</strong><small>${escapeHtml(row.event)} · ${escapeHtml(id)}</small></div></article>`;
          })
          .join("")}</div>`
      : '<div class="inspector-empty"><p>当前会话没有工具调用</p></div>';
    return;
  }

  const model = state.llmModels.find((item) => item.id === state.llmModelId);
  const context = window.DeepAgentConsoleContext?.effectiveContext || {};
  const contextStatus = state.contextStatus || {};
  const compaction = contextStatus.last_compaction || {};
  const formatTokens = (value) =>
    Number.isFinite(Number(value)) ? Number(value).toLocaleString("zh-CN") : "—";
  const strategyLabels = {
    none: "未触发",
    tiered_deterministic: "确定性压缩",
    tiered_summary: "分层摘要",
    sliding_window_fallback: "滑动窗口兜底",
  };
  host.innerHTML = `<dl class="inspector-grid">
    <dt>会话</dt><dd class="mono">${escapeHtml(state.sessionId || "尚未创建")}</dd>
    <dt>Agent</dt><dd>${escapeHtml(currentAgentName(state))}</dd>
    <dt>模型</dt><dd>${escapeHtml(model?.name || model?.model || "—")}</dd>
    <dt>状态</dt><dd><span class="status-badge ${state.streaming ? "running" : state.awaitingHitl ? "waiting" : "idle"}">${state.streaming ? "生成中" : state.awaitingHitl ? "等待用户" : "空闲"}</span></dd>
    <dt>附件</dt><dd>${state.pendingAttachments.length} 个待发送</dd>
    <dt>工具事件</dt><dd>${eventToolRows().length} 条</dd>
    <dt>历史估算</dt><dd>${escapeHtml(formatTokens(contextStatus.estimated_history_tokens))} Token</dd>
    <dt>上下文窗口</dt><dd>${escapeHtml(formatTokens(contextStatus.context_window_tokens))} Token</dd>
    <dt>输出预估</dt><dd>${escapeHtml(formatTokens(contextStatus.predicted_output_tokens))} Token</dd>
    <dt>压缩阈值</dt><dd>${escapeHtml(formatTokens(contextStatus.trigger_tokens))} Token</dd>
    <dt>压缩状态</dt><dd>${escapeHtml(
      compaction.triggered
        ? strategyLabels[compaction.strategy] || compaction.strategy
        : contextStatus.compaction_enabled
          ? "未触发"
          : "未启用"
    )}</dd>
    <dt>压缩前/后</dt><dd>${escapeHtml(formatTokens(compaction.estimated_tokens_before))} / ${escapeHtml(formatTokens(compaction.estimated_tokens_after))}</dd>
    <dt>Checkpoint</dt><dd>${contextStatus.checkpoint_exists ? "已生成" : "暂无"}</dd>
    <dt>App</dt><dd class="mono">${escapeHtml(context.app || "继承")}</dd>
    <dt>Space</dt><dd class="mono">${escapeHtml(context.space || "继承")}</dd>
    <dt>Language</dt><dd>${escapeHtml(context.language || "继承")}</dd>
  </dl>`;
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
      tab: activeInspectorTab,
      eventCount: eventRows.length,
      contextStatus: api.state.contextStatus,
    });
    if (key === lastRenderKey) return;
    lastRenderKey = key;
    renderAgents();
    renderInspector();
  }, 350);
}

function bindWorkspaceControls() {
  document.getElementById("agentSearchInput")?.addEventListener("input", renderAgents);
  document.getElementById("refreshAgentsBtn")?.addEventListener("click", async () => {
    await window.DeepAgentConsoleChat?.loadAgents();
    renderAgents();
  });
  document.getElementById("contextTestChatBtn")?.addEventListener("click", openContextTestDialog);
  document.getElementById("createContextTestBtn")?.addEventListener("click", createContextTestSession);
  document.querySelectorAll("[data-inspector-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-inspector-tab]").forEach((item) => item.classList.toggle("active", item === button));
      activeInspectorTab = button.dataset.inspectorTab;
      renderInspector();
    });
  });
  window.addEventListener("deep-agent-console-event", (event) => {
    eventRows.push({
      event: event.detail?.event || "event",
      data: event.detail?.data ?? null,
      time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
    });
    if (eventRows.length > MAX_EVENTS) eventRows = eventRows.slice(-MAX_EVENTS);
    renderInspector();
  });
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
}

export async function initWorkspace() {
  if (initialized) return;
  initialized = true;
  const source = await fetchLegacyPage();
  installLegacyDom(source);
  await import(`./chat.js?v=${WORKSPACE_ASSET_VERSION}`);
  bindWorkspaceControls();
  renderAgents();
  renderInspector();
  startStateMonitor();
  document.getElementById("workspaceLoading")?.classList.add("hidden");
  document.getElementById("workspaceGrid")?.classList.remove("hidden");
  document.getElementById("workspaceMobileBar")?.classList.remove("hidden");
  window.lucide?.createIcons();
}
