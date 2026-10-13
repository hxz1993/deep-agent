import { sessionLoadingHtml, setSessionLoading } from "./session-loading.js?v=20260918-release01012";
import { renderContextReferenceContent, sessionStatusLabels, savedRunPresentation } from "./session-presentation.js?v=20260923-usage-tab-2";
import { renderSessionInspector } from "./session-inspector.js?v=20261013-providers";
import { channelOriginPresentation } from "./session-display.js?v=20260929-release0124";
import { copyTextToClipboard } from "./message-actions.js?v=20260912-21";
import { registerSessionViewTemplate } from "./session-view-shell.js?v=20260912-21";

const WORKSPACE_ASSET_VERSION = "20261013-console-ui-4";

const WORKSPACE_PANE_LIMITS = {
  session: { min: 220, max: 420, initial: 300, variable: "--console-session-width" },
  inspector: { min: 260, max: 720, initial: 320, variable: "--console-inspector-width" },
};

let initialized = false;
let lastRenderKey = "";
let selectedContextReference = null;
let inspectorActiveTab = "session";
let workspaceChannelAccounts = [];
let channelAccountsRequest = null;

function refreshChannelAccounts() {
  if (channelAccountsRequest) return channelAccountsRequest;
  channelAccountsRequest = (async () => {
    try {
      const response = await window.daFetch(window.serviceUrl("api/v1/channel-accounts"), { reportStatus: false, signal: AbortSignal.timeout(5000) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const accounts = await response.json();
      workspaceChannelAccounts = Array.isArray(accounts) ? accounts.map(({ id, name, channel_type, agent_id }) => ({ id, name, channel_type, agent_id })) : [];
    } catch {
      // Optional association data must not block chat or replace persisted origins.
      workspaceChannelAccounts = [];
    } finally {
      channelAccountsRequest = null;
      lastRenderKey = "";
      renderAgentSwitcher();
      renderInspector();
    }
  })();
  return channelAccountsRequest;
}

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
  registerSessionViewTemplate(source);
  const legacyApp = source.querySelector(".app");
  const sidebar = legacyApp?.querySelector("#sidebar");
  const chat = legacyApp?.querySelector(".chat");
  const attachmentImageDialog = legacyApp?.querySelector("#attachmentImageDialog");
  const menu = source.querySelector("#newChatMenu");
  if (!legacyApp || !sidebar || !chat || !attachmentImageDialog || !menu) {
    throw new Error("工作台页面结构不完整");
  }

  sidebar.querySelector(".sidebar-footer")?.remove();
  const sidebarHeader = sidebar.querySelector(".sidebar-header");
  const agentSelect = document.getElementById("agentSelect");
  const agentSwitcher = document.createElement("div");
  agentSwitcher.className = "agent-switcher";
  agentSwitcher.innerHTML = `
    <button type="button" class="icon-action refresh-agent" id="refreshWorkspaceAgentsBtn" title="刷新 Agent" aria-label="刷新 Agent">
      <i data-lucide="refresh-cw"></i>
    </button>`;
  if (agentSelect) {
    const selectField = document.createElement("div");
    selectField.className = "agent-select-field";
    selectField.innerHTML = `
      <button type="button" class="agent-select-trigger" id="workspaceAgentSelectTrigger" aria-haspopup="listbox" aria-expanded="false">
        <span class="agent-select-icon"><i data-lucide="bot" aria-hidden="true"></i></span>
        <span class="agent-select-label">加载中…</span>
        <i class="agent-select-chevron" data-lucide="chevron-down" aria-hidden="true"></i>
      </button>
      <div class="agent-select-menu hidden" id="workspaceAgentSelectMenu" role="listbox" aria-label="切换测试 Agent"></div>`;
    agentSelect.setAttribute("aria-label", "切换测试 Agent");
    agentSelect.setAttribute("aria-hidden", "true");
    agentSelect.tabIndex = -1;
    agentSelect.hidden = true;
    agentSelect.classList.add("workspace-native-agent-select");
    selectField.append(agentSelect);
    agentSwitcher.insertBefore(selectField, agentSwitcher.firstChild);
  }
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
    search.innerHTML = '<i data-lucide="search" aria-hidden="true"></i><input autocomplete="off" type="search" id="sessionSearchInput" maxlength="256" placeholder="搜索会话名称或 ID" aria-label="搜索当前 Agent 会话：名称模糊匹配，会话 ID 精确匹配" title="名称模糊匹配，完整会话 ID 精确匹配；仅搜索当前 Agent 的本人会话" />';
    const newChatButton = document.createElement("button");
    newChatButton.type = "button";
    newChatButton.id = "newChatBtn";
    newChatButton.className = "icon-action session-new-action";
    newChatButton.dataset.consoleDirectNewChat = "true";
    newChatButton.title = "为当前 Agent 新建对话";
    newChatButton.setAttribute("aria-label", "为当前 Agent 新建对话");
    newChatButton.innerHTML = '<i data-lucide="message-circle-plus"></i><span>新建</span>';
    toolbar.append(search, newChatButton);
    sessionSection.insertBefore(toolbar, sessionSection.querySelector("#sessionList"));
  }

  const sessionResizer = document.createElement("div");
  sessionResizer.id = "sessionPaneResizer";
  sessionResizer.className = "workspace-resizer workspace-resizer-session";
  sessionResizer.dataset.resizePane = "session";
  sessionResizer.tabIndex = 0;
  sessionResizer.setAttribute("role", "separator");
  sessionResizer.setAttribute("aria-label", "调整会话列表宽度");
  sessionResizer.setAttribute("aria-orientation", "vertical");
  sessionResizer.setAttribute("aria-valuemin", "220");
  sessionResizer.setAttribute("aria-valuemax", "420");
  sessionResizer.setAttribute("aria-valuenow", "300");

  const host = document.getElementById("workspaceLegacyHost");
  host.replaceChildren(sidebar, sessionResizer, chat, attachmentImageDialog);
}










