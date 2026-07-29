import { buildChoiceResult, normalizeOption, normalizeStructuredFields, validateUiToolCall } from "./hitl.js?v=20260728-2";
import { parseSseBlock, readSse } from "./protocol.js?v=20260720-1";
import { formatSessionIdSubtitle } from "./session-display.js?v=20260728-2";

/* ------------------------------------------------------------------ *
 * deep-agent 前端 —— 适配 FastAPI /api/v1/chat/stream (SSE over POST)
 * 协议详见 docs/guides/sse-protocol.md
 *
 * 后端 SSE 事件 (event: <type>, data: <json>):
 *   session      -> { session_id, agent_id }
 *   text         -> { type:"text", delta }
 *   thinking     -> { type:"thinking", delta }
 *   tool_call    -> { type:"tool_call", tool, args, tool_call_id, widget?, metadata? }
 *   tool_result  -> { type:"tool_result", tool, result, tool_call_id }
 *   final        -> { type:"final", status, output, usage, deferred? }
 *   cancelled    -> { type:"cancelled", status, model_message_count?, user_branch_point? }
 *   error        -> { type:"error", message }
 * ------------------------------------------------------------------ */

const DEFAULT_BASE_URL = defaultApiBaseUrl();

const BUILTIN_UI_TOOLS = new Set(["builtin__confirm", "builtin__choice"]);
const LEGACY_BUILTIN_UI_TOOLS = new Set(["confirm_action", "ask_user_choice"]);
/** @type {Map<string, object>} */
const uiToolsByName = new Map();

function mergeUiToolCall(call) {
  const def = uiToolsByName.get(call.tool);
  if (!def) return call;
  return {
    ...call,
    widget: call.widget || def.widget || null,
    metadata: call.metadata ?? def.metadata ?? null,
    source: call.source || def.source || null,
    parameters_json_schema: call.parameters_json_schema || def.parameters_json_schema || null,
    result_schema: call.result_schema || def.result_schema || null,
  };
}

function isUiToolCall(call) {
  if (!call?.tool) return false;
  if (BUILTIN_UI_TOOLS.has(call.tool)) return true;
  if (LEGACY_BUILTIN_UI_TOOLS.has(call.tool) && !uiToolsByName.has(call.tool)) return false;
  if (call.widget) return true;
  return uiToolsByName.has(call.tool);
}

function resolveWidgetType(call) {
  if (call.tool === "builtin__confirm" || call.widget === "confirm") return "confirm";
  if (call.tool === "builtin__choice" || call.widget === "choice") return "choice";
  if (call.widget === "text") return "text";
  if (call.widget === "date_picker") return "date_picker";
  if (["structured", "structured_form", "form"].includes(call.widget) || Array.isArray(call.args?.fields)) {
    return "structured";
  }
  if (call.widget) return "custom";
  return null;
}

async function loadUiTools(agentId) {
  try {
    const params = new URLSearchParams({ enabled: "true" });
    if (agentId) params.set("agent_id", agentId);
    const r = await daFetch(serviceUrl(`api/v1/ui-tools?${params}`));
    if (!r.ok) return;
    const data = await r.json();
    const rows = Array.isArray(data?.items) ? data.items : [];
    uiToolsByName.clear();
    for (const tool of rows) {
      uiToolsByName.set(tool.name, tool);
    }
  } catch (e) {
    console.warn("[deep-agent] 加载 UI 工具失败", e);
  }
}

const state = {
  baseUrl: DEFAULT_BASE_URL,
  agentId: localStorage.getItem("da_console_agent_id_v1") || "",
  agents: [],
  llmModels: [],
  uploadCapabilities: { kinds: [], labels: {} },
  llmModelId: "",
  sessionModelIds: {},
  sessionId: null,
  sessions: [], // [{id, agentId, agentName, title}] — 仅来自 GET /api/v1/sessions
  streaming: false,
  awaitingHitl: false,
  abortController: null,
  streamCancelled: false,
  skipHistoryRefresh: false,
  branchFrom: null,
  modelMessageCount: 0,
  lastUserBranchPoint: null,
  pendingAttachments: [],
  pendingTitleRefresh: false,
  titlePollGeneration: 0,
};

const TITLE_POLL_TIMEOUT_MS = 30000;
const TITLE_POLL_INTERVAL_MS = 500;

const LLM_MODALITY_LABELS = {
  text: "文本",
  image: "图像",
  audio: "音频",
  video: "视频",
  pdf: "PDF（原生）",
};

const LLM_MODALITY_ICONS = {
  text: "T",
  image: "🖼",
  audio: "🔊",
  video: "🎬",
  pdf: "📄",
};

/** LLM-native modalities only (Admin ``input_modalities``). */
const ALL_LLM_MODALITIES = ["text", "image", "audio", "video", "pdf"];

const UPLOAD_KIND_ORDER = ["image", "text_file", "pdf_text", "docx_text", "xlsx_text"];

const UPLOAD_KIND_ICONS = {
  image: "🖼",
  text_file: "📄",
  pdf_text: "📄",
  docx_text: "📝",
  xlsx_text: "📊",
};

/* ----------------------------- DOM ----------------------------- */
const $ = (id) => document.getElementById(id);
const el = {
  sidebar: $("sidebar"),
  messages: $("messages"),
  emptyState: $("emptyState"),
  input: $("input"),
  sendBtn: $("sendBtn"),
  stopBtn: $("stopBtn"),
  sessionList: $("sessionList"),
  capList: $("capList"),
  agentSelect: $("agentSelect"),
  connStatus: $("connStatus"),
  sessionBadge: $("sessionBadge"),
  chatTitle: $("chatTitle"),
  newChatMenu: $("newChatMenu"),
  newChatBtnWide: $("newChatBtnWide"),
  usageInfo: $("usageInfo"),
  attachBtn: $("attachBtn"),
  fileInput: $("fileInput"),
  attachmentPreview: $("attachmentPreview"),
  composerMeta: $("composerMeta"),
  modelSelect: $("modelSelect"),
  composerModelModalities: $("composerModelModalities"),
  composerUploadCapabilities: $("composerUploadCapabilities"),
};

/* --------------------------- 初始化 --------------------------- */
function updateAdminLink() {
  const link = $("adminLink");
  if (link) link.href = "#/configuration";
}

/** Normalize local / API session records to display id + agentId. */
function normalizeSession(session) {
  let id = session.id;
  let agentId = session.agentId || "";
  const colon = id.indexOf(":");
  if (colon > 0) {
    const prefix = id.slice(0, colon);
    const suffix = id.slice(colon + 1);
    if (!agentId || agentId === prefix) {
      agentId = prefix;
      id = suffix;
    }
  }
  return { ...session, id, agentId };
}

function sessionApiUrl(session, suffix = "") {
  const s = normalizeSession(session);
  const params = new URLSearchParams();
  if (s.agentId) params.set("agent_id", s.agentId);
  const qs = params.toString();
  return serviceUrl(`api/v1/sessions/${encodeURIComponent(s.id)}${suffix}${qs ? `?${qs}` : ""}`);
}

function sessionLookupKey(session) {
  const s = normalizeSession(session);
  return `${s.agentId || ""}:${s.id}`;
}

function sessionSortTime(session) {
  return Date.parse(session.updatedAt || session.createdAt || 0) || 0;
}

function formatSessionActivity(session) {
  const value = session.updatedAt || session.createdAt;
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";

  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const activityDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diffDays = Math.round((today - activityDay) / 86400000);
  const time = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);

  if (diffDays === 0) return `今天 ${time}`;
  if (diffDays === 1) return `昨天 ${time}`;
  if (date.getFullYear() === now.getFullYear()) {
    return `${date.getMonth() + 1}月${date.getDate()}日 ${time}`;
  }
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

function formatSessionMessageCount(session) {
  const count = Number(session.messageCount);
  return Number.isFinite(count) && count > 0 ? `${count} 条消息` : "暂无消息";
}

function sortSessions() {
  state.sessions.sort((a, b) => sessionSortTime(b) - sessionSortTime(a));
}

function hideEmptyState() {
  el.emptyState.classList.add("hidden");
}

function showEmptyState() {
  el.emptyState.classList.remove("hidden");
  if (!el.messages.contains(el.emptyState)) {
    el.messages.appendChild(el.emptyState);
  }
}

function clearMessages() {
  el.messages.innerHTML = "";
}

function currentSessionSnapshot() {
  if (!state.sessionId) return null;
  const title = (el.chatTitle.textContent || "").trim();
  const displayTitle =
    title && title !== "新对话" && title !== "对话"
      ? title.slice(0, 40)
      : (lastUserText() || state.sessionId.slice(0, 8)).slice(0, 40);
  return normalizeSession({
    id: state.sessionId,
    agentId: state.agentId || "",
    agentName: agentNameById(state.agentId),
    title: displayTitle,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    messageCount: state.modelMessageCount,
  });
}

function mergeSessionsFromBackend(rows) {
  const mapped = rows.map((row) => {
    const agentId = row.agent_id || "";
    const title = (row.title || row.user_prompt || row.session_id.slice(0, 8)).slice(0, 40);
    return normalizeSession({
      id: row.session_id,
      agentId,
      agentName: row.agent_name,
      title,
      createdAt: row.created_at || null,
      updatedAt: row.updated_at || row.created_at || null,
      messageCount: Number(row.message_count) || 0,
    });
  });

  const mergedByKey = new Map(mapped.map((s) => [sessionLookupKey(s), s]));

  const current = currentSessionSnapshot();
  if (current) {
    const key = sessionLookupKey(current);
    const existing = mergedByKey.get(key);
    if (existing) {
      if (!existing.title || existing.title === existing.id.slice(0, 8)) {
        existing.title = current.title;
      }
      existing.updatedAt = existing.updatedAt || current.updatedAt;
    } else {
      mergedByKey.set(key, current);
    }
  }

  state.sessions = [...mergedByKey.values()];
  sortSessions();
}

