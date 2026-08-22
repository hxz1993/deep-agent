const WORKSPACE_ASSET_VERSION = "20260822-3";

const WORKSPACE_PANE_LIMITS = {
  agent: { min: 150, max: 280, initial: 188, variable: "--console-agent-width" },
  session: { min: 220, max: 420, initial: 300, variable: "--console-session-width" },
  inspector: { min: 260, max: 460, initial: 320, variable: "--console-inspector-width" },
};

let initialized = false;
let lastRenderKey = "";
let contextTestCatalog = null;
let contextTestCatalogAgentId = "";
let contextTestRequestAgentId = "";
let selectedContextReference = null;
let inspectorActiveTab = "session";

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
  const agentSelect = document.getElementById("agentSelect");
  const agentSwitcher = document.createElement("div");
  agentSwitcher.className = "agent-switcher";
  agentSwitcher.innerHTML = `
    <button type="button" class="icon-action refresh-agent" id="refreshWorkspaceAgentsBtn" title="刷新 Agent" aria-label="刷新 Agent">
      <i data-lucide="refresh-cw"></i>
    </button>
    <button type="button" class="icon-action pin-agent" id="pinAgentPaneBtn" title="固定 Agent 列表到左侧" aria-label="固定 Agent 列表到左侧" aria-pressed="false">
      <i data-lucide="pin"></i>
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
  host.replaceChildren(sidebar, sessionResizer, chat);
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

function contextBudgetPercent(value, total) {
  const amount = Number(value);
  const capacity = Number(total);
  if (!Number.isFinite(amount) || !Number.isFinite(capacity) || capacity <= 0) return null;
  return Math.max(0, Math.min(100, (amount / capacity) * 100));
}

function finiteTokenValue(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : 0;
}

function renderContextBudgetOverview(contextStatus) {
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
      void selectWorkspaceAgent(button.dataset.agentId);
    });
  });
}

function renderAgentSwitcher(state) {
  const select = document.getElementById("agentSelect");
  const field = select?.closest(".agent-select-field");
  const trigger = document.getElementById("workspaceAgentSelectTrigger");
  const labelNode = trigger?.querySelector(".agent-select-label");
  const menu = document.getElementById("workspaceAgentSelectMenu");
  if (!select || !field || !trigger || !labelNode || !menu) return;
  const label = state.agents.length ? currentAgentName(state) : "无可用 Agent";
  field.title = `切换测试 Agent：${label}`;
  labelNode.textContent = label;
  trigger.disabled = !!state.streaming || !state.agents.length;
  menu.innerHTML = state.agents.map((agent) => {
    const selected = agent.id === state.agentId;
    return `<button type="button" class="agent-select-option${selected ? " selected" : ""}" role="option" aria-selected="${selected}" data-switch-agent-id="${escapeHtml(agent.id)}">
      <span class="agent-select-option-icon"><i data-lucide="bot" aria-hidden="true"></i></span>
      <span>${escapeHtml(agent.name || agent.id)}</span>
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
    await window.DeepAgentConsoleChat?.refreshAgentWorkspace();
    lastRenderKey = "";
    renderAgents();
    renderInspector();
  } finally {
    if (button) {
      button.disabled = false;
      button.classList.remove("loading");
    }
  }
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
}

function clampWorkspacePaneWidth(value, limits) {
  return Math.max(limits.min, Math.min(limits.max, Math.round(Number(value) || limits.initial)));
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
  const width = clampWorkspacePaneWidth(value, limits);
  grid.style.setProperty(limits.variable, `${width}px`);
  handle.setAttribute("aria-valuenow", String(width));
}

function resetWorkspacePaneWidth(grid, handle, pane) {
  const limits = WORKSPACE_PANE_LIMITS[pane];
  if (!limits) return;
  grid.style.removeProperty(limits.variable);
  handle.setAttribute("aria-valuenow", String(limits.initial));
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
  button.title = visible ? "隐藏会话详情" : "显示会话详情";
}

function toggleInspectorPane() {
  const grid = document.getElementById("workspaceGrid");
  const inspector = document.getElementById("inspectorPane");
  if (!grid || !inspector) return;
  if (!grid.classList.contains("inspector-hidden") && inspectorActiveTab === "context") {
    inspectorActiveTab = "session";
    lastRenderKey = "";
    renderInspector();
    return;
  }
  grid.classList.toggle("inspector-hidden");
  syncInspectorToggle();
}

async function selectWorkspaceAgent(agentId) {
  const api = window.DeepAgentConsoleChat;
  if (!api || !agentId) return;
  closeAgentSelectMenu();
  await api.selectAgent(agentId);
  lastRenderKey = "";
  renderAgents();
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
  grid.classList.remove("inspector-hidden");
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
  inspectorActiveTab = "context";
  openInspectorPane();
  lastRenderKey = "";
  syncContextReferenceButtons();
  renderInspector();
}