function workspaceAgentChannelBadge(agentId) {
  const accounts = workspaceChannelAccounts.filter((account) => account.agent_id === agentId);
  if (!accounts.length) return "";
  const title = escapeHtml(`已配置渠道：${accounts.map((account) => account.name).join("、")}`);
  return `<span class="workspace-agent-channel" title="${title}" aria-label="${title}"><i data-lucide="rss" aria-hidden="true"></i></span>`;
}

function renderAgentSwitcher(state = window.DeepAgentConsoleChat?.state) {
  if (!state) return;
  const select = document.getElementById("agentSelect");
  const field = select?.closest(".agent-select-field");
  const trigger = document.getElementById("workspaceAgentSelectTrigger");
  const labelNode = trigger?.querySelector(".agent-select-label");
  const menu = document.getElementById("workspaceAgentSelectMenu");
  if (!select || !field || !trigger || !labelNode || !menu) return;
  const label = state.agents.length ? currentAgentName(state) : "无可用 Agent";
  field.title = `切换测试 Agent：${label}`;
  labelNode.innerHTML = `${escapeHtml(label)}${workspaceAgentChannelBadge(state.agentId)}`;
  trigger.disabled = !state.agents.length;
  menu.innerHTML = state.agents.map((agent) => {
    const selected = agent.id === state.agentId;
    return `<button type="button" class="agent-select-option${selected ? " selected" : ""}" role="option" aria-selected="${selected}" data-switch-agent-id="${escapeHtml(agent.id)}">
      <span class="agent-select-option-icon"><i data-lucide="bot" aria-hidden="true"></i></span>
      <span>${escapeHtml(agent.name || agent.id)}${workspaceAgentChannelBadge(agent.id)}</span>
      ${selected ? '<i class="agent-select-check" data-lucide="check" aria-hidden="true"></i>' : ""}
    </button>`;
  }).join("");
  window.lucide?.createIcons();
}

async function refreshWorkspaceAgents(button) {
  if (button) {
    button.disabled = true;
    button.classList.add("loading");
  }
  try {
    await Promise.all([window.DeepAgentConsoleChat?.refreshAgentWorkspace(), refreshChannelAccounts()]);
    lastRenderKey = "";
    renderAgentSwitcher();
    renderInspector();
  } finally {
    if (button) {
      button.disabled = false;
      button.classList.remove("loading");
    }
  }
}