function agentNameById(agentId) {
  const found = state.agents.find((a) => a.id === agentId);
  return found ? found.name : agentId;
}

function defaultAgentId() {
  if (!state.agents.length) return "";
  const named = state.agents.find((a) => a.name === "default");
  return named ? named.id : state.agents[0].id;
}

function ensureAgentSelected() {
  const valid = state.agentId && state.agents.some((a) => a.id === state.agentId);
  if (!valid && state.agents.length) {
    state.agentId = defaultAgentId();
    localStorage.setItem("da_console_agent_id_v1", state.agentId);
  }
  if (el.agentSelect && state.agentId) el.agentSelect.value = state.agentId;
  return !!state.agentId;
}

function generateSessionId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return Math.random().toString(16).slice(2) + Date.now().toString(16);
}

function init() {
  updateAdminLink();

  const agentFromUrl = new URLSearchParams(location.search).get("agent_id");
  if (agentFromUrl) state.agentId = agentFromUrl;

  el.agentSelect?.addEventListener("change", () => {
    state.agentId = el.agentSelect.value;
    localStorage.setItem("da_console_agent_id_v1", state.agentId);
    if (!state.sessionId) {
      state.llmModelId = defaultLlmModelId();
    }
    updateChatHeader();
    loadUiTools(state.agentId);
  });
  el.modelSelect?.addEventListener("change", () => {
    state.llmModelId = el.modelSelect.value;
    rememberSessionModel();
    updateComposerModelInfo();
  });

  $("newChatBtn").addEventListener("click", handleNewChatControl);
  el.newChatBtnWide?.addEventListener("click", (e) => {
    handleNewChatControl(e);
  });
  document.addEventListener("click", (e) => {
    if (el.newChatMenu?.classList.contains("hidden")) return;
    if (e.target.closest("#newChatMenu, #newChatBtn, #newChatBtnWide")) return;
    closeNewChatMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeNewChatMenu();
  });
  $("toggleSidebar").addEventListener("click", () => el.sidebar.classList.toggle("collapsed"));
  $("refreshCapsBtn").addEventListener("click", loadCapabilities);
  el.sendBtn.addEventListener("click", onSend);
  el.stopBtn.addEventListener("click", stopStream);

  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  });
  el.input.addEventListener("input", autoGrow);
  el.attachBtn?.addEventListener("click", () => el.fileInput?.click());
  el.fileInput?.addEventListener("change", onFilesSelected);
  el.input?.addEventListener("paste", onPaste);

  document.querySelectorAll(".suggestion").forEach((b) =>
    b.addEventListener("click", () => {
      el.input.value = b.textContent;
      autoGrow();
      onSend();
    })
  );

  // 聊天标题点击编辑
  el.chatTitle.addEventListener("click", () => {
    if (state.streaming || !state.sessionId) return;
    const currentTitle = el.chatTitle.textContent;
    const input = document.createElement("input");
    input.type = "text";
    input.className = "chat-title-input";
    input.value = currentTitle === "新对话" || currentTitle === "对话" ? "" : currentTitle;
    input.maxLength = 40;
    input.placeholder = "输入会话名称…";

    el.chatTitle.textContent = "";
    el.chatTitle.appendChild(input);
    input.focus();
    input.select();

    const finishEdit = async (save) => {
      const newTitle = save ? input.value.trim() : currentTitle;
      el.chatTitle.removeChild(input);
      if (save && newTitle && newTitle !== currentTitle) {
        el.chatTitle.textContent = newTitle;
        const session = state.sessions.find(
          (s) => s.id === state.sessionId && (s.agentId || "") === (state.agentId || "")
        );
        if (session) {
          await renameSession(session, newTitle);
        }
      } else {
        el.chatTitle.textContent = currentTitle;
      }
    };

    input.addEventListener("blur", () => finishEdit(true));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        finishEdit(true);
      } else if (e.key === "Escape") {
        e.preventDefault();
        finishEdit(false);
      }
    });
  });

  initMarkdown();
  localStorage.removeItem("da_sessions");
  renderSessions();
  syncSessionsFromBackend();
  checkHealth();
  loadCapabilities();
  loadUploadCapabilities();
  loadAgents().then(async () => {
    await loadLlmModels();
    loadUiTools(state.agentId);
    if (agentFromUrl) newChat(agentFromUrl);
    else updateAgentSelectState();
  });
}

async function syncSessionsFromBackend() {
  try {
    const r = await daFetch(serviceUrl("api/v1/sessions?limit=50"));
    if (!r.ok) return;
    const payload = await r.json();
    const rows = Array.isArray(payload?.items) ? payload.items : [];
    mergeSessionsFromBackend(rows);
    renderSessions();
  } catch (_) {
    if (state.sessionId) {
      const current = currentSessionSnapshot();
      state.sessions = current ? [current] : [];
    } else {
      state.sessions = [];
    }
    renderSessions();
  }
}

function autoGrow() {
  el.input.style.height = "auto";
  el.input.style.height = Math.min(el.input.scrollHeight, 200) + "px";
}

/* --------------------------- 会话管理 --------------------------- */
function normalizeAgentId(agentId) {
  return typeof agentId === "string" && agentId ? agentId : null;
}

function handleNewChatControl(event) {
  event.stopPropagation();
  if (event.currentTarget?.dataset.consoleDirectNewChat === "true") {
    newChat(state.agentId);
    return;
  }
  toggleNewChatMenu(event.currentTarget);
}

function closeNewChatMenu() {
  if (!el.newChatMenu) return;
  el.newChatMenu.classList.add("hidden");
  el.newChatBtnWide?.setAttribute("aria-expanded", "false");
}

function positionNewChatMenu(anchor) {
  if (!el.newChatMenu || !anchor) return;
  const rect = anchor.getBoundingClientRect();
  const menuWidth = Math.max(rect.width, 240);
  let left = rect.left;
  if (left + menuWidth > window.innerWidth - 12) {
    left = Math.max(12, window.innerWidth - menuWidth - 12);
  }
  el.newChatMenu.style.width = `${menuWidth}px`;
  el.newChatMenu.style.left = `${left}px`;
  el.newChatMenu.style.top = `${rect.bottom + 6}px`;
}

function renderNewChatMenu() {
  if (!el.newChatMenu) return;
  if (!state.agents.length) {
    el.newChatMenu.innerHTML = `
      <div class="new-chat-menu-empty">
        暂无可用 Agent。<br />
        <a href="#/configuration">前往配置中心创建</a>
      </div>`;
    return;
  }
  const items = state.agents
    .map((agent) => {
      const active = agent.id === state.agentId ? " active" : "";
      const modelLabel = agent.model?.name || agent.model?.model || "";
      return `
        <button type="button" class="new-chat-menu-item${active}" role="menuitem" data-agent-id="${escapeHtml(agent.id)}">
          <span class="new-chat-menu-name">${escapeHtml(agent.name)}</span>
          ${modelLabel ? `<span class="new-chat-menu-model">${escapeHtml(modelLabel)}</span>` : ""}
        </button>`;
    })
    .join("");
  el.newChatMenu.innerHTML = `<div class="new-chat-menu-title">选择 Agent 新建对话</div>${items}`;
  el.newChatMenu.querySelectorAll("[data-agent-id]").forEach((btn) => {
    btn.addEventListener("click", () => newChat(btn.dataset.agentId));
  });
}

function toggleNewChatMenu(anchor) {
  if (!el.newChatMenu) return;
  const isOpen = !el.newChatMenu.classList.contains("hidden");
  if (isOpen) {
    closeNewChatMenu();
    return;
  }
  renderNewChatMenu();
  positionNewChatMenu(anchor);
  el.newChatMenu.classList.remove("hidden");
  el.newChatBtnWide?.setAttribute("aria-expanded", "true");
  el.newChatMenu.querySelector(".new-chat-menu-item")?.focus();
}

function newChat(agentId) {
  const id = normalizeAgentId(agentId);
  closeNewChatMenu();
  if (state.streaming) stopStream();
  cancelTitlePoll();
  state.pendingTitleRefresh = false;
  cancelTitlePoll();
  state.pendingTitleRefresh = false;
  if (id) {
    state.agentId = id;
    localStorage.setItem("da_console_agent_id_v1", state.agentId);
    if (el.agentSelect) el.agentSelect.value = state.agentId;
  } else {
    ensureAgentSelected();
  }
  loadUiTools(state.agentId);
  state.sessionId = null;
  state.branchFrom = null;
  state.modelMessageCount = 0;
  state.lastUserBranchPoint = null;
  state.llmModelId = defaultLlmModelId();
  clearMessages();
  showEmptyState();
  el.sessionBadge.textContent = "";
  el.chatTitle.textContent = "新对话";
  el.usageInfo.textContent = "";
  updateChatHeader();
  updateAgentSelectState();
  renderSessions();
  el.input.focus();
}

function upsertSession(id, title, agentId, agentName) {
  const normalized = normalizeSession({ id, agentId: agentId || state.agentId });
  const key = `${normalized.agentId || ""}:${normalized.id}`;
  const now = new Date().toISOString();
  const existing = state.sessions.find((s) => sessionLookupKey(s) === key);
  if (existing) {
    Object.assign(existing, normalized);
    if (title) existing.title = title.slice(0, 40);
    if (agentName) existing.agentName = agentName;
    existing.updatedAt = now;
  } else {
    state.sessions.push({
      ...normalized,
      agentName: agentName || agentNameById(normalized.agentId),
      title: (title || normalized.id.slice(0, 8)).slice(0, 40),
      createdAt: now,
      updatedAt: now,
      messageCount: state.modelMessageCount,
    });
  }
  sortSessions();
  renderSessions();
}

