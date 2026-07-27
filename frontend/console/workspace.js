const MAX_EVENTS = 80;
const WORKSPACE_ASSET_VERSION = "20260728-2";

let initialized = false;
let eventRows = [];
let activeInspectorTab = "overview";
let lastRenderKey = "";

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
    const newChatButton = document.createElement("button");
    newChatButton.type = "button";
    newChatButton.id = "newChatBtn";
    newChatButton.className = "icon-action session-new-action";
    newChatButton.dataset.consoleDirectNewChat = "true";
    newChatButton.title = "为当前 Agent 新建对话";
    newChatButton.setAttribute("aria-label", "为当前 Agent 新建对话");
    newChatButton.innerHTML = '<i data-lucide="square-pen"></i>';
    toolbar.append(search, newChatButton);
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

  document.getElementById("sessionSearchInput")?.addEventListener("input", filterSessions);
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
  host.innerHTML = `<dl class="inspector-grid">
    <dt>会话</dt><dd class="mono">${escapeHtml(state.sessionId || "尚未创建")}</dd>
    <dt>Agent</dt><dd>${escapeHtml(currentAgentName(state))}</dd>
    <dt>模型</dt><dd>${escapeHtml(model?.name || model?.model || "—")}</dd>
    <dt>状态</dt><dd><span class="status-badge ${state.streaming ? "running" : state.awaitingHitl ? "waiting" : "idle"}">${state.streaming ? "生成中" : state.awaitingHitl ? "等待用户" : "空闲"}</span></dd>
    <dt>附件</dt><dd>${state.pendingAttachments.length} 个待发送</dd>
    <dt>工具事件</dt><dd>${eventToolRows().length} 条</dd>
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
      agents: api.state.agents.map((a) => [a.id, a.name]),
      agentId: api.state.agentId,
      sessionId: api.state.sessionId,
      llmModelId: api.state.llmModelId,
      streaming: api.state.streaming,
      awaitingHitl: api.state.awaitingHitl,
      attachments: api.state.pendingAttachments.length,
      tab: activeInspectorTab,
      eventCount: eventRows.length,
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