function clampWorkspacePaneWidth(value, limits) {
  return Math.max(limits.min, Math.min(limits.max, Math.round(Number(value) || limits.initial)));
}

function finiteLayoutLength(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, number) : fallback;
}

export function calculateInspectorPaneWidth(value, layout = {}) {
  const minimum = finiteLayoutLength(layout.min, WORKSPACE_PANE_LIMITS.inspector.min);
  const maximum = Math.max(minimum, finiteLayoutLength(layout.max, WORKSPACE_PANE_LIMITS.inspector.max));
  const available = Math.floor(
    finiteLayoutLength(layout.gridWidth)
      - finiteLayoutLength(layout.horizontalPadding)
      - finiteLayoutLength(layout.sessionWidth)
      - finiteLayoutLength(layout.resizerWidth) * finiteLayoutLength(layout.resizerCount)
      - finiteLayoutLength(layout.mainMinWidth),
  );
  const effectiveMaximum = Math.max(minimum, Math.min(maximum, available));
  const requested = Math.round(Number(value) || finiteLayoutLength(layout.initial, WORKSPACE_PANE_LIMITS.inspector.initial));
  return Math.max(minimum, Math.min(effectiveMaximum, requested));
}

function readWorkspaceCssLength(styles, property, fallback) {
  const value = Number.parseFloat(styles.getPropertyValue(property));
  return Number.isFinite(value) ? value : fallback;
}

function inspectorPaneLayout(grid) {
  const styles = getComputedStyle(grid);
  const resizerWidth = readWorkspaceCssLength(styles, "--console-resizer-width", 10);
  return {
    ...WORKSPACE_PANE_LIMITS.inspector,
    gridWidth: grid.clientWidth,
    horizontalPadding: readWorkspaceCssLength(styles, "padding-left", 0)
      + readWorkspaceCssLength(styles, "padding-right", 0),
    sessionWidth: readWorkspacePaneWidth(grid, "session"),
    resizerWidth,
    resizerCount: 2,
    mainMinWidth: readWorkspaceCssLength(styles, "--console-workspace-main-min-width", 520),
    min: readWorkspaceCssLength(styles, "--console-inspector-min-width", WORKSPACE_PANE_LIMITS.inspector.min),
  };
}

function readWorkspacePaneWidth(grid, pane) {
  const limits = WORKSPACE_PANE_LIMITS[pane];
  if (!limits) return 0;
  const value = getComputedStyle(grid).getPropertyValue(limits.variable);
  return clampWorkspacePaneWidth(Number.parseFloat(value), limits);
}

function writeWorkspacePaneWidth(grid, handle, pane, value) {
  const limits = WORKSPACE_PANE_LIMITS[pane];
  if (!limits) return;
  const width = pane === "inspector"
    ? calculateInspectorPaneWidth(value, inspectorPaneLayout(grid))
    : clampWorkspacePaneWidth(value, limits);
  grid.style.setProperty(limits.variable, `${width}px`);
  if (pane === "inspector") {
    const inspectorLimits = inspectorPaneLayout(grid);
    const maximum = calculateInspectorPaneWidth(inspectorLimits.max, inspectorLimits);
    handle.setAttribute("aria-valuemin", String(inspectorLimits.min));
    handle.setAttribute("aria-valuemax", String(maximum));
  }
  handle.setAttribute("aria-valuenow", String(width));
}

function resetWorkspacePaneWidth(grid, handle, pane) {
  const limits = WORKSPACE_PANE_LIMITS[pane];
  if (!limits) return;
  grid.style.removeProperty(limits.variable);
  writeWorkspacePaneWidth(grid, handle, pane, limits.initial);
}

function constrainWorkspaceInspector() {
  const grid = document.getElementById("workspaceGrid");
  const handle = document.getElementById("inspectorPaneResizer");
  if (!grid || !handle || grid.clientWidth <= 0) return;
  writeWorkspacePaneWidth(grid, handle, "inspector", readWorkspacePaneWidth(grid, "inspector"));
}