function cancelTitlePoll() {
  state.titlePollGeneration += 1;
}

function applySessionTitle(session, title) {
  const normalized = normalizeSession(session);
  const displayTitle = (title || "").slice(0, 40);
  if (!displayTitle) return;

  const key = sessionLookupKey(normalized);
  const existing = state.sessions.find((s) => sessionLookupKey(s) === key);
  if (existing) existing.title = displayTitle;

  if (
    normalized.id === state.sessionId &&
    (normalized.agentId || "") === (state.agentId || "")
  ) {
    el.chatTitle.textContent = displayTitle;
  }
  renderSessions();
}

function knownSessionTitle(session) {
  const normalized = normalizeSession(session);
  const key = sessionLookupKey(normalized);
  const found = state.sessions.find((s) => sessionLookupKey(s) === key);
  return found?.title || "";
}

async function pollSessionTitle(session, knownTitle, generation) {
  const normalized = normalizeSession(session);
  const deadline = Date.now() + TITLE_POLL_TIMEOUT_MS;

  while (Date.now() < deadline) {
    if (generation !== state.titlePollGeneration) return;
    await new Promise((r) => setTimeout(r, TITLE_POLL_INTERVAL_MS));
    if (generation !== state.titlePollGeneration) return;

    try {
      const r = await daFetch(sessionApiUrl(normalized));
      if (!r.ok) continue;
      const data = await r.json();
      const title = data.title ? data.title.slice(0, 40) : "";
      if (title && title !== knownTitle) {
        applySessionTitle(normalized, title);
        return;
      }
    } catch (_) {
      /* retry until timeout */
    }
  }

  if (generation === state.titlePollGeneration) {
    await syncSessionsFromBackend();
    const key = sessionLookupKey(normalized);
    const existing = state.sessions.find((s) => sessionLookupKey(s) === key);
    if (existing?.title && existing.title !== knownTitle) {
      applySessionTitle(normalized, existing.title);
    }
  }
}

function scheduleTitleRefresh() {
  if (!state.sessionId) return;
  const session = { id: state.sessionId, agentId: state.agentId };
  const knownTitle = knownSessionTitle(session) || lastUserText().slice(0, 40);
  cancelTitlePoll();
  const generation = state.titlePollGeneration;
  void pollSessionTitle(session, knownTitle, generation);
}

function renderSessions() {
  el.sessionList.innerHTML = "";
  const activeAgent = state.agents.find((agent) => agent.id === state.agentId);
  const scopedSessions = state.sessions.filter(
    (session) => (session.agentId || "") === (state.agentId || ""),
  );
  const panelTitle = document.getElementById("sessionPanelTitle");
  const panelSummary = document.getElementById("sessionPanelSummary");
  if (panelTitle) panelTitle.textContent = activeAgent?.name || "Agent 会话";
  if (panelSummary) {
    panelSummary.textContent = `${scopedSessions.length} 个历史会话`;
    panelSummary.title = state.sessionId ? "已选择会话" : "尚未选择会话";
  }
  if (scopedSessions.length === 0) {
    el.sessionList.innerHTML = '<li class="session-empty">该 Agent 暂无历史会话</li>';
    return;
  }
  for (const s of scopedSessions) {
    const li = document.createElement("li");
    li.className =
      "session-item" +
      (s.id === state.sessionId && (s.agentId || "") === (state.agentId || "") ? " active" : "");
    li.innerHTML = `<span class="sid" title="${escapeHtml(s.title)}"><span class="session-title">${escapeHtml(s.title)}</span><span class="session-meta"><span>${escapeHtml(formatSessionActivity(s))}</span><span class="session-meta-dot" aria-hidden="true"></span><span>${escapeHtml(formatSessionMessageCount(s))}</span></span></span><span class="session-actions"><span class="rename-btn" title="重命名">✏️</span><span class="auto-rename-btn" title="自动重命名">🔄</span><span class="del" title="删除">✕</span></span>`;
    li.querySelector(".sid").addEventListener("click", (e) => {
      if (e.target.closest(".session-title-input")) return;
      switchSession(s);
    });
    li.querySelector(".rename-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      startInlineEdit(s, li.querySelector(".session-title"));
    });
    li.querySelector(".auto-rename-btn").addEventListener("click", async (e) => {
      e.stopPropagation();
      const titleEl = li.querySelector(".session-title");
      const originalTitle = titleEl.textContent;
      titleEl.textContent = "生成中…";
      const result = await autoRenameSession(s);
      // 如果失败，恢复原标题
      if (!result) {
        titleEl.textContent = originalTitle;
      }
    });
    li.querySelector(".del").addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSession(s);
    });
    el.sessionList.appendChild(li);
  }
}

async function switchSession(s) {
  if (state.streaming) stopStream();
  cancelTitlePoll();
  state.pendingTitleRefresh = false;
  const session = normalizeSession(s);
  state.sessionId = session.id;
  state.branchFrom = null;
  state.lastUserBranchPoint = null;
  if (session.agentId) {
    state.agentId = session.agentId;
    localStorage.setItem("da_console_agent_id_v1", state.agentId);
    if (el.agentSelect) el.agentSelect.value = state.agentId;
  }
  restoreSessionModel(session);
  el.chatTitle.textContent = session.title || "对话";
  updateChatHeader();
  updateAgentSelectState();
  renderSessions();
  await loadSessionHistory(session);
}

/** 从后端拉取并渲染指定会话的历史（唯一消息来源）。 */
async function loadSessionHistory(session) {
  const normalized = normalizeSession(session);
  clearMessages();
  hideEmptyState();
  try {
    await loadUiTools(state.agentId);
    const r = await daFetch(sessionApiUrl(normalized, "/history"));
    if (r.status === 404) {
      addSystemNote("会话不存在或尚无历史记录。");
      return;
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    if (!data.messages || data.messages.length === 0) {
      showEmptyState();
      state.modelMessageCount = 0;
      state.awaitingHitl = false;
      updateHitlInputState();
      return;
    }
    renderHistory(data.messages, data.model_message_count);
  } catch (e) {
    addSystemNote(`加载历史失败：${e.message}`);
  }
}

async function refreshCurrentHistory() {
  if (!state.sessionId) return;
  await loadSessionHistory({ id: state.sessionId, agentId: state.agentId });
}

function renderHistory(messages, modelMessageCount) {
  state.modelMessageCount = modelMessageCount ?? 0;
  let lastDayKey = null;
  for (const msg of messages) {
    const ts = msg.timestamp || null;
    if (ts) {
      const dayKey = localDayKey(ts);
      if (dayKey !== lastDayKey) {
        addDateSeparator(ts);
        lastDayKey = dayKey;
      }
    }
    if (msg.role === "user" && (msg.content || (msg.attachments && msg.attachments.length))) {
      addMessage("user", msg.content || "", ts, msg.branch_point, msg.attachments);
      continue;
    }
    if (msg.role === "assistant" && msg.parts && msg.parts.length) {
      const assistant = createAssistantMessage(ts, msg.branch_point);
      const completedToolIds = collectCompletedToolIds(msg.parts);
      for (const part of msg.parts) {
        if (part.type === "thinking" && part.content) {
          assistant.addThinkingBlock(part.content);
        } else if (part.type === "text" && part.content) {
          assistant.appendText(part.content);
        } else if (part.type === "tool_call") {
          assistant.addToolCall({
            tool: part.tool,
            args: part.args,
            tool_call_id: part.tool_call_id,
          });
        } else if (part.type === "tool_result") {
          assistant.addToolResult({
            tool: part.tool,
            result: part.result,
            tool_call_id: part.tool_call_id,
          });
        }
      }
      for (const part of msg.parts) {
        if (isPendingUiToolCall(part, completedToolIds)) {
          assistant.attachHitlWidget(mergeUiToolCall(part));
        }
      }
      assistant.finalizeThinking();
      assistant.finalizeText();
    }
  }
  state.awaitingHitl = findResumablePendingUiCall(messages) !== null;
  updateHitlInputState();
  scrollToBottom();
}

function collectCompletedToolIds(parts) {
  const ids = new Set();
  for (const part of parts) {
    if (part.type === "tool_result" && part.tool_call_id) {
      ids.add(part.tool_call_id);
    }
  }
  return ids;
}

function isPendingUiToolCall(part, completedToolIds) {
  return (
    part.type === "tool_call" &&
    part.tool_call_id &&
    isUiToolCall(mergeUiToolCall(part)) &&
    !completedToolIds.has(part.tool_call_id)
  );
}

/** Pending UI tool call at the end of history (safe to resume). */
function findResumablePendingUiCall(messages) {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === "user") return null;
    if (msg.role !== "assistant" || !msg.parts?.length) continue;
    const completedToolIds = collectCompletedToolIds(msg.parts);
    for (const part of msg.parts) {
      if (isPendingUiToolCall(part, completedToolIds)) {
        return part;
      }
    }
    return null;
  }
  return null;
}

async function deleteSession(session) {
  const normalized = normalizeSession(session);
  const key = sessionLookupKey(normalized);
  state.sessions = state.sessions.filter((s) => sessionLookupKey(s) !== key);
  renderSessions();
  try {
    await daFetch(sessionApiUrl(normalized), { method: "DELETE" });
  } catch (_) { /* 忽略 */ }
  if (normalized.id === state.sessionId && (normalized.agentId || "") === (state.agentId || "")) newChat();
  else renderSessions();
}