function renderContextReferenceInspector(state) {
  if (!selectedContextReference) {
    return `<div class="inspector-tab-empty">
      <i data-lucide="braces"></i>
      <strong>尚未选择上下文引用</strong>
      <span>点击对话中上下文标签旁的信息图标后，可在这里查看只读 JSON。</span>
    </div>`;
  }
  if (selectedContextReference.sessionId !== (state.sessionId || null)) {
    selectedContextReference = null;
    syncContextReferenceButtons();
    return renderContextReferenceInspector(state);
  }
  const reference = selectedContextReference.reference;
  const rawJson = JSON.stringify(reference, null, 2);
  return `<section class="inspector-section context-reference-inspector">
      <header><strong>上下文引用</strong><span>只读 JSON</span></header>
      <div class="inspector-facts compact">
        <div><span>名称</span><strong>${escapeHtml(reference.label || "—")}</strong></div>
        <div><span>Key</span><strong class="mono">${escapeHtml(reference.key || "—")}</strong></div>
        <div><span>类型</span><strong class="mono">${escapeHtml(reference.type || "—")}</strong></div>
        <div><span>版本</span><strong>${escapeHtml(reference.schema_version || "—")}</strong></div>
      </div>
      <pre class="inspector-json" aria-label="上下文引用 JSON">${escapeHtml(rawJson)}</pre>
      <p class="inspector-help">这是会话历史保存的完整上下文引用；metadata 不进入模型输入。</p>
    </section>`;
}

function setInspectorTab(tab) {
  if (!["session", "run", "context"].includes(tab)) return;
  inspectorActiveTab = tab;
  lastRenderKey = "";
  renderInspector();
}

function bindInspectorTabs(host) {
  host.querySelectorAll("[data-inspector-tab]").forEach((button) => {
    button.addEventListener("click", () => setInspectorTab(button.dataset.inspectorTab));
  });
}

function inspectorFact(label, value, options = {}) {
  const classes = [options.mono ? "mono" : "", options.wide ? "wide" : ""].filter(Boolean).join(" ");
  return `<div class="${classes}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "—")}</strong></div>`;
}

function inspectorFactHtml(label, valueHtml, options = {}) {
  const classes = [options.mono ? "mono" : "", options.wide ? "wide" : ""].filter(Boolean).join(" ");
  return `<div class="${classes}"><span>${escapeHtml(label)}</span><strong>${valueHtml}</strong></div>`;
}