function bindWorkspaceResizer(handle) {
  const pane = handle?.dataset.resizePane;
  const limits = WORKSPACE_PANE_LIMITS[pane];
  const grid = document.getElementById("workspaceGrid");
  if (!handle || !limits || !grid) return;

  let startX = 0;
  let startWidth = limits.initial;
  const pointerDirection = pane === "inspector" ? -1 : 1;

  const finishDrag = (event) => {
    if (!handle.classList.contains("dragging")) return;
    handle.classList.remove("dragging");
    document.body.classList.remove("workspace-pane-resizing");
    if (event?.pointerId !== undefined && handle.hasPointerCapture?.(event.pointerId)) {
      handle.releasePointerCapture(event.pointerId);
    }
  };

  handle.addEventListener("pointerdown", (event) => {
    if (getComputedStyle(handle).display === "none") return;
    startX = event.clientX;
    startWidth = readWorkspacePaneWidth(grid, pane);
    handle.classList.add("dragging");
    document.body.classList.add("workspace-pane-resizing");
    handle.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  });
  handle.addEventListener("pointermove", (event) => {
    if (!handle.classList.contains("dragging")) return;
    writeWorkspacePaneWidth(grid, handle, pane, startWidth + (event.clientX - startX) * pointerDirection);
  });
  handle.addEventListener("pointerup", finishDrag);
  handle.addEventListener("pointercancel", finishDrag);
  handle.addEventListener("dblclick", () => resetWorkspacePaneWidth(grid, handle, pane));
  handle.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Home") {
      resetWorkspacePaneWidth(grid, handle, pane);
      return;
    }
    const coordinateDelta = event.key === "ArrowRight" ? 12 : -12;
    writeWorkspacePaneWidth(
      grid,
      handle,
      pane,
      readWorkspacePaneWidth(grid, pane) + coordinateDelta * pointerDirection,
    );
  });
}

function bindWorkspaceResizers() {
  document.querySelectorAll("[data-resize-pane]").forEach(bindWorkspaceResizer);
}

function syncInspectorToggle() {
  const grid = document.getElementById("workspaceGrid");
  const inspector = document.getElementById("inspectorPane");
  const button = document.getElementById("toggleInspectorPaneBtn");
  if (!grid || !inspector || !button) return;
  const visible = !grid.classList.contains("inspector-hidden");
  button.classList.toggle("active", visible);
  button.setAttribute("aria-pressed", String(visible));
  button.title = visible ? "隐藏详情" : "显示详情";
}

function syncWorkspaceLayout() {
  syncInspectorToggle();
  constrainWorkspaceInspector();
}

let finishInspectorTransition = null;

function toggleInspectorPane() {
  const grid = document.getElementById("workspaceGrid");
  const inspector = document.getElementById("inspectorPane");
  if (!grid || !inspector) return;
  finishInspectorTransition?.();
  if (!grid.classList.contains("inspector-hidden") && inspectorActiveTab === "context") {
    inspectorActiveTab = "session";
    lastRenderKey = "";
    renderInspector();
    return;
  }
  const before = getComputedStyle(grid).gridTemplateColumns;
  const header = grid.querySelector(".chat-header");
  const headerWidth = header.getBoundingClientRect().width;
  const opening = grid.classList.contains("inspector-hidden");
  grid.classList.toggle("inspector-hidden");
  syncWorkspaceLayout();
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const after = getComputedStyle(grid).gridTemplateColumns;
  const handle = document.getElementById("inspectorPaneResizer");
  inspector.style.display = "flex";
  inspector.inert = !opening;
  handle.style.display = "block";
  header.style.width = `${headerWidth}px`;
  const motion = grid.animate([
    { gridTemplateColumns: opening ? `${before} 0px 0px` : before },
    { gridTemplateColumns: opening ? after : `${after} 0px 0px` },
  ], { duration: 180, easing: "ease-in-out" });
  const fade = inspector.animate([{ opacity: opening ? 0 : 1 }, { opacity: opening ? 1 : 0 }], { duration: 160 });
  const finish = () => {
    if (finishInspectorTransition !== finish) return;
    finishInspectorTransition = null;
    motion.cancel();
    fade.cancel();
    inspector.style.removeProperty("display");
    inspector.inert = false;
    handle.style.removeProperty("display");
    header.style.removeProperty("width");
  };
  finishInspectorTransition = finish;
  motion.finished.then(finish, finish);
}