/* --------------------------- 会话重命名 --------------------------- */
function noteSessionNotFound() {
  addSystemNote("会话不存在，请刷新页面。");
}

async function renameSession(session, newTitle) {
  const normalized = normalizeSession(session);
  try {
    const r = await daFetch(sessionApiUrl(normalized), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: newTitle }),
    });
    if (r.status === 404) {
      noteSessionNotFound();
      return null;
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    // 更新本地会话列表
    const key = sessionLookupKey(normalized);
    const existing = state.sessions.find((s) => sessionLookupKey(s) === key);
    if (existing && data.title) {
      existing.title = data.title.slice(0, 40);
      renderSessions();
    }
    // 如果是当前会话，更新标题显示
    if (normalized.id === state.sessionId && (normalized.agentId || "") === (state.agentId || "")) {
      el.chatTitle.textContent = data.title || "对话";
    }
    return data.title;
  } catch (e) {
    console.warn("[deep-agent] 重命名失败", e);
    addSystemNote(`重命名失败：${e.message}`);
    return null;
  }
}

async function autoRenameSession(session) {
  const normalized = normalizeSession(session);
  try {
    const r = await daFetch(sessionApiUrl(normalized), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto: true }),
    });
    if (r.status === 404) {
      noteSessionNotFound();
      return null;
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    // 更新本地会话列表
    const key = sessionLookupKey(normalized);
    const existing = state.sessions.find((s) => sessionLookupKey(s) === key);
    if (existing && data.title) {
      existing.title = data.title.slice(0, 40);
      renderSessions();
    }
    // 如果是当前会话，更新标题显示
    if (normalized.id === state.sessionId && (normalized.agentId || "") === (state.agentId || "")) {
      el.chatTitle.textContent = data.title || "对话";
    }
    return data.title;
  } catch (e) {
    console.warn("[deep-agent] 自动重命名失败", e);
    addSystemNote(`自动重命名失败：${e.message}`);
    return null;
  }
}

function startInlineEdit(session, titleElement) {
  if (state.streaming) return;

  const currentTitle = titleElement.textContent;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "session-title-input";
  input.value = currentTitle;
  input.maxLength = 40;

  // 替换文本为输入框
  titleElement.textContent = "";
  titleElement.appendChild(input);
  input.focus();
  input.select();

  const stopBubble = (e) => e.stopPropagation();
  input.addEventListener("mousedown", stopBubble);
  input.addEventListener("click", stopBubble);
  input.addEventListener("pointerdown", stopBubble);

  let finished = false;
  const finishEdit = async (save) => {
    if (finished) return;
    finished = true;
    const newTitle = save ? input.value.trim() : currentTitle;
    titleElement.removeChild(input);
    if (save && newTitle !== currentTitle) {
      titleElement.textContent = newTitle || currentTitle;
      await renameSession(session, newTitle);
    } else {
      titleElement.textContent = currentTitle;
    }
  };

  input.addEventListener("blur", () => finishEdit(true));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      finishEdit(true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      finishEdit(false);
    }
  });
}

/* --------------------------- 健康检查 / 能力 --------------------------- */
async function checkHealth() {
  setConn("pending", "检测中…");
  try {
    const r = await daFetch(serviceUrl("health"), { method: "GET" });
    if (r.ok) setConn("ok", "已连接");
    else setConn("err", `HTTP ${r.status}`);
  } catch (_) {
    setConn("err", "未连接");
  }
}

function setConn(kind, text) {
  el.connStatus.classList.toggle("ok", kind === "ok");
  el.connStatus.classList.toggle("err", kind === "err");
  el.connStatus.querySelector(".conn-text").textContent = text;
}

async function loadAgents() {
  if (!el.agentSelect) return;
  el.agentSelect.innerHTML = '<option value="">加载中…</option>';
  try {
    const r = await daFetch(serviceUrl("api/v1/agents"));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const agents = await r.json();
    if (!Array.isArray(agents)) {
      throw new Error(`Agent 列表响应格式错误：${serviceUrl("api/v1/agents")}`);
    }
    state.agents = agents;
    el.agentSelect.innerHTML = "";
    if (!state.agents.length) {
      el.agentSelect.innerHTML = '<option value="">无可用 Agent</option>';
      return;
    }
    for (const a of state.agents) {
      const opt = document.createElement("option");
      opt.value = a.id;
      opt.textContent = a.name;
      el.agentSelect.appendChild(opt);
    }
    ensureAgentSelected();
    updateChatHeader();
    await loadLlmModels();
    if (el.newChatMenu && !el.newChatMenu.classList.contains("hidden")) {
      renderNewChatMenu();
    }
  } catch (e) {
    el.agentSelect.innerHTML = `<option value="">加载失败</option>`;
    console.warn("[deep-agent] 加载 Agent 失败", e);
  }
}

function currentAgent() {
  return state.agents.find((a) => a.id === state.agentId) || null;
}

function defaultLlmModelId() {
  const agent = currentAgent();
  return agent?.model_id || agent?.model?.id || "";
}

function rememberSessionModel() {
  if (!state.sessionId) return;
  const key = sessionLookupKey({ id: state.sessionId, agentId: state.agentId });
  if (state.llmModelId) state.sessionModelIds[key] = state.llmModelId;
}

function restoreSessionModel(session) {
  const normalized = normalizeSession(session);
  const key = sessionLookupKey(normalized);
  const remembered = state.sessionModelIds[key];
  if (remembered && state.llmModels.some((m) => m.id === remembered)) {
    state.llmModelId = remembered;
  } else {
    state.llmModelId = defaultLlmModelId();
  }
  ensureLlmModelSelected();
}

function selectedLlmModel() {
  const id = state.llmModelId || defaultLlmModelId();
  return state.llmModels.find((m) => m.id === id) || null;
}

function ensureLlmModelSelected() {
  if (!state.llmModels.length) return false;
  const fallback = defaultLlmModelId();
  const valid = state.llmModelId && state.llmModels.some((m) => m.id === state.llmModelId);
  if (!valid) state.llmModelId = fallback || state.llmModels[0]?.id || "";
  return !!state.llmModelId;
}

function chatPayloadModelId() {
  ensureLlmModelSelected();
  return state.llmModelId || undefined;
}

function currentModelInfo() {
  const model = selectedLlmModel();
  const agent = currentAgent();
  if (!model && !agent?.model) return null;
  const presetName = model?.name || agent?.model?.name || agent?.name || "";
  const modelId = model?.model || agent?.model?.model || "";
  const modalities = Array.isArray(model?.input_modalities)
    ? model.input_modalities
    : Array.isArray(agent?.model?.input_modalities)
      ? agent.model.input_modalities
      : [];
  return { presetName, modelId, modalities };
}

async function loadUploadCapabilities() {
  try {
    const r = await daFetch(serviceUrl("api/v1/uploads/capabilities"));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state.uploadCapabilities = await r.json();
  } catch (e) {
    state.uploadCapabilities = { kinds: [], labels: {} };
    console.warn("[deep-agent] 加载上传能力失败", e);
  }
  updateComposerModelInfo();
}

async function loadLlmModels() {
  try {
    const r = await daFetch(serviceUrl("api/v1/llm-models"));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state.llmModels = await r.json();
    ensureLlmModelSelected();
    updateComposerModelInfo();
  } catch (e) {
    state.llmModels = [];
    console.warn("[deep-agent] 加载 LLM 模型失败", e);
    updateComposerModelInfo();
  }
}

function modelOptionLabel(m) {
  const name = String(m?.name || "").trim();
  const model = String(m?.model || "").trim();
  if (!name) return model;
  if (!model || name === model) return name;
  if (name.includes(model) || model.includes(name)) return name;
  return `${name} — ${model}`;
}