function renderRunUsageOverview(run, usage, contextStatus) {
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

function renderInspector() {
  const api = window.DeepAgentConsoleChat;
  const host = document.getElementById("inspectorContent");
  if (!api || !host) return;
  const state = api.state;

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
  const runModel = state.llmModels.find((item) => item.id === run.model_id || item.model === run.model_id);
  const compactionState = compaction.triggered
    ? strategyLabels[compaction.strategy] || compaction.strategy
    : contextStatus.compaction_enabled
      ? "未触发"
      : "未启用";
  const estimatorLabel = [contextStatus.estimate_scope, contextStatus.estimator_source].filter(Boolean).join(" · ") || "—";
  const compactionContent = `<section class="inspector-section">
    <header><strong>压缩与 Checkpoint</strong><span>${escapeHtml(compactionState)}</span></header>
    <div class="checkpoint-comparison">
      <span><small>压缩前</small><strong>${escapeHtml(formatTokens(compaction.estimated_tokens_before))}</strong></span>
      <i data-lucide="arrow-right"></i>
      <span><small>压缩后</small><strong>${escapeHtml(formatTokens(compaction.estimated_tokens_after))}</strong></span>
    </div>
    <div class="inspector-facts compact">
      ${inspectorFact("Checkpoint", contextStatus.checkpoint_exists ? "已生成" : "暂无")}
      ${inspectorFact("Checkpoint Token", formatTokens(contextStatus.checkpoint_estimated_tokens))}
      ${inspectorFact("估算方式", estimatorLabel)}
    </div>
  </section>`;
  const agentName = currentAgentName(state);
  const sessionCounts = `<span class="inspector-inline-metrics">
    <span title="消息"><i data-lucide="message-square"></i>${escapeHtml(String(contextStatus.raw_message_count ?? state.modelMessageCount ?? 0))}</span>
    <span title="待发附件"><i data-lucide="paperclip"></i>${escapeHtml(String(state.pendingAttachments.length))}</span>
  </span>`;
  const agentIdentity = `<span class="inspector-stacked-value"><span>${escapeHtml(agentName)}</span><code>${escapeHtml(state.agentId || "—")}</code></span>`;
  const requestScope = `<span class="inspector-scope-value"><code>${escapeHtml(context.app || "继承")}</code><span>/</span><code>${escapeHtml(context.space || "继承")}</code></span>`;
  const sessionContent = `<section class="inspector-section inspector-session-group">
    <header><strong>会话概览</strong></header>
    <div class="inspector-facts">
      ${inspectorFact("会话 ID", state.sessionId || "尚未创建", { mono: true, wide: true })}
      ${inspectorFactHtml("Agent", agentIdentity, { wide: true })}
      ${inspectorFact("模型", model?.name || model?.model || "—")}
      ${inspectorFactHtml("消息与附件", sessionCounts)}
    </div>
    <header class="inspector-subheading"><strong>请求 Header</strong><span>自动推导</span></header>
    <div class="inspector-facts compact">
      ${inspectorFactHtml("App / Space", requestScope, { wide: true })}
      ${inspectorFact("Language", context.language || "继承")}
      ${inspectorFact("User ID", identity?.user || "未认证", { mono: true, wide: true })}
    </div>
  </section>
  <section class="inspector-section inspector-budget-section">
    <header><strong>上下文预算</strong><span>阈值与预留合并展示</span></header>
    ${renderContextBudgetOverview(contextStatus)}
  </section>
  ${compactionContent}`;

  const runContent = `<section class="inspector-section">
    <header><strong>当前回合</strong><span>${run.detailed === false ? "详细指标请查看运行日志" : "当前页面最近一次"}</span></header>
    ${renderRunUsageOverview(run, usage, contextStatus)}
    <div class="inspector-facts compact run-facts">
      ${inspectorFact("终态", run.status || "—")}
      ${inspectorFact("请求", run.requests ?? usage.requests ?? "—")}
      ${inspectorFact("工具调用", run.tool_calls ?? usage.tool_calls ?? "—")}
      ${inspectorFact("Tool Search", run.tool_search_calls ?? "—")}
      ${inspectorFact("缓存读取", formatTokens(run.cache_read_tokens))}
      ${inspectorFact("缓存写入", formatTokens(run.cache_write_tokens))}
      ${inspectorFact("耗时", Number.isFinite(Number(run.duration_ms)) ? `${Number(run.duration_ms).toLocaleString("zh-CN")} ms` : "—")}
      ${inspectorFact("压缩", run.compaction || "—")}
      ${inspectorFact("Conversation ID", state.conversationId || run.conversation_id || "—", { mono: true, wide: true })}
      ${inspectorFact("Run ID", run.run_id || "—", { mono: true, wide: true })}
      ${inspectorFact("模型", runModel?.name || runModel?.model || model?.name || model?.model || run.model_id || "—", { wide: true })}
    </div>
  </section>`;

  const tabContent = inspectorActiveTab === "run"
    ? runContent
    : inspectorActiveTab === "context"
      ? renderContextReferenceInspector(state)
      : sessionContent;
  host.innerHTML = `<nav class="inspector-tabs" role="tablist" aria-label="会话详情">
      <span class="inspector-tab-buttons">
        <button type="button" role="tab" data-inspector-tab="session" aria-selected="${inspectorActiveTab === "session"}">会话</button>
        <button type="button" role="tab" data-inspector-tab="run" aria-selected="${inspectorActiveTab === "run"}">当前回合</button>
        <button type="button" role="tab" data-inspector-tab="context" aria-selected="${inspectorActiveTab === "context"}">上下文引用</button>
      </span>
      <span class="status-badge inspector-tab-status ${statusClass}">${statusText}</span>
    </nav>
    <div class="inspector-tab-panel">${tabContent}</div>`;
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
  document.getElementById("pinAgentPaneBtn")?.addEventListener("click", () => {
    const pinned = document.getElementById("workspaceGrid")?.classList.contains("agent-pinned");
    setAgentPanePinned(!pinned);
  });
  document.getElementById("unpinAgentPaneBtn")?.addEventListener("click", () => {
    setAgentPanePinned(false);
  });
  document.getElementById("agentSearchInput")?.addEventListener("input", renderAgents);
  document.getElementById("refreshAgentsBtn")?.addEventListener("click", (event) => {
    void refreshWorkspaceAgents(event.currentTarget);
  });
  document.getElementById("contextTestChatBtn")?.addEventListener("click", openContextTestDialog);
  document.getElementById("createContextTestBtn")?.addEventListener("click", createContextTestSession);
  document.getElementById("toggleInspectorPaneBtn")?.addEventListener("click", toggleInspectorPane);
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
  bindWorkspaceResizers();
  setAgentPanePinned(false);
  syncInspectorToggle();
  window.addEventListener("resize", syncInspectorToggle);
  renderAgents();
  renderInspector();
  startStateMonitor();
  document.getElementById("workspaceLoading")?.classList.add("hidden");
  document.getElementById("workspaceGrid")?.classList.remove("hidden");
  window.lucide?.createIcons();
}