async function selectWorkspaceAgent(agentId) {
  const api = window.DeepAgentConsoleChat;
  if (!api || !agentId) return;
  closeAgentSelectMenu();
  await api.selectAgent(agentId);
  lastRenderKey = "";
  renderAgentSwitcher();
  renderInspector();
}

function closeAgentSelectMenu() {
  const trigger = document.getElementById("workspaceAgentSelectTrigger");
  const menu = document.getElementById("workspaceAgentSelectMenu");
  trigger?.setAttribute("aria-expanded", "false");
  menu?.classList.add("hidden");
}

function toggleAgentSelectMenu() {
  const trigger = document.getElementById("workspaceAgentSelectTrigger");
  const menu = document.getElementById("workspaceAgentSelectMenu");
  if (!trigger || !menu || trigger.disabled) return;
  const open = menu.classList.contains("hidden");
  closeAgentSelectMenu();
  if (!open) return;
  trigger.setAttribute("aria-expanded", "true");
  menu.classList.remove("hidden");
  menu.querySelector('[aria-selected="true"]')?.focus();
}

function currentAgentName(state) {
  return state.agents.find((agent) => agent.id === state.agentId)?.name || null;
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
  grid.classList.remove("inspector-hidden");
  syncWorkspaceLayout();
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
  inspectorActiveTab = "context";
  openInspectorPane();
  lastRenderKey = "";
  syncContextReferenceButtons();
  renderInspector();
}

function renderContextReferenceInspector(state) {
  if (selectedContextReference && selectedContextReference.sessionId !== (state.sessionId || null)) {
    selectedContextReference = null;
    syncContextReferenceButtons();
  }
  return renderContextReferenceContent(selectedContextReference?.reference);
}

function setHistoryLoading(active) {
  const grid = document.getElementById("workspaceGrid");
  if (!grid) return;
  if (!grid.querySelector(".session-loading")) {
    grid.insertAdjacentHTML("beforeend", sessionLoadingHtml());
  }
  setSessionLoading(grid, grid.querySelectorAll("main.chat, #inspectorPane, #inspectorPaneResizer"), active);
  if (!active) renderInspector();
}

function setInspectorTab(tab) {
  if (!["session", "channel", "run", "context"].includes(tab)) return;
  inspectorActiveTab = tab;
  lastRenderKey = "";
  renderInspector();
}

function bindInspectorTabs(host) {
  host.querySelectorAll("[data-inspector-tab]").forEach((button) => {
    button.addEventListener("click", () => setInspectorTab(button.dataset.inspectorTab));
  });
  host.querySelectorAll("[data-inspector-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await copyTextToClipboard(button.dataset.inspectorCopy);
        window.DeepAgentConsole?.toast("已复制");
      } catch {
        window.DeepAgentConsole?.toast("复制失败，请手动选择 ID 复制", true);
      }
    });
  });
}