function updateComposerModelInfo() {
  if (!el.composerMeta) return;
  const info = currentModelInfo();
  if (!info && !state.llmModels.length) {
    el.composerMeta.classList.add("hidden");
    return;
  }
  el.composerMeta.classList.remove("hidden");

  if (el.modelSelect) {
    if (!state.llmModels.length) {
      el.modelSelect.innerHTML = `<option value="">${escapeHtml(info?.presetName || "无可用模型")}</option>`;
      el.modelSelect.disabled = true;
    } else {
      ensureLlmModelSelected();
      el.modelSelect.innerHTML = state.llmModels
        .map((m) => {
          const label = modelOptionLabel(m);
          return `<option value="${escapeHtml(m.id)}" title="${escapeHtml(label)}">${escapeHtml(label)}</option>`;
        })
        .join("");
      el.modelSelect.value = state.llmModelId;
      el.modelSelect.disabled = state.streaming;
      const selected = selectedLlmModel();
      el.modelSelect.title = selected ? modelOptionLabel(selected) : "选择本会话使用的 LLM 模型";
    }
  }

  if (el.composerModelModalities) {
    const supported = new Set((info?.modalities || []).map((m) => String(m).toLowerCase()));
    if (!supported.size) {
      el.composerModelModalities.innerHTML =
        '<span class="composer-modalities-label">模型输入</span><span class="modality-chip">未配置（管理后台可刷新 Meta）</span>';
    } else {
      const chips = ALL_LLM_MODALITIES.map((key) => {
        const on = supported.has(key);
        const label = LLM_MODALITY_LABELS[key] || key;
        const icon = LLM_MODALITY_ICONS[key] || "";
        const title = on ? `LLM 原生支持：${label}` : `LLM 原生不支持：${label}`;
        return `<span class="modality-chip ${on ? "supported" : "unsupported"}" title="${escapeHtml(title)}">${icon} ${escapeHtml(label)}</span>`;
      }).join("");
      el.composerModelModalities.innerHTML = `<span class="composer-modalities-label">模型输入</span>${chips}`;
    }
  }

  if (el.composerUploadCapabilities) {
    const kinds = Array.isArray(state.uploadCapabilities?.kinds) ? state.uploadCapabilities.kinds : [];
    const labels = state.uploadCapabilities?.labels || {};
    const vision = new Set((info?.modalities || []).map((m) => String(m).toLowerCase())).has("image");
    if (!kinds.length) {
      el.composerUploadCapabilities.innerHTML =
        '<span class="composer-modalities-label">附件</span><span class="modality-chip">未配置</span>';
    } else {
      const ordered = UPLOAD_KIND_ORDER.filter((k) => kinds.includes(k)).concat(
        kinds.filter((k) => !UPLOAD_KIND_ORDER.includes(k)),
      );
      const chips = ordered
        .map((key) => {
          const label = labels[key] || key;
          const icon = UPLOAD_KIND_ICONS[key] || "📎";
          let chipClass = "supported";
          let title = `平台上传：${label}`;
          if (key === "image" && !vision) {
            chipClass = "upload-partial";
            title = `${label}：可上传，但当前模型不支持原生看图`;
          } else if (key === "image") {
            title = `${label}：可上传，当前模型支持原生看图`;
          } else if (key.endsWith("_text") || key === "text_file") {
            title = `${label}：上传后抽文本注入对话（非 LLM 原生多模态）`;
          }
          return `<span class="modality-chip ${chipClass}" title="${escapeHtml(title)}">${icon} ${escapeHtml(label)}</span>`;
        })
        .join("");
      el.composerUploadCapabilities.innerHTML = `<span class="composer-modalities-label">附件</span>${chips}`;
    }
  }
}

function updateChatHeader() {
  el.sessionBadge.textContent = formatSessionIdSubtitle(state.sessionId);
  updateComposerModelInfo();
}

function updateAgentSelectState() {
  if (el.agentSelect) el.agentSelect.disabled = state.streaming || !!state.sessionId;
  updateComposerModelInfo();
}

async function loadCapabilities() {
  el.capList.innerHTML = '<li class="cap-empty">加载中…</li>';
  try {
    const r = await daFetch(serviceUrl("api/v1/tools"));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const caps = await r.json();
    el.capList.innerHTML = "";
    if (!caps.length) {
      el.capList.innerHTML = '<li class="cap-empty">无可用能力</li>';
      return;
    }
    for (const c of caps) {
      const li = document.createElement("li");
      li.className = "cap-item";
      const tools = (c.tools || []).map((t) => t.name).join(", ");
      li.innerHTML = `
        <div class="cap-name">${escapeHtml(c.name)} <span class="cap-kind">${escapeHtml(c.kind)}</span></div>
        ${tools ? `<div class="cap-tools">${escapeHtml(tools)}</div>` : ""}`;
      el.capList.appendChild(li);
    }
  } catch (e) {
    el.capList.innerHTML = `<li class="cap-empty">加载失败：${escapeHtml(e.message)}</li>`;
  }
}

/* --------------------------- 发送 / 流式 --------------------------- */
async function onSend() {
  const text = el.input.value.trim();
  const attachments = state.pendingAttachments.slice();
  if ((!text && attachments.length === 0) || state.streaming) return;
  if (state.awaitingHitl) {
    addSystemNote("请先完成上方待处理的交互。");
    return;
  }
  if (!ensureAgentSelected()) {
    addSystemNote("请先选择 Agent。");
    return;
  }

  hideEmptyState();
  el.input.value = "";
  clearPendingAttachments();
  autoGrow();

  const branchFrom = state.branchFrom;
  if (branchFrom != null) {
    removeDomFromBranchPoint(branchFrom);
  }

  const userBranchPoint = branchFrom != null ? branchFrom : state.modelMessageCount;
  const isFirstTurn = branchFrom == null && state.modelMessageCount === 0;
  addMessage("user", text, null, userBranchPoint, attachments);
  state.lastUserBranchPoint = userBranchPoint;

  const assistant = createAssistantMessage();
  setStreaming(true);
  updateAgentSelectState();

  if (!state.sessionId) state.sessionId = generateSessionId();

  const payload = {
    agent_id: state.agentId,
    message: text || null,
    session_id: state.sessionId,
  };
  const modelId = chatPayloadModelId();
  if (modelId) payload.model_id = modelId;
  rememberSessionModel();
  if (attachments.length) {
    payload.attachments = attachments.map((a) => ({
      id: a.id,
      name: a.name,
      mime: a.mime,
      extracted_text: a.extracted_text ?? null,
    }));
  }
  if (branchFrom != null) {
    payload.branch_from = branchFrom;
    state.modelMessageCount = branchFrom + 1;
    state.branchFrom = null;
  } else {
    state.modelMessageCount += 1;
  }
  state.pendingTitleRefresh = isFirstTurn;
  await runChatStream(payload, assistant);
}

async function resumeDeferred(deferredResults) {
  if (!state.sessionId) return;
  if (state.streaming) {
    stopStream();
    const deadline = Date.now() + 2000;
    while (state.streaming && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 50));
    }
    if (state.streaming) return;
  }
  state.awaitingHitl = false;
  updateHitlInputState();

  const assistant = createAssistantMessage();
  setStreaming(true);
  updateAgentSelectState();

  const payload = {
    agent_id: state.agentId,
    session_id: state.sessionId,
    deferred_tool_results: deferredResults,
  };
  const modelId = chatPayloadModelId();
  if (modelId) payload.model_id = modelId;
  rememberSessionModel();
  await runChatStream(payload, assistant);
}

async function runChatStream(payload, assistant) {
  state.abortController = new AbortController();
  state.streamCancelled = false;
  state.skipHistoryRefresh = false;
  let stoppedByUser = false;
  const shouldRefreshTitle = state.pendingTitleRefresh;
  state.pendingTitleRefresh = false;
  try {
    const resp = await daFetch(serviceUrl("api/v1/chat/stream"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(payload),
      signal: state.abortController.signal,
    });

    if (!resp.ok || !resp.body) {
      const detail = await resp.text().catch(() => "");
      throw new Error(`HTTP ${resp.status} ${detail}`.trim());
    }
    setConn("ok", "已连接");

    await readSse(resp.body, (evt) => handleEvent(evt, assistant));
  } catch (e) {
    if (e.name === "AbortError") {
      stoppedByUser = true;
      assistant.finalizeThinking();
      assistant.finalizeText();
      if (!state.streamCancelled) {
        addErrorTo(assistant, "（已停止生成）");
      }
    } else {
      setConn("err", "请求失败");
      addErrorTo(assistant, `请求失败：${e.message}`);
    }
  } finally {
    assistant.finalizeThinking();
    assistant.finalizeText();
    setStreaming(false);
    state.abortController = null;
    if (state.sessionId) {
      if (!state.skipHistoryRefresh) {
        if (stoppedByUser || state.streamCancelled) {
          await new Promise((r) => setTimeout(r, 250));
        }
        await refreshCurrentHistory();
      }
      await syncSessionsFromBackend();
      if (shouldRefreshTitle && state.sessionId) {
        const title = lastUserText() || el.chatTitle.textContent;
        upsertSession(state.sessionId, title, state.agentId, agentNameById(state.agentId));
      }
      if (
        shouldRefreshTitle &&
        !state.awaitingHitl &&
        !stoppedByUser &&
        !state.streamCancelled
      ) {
        scheduleTitleRefresh();
      }
    }
  }
}

function updateHitlInputState() {
  if (el.input) {
    el.input.placeholder = state.awaitingHitl ? "请先完成上方交互…" : "输入消息…";
    el.input.disabled = state.awaitingHitl;
  }
  if (el.sendBtn) el.sendBtn.disabled = state.awaitingHitl;
}

function stopStream() {
  if (state.abortController) state.abortController.abort();
}

function setStreaming(v) {
  state.streaming = v;
  el.sendBtn.classList.toggle("hidden", v);
  el.stopBtn.classList.toggle("hidden", !v);
  updateAgentSelectState();
  updateHitlInputState();
}

function syncHistoryMeta({ model_message_count: count, user_branch_point: branchPt }) {
  if (count != null) state.modelMessageCount = count;
  if (branchPt != null) {
    state.lastUserBranchPoint = branchPt;
    const rows = el.messages.querySelectorAll(".msg-row.user");
    const last = rows[rows.length - 1];
    if (last) last.dataset.branchPoint = String(branchPt);
  }
}

function handleEvent({ event, data }, assistant) {
  const type = event || data.type;
  window.dispatchEvent(
    new CustomEvent("deep-agent-console-event", {
      detail: { event: type, data },
    }),
  );
  switch (type) {
    case "session": {
      const id = data.session_id;
      if (id) {
        state.sessionId = id;
        if (data.agent_id) {
          state.agentId = data.agent_id;
          localStorage.setItem("da_console_agent_id_v1", state.agentId);
          if (el.agentSelect) el.agentSelect.value = state.agentId;
        }
        rememberSessionModel();
        updateChatHeader();
        const sessionKey = sessionLookupKey({ id, agentId: state.agentId });
        const isNewSession = !state.sessions.some((s) => sessionLookupKey(s) === sessionKey);
        const title = isNewSession ? lastUserText() || id.slice(0, 8) : null;
        upsertSession(id, title, state.agentId, agentNameById(state.agentId));
        if (isNewSession && el.chatTitle.textContent === "新对话") {
          el.chatTitle.textContent = (title || id.slice(0, 8)).slice(0, 40);
        }
        updateAgentSelectState();
      }
      break;
    }
    case "thinking":
      assistant.appendThinking(data.delta || "");
      break;
    case "text":
      assistant.finalizeThinking();
      assistant.appendText(data.delta || "");
      break;
    case "tool_call":
      assistant.addToolCall(data);
      break;
    case "tool_result":
      assistant.addToolResult(data);
      break;
    case "final":
      assistant.finalizeThinking();
      assistant.finalizeText();
      syncHistoryMeta(data);
      if (data.usage) renderUsage(data.usage);
      state.skipHistoryRefresh = true;
      if (data.status === "pending" && data.deferred) {
        state.awaitingHitl = true;
        setStreaming(false);
        const pending = [...(data.deferred.calls || []), ...(data.deferred.approvals || [])];
        for (const call of pending) {
          assistant.attachHitlWidget(mergeUiToolCall(call));
        }
      } else {
        state.awaitingHitl = false;
      }
      updateHitlInputState();
      break;
    case "error":
      assistant.finalizeThinking();
      assistant.finalizeText();
      syncHistoryMeta(data);
      // Error is transient SSE only — refreshing history would wipe the bubble.
      state.skipHistoryRefresh = true;
      addErrorTo(assistant, data.message || "未知错误", {
        branchFrom: state.lastUserBranchPoint,
        message: lastUserText(),
      });
      break;
    case "cancelled":
      state.streamCancelled = true;
      assistant.finalizeThinking();
      assistant.finalizeText();
      syncHistoryMeta(data);
      break;
    default:
      break;
  }
  scrollToBottom();
}

function renderUsage(usage) {
  const parts = [];
  if (usage.requests != null) parts.push(`${usage.requests} 次请求`);
  if (usage.input_tokens != null) parts.push(`in ${usage.input_tokens}`);
  if (usage.output_tokens != null) parts.push(`out ${usage.output_tokens}`);
  if (usage.total_tokens != null) parts.push(`共 ${usage.total_tokens} tokens`);
  el.usageInfo.textContent = parts.join(" · ");
}

/* --------------------------- 消息渲染 --------------------------- */
function addMessage(role, text, timestamp, branchPoint, attachments) {
  const row = document.createElement("div");
  row.className = `msg-row ${role}`;
  if (branchPoint != null) row.dataset.branchPoint = String(branchPoint);
  row.innerHTML = `
    <div class="avatar ${role}">${role === "user" ? "你" : "🤖"}</div>
    <div class="bubble-wrap"><div class="bubble"></div></div>`;
  const wrap = row.querySelector(".bubble-wrap");
  if (timestamp) prependMsgTime(wrap, timestamp);
  const bubble = wrap.querySelector(".bubble");
  if (text) {
    const textEl = document.createElement("div");
    textEl.className = "bubble-text";
    textEl.textContent = text;
    bubble.appendChild(textEl);
  }
  if (attachments?.length) {
    renderMessageAttachments(bubble, attachments);
  }
  if (role === "user") {
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "msg-edit-btn";
    editBtn.title = "编辑";
    editBtn.textContent = "编辑";
    editBtn.addEventListener("click", () => startEditUserMessage(row, text, branchPoint));
    actions.appendChild(editBtn);
    wrap.appendChild(actions);
  }
  el.messages.appendChild(row);
  scrollToBottom();
  return row;
}

function renderMessageAttachments(container, attachments) {
  const wrap = document.createElement("div");
  wrap.className = "msg-attachments";
  for (const att of attachments) {
    const chip = document.createElement("div");
    chip.className = "attachment-chip";
    const mime = (att.mime || "").toLowerCase();
    if (mime.startsWith("image/")) {
      const img = document.createElement("div");
      img.className = "attachment-thumb";
      img.textContent = "🖼";
      chip.appendChild(img);
    }
    const label = document.createElement("span");
    label.className = "attachment-name";
    label.textContent = att.name || att.id || "附件";
    chip.appendChild(label);
    wrap.appendChild(chip);
  }
  container.appendChild(wrap);
}

async function onFilesSelected(e) {
  const files = Array.from(e.target.files || []);
  e.target.value = "";
  for (const file of files) {
    await uploadAttachment(file);
  }
}

async function onPaste(e) {
  const items = Array.from(e.clipboardData?.items || []);
  for (const item of items) {
    if (!item.type.startsWith("image/")) continue;
    e.preventDefault();
    const file = item.getAsFile();
    if (file) await uploadAttachment(file);
  }
}

async function uploadAttachment(file) {
  if (state.pendingAttachments.length >= 10) {
    addSystemNote("单次最多 10 个附件。");
    return;
  }
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    const r = await daFetch(serviceUrl("api/v1/uploads"), { method: "POST", body: form });
    if (!r.ok) {
      const detail = await r.text().catch(() => "");
      throw new Error(detail || `HTTP ${r.status}`);
    }
    const data = await r.json();
    state.pendingAttachments.push({
      id: data.id,
      name: data.name,
      mime: data.mime,
      extracted_text: data.extracted_text,
    });
    renderPendingAttachments();
  } catch (err) {
    addSystemNote(`上传失败：${err.message}`);
  }
}

function renderPendingAttachments() {
  if (!el.attachmentPreview) return;
  if (!state.pendingAttachments.length) {
    el.attachmentPreview.classList.add("hidden");
    el.attachmentPreview.innerHTML = "";
    return;
  }
  el.attachmentPreview.classList.remove("hidden");
  el.attachmentPreview.innerHTML = "";
  for (const att of state.pendingAttachments) {
    const chip = document.createElement("div");
    chip.className = "pending-attachment";
    chip.innerHTML = `<span>${escapeHtml(att.name || att.id)}</span><button type="button" title="移除">×</button>`;
    chip.querySelector("button").addEventListener("click", () => {
      state.pendingAttachments = state.pendingAttachments.filter((a) => a.id !== att.id);
      renderPendingAttachments();
    });
    el.attachmentPreview.appendChild(chip);
  }
}

function clearPendingAttachments() {
  state.pendingAttachments = [];
  renderPendingAttachments();
}

function startEditUserMessage(row, text, branchPoint) {
  if (state.streaming) return;
  if (branchPoint == null) return;
  state.branchFrom = branchPoint;
  el.input.value = text;
  autoGrow();
  truncateDomAfterRow(row);
  el.input.focus();
}

function truncateDomAfterRow(row) {
  let node = row.nextSibling;
  while (node) {
    const next = node.nextSibling;
    node.remove();
    node = next;
  }
}

/** Remove the user bubble at branch_point and everything after it. */
function removeDomFromBranchPoint(branchPoint) {
  const row = el.messages.querySelector(
    `.msg-row.user[data-branch-point="${branchPoint}"]`
  );
  if (!row) return;
  truncateDomAfterRow(row);
  row.remove();
}

async function retryFromBranch({ branchFrom, message }) {
  if (state.streaming || branchFrom == null || !message) return;
  if (!ensureAgentSelected()) return;
  state.branchFrom = branchFrom;
  el.input.value = message;
  await onSend();
}

function addDateSeparator(iso) {
  const sep = document.createElement("div");
  sep.className = "date-separator";
  sep.textContent = formatDateSeparator(iso);
  el.messages.appendChild(sep);
}

function prependMsgTime(wrap, iso) {
  const timeEl = document.createElement("div");
  timeEl.className = "msg-time";
  timeEl.textContent = formatMessageTime(iso);
  wrap.insertBefore(timeEl, wrap.firstChild);
}

function addSystemNote(text) {
  const row = document.createElement("div");
  row.className = "msg-row assistant";
  row.innerHTML = `<div class="avatar assistant">ℹ️</div><div class="bubble-wrap"><div class="bubble" style="opacity:.7;font-size:13px"></div></div>`;
  row.querySelector(".bubble").textContent = text;
  el.messages.appendChild(row);
  scrollToBottom();
}