function renderInspector() {
  const api = window.DeepAgentConsoleChat;
  const host = document.getElementById("inspectorContent");
  if (!api || !host || document.querySelector("main.chat")?.classList.contains("trajectory-active")) return;
  const state = api.state;
  const activeSession = state.sessions.find((session) => session.id === state.sessionId && (session.agentId || "") === (state.agentId || "")) || {};
  const run = state.lastRunMetrics || {};
  const saved = savedRunPresentation({ model_name: run.model_name, run_id: run.run_id, runs: [{ ...run, usage: state.lastRunUsage || run.usage }], latest_status: run.status });
  const historicalModel = saved.runModel || (run.model_id
    ? state.llmModels.find(item => item.id === run.model_id || item.model === run.model_id) : null);
  const requestedModel = !state.sessionId || (state.streaming && !state.observingRun)
    ? state.llmModels.find(item => item.id === state.llmModelId) : null;
  const persistedModel = run.model_id ? state.llmModels.find(item => item.id === run.model_id) : null;
  const historicalProvider = persistedModel?.provider || persistedModel?.llm_provider
    || historicalModel?.provider || historicalModel?.llm_provider
    || (run.provider_name ? { name: run.provider_name } : null);
  const model = historicalModel || requestedModel || (activeSession.modelName ? { name: activeSession.modelName } : null);
  const channelOrigin = channelOriginPresentation(activeSession);
  const agentChannels = workspaceChannelAccounts.filter((account) => account.agent_id === state.agentId);
  if (!channelOrigin && !agentChannels.length && inspectorActiveTab === "channel") inspectorActiveTab = "session";
  host.innerHTML = renderSessionInspector({
    state, activeSession: state.sessionId ? activeSession : { ...activeSession, turnCount: 0, messageCount: 0, attachmentCount: 0 }, run, channelOrigin, agentChannels,
    model,
    modelProvider: historicalModel
      ? historicalProvider
      : requestedModel?.provider || requestedModel?.llm_provider || null,
    runModel: historicalModel,
    context: { language: activeSession.language },
    identity: window.DeepAgentConsoleContext?.identity || {},
    contextStatus: state.contextStatus || {}, usage: saved.usage,
    sessionCache: state.sessionCache || {}, runCache: state.runCache || run.cache_usage || {},
    agentName: currentAgentName(state), activeTab: inspectorActiveTab,
    contextContent: renderContextReferenceInspector(state),
    statusText: state.awaitingHitl ? "待操作" : state.streaming ? "进行中" : sessionStatusLabels[run.status] || (state.sessionId ? "未开始" : "空闲"),
    statusClass: state.streaming ? "running" : state.awaitingHitl ? "waiting" : "idle",
  });
  bindInspectorTabs(host);
  window.lucide?.createIcons();
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
      observingRun: api.state.observingRun,
      awaitingHitl: api.state.awaitingHitl,
      attachments: api.state.pendingAttachments.length,
      contextStatus: api.state.contextStatus,
      lastRunUsage: api.state.lastRunUsage,
      lastRunMetrics: api.state.lastRunMetrics,
      sessionCache: api.state.sessionCache,
      runCache: api.state.runCache,
      identity: window.DeepAgentConsoleContext?.identity,
      conversationId: api.state.conversationId,
      sessionMetrics: api.state.sessions
        .filter((session) => session.id === api.state.sessionId && (session.agentId || "") === (api.state.agentId || ""))
        .map((session) => [session.turnCount, session.messageCount, session.attachmentCount, session.channel, session.source_type, session.source_id, session.modelName, session.appId, session.spaceId]),
    });
    if (key === lastRenderKey) return;
    lastRenderKey = key;
    renderAgentSwitcher();
    renderInspector();
  }, 350);
}

function bindWorkspaceControls() {
  document.getElementById("workspaceAgentSelectTrigger")?.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleAgentSelectMenu();
  });
  document.getElementById("workspaceAgentSelectMenu")?.addEventListener("click", (event) => {
    const option = event.target.closest("[data-switch-agent-id]");
    if (!option) return;
    void selectWorkspaceAgent(option.dataset.switchAgentId);
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".agent-select-field")) closeAgentSelectMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeAgentSelectMenu();
  });
  document.getElementById("refreshWorkspaceAgentsBtn")?.addEventListener("click", (event) => {
    void refreshWorkspaceAgents(event.currentTarget);
  });
  document.getElementById("toggleInspectorPaneBtn")?.addEventListener("click", toggleInspectorPane);
}

window.DeepAgentConsoleWorkspace = {
  setHistoryLoading,
  refreshChannelAccounts,
  isContextReferenceSelected,
  openInspectorPane,
  renderInspector,
  toggleContextReferenceInspector,
};

export async function initWorkspace() {
  if (initialized) return;
  initialized = true;
  const source = await fetchLegacyPage();
  installLegacyDom(source);
  window.DeepAgentConsoleContext?.render?.();
  await import(`./chat.js?v=${WORKSPACE_ASSET_VERSION}`);
  bindWorkspaceControls();
  bindWorkspaceResizers();
  window.addEventListener("resize", syncWorkspaceLayout);
  renderAgentSwitcher();
  renderInspector();
  startStateMonitor();
  void refreshChannelAccounts();
  document.getElementById("workspaceLoading")?.classList.add("hidden");
  document.getElementById("workspaceGrid")?.classList.remove("hidden");
  syncWorkspaceLayout();
  window.lucide?.createIcons();
}