function createAssistantMessage(timestamp, branchPoint) {
  const row = document.createElement("div");
  row.className = "msg-row assistant";
  if (branchPoint != null) row.dataset.branchPoint = String(branchPoint);
  row.innerHTML = `
    <div class="avatar assistant">🤖</div>
    <div class="bubble-wrap"></div>`;
  const wrap = row.querySelector(".bubble-wrap");
  if (timestamp) prependMsgTime(wrap, timestamp);
  el.messages.appendChild(row);
  scrollToBottom();

  let textBuffer = "";
  let textBubble = null;
  let thinkingBlock = null;
  let thinkingBuffer = "";
  const toolBlocks = new Map(); // tool_call_id -> element

  function ensureThinkingBlock() {
    if (!thinkingBlock) {
      thinkingBlock = document.createElement("div");
      thinkingBlock.className = "thinking-block";
      thinkingBlock.innerHTML = `
        <div class="thinking-head">
          <span class="chevron">▶</span>
          <span class="thinking-icon">💭</span>
          <span class="thinking-title">思考中…</span>
          <span class="thinking-status"><span class="spinner"></span></span>
        </div>
        <div class="thinking-body"><pre class="thinking-content"></pre></div>`;
      thinkingBlock.querySelector(".thinking-head").addEventListener("click", () => {
        thinkingBlock.classList.toggle("open");
      });
      wrap.appendChild(thinkingBlock);
    }
    return thinkingBlock;
  }

  function renderThinkingContent() {
    const pre = ensureThinkingBlock().querySelector(".thinking-content");
    pre.textContent = thinkingBuffer;
  }

  function finalizeThinkingBlock() {
    if (!thinkingBlock) return;
    thinkingBlock.querySelector(".thinking-title").textContent = "思考过程";
    thinkingBlock.querySelector(".thinking-status").innerHTML = "";
    thinkingBlock.classList.remove("open");
  }

  function ensureTextBubble() {
    if (!textBubble) {
      textBubble = document.createElement("div");
      textBubble.className = "bubble markdown streaming";
      wrap.appendChild(textBubble);
    }
    return textBubble;
  }

  function sealTextBubble() {
    if (!textBubble) return;
    textBubble.classList.remove("streaming");
    textBubble.innerHTML = renderMarkdownBody(textBuffer);
  }

  return {
    row,
    appendThinking(delta) {
      thinkingBuffer += delta;
      renderThinkingContent();
      scrollToBottom();
    },
    finalizeThinking() {
      finalizeThinkingBlock();
    },
    addThinkingBlock(content) {
      thinkingBuffer = content;
      renderThinkingContent();
      finalizeThinkingBlock();
    },
    appendText(delta) {
      textBuffer += delta;
      ensureTextBubble().innerHTML = renderMarkdownBody(textBuffer);
    },
    finalizeText() {
      sealTextBubble();
    },
    addToolCall(data) {
      finalizeThinkingBlock();
      const merged = mergeUiToolCall(data);
      const key = merged.tool_call_id || `${merged.tool}-${toolBlocks.size}`;
      const hasArgs = hasNonEmptyArgs(merged.args);
      const existing = toolBlocks.get(key);

      if (existing) {
        if (hasArgs) {
          const argsEl = existing.querySelector(".args");
          if (argsEl) argsEl.textContent = pretty(merged.args);
        }
        if (merged.widget) existing.dataset.widget = merged.widget;
        if (merged.metadata) existing.dataset.metadata = JSON.stringify(merged.metadata);
        maybeAttachHitlWidget(existing, merged);
        scrollToBottom();
        return;
      }

      // 工具调用开始后，文本若在流式中先固定，避免后续文本拼接错乱
      const block = document.createElement("div");
      block.className = "tool-block open";
      block.dataset.toolCallId = key;
      if (merged.widget) block.dataset.widget = merged.widget;
      if (merged.metadata) block.dataset.metadata = JSON.stringify(merged.metadata);
      const argsHtml = hasArgs
        ? `<pre class="args">${escapeHtml(pretty(merged.args))}</pre>`
        : `<pre class="args args-pending">参数接收中…</pre>`;
      block.innerHTML = `
        <div class="tool-head">
          <span class="chevron">▶</span>
          <span class="tool-icon">🛠️</span>
          <span class="tool-name">${escapeHtml(merged.tool || "tool")}</span>
          <span class="tool-status"><span class="spinner"></span></span>
        </div>
        <div class="tool-body">
          <div class="label">参数 args</div>
          ${argsHtml}
        </div>`;
      block.querySelector(".tool-head").addEventListener("click", () => block.classList.toggle("open"));
      wrap.appendChild(block);
      toolBlocks.set(key, block);
      maybeAttachHitlWidget(block, merged);
      // 工具调用后开启新文本段；先封存当前气泡，避免多个 streaming 光标并存
      sealTextBubble();
      textBubble = null;
      textBuffer = "";
      scrollToBottom();
    },
    addToolResult(data) {
      const key = data.tool_call_id;
      let block = key && toolBlocks.get(key);
      if (!block) {
        block = [...toolBlocks.values()].reverse().find((b) => !b.dataset.done);
      }
      if (!block) return;
      block.dataset.done = "1";
      block.classList.remove("hitl-pending");
      block.querySelector(".tool-status").innerHTML = '<span style="color:#3fb950">✓ 完成</span>';
      const body = block.querySelector(".tool-body");
      const existingWidget = body.querySelector(".hitl-widget");
      if (existingWidget) existingWidget.remove();
      const res = document.createElement("div");
      res.innerHTML = `<div class="label" style="margin-top:8px">结果 result</div>${renderToolResult(data.result)}`;
      body.appendChild(res);
    },
    attachHitlWidget(call) {
      const merged = mergeUiToolCall(call);
      const key = merged.tool_call_id;
      let block = key && toolBlocks.get(key);
      if (!block) {
        this.addToolCall(merged);
        block = toolBlocks.get(key);
      }
      if (!block || !isUiToolCall(merged)) return;
      renderHitlWidget(block, merged, resumeDeferred);
    },
  };
}

function maybeAttachHitlWidget(block, call) {
  const merged = mergeUiToolCall(call);
  if (!block || !isUiToolCall(merged) || !hasNonEmptyArgs(merged.args)) return;
  if (block.querySelector(".hitl-widget")) return;
  const validation = validateUiToolCall(merged);
  if (!validation.ok) {
    if (!validation.pending) renderHitlError(block, validation.error);
    return;
  }
  renderHitlWidget(block, merged, resumeDeferred);
  state.awaitingHitl = true;
  state.skipHistoryRefresh = true;
  updateHitlInputState();
}

function renderHitlWidget(block, call, onSubmit) {
  const merged = mergeUiToolCall(call);
  const { tool, args = {}, tool_call_id: toolCallId, metadata = {} } = merged;
  const body = block.querySelector(".tool-body");
  if (!body || body.querySelector(".hitl-widget")) return;

  block.classList.add("open", "hitl-pending");
  if (merged.widget) block.dataset.widget = merged.widget;
  if (metadata && Object.keys(metadata).length) {
    block.dataset.metadata = JSON.stringify(metadata);
  }
  block.querySelector(".tool-status").innerHTML = '<span style="color:#d29922">等待操作</span>';

  const widgetEl = document.createElement("div");
  widgetEl.className = "hitl-widget";
  const widgetType = resolveWidgetType(merged);
  const validation = validateUiToolCall(merged);
  if (!validation.ok) {
    if (!validation.pending) renderHitlError(block, validation.error);
    return;
  }

  if (widgetType === "confirm") {
    const message = args.message || "确认执行？";
    widgetEl.innerHTML = `
      ${args.title ? `<strong class="hitl-title">${escapeHtml(args.title)}</strong>` : ""}
      <p class="hitl-prompt">${escapeHtml(message)}</p>
      <div class="hitl-actions">
        <button type="button" class="hitl-btn hitl-confirm">${escapeHtml(args.confirm_label || "确认")}</button>
        <button type="button" class="hitl-btn hitl-cancel">${escapeHtml(args.cancel_label || "取消")}</button>
      </div>`;
    widgetEl.querySelector(".hitl-confirm").addEventListener("click", () => {
      onSubmit([{ tool_call_id: toolCallId, result: { confirmed: true } }]);
    });
    widgetEl.querySelector(".hitl-cancel").addEventListener("click", () => {
      onSubmit([{ tool_call_id: toolCallId, result: { confirmed: false } }]);
    });
  } else if (widgetType === "choice") {
    const options = args.options.map(normalizeOption).filter((option) => option.value);
    const allowMultiple = !!args.allow_multiple;
    widgetEl.innerHTML = `
      ${args.title ? `<strong class="hitl-title">${escapeHtml(args.title)}</strong>` : ""}
      <p class="hitl-prompt">${escapeHtml(args.prompt || "请选择：")}</p>
      <div class="hitl-choices"></div>
      <textarea class="hitl-text-input hitl-choice-custom-input" rows="2" placeholder="${escapeHtml(args.custom_placeholder || "请输入其他内容")}"></textarea>
      <div class="hitl-form-error hidden"></div>
      <button type="button" class="hitl-btn hitl-submit">${escapeHtml(args.submit_label || "提交")}</button>`;
    const choicesEl = widgetEl.querySelector(".hitl-choices");
    for (const opt of options) {
      const label = document.createElement("label");
      label.className = "hitl-choice";
      const input = document.createElement("input");
      input.type = allowMultiple ? "checkbox" : "radio";
      input.name = `choice-${toolCallId}`;
      input.value = opt.value;
      label.appendChild(input);
      const copy = document.createElement("span");
      copy.innerHTML = `<strong>${escapeHtml(opt.label)}</strong>${opt.description ? `<small>${escapeHtml(opt.description)}</small>` : ""}`;
      label.appendChild(copy);
      choicesEl.appendChild(label);
    }
    const customLabel = document.createElement("label");
    customLabel.className = "hitl-choice hitl-choice-custom";
    const customChoice = document.createElement("input");
    customChoice.type = allowMultiple ? "checkbox" : "radio";
    customChoice.name = `choice-${toolCallId}`;
    customChoice.className = "hitl-choice-custom-control";
    customLabel.appendChild(customChoice);
    const customCopy = document.createElement("span");
    customCopy.innerHTML = `<strong>${escapeHtml(args.custom_label || "其他（自行输入）")}</strong>`;
    customLabel.appendChild(customCopy);
    choicesEl.appendChild(customLabel);
    const customInput = widgetEl.querySelector(".hitl-choice-custom-input");
    customInput.addEventListener("input", () => {
      if (customInput.value.trim()) customChoice.checked = true;
    });
    widgetEl.querySelector(".hitl-submit").addEventListener("click", () => {
      const selected = [...choicesEl.querySelectorAll("input:checked")]
        .filter((input) => input !== customChoice)
        .map((input) => input.value);
      const choiceResult = buildChoiceResult(
        selected,
        customChoice.checked ? customInput.value : "",
      );
      const error = widgetEl.querySelector(".hitl-form-error");
      if (!choiceResult.ok) {
        error.textContent = choiceResult.error;
        error.classList.remove("hidden");
        return;
      }
      error.classList.add("hidden");
      onSubmit([{ tool_call_id: toolCallId, result: choiceResult.result }]);
    });
  } else if (widgetType === "text") {
    const rows = args.multiline ? 4 : 1;
    widgetEl.innerHTML = `
      <p class="hitl-prompt">${escapeHtml(args.prompt || "请输入：")}</p>
      <textarea class="hitl-text-input" rows="${rows}" placeholder="${escapeHtml(args.placeholder || "")}"></textarea>
      <button type="button" class="hitl-btn hitl-submit">提交</button>`;
    const input = widgetEl.querySelector(".hitl-text-input");
    widgetEl.querySelector(".hitl-submit").addEventListener("click", () => {
      onSubmit([{ tool_call_id: toolCallId, result: { text: input.value } }]);
    });
  } else if (widgetType === "date_picker") {
    widgetEl.innerHTML = `
      <p class="hitl-prompt">${escapeHtml(args.prompt || "请选择日期：")}</p>
      <input type="date" class="hitl-date-input"${args.min_date ? ` min="${escapeHtml(args.min_date)}"` : ""}${args.max_date ? ` max="${escapeHtml(args.max_date)}"` : ""} />
      <button type="button" class="hitl-btn hitl-submit">提交</button>`;
    const input = widgetEl.querySelector(".hitl-date-input");
    widgetEl.querySelector(".hitl-submit").addEventListener("click", () => {
      onSubmit([{ tool_call_id: toolCallId, result: { date: input.value } }]);
    });
  } else if (widgetType === "structured") {
    renderStructuredForm(widgetEl, merged, onSubmit);
  } else if (widgetType === "custom" || isUiToolCall(merged)) {
    const prompt = args.prompt || args.title || args.message || tool;
    const metaHtml =
      metadata && Object.keys(metadata).length
        ? `<pre class="hitl-metadata">${escapeHtml(pretty(metadata))}</pre>`
        : "";
    widgetEl.innerHTML = `
      <p class="hitl-prompt">${escapeHtml(prompt)}</p>
      ${metaHtml}
      <textarea class="hitl-custom-result" rows="4" placeholder="JSON 结果"></textarea>
      <button type="button" class="hitl-btn hitl-submit">提交</button>`;
    widgetEl.querySelector(".hitl-submit").addEventListener("click", () => {
      const raw = widgetEl.querySelector(".hitl-custom-result").value.trim();
      let result;
      try {
        result = raw ? JSON.parse(raw) : {};
      } catch (_) {
        result = { text: raw };
      }
      onSubmit([{ tool_call_id: toolCallId, result }]);
    });
  } else {
    return;
  }

  body.appendChild(widgetEl);
}

function renderHitlError(block, message) {
  const body = block.querySelector(".tool-body");
  if (!body || body.querySelector(".hitl-format-error")) return;
  block.classList.remove("hitl-pending");
  block.querySelector(".tool-status").innerHTML = '<span style="color:#f85149">参数无效</span>';
  const error = document.createElement("div");
  error.className = "hitl-format-error";
  error.textContent = `工具参数格式无效：${message}`;
  body.appendChild(error);
}

function renderStructuredForm(widgetEl, call, onSubmit) {
  const args = call.args || {};
  const normalized = normalizeStructuredFields(args.fields);
  if (!normalized.ok) return;
  widgetEl.classList.add("hitl-structured-form");
  const heading = document.createElement("div");
  heading.className = "hitl-form-heading";
  heading.innerHTML = `<strong>${escapeHtml(args.title || args.prompt || "请补充信息")}</strong>${args.reason || args.description ? `<p>${escapeHtml(args.reason || args.description)}</p>` : ""}`;
  widgetEl.appendChild(heading);

  const controls = new Map();
  for (const field of normalized.fields) {
    const row = document.createElement("label");
    row.className = "hitl-form-field";
    row.innerHTML = `<span>${escapeHtml(field.label)}${field.required ? '<em aria-label="必填">*</em>' : ""}</span>${field.description ? `<small>${escapeHtml(field.description)}</small>` : ""}`;
    let control;
    if (["select", "choice", "radio"].includes(field.type)) {
      control = document.createElement("select");
      control.innerHTML = '<option value="">请选择</option>';
      for (const option of field.options) {
        const node = document.createElement("option");
        node.value = option.value;
        node.textContent = option.description ? `${option.label} - ${option.description}` : option.label;
        control.appendChild(node);
      }
    } else if (["textarea", "multiline", "long_text"].includes(field.type)) {
      control = document.createElement("textarea");
      control.rows = 4;
    } else {
      control = document.createElement("input");
      control.type = field.type === "date" ? "date" : field.type === "number" ? "number" : "text";
    }
    control.name = field.id;
    control.placeholder = field.placeholder;
    control.required = field.required;
    control.value = field.defaultValue == null ? "" : String(field.defaultValue);
    row.appendChild(control);
    widgetEl.appendChild(row);
    controls.set(field.id, { control, field });
  }

  const error = document.createElement("p");
  error.className = "hitl-form-error hidden";
  widgetEl.appendChild(error);
  const submit = document.createElement("button");
  submit.type = "button";
  submit.className = "hitl-btn hitl-submit";
  submit.textContent = args.submit_label || "确认提交";
  submit.addEventListener("click", () => {
    const values = {};
    for (const [id, { control, field }] of controls) {
      if (field.required && !control.value) {
        error.textContent = `请填写${field.label}`;
        error.classList.remove("hidden");
        control.focus();
        return;
      }
      const selected = field.options.find((option) => option.value === control.value);
      values[id] = selected
        ? { value: selected.value, label: selected.label }
        : { value: control.value };
    }
    error.classList.add("hidden");
    submit.disabled = true;
    onSubmit([{ tool_call_id: call.tool_call_id, result: { confirmed: true, values } }]);
  });
  widgetEl.appendChild(submit);
}

function addErrorTo(assistant, message, retryOpts) {
  const wrap = assistant.row.querySelector(".bubble-wrap");
  const div = document.createElement("div");
  div.className = "error-bubble";
  div.textContent = "⚠ " + message;
  wrap.appendChild(div);
  if (retryOpts && retryOpts.branchFrom != null && retryOpts.message) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "msg-retry-btn";
    btn.textContent = "重试";
    btn.addEventListener("click", () => retryFromBranch(retryOpts));
    wrap.appendChild(btn);
  }
  scrollToBottom();
}

/* --------------------------- 工具函数 --------------------------- */
function localDayKey(iso) {
  const d = new Date(iso);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function formatDateSeparator(iso) {
  const d = new Date(iso);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const msgDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.round((today - msgDay) / 86400000);
  if (diffDays === 0) return "今天";
  if (diffDays === 1) return "昨天";
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

function formatMessageTime(iso) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(
    new Date(iso)
  );
}

function lastUserText() {
  const rows = el.messages.querySelectorAll(".msg-row.user .bubble");
  return rows.length ? rows[rows.length - 1].textContent : "";
}

function scrollToBottom() {
  el.messages.scrollTop = el.messages.scrollHeight;
}

function hasNonEmptyArgs(args) {
  if (args == null) return false;
  if (typeof args === "object" && !Array.isArray(args)) return Object.keys(args).length > 0;
  return true;
}

function pretty(v) {
  if (v == null) return "null";
  if (typeof v === "string") return v;
  try { return JSON.stringify(v, null, 2); } catch (_) { return String(v); }
}

function renderToolResult(result) {
  if (typeof result === "string") {
    return `<div class="md-content tool-result-md markdown-body">${renderMarkdown(result)}</div>`;
  }
  return `<pre>${escapeHtml(pretty(result))}</pre>`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* --------------------------- Markdown (marked + DOMPurify + hljs) --------------------------- */
let markdownReady = false;

function initMarkdown() {
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
    console.warn("[deep-agent] Markdown 库未加载，将回退为纯文本");
    return;
  }

  marked.use({
    gfm: true,
    breaks: false,
    renderer: {
      code(codeText, lang) {
        const text = String(codeText ?? "");
        let highlighted = escapeHtml(text);
        if (typeof hljs !== "undefined") {
          if (lang && hljs.getLanguage(lang)) {
            try {
              highlighted = hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
            } catch (_) { /* keep escaped fallback */ }
          } else {
            try {
              highlighted = hljs.highlightAuto(text).value;
            } catch (_) { /* keep escaped fallback */ }
          }
        }
        const langAttr = lang ? ` class="hljs language-${escapeHtml(lang)}"` : ' class="hljs"';
        return `<pre><code${langAttr}>${highlighted}</code></pre>\n`;
      },
    },
  });

  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A" && node.hasAttribute("href")) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });

  markdownReady = true;
}

function renderMarkdown(src) {
  const text = String(src ?? "");
  if (!markdownReady) return `<pre class="md-fallback">${escapeHtml(text)}</pre>`;

  const raw = marked.parse(text);
  return DOMPurify.sanitize(raw, {
    USE_PROFILES: { html: true },
    ADD_ATTR: ["target", "rel", "class", "data-lang"],
  });
}

function renderMarkdownBody(src) {
  return `<div class="markdown-body">${renderMarkdown(src)}</div>`;
}

window.DeepAgentConsoleChat = {
  state,
  newChat,
  loadAgents,
  loadCapabilities,
  parseSseBlock,
  readSse,
  syncSessionsFromBackend,
  stopStream,
  selectAgent(agentId) {
    if (!agentId || !state.agents.some((agent) => agent.id === agentId)) return;
    if (state.agentId === agentId) return;
    newChat(agentId);
  },
};

init();
