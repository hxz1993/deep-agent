import { prepareSessionRender } from "./session-loading.js?v=20260918-release01012";
import { savedSessionCounts, savedRunFacts, savedRunState } from "./session-presentation.js?v=20260918-release01012";
import { createConversationPresenter } from "./conversation-presenter.js?v=20260912-21";
import { buildChoiceResult, normalizeOption, normalizeStructuredFields, validateUiToolCall } from "./hitl.js?v=20260912-21";
import {
  contextCompactionPresentation,
  parseSseBlock,
  readSse,
} from "./protocol.js?v=20260912-21";
import { channelOriginPresentation, formatSessionIdSubtitle } from "./session-display.js?v=20260918-release01012";
import { copyTextToClipboard } from "./message-actions.js?v=20260912-21";
import {
  applyAssistantPresentationActions,
  assistantActionsFromDeferred,
  assistantActionsFromSse,
  isPlanningToolEvent,
  planPanelState,
  processDisclosureLabel,
  toolResultPresentation,
} from "./assistant-presentation.js?v=20260912-21";
import { createTrajectoryController } from "./trajectory.js?v=20260915-scroll-focus";
import { attachmentPreviewUrl, bindAttachmentImagePreviews, createImageGallery } from "./attachment-preview.js?v=20260912-21";
import { bindWebImagePreviewFallback, renderWebImagePreview } from "./web-image.js?v=20260912-21";
import { parseServerTimestamp, serverTimestampMilliseconds } from "./time.js?v=20260912-21";
import {
  latestPendingDeferredTool,
  normalizeConversationHistory,
  projectConversationItems,
} from "./conversation-items.js?v=20260912-21";
import {
  firstSelectableModelId,
  isModelSelectable,
  modelProvider,
  modelOptionLabel,
  preserveCurrentModel,
  renderGroupedModelOptions,
} from "./model-options.js?v=20260912-21";
import { shouldFollowMessages } from "./message-scroll.js?v=20260912-21";
import { boundedStreamRequest, verifyStreamStop } from "./stop-recovery.js?v=20260912-21";
import { createHistoryFollower } from "./history-follower.js?v=20260912-21";
import { createSessionObserver, historyThroughRun } from "./session-observer.js?v=20260912-21";

/* ------------------------------------------------------------------ *
 * deep-agent 前端 —— 适配 FastAPI /api/v1/chat/stream (SSE over POST)
 * 协议详见 docs/guides/sse-protocol.md
 *
 * 后端 SSE 事件 (event: <type>, data: <json>):
 *   session      -> { session_id, agent_id }
 *   context_compaction_progress -> { persisted:false, event_id, phase, message }
 *   session_event_appended/session_event_updated -> { persisted:true, event }
 *   text         -> { type:"text", delta }
 *   thinking     -> { type:"thinking", delta }
 *   tool_call    -> { type:"tool_call", tool, display_name?, args, tool_call_id, widget?, metadata? }
 *   tool_call_removed -> { type:"tool_call_removed", tool, display_name?, tool_call_id, reason }
 *   tool_result  -> { type:"tool_result", tool, display_name?, result, tool_call_id, artifacts? }
 *   final        -> { type:"final", status, output, usage, deferred? }
 *   cancelled    -> { type:"cancelled", status, usage, run_id }
 *   error        -> { type:"error", message }
 * ------------------------------------------------------------------ */

const DEFAULT_BASE_URL = defaultApiBaseUrl();

bindWebImagePreviewFallback();

const BUILTIN_UI_TOOLS = new Set(["builtin__confirm", "builtin__choice"]);
const LEGACY_BUILTIN_UI_TOOLS = new Set(["confirm_action", "ask_user_choice"]);
/** @type {Map<string, object>} */
const uiToolsByName = new Map();

function lucideIcon(name, className = "") {
  const classAttr = className ? ` class="${className}"` : "";
  return `<i data-lucide="${name}"${classAttr} aria-hidden="true"></i>`;
}

function refreshLucideIcons() {
  window.requestAnimationFrame(() => window.lucide?.createIcons());
}

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

async function loadUiTools(agentId, { signal, isCurrent = () => true } = {}) {
  try {
    const params = new URLSearchParams();
    if (agentId) params.set("agent_id", agentId);
    const r = await daFetch(serviceUrl(`api/v1/ui-tools?${params}`), { signal });
    if (!r.ok) return;
    const data = await r.json();
    if (!isCurrent() || signal?.aborted) return;
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
  uploadCapabilities: { kinds: [], labels: {}, max_count: 10, max_size_mb: 20 },
  llmModelId: "",
  sessionModelIds: {},
  sessionId: null,
  conversationId: null,
  sessions: [], // [{id, agentId, agentName, title}] — 仅来自 GET /api/v1/sessions
  streaming: false,
  observingRun: false,
  historyLoading: false,
  historyController: null,
  historyFollower: null,
  historyObservationPending: false,
  historyRendered: [],
  awaitingHitl: false,
  abortController: null,
  stopController: null,
  streamRecoveryNote: null,
  stopRequested: false,
  streamSessionConfirmed: false,
  refreshHistoryAfterStream: false,
  modelMessageCount: 0,
  pendingAttachments: [],
  contextStatus: null,
  lastRunUsage: null,
  lastRunMetrics: null,
  automaticCompactionRow: null,
  automaticCompactionTimer: null,
  pendingAutomaticCompaction: null,
  automaticCompactionAnchor: null,
  assistantPresenter: null,
  messageAutoFollow: true,
  messageScrollFrame: null,
  modelMenuOpen: false,
  composerDragDepth: 0,
  planSnapshot: null,
  planningCallIds: new Set(),
};

window.addEventListener("pagehide", () => {
  clearTimeout(sessionSearchTimer);
  sessionPage.controller?.abort();
  state.historyFollower?.stop();
  state.historyController?.abort();
  for (const record of state.historyRendered) record.presenter?.dispose?.();
  const streamController = state.abortController;
  state.abortController = null;
  state.stopController?.abort();
  state.stopController = null;
  streamController?.abort();
});

const LLM_MODALITY_LABELS = {
  text: "文本",
  image: "图像",
  audio: "音频",
  video: "视频",
};

const UPLOAD_KIND_ORDER = ["image", "text_file", "docx", "xlsx", "audio", "video"];
const COMPOSER_PROVIDER_BRANDS = {
  deepseek: "./assets/provider-logos/deepseek.ico",
  moonshotai: "./assets/provider-logos/kimi.ico",
  alibaba: "./assets/provider-logos/qwen.png",
  zai: "./assets/provider-logos/zai.svg",
  openrouter: "./assets/provider-logos/openrouter.png",
  openai: "./assets/provider-logos/openai.svg",
  anthropic: "./assets/provider-logos/anthropic.png",
  google: "./assets/provider-logos/google-gemini.png",
  openai_compatible: "./assets/provider-logos/openai-compatible.svg",
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
  copySessionIdBtn: $("copySessionIdBtn"),
  chatTitle: $("chatTitle"),
  newChatMenu: $("newChatMenu"),
  newChatBtnWide: $("newChatBtnWide"),
  attachBtn: $("attachBtn"),
  contextBudgetBadge: $("contextBudgetBadge"),
  fileInput: $("fileInput"),
  attachmentPreview: $("attachmentPreview"),
  composerMeta: $("composerMeta"),
  composerModelTrigger: $("composerModelTrigger"),
  composerModelMenu: $("composerModelMenu"),
  composerDropZone: $("composerDropZone"),
  composerDropOverlay: $("composerDropOverlay"),
  attachmentTooltip: $("attachmentTooltip"),
  contextBudgetTooltip: $("contextBudgetTooltip"),
  attachmentImageDialog: $("attachmentImageDialog"),
  attachmentImageDialogImage: $("attachmentImageDialogImage"),
  attachmentImageDialogName: $("attachmentImageDialogName"),
  attachmentImageDialogClose: $("attachmentImageDialogClose"),
  attachmentImageDialogPrevious: $("attachmentImageDialogPrevious"),
  attachmentImageDialogNext: $("attachmentImageDialogNext"),
  composerPlan: $("composerPlan"),
  composerPlanSummary: $("composerPlanSummary"),
  composerPlanProgress: $("composerPlanProgress"),
  composerPlanItems: $("composerPlanItems"),
  modelSelect: $("modelSelect"),
};
const trajectory = createTrajectoryController({ state });
let attachmentImageGallery = { items: [], index: 0 };

/* --------------------------- 初始化 --------------------------- */
function updateAdminLink() {
  const link = $("adminLink");
  if (link) link.href = "#/configuration";
}

/** Normalize local / API session records to display id + agentId. */
function normalizeSession(session) {
  if (typeof session === "string") {
    return { id: session, agentId: state.agentId || "" };
  }
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

function sessionApiUrl(session, suffix = "", extraParams = {}) {
  const s = normalizeSession(session);
  const params = new URLSearchParams();
  if (s.agentId) params.set("agent_id", s.agentId);
  for (const [key, value] of Object.entries(extraParams || {})) {
    if (value != null && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return serviceUrl(`api/v1/sessions/${encodeURIComponent(s.id)}${suffix}${qs ? `?${qs}` : ""}`);
}

function sessionLookupKey(session) {
  const s = normalizeSession(session);
  return `${s.agentId || ""}:${s.id}`;
}

function sessionSortTime(session) {
  return serverTimestampMilliseconds(session.updatedAt || session.createdAt);
}

function formatSessionActivity(session) {
  const value = session.updatedAt || session.createdAt;
  if (!value) return "时间未知";
  const date = parseServerTimestamp(value);
  if (!date) return "时间未知";

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

function sortSessions() {
  state.sessions.sort((a, b) => sessionSortTime(b) - sessionSortTime(a));
}

function hideEmptyState() {
  el.emptyState.classList.add("hidden");
}

function showEmptyState() {
  updateEmptyAgentName();
  el.emptyState.classList.remove("hidden");
  if (!el.messages.contains(el.emptyState)) {
    el.messages.appendChild(el.emptyState);
  }
}

function clearMessages() {
  for (const record of state.historyRendered) record.presenter?.dispose?.();
  state.historyRendered = [];
  clearAutomaticCompactionState();
  state.assistantPresenter = null;
  state.messageAutoFollow = true;
  clearComposerPlan();
  el.messages.innerHTML = "";
}

function clearComposerPlan() {
  state.planSnapshot = null;
  state.planningCallIds.clear();
  if (!el.composerPlan) return;
  el.composerPlan.classList.add("hidden");
  el.composerPlanProgress.textContent = "";
  el.composerPlanItems.innerHTML = "";
}

function setComposerPlanExpanded(expanded) {
  if (!el.composerPlan) return;
  el.composerPlan.classList.toggle("open", expanded);
  el.composerPlanSummary?.setAttribute("aria-expanded", String(expanded));
}

function showComposerPlanPlaceholder(toolCallId) {
  if (toolCallId) state.planningCallIds.add(toolCallId);
  if (!el.composerPlan || state.planSnapshot) return;
  const wasHidden = el.composerPlan.classList.contains("hidden");
  el.composerPlan.classList.remove("hidden");
  el.composerPlanProgress.textContent = "正在制定…";
  el.composerPlanItems.innerHTML = "";
  if (wasHidden) setComposerPlanExpanded(true);
  refreshLucideIcons();
}

function renderComposerPlan(snapshot) {
  const presentation = planPanelState(snapshot);
  if (!presentation || presentation.plan.completed === presentation.plan.total) {
    clearComposerPlan();
    return;
  }
  const wasHidden = el.composerPlan.classList.contains("hidden");
  state.planSnapshot = presentation.plan;
  el.composerPlan.classList.remove("hidden");
  el.composerPlanProgress.textContent = presentation.label;
  el.composerPlanItems.innerHTML = presentation.plan.items.map((item) => {
    const display = item.status === "in_progress" && item.active_form
      ? item.active_form
      : item.content;
    const icon = item.status === "completed"
      ? "circle-check"
      : item.status === "in_progress" ? "loader-circle" : "circle";
    return `<li class="composer-plan-item status-${escapeHtml(item.status)}">
      <span class="composer-plan-status">${lucideIcon(icon)}</span>
      <span>${escapeHtml(display)}</span>
    </li>`;
  }).join("");
  if (wasHidden) setComposerPlanExpanded(true);
  refreshLucideIcons();
}

function removePlanningCall(toolCallId) {
  if (!toolCallId || !state.planningCallIds.delete(toolCallId)) return;
  if (!state.planSnapshot && state.planningCallIds.size === 0) clearComposerPlan();
}

function currentSessionSnapshot() {
  if (!state.sessionId) return null;
  const persisted = state.sessions.find(
    (session) => session.id === state.sessionId && (session.agentId || "") === (state.agentId || ""),
  );
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
    turnCount: persisted?.turnCount || 0,
    attachmentCount: persisted?.attachmentCount || 0,
    channel: persisted?.channel || null,
    appId: persisted?.appId ?? null,
    spaceId: persisted?.spaceId ?? null,
  });
}

function mergeSessionsFromBackend(rows, { append = false, agentId = state.agentId } = {}) {
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
      turnCount: Number(row.turn_count) || 0,
      attachmentCount: Number(row.attachment_count) || 0,
      channel: row.channel || null,
      appId: row.app_id ?? null,
      spaceId: row.space_id ?? null,
    });
  });

  const retained = state.sessions.filter((session) => append || (session.agentId || "") !== (agentId || ""));
  const mergedByKey = new Map([...retained, ...mapped].map((s) => [sessionLookupKey(s), s]));

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
    void selectAgent(el.agentSelect.value);
  });
  el.modelSelect?.addEventListener("change", () => {
    selectComposerModel(el.modelSelect.value);
  });
  el.composerModelTrigger?.addEventListener("click", () => {
    setComposerModelMenuOpen(!state.modelMenuOpen);
  });
  el.copySessionIdBtn?.addEventListener("click", copyCurrentSessionId);

  $("newChatBtn").addEventListener("click", handleNewChatControl);
  el.newChatBtnWide?.addEventListener("click", (e) => {
    handleNewChatControl(e);
  });
  document.addEventListener("click", (e) => {
    if (state.modelMenuOpen && !e.target.closest("#composerMeta")) {
      setComposerModelMenuOpen(false);
    }
    if (el.newChatMenu?.classList.contains("hidden")) return;
    if (e.target.closest("#newChatMenu, #newChatBtn, #newChatBtnWide")) return;
    closeNewChatMenu();
  });
  document.addEventListener("click", (e) => {
    if (!document.getElementById("workspaceGrid")?.contains(e.target)) return;
    const button = e.target.closest("[data-copy-file-id]");
    if (!button) return;
    const fileId = button.dataset.copyFileId;
    if (!fileId) return;
    button.disabled = true;
    copyTextToClipboard(fileId).then(() => {
      button.classList.add("copied");
      button.title = "File ID 已复制";
      button.innerHTML = lucideIcon("check");
      refreshLucideIcons();
      window.setTimeout(() => {
        if (!button.isConnected) return;
        button.classList.remove("copied");
        button.title = "复制 File ID";
        button.innerHTML = lucideIcon("copy");
        button.disabled = false;
        refreshLucideIcons();
      }, 1500);
    }).catch(() => {
      button.disabled = false;
    });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeNewChatMenu();
      setComposerModelMenuOpen(false);
    }
  });
  el.attachmentImageDialogClose?.addEventListener("click", closeAttachmentImagePreview);
  bindAttachmentImagePreviews({ root: document.getElementById("workspaceGrid"), openImage: openAttachmentImagePreview });
  el.attachmentImageDialog?.addEventListener("click", (event) => {
    if (event.target === el.attachmentImageDialog || event.target.classList.contains("attachment-image-dialog-content")) closeAttachmentImagePreview();
  });
  el.attachmentImageDialogPrevious?.addEventListener("click", () => moveAttachmentImagePreview(-1));
  el.attachmentImageDialogNext?.addEventListener("click", () => moveAttachmentImagePreview(1));
  el.attachmentImageDialog?.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      moveAttachmentImagePreview(event.key === "ArrowLeft" ? -1 : 1);
    }
  });
  el.attachmentImageDialog?.addEventListener("close", () => {
    el.attachmentImageDialogImage?.removeAttribute("src");
    attachmentImageGallery = { items: [], index: 0 };
  });
  $("toggleSidebar").addEventListener("click", () => el.sidebar.classList.toggle("collapsed"));
  $("refreshCapsBtn").addEventListener("click", loadCapabilities);
  el.sendBtn.addEventListener("click", onSend);
  el.stopBtn.addEventListener("click", stopStream);
  el.composerPlanSummary?.addEventListener("click", () => {
    setComposerPlanExpanded(!el.composerPlan.classList.contains("open"));
  });

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
  el.messages?.addEventListener("scroll", () => {
    state.messageAutoFollow = state.messageAutoFollow
      ? shouldFollowMessages(el.messages)
      : shouldFollowMessages(el.messages, 4);
  }, { passive: true });
  el.messages?.addEventListener("wheel", (event) => {
    if (event.deltaY < 0) state.messageAutoFollow = false;
  }, { passive: true });
  el.composerDropZone?.addEventListener("dragenter", onComposerDragEnter);
  el.composerDropZone?.addEventListener("dragover", onComposerDragOver);
  el.composerDropZone?.addEventListener("dragleave", onComposerDragLeave);
  el.composerDropZone?.addEventListener("drop", onComposerDrop);

  // 聊天标题点击编辑
  el.chatTitle.addEventListener("click", () => {
    if (state.streaming || !state.sessionId || el.chatTitle.querySelector("input")) return;
    const sessionId = state.sessionId;
    const agentId = state.agentId;
    const currentTitle = el.chatTitle.textContent;
    const titleWidth = el.chatTitle.getBoundingClientRect().width;
    const input = document.createElement("input");
    input.type = "text";
    input.className = "chat-title-input";
    input.value = currentTitle;
    input.setAttribute("aria-label", "会话标题");
    input.style.width = `${Math.max(180, titleWidth)}px`;
    input.maxLength = 40;
    input.placeholder = "输入会话名称…";

    el.chatTitle.textContent = "";
    el.chatTitle.appendChild(input);
    input.focus();
    input.select();

    let finished = false;
    const finishEdit = async (save) => {
      if (finished) return;
      finished = true;
      if (!el.chatTitle.contains(input) || state.sessionId !== sessionId || state.agentId !== agentId) return;
      const newTitle = input.value.trim();
      save = save && Boolean(newTitle);
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

    input.addEventListener("click", (event) => event.stopPropagation());
    input.addEventListener("blur", () => finishEdit(true));
    input.addEventListener("keydown", (e) => {
      if (e.isComposing) return;
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
  el.sessionList.addEventListener("scroll", maybeLoadMoreSessions, { passive: true });
  document.getElementById("sessionSearchInput")?.addEventListener("input", scheduleSessionSearch);
  document.getElementById("sessionSearchInput")?.addEventListener("compositionend", scheduleSessionSearch);
  bindSessionListFeedback();
  checkHealth();
  loadCapabilities();
  loadUploadCapabilities();
  loadAgents().then(async () => {
    await loadLlmModels();
    loadUiTools(state.agentId);
    if (agentFromUrl) newChat(agentFromUrl);
    else updateAgentSelectState();
    await syncSessionsFromBackend();
  });
}

let sessionPage = { agentId: null, offset: 0, hasMore: true, loading: false, error: false };
let sessionSearchTimer = null;

function sessionSearchQuery() {
  return document.getElementById("sessionSearchInput")?.value.trim() || "";
}

function resetSessionPage() {
  clearTimeout(sessionSearchTimer);
  sessionSearchTimer = null;
  sessionPage.controller?.abort();
  sessionPage = {
    agentId: state.agentId, query: sessionSearchQuery(), ids: new Set(),
    offset: 0, hasMore: true, loading: false, error: false, scheduled: false,
  };
  el.sessionList.scrollTop = 0;
}

function scheduleSessionSearch(event) {
  // Invalidate immediately: neither an old page nor an old search may append
  // while a new input is waiting for its debounce (including IME input).
  resetSessionPage();
  sessionPage.scheduled = true;
  renderSessions();
  if (event?.isComposing) return;
  sessionSearchTimer = setTimeout(() => {
    sessionSearchTimer = null;
    sessionPage.scheduled = false;
    void loadMoreSessions();
  }, 250);
}

function syncSessionsFromBackend() {
  if (!state.agentId) return Promise.resolve();
  if (sessionPage.agentId === state.agentId && sessionPage.query === sessionSearchQuery()
      && sessionPage.loading && sessionPage.offset === 0) return sessionPage.promise;
  resetSessionPage();
  return loadMoreSessions();
}

async function loadMoreSessions() {
  const page = sessionPage;
  if (!page.agentId || page.agentId !== state.agentId || page.loading || page.scheduled || !page.hasMore) return;
  page.loading = true;
  page.error = false;
  page.controller = new AbortController();
  renderSessions();
  const request = (async () => {
  try {
    const params = new URLSearchParams({ agent_id: page.agentId, limit: "50", offset: String(page.offset) });
    if (page.query) params.set("query", page.query);
    const r = await daFetch(serviceUrl(`api/v1/sessions?${params}`), { signal: page.controller.signal });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const payload = await r.json();
    if (page !== sessionPage || page.agentId !== state.agentId || page.controller.signal.aborted) return;
    const rows = Array.isArray(payload?.items) ? payload.items : [];
    for (const row of rows) page.ids.add(row.session_id);
    mergeSessionsFromBackend(rows, { append: page.offset > 0, agentId: page.agentId });
    page.offset += rows.length;
    page.hasMore = rows.length > 0 && page.offset < Number(payload.total);
  } catch (error) {
    if (page === sessionPage && error.name !== "AbortError") page.error = true;
  } finally {
    page.loading = false;
    if (page === sessionPage && page.agentId === state.agentId) {
      renderSessions();
      requestAnimationFrame(maybeLoadMoreSessions);
    }
  }
  })();
  page.promise = request;
  return request;
}

function maybeLoadMoreSessions() {
  const list = el.sessionList;
  if (list.scrollTop > 0 && list.scrollHeight > list.clientHeight) sessionPage.hasScrolled = true;
  updateSessionListFooter();
  if (!sessionPage.error && list.clientHeight > 0 && list.scrollHeight - list.scrollTop - list.clientHeight < 80) {
    void loadMoreSessions();
  }
}

function updateSessionListFooter() {
  const list = el.sessionList;
  const footer = list.querySelector(".session-list-footer");
  if (!footer) return;
  const rows = list.querySelectorAll(".session-item:not(.console-filtered)");
  const last = rows[rows.length - 1];
  // Measure history only: the footer must not make a short list qualify as a full screen.
  const contentHeight = last ? last.offsetTop + last.offsetHeight - rows[0].offsetTop : 0;
  // A screen is full when there is not enough room for another complete row.
  const fillsViewport = !!last && list.clientHeight > 0 && contentHeight + last.offsetHeight > list.clientHeight;
  footer.hidden = !sessionPage.loading && !sessionPage.error && !sessionPage.hasMore
    && !(sessionPage.hasScrolled && fillsViewport);
}

function bindSessionListFeedback() {
  const list = el.sessionList;
  let feedbackPlayed = false;
  let wheelIdleTimer = null;
  const feedback = (deltaY) => {
    if (!deltaY) return;
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight <= 1;
    sessionPage.hasScrolled = true;
    updateSessionListFooter();
    if (deltaY <= 0 || !atBottom || feedbackPlayed
      || sessionPage.hasMore || sessionPage.loading || sessionPage.error
      || list.classList.contains("session-edge-feedback")
      || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    feedbackPlayed = true;
    list.classList.add("session-edge-feedback");
    setTimeout(() => list.classList.remove("session-edge-feedback"), 200);
  };
  list.addEventListener("wheel", (event) => {
    if (event.ctrlKey || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
    // A wheel gesture includes its momentum tail, even after the short animation ends.
    clearTimeout(wheelIdleTimer);
    wheelIdleTimer = setTimeout(() => { feedbackPlayed = false; }, 350);
    feedback(event.deltaY);
  }, { passive: true });
  let touchY = null;
  list.addEventListener("touchstart", (event) => {
    clearTimeout(wheelIdleTimer);
    feedbackPlayed = false;
    touchY = event.touches.length === 1 ? event.touches[0].clientY : null;
  }, { passive: true });
  list.addEventListener("touchmove", (event) => {
    if (touchY === null || event.touches.length !== 1) return;
    const nextY = event.touches[0].clientY;
    feedback(touchY - nextY);
    touchY = nextY;
  }, { passive: true });
  list.addEventListener("touchend", () => { touchY = null; }, { passive: true });
  list.addEventListener("touchcancel", () => { touchY = null; }, { passive: true });
  new ResizeObserver(updateSessionListFooter).observe(list);
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
  if (state.streaming && !state.observingRun) {
    showConsoleToast("请先停止当前生成，再新建会话", true);
    return;
  }
  stopHistoryObservation();
  if (id) {
    state.agentId = id;
    localStorage.setItem("da_console_agent_id_v1", state.agentId);
    if (el.agentSelect) el.agentSelect.value = state.agentId;
  } else {
    ensureAgentSelected();
  }
  loadUiTools(state.agentId);
  state.sessionId = null;
  state.conversationId = null;
  state.awaitingHitl = false;
  state.modelMessageCount = 0;
  state.contextStatus = null;
  state.lastRunUsage = null;
  state.lastRunMetrics = null;
  state.llmModelId = defaultLlmModelId();
  clearPendingAttachments();
  clearMessages();
  trajectory?.reset();
  showEmptyState();
  el.sessionBadge.textContent = "";
  el.chatTitle.textContent = "新对话";
  updateChatHeader();
  updateAgentSelectState();
  updateHitlInputState();
  updateComposerContextControls();
  renderSessions();
  el.input.focus();
  if (sessionPage.agentId !== state.agentId) {
    el.sessionList.scrollTop = 0;
    void syncSessionsFromBackend();
  }
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

function renderSessions() {
  const scrollTop = el.sessionList.scrollTop;
  el.sessionList.innerHTML = "";
  const activeAgent = state.agents.find((agent) => agent.id === state.agentId);
  const scopedSessions = state.sessions.filter(
    (session) => (session.agentId || "") === (state.agentId || "")
      && !(sessionPage.scheduled || (sessionPage.loading && sessionPage.offset === 0))
      && (!sessionPage.query || sessionPage.ids?.has(session.id)),
  );
  const panelTitle = document.getElementById("sessionPanelTitle");
  const panelSummary = document.getElementById("sessionPanelSummary");
  if (panelTitle) panelTitle.textContent = activeAgent?.name || "Agent 会话";
  if (panelSummary) {
    panelSummary.textContent = `${scopedSessions.length} 个历史会话`;
    panelSummary.title = state.sessionId ? "已选择会话" : "尚未选择会话";
  }
  if (scopedSessions.length === 0) {
    if (!sessionPage.loading && !sessionPage.scheduled && !sessionPage.error) {
      el.sessionList.innerHTML = `<li class="session-empty">${sessionPage.query ? "未找到匹配会话" : "该 Agent 暂无历史会话"}</li>`;
    }
  }
  for (const s of scopedSessions) {
    const li = document.createElement("li");
    li.className =
      "session-item" +
      (s.id === state.sessionId && (s.agentId || "") === (state.agentId || "") ? " active" : "");
    const activity = formatSessionActivity(s);
    const origin = channelOriginPresentation(s.channel);
    const sourceMarker = origin ? `<span class="session-channel-source" title="${escapeHtml(origin.description)}" aria-label="${escapeHtml(origin.description)}">${lucideIcon("radio")}</span>` : "";
    li.innerHTML = `<span class="sid" title="${escapeHtml(`${s.title} · ${activity}`)}"><span class="session-title-row"><span class="session-title">${escapeHtml(s.title)}</span>${sourceMarker}</span><span class="session-meta"><span class="session-activity">${escapeHtml(activity)}</span><span class="session-stat-group"><span title="提问数">${lucideIcon("message-circle-more")}${escapeHtml(String(s.turnCount || 0))}</span></span></span></span><span class="session-actions"><span class="rename-btn" title="重命名" aria-label="重命名">${lucideIcon("pencil")}</span><span class="del" title="删除" aria-label="删除">${lucideIcon("trash-2")}</span></span>`;
    li.querySelector(".sid").addEventListener("click", (e) => {
      if (e.target.closest(".session-title-input")) return;
      switchSession(s);
    });
    li.querySelector(".rename-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      startInlineEdit(s, li.querySelector(".session-title"));
    });
    li.querySelector(".del").addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSession(s);
    });
    el.sessionList.appendChild(li);
  }
  const footer = document.createElement("li");
  footer.className = "session-list-footer";
  footer.setAttribute("role", "status");
  if (sessionPage.error) {
    footer.innerHTML = '<button type="button">加载失败，点击重试</button>';
    footer.querySelector("button").addEventListener("click", () => void loadMoreSessions());
  } else {
    footer.textContent = sessionPage.loading || sessionPage.scheduled ? "正在加载…" : sessionPage.hasMore ? "向下滚动加载更多" : "已经到底了";
  }
  footer.hidden = !sessionPage.loading && !sessionPage.error && !sessionPage.hasMore && !sessionPage.hasScrolled;
  el.sessionList.appendChild(footer);
  el.sessionList.scrollTop = scrollTop;
  updateSessionListFooter();
  refreshLucideIcons();
}

async function switchSession(s) {
  if (state.streaming && !state.observingRun) {
    showConsoleToast("请先停止当前生成，再切换会话", true);
    return;
  }
  stopHistoryObservation();
  const session = normalizeSession(s);
  state.sessionId = session.id;
  state.lastRunUsage = null;
  state.lastRunMetrics = null;
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
async function loadAllSessionEvents(session, { signal } = {}) {
  const items = [];
  let afterSequence = 0;
  for (;;) {
    const response = await daFetch(
      sessionApiUrl(session, "/events", { after_sequence: afterSequence, limit: 500 }),
      { signal },
    );
    if (!response.ok) return { response, data: null };
    const page = await response.json();
    items.push(...(Array.isArray(page.items) ? page.items : []));
    const next = Number(page.next_after_sequence);
    if (!Number.isInteger(next) || next <= afterSequence) {
      return { response, data: { items, next_after_sequence: null } };
    }
    afterSequence = next;
  }
}

async function loadSessionHistory(session, { signal, isCurrent = () => true } = {}) {
  const normalized = normalizeSession(session);
  stopHistoryObservation();
  const controller = new AbortController();
  state.historyController = controller;
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) abort();
  const current = () => !controller.signal.aborted && isCurrent()
    && state.historyController === controller
    && state.sessionId === normalized.id && state.agentId === normalized.agentId;
  state.historyLoading = true;
  state.historyReady = false;
  globalThis.DeepAgentConsoleWorkspace?.setHistoryLoading?.(true);
  updateHitlInputState();
  clearMessages();
  trajectory?.setHistory({ session_id: normalized.id, items: [] });
  hideEmptyState();
  try {
    await prepareSessionRender();
    if (!current()) return;
    await boundedStreamRequest((requestSignal) => loadUiTools(normalized.agentId, {
      signal: requestSignal, isCurrent: current,
    }), { signal: controller.signal }).catch(() => {});
    if (!current()) return;
    if (state.abortController) {
      // Abnormal SSE recovery may still own its transport's finally block.
      // Read once here, then follow only after that block releases ownership.
      const [response, events] = await Promise.all([
        daFetch(sessionApiUrl(normalized, "/history"), { signal: controller.signal }),
        loadAllSessionEvents(normalized, { signal: controller.signal }),
      ]);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const history = normalizeConversationHistory(await response.json());
      if (!current()) return;
      applyObservedHistory(history, { status: "running" });
      if (events.response.ok) trajectory?.setHistory(events.data);
      state.historyObservationPending = true;
    } else {
      await observeSessionHistory(normalized, controller, current);
    }
    if (current()) void loadContextStatus(normalized, { signal: controller.signal, isCurrent: current });
  } catch (e) {
    if (!current()) return;
    addSystemNote(`加载历史失败：${e.message}`);
    globalThis.DeepAgentConsoleWorkspace?.setHistoryLoading?.(false);
    if (state.abortController) state.historyObservationPending = true;
  } finally {
    signal?.removeEventListener("abort", abort);
    await prepareSessionRender();
    if (state.historyController === controller && (state.historyReady || controller.signal.aborted)) globalThis.DeepAgentConsoleWorkspace?.setHistoryLoading?.(false);
  }
}

function stopHistoryObservation() {
  globalThis.DeepAgentConsoleWorkspace?.setHistoryLoading?.(false);
  state.historyFollower?.stop();
  state.historyFollower = null;
  state.historyController?.abort();
  state.historyController = null;
  state.historyLoading = false;
  state.historyObservationPending = false;
  if (state.observingRun) {
    state.stopController?.abort();
    state.stopController = null;
    state.stopRequested = false;
    state.observingRun = false;
    state.streamSessionConfirmed = false;
    setStreaming(false);
  }
  updateHitlInputState();
}

async function observeSessionHistory(session, controller, current) {
  let status;
  try {
    status = await boundedStreamRequest(async (signal) => {
      const response = await daFetch(sessionApiUrl(session, "/run-status"), { signal });
      if (!response.ok) throw new Error(`运行状态 HTTP ${response.status}`);
      return response.json();
    }, { signal: controller.signal });
  } catch {
    if (current()) return observePersistedSessionHistory(session, controller, current, {
      snapshotOnly: true, readOnly: true,
    });
    return;
  }
  if (!current()) return;
  if (status.status === "idle" || !status.run_id) {
    return observePersistedSessionHistory(session, controller, current, {
      snapshotOnly: true, readOnly: status.status !== "idle",
    });
  }
  const runId = status.run_id;
  let baselineSequence = null;
  let assistant = null;
  let fallback = null;
  let lastError = null;
  let liveRevision = 0;
  const discardPreview = () => {
    assistant?.dispose?.();
    assistant?.row?.remove();
    assistant = null;
  };
  const readHistory = async (signal, { replay = false } = {}) => {
    const data = await boundedStreamRequest(async (requestSignal) => {
      const response = await daFetch(sessionApiUrl(session, "/history"), { signal: requestSignal });
      if (!response.ok) throw new Error(`历史 HTTP ${response.status}`);
      return response.json();
    }, { signal });
    const history = historyThroughRun(normalizeConversationHistory(data), runId, { replay, baselineSequence });
    if (!current() || signal.aborted) return;
    discardPreview();
    applyObservedHistory(history, { status: replay ? "running" : "idle" });
    return history;
  };
  const refreshTrajectory = async (signal, { replay = false } = {}) => {
    const revision = liveRevision;
    const events = await loadAllSessionEvents(session, { signal });
    if (current() && !signal.aborted && events.response.ok && revision === liveRevision) {
      const items = Number.isInteger(baselineSequence)
        ? events.data.items.filter((event) => event.sequence <= baselineSequence || (!replay && event.run_id === runId))
        : events.data.items;
      trajectory?.setHistory({ ...events.data, items }, { preserveView: true });
      updateObservedSessionMetrics(session, items);
    }
  };
  const setObservedStatus = (active) => {
    state.historyLoading = false;
    state.observingRun = active;
    state.streamSessionConfirmed = active;
    if (!active) state.stopRequested = false;
    setStreaming(active);
    updateHitlInputState();
  };
  const makeAssistant = () => {
    hideEmptyState();
    assistant = createAssistantMessage(null, null, null, { autoAttachDeferred: false });
    state.assistantPresenter = assistant;
    return assistant;
  };
  const observer = createSessionObserver({
    runId,
    isCurrent: current,
    connect: (signal) => daFetch(sessionApiUrl(session, "/live", { run_id: runId }), {
      signal, headers: { Accept: "text/event-stream" },
    }),
    onSync: async (data, signal) => {
      baselineSequence = data.baseline_sequence;
      fallback?.stop();
      fallback = null;
      await readHistory(signal, { replay: true });
      if (!current() || signal.aborted) return;
      // Install the audit baseline before replaying its live mirrors; a late
      // Event API response must not replace newly received thinking/text rows.
      await boundedStreamRequest((requestSignal) => refreshTrajectory(requestSignal, { replay: true }), {
        signal, timeoutMs: 5000,
      }).catch(() => {});
      if (!current() || signal.aborted) return;
      setObservedStatus(true);
      lastError = null;
    },
    onEvent: async (frame, signal) => {
      if (!current() || signal.aborted) return;
      liveRevision += 1;
      // Input commits after the first session frame. Reload its authoritative
      // projection before replaying any model deltas (attachments/refs included).
      if (frame.event === "session_event_appended" && frame.data?.event?.event_type === "user/message") {
        await readHistory(signal, { replay: true });
        if (!current() || signal.aborted) return;
      }
      handleEvent({ event: frame.event, data: frame.data }, assistant || makeAssistant(), { allowDeferred: false });
    },
    onEnd: async (_data, signal) => {
      await readHistory(signal);
      if (!current() || signal.aborted) return;
      setObservedStatus(false);
      void refreshTrajectory(controller.signal).catch(() => {});
      void loadContextStatus(session, { signal: controller.signal, isCurrent: current });
    },
    onError: (error) => {
      if (error.message !== lastError) showConsoleToast(`实时连接中断，正在同步已保存内容：${error.message}`, true);
      lastError = error.message;
    },
    onFallback: async () => {
      if (!current() || fallback) return;
      discardPreview();
      fallback = await observePersistedSessionHistory(session, controller, current, {
        runId, baselineSequence, register: false, onIdle: () => observer.stop(),
      });
    },
  });
  state.historyFollower = observer;
  controller.signal.addEventListener("abort", () => {
    observer.stop();
    fallback?.stop();
    assistant?.dispose?.();
  }, { once: true });
  await observer.start();
}

async function observePersistedSessionHistory(session, controller, current, {
  runId = null, baselineSequence = null, register = true, onIdle,
  snapshotOnly = false, readOnly = false,
} = {}) {
  let lastHistory = null;
  let lastStatus = null;
  let lastError = null;
  let eventRequest = null;
  let eventsNeedRefresh = false;
  let trajectoryLoaded = false;
  const refreshTrajectory = () => {
    if (!current() || state.abortController) return;
    if (eventRequest) { eventsNeedRefresh = true; return; }
    eventsNeedRefresh = false;
    // A slow/failed audit request must not hold up visible History or leave
    // the stop button spinning after the authoritative terminal read.
    eventRequest = boundedStreamRequest((signal) => loadAllSessionEvents(session, { signal }), {
      signal: controller.signal, timeoutMs: 10000,
    }).then((events) => {
      if (!current() || state.abortController || !events.response.ok) return;
      const history = historyThroughRun(events.data, runId, { baselineSequence });
      trajectory?.setHistory(history, { preserveView: trajectoryLoaded });
      updateObservedSessionMetrics(session, history.items);
      trajectoryLoaded = true;
    }).catch(() => {}).finally(() => {
      eventRequest = null;
      if (eventsNeedRefresh && current()) refreshTrajectory();
    });
  };
  const follower = createHistoryFollower({
    isCurrent: current,
    readStatus: async (signal) => {
      if (snapshotOnly) return { status: "idle", read_only: readOnly };
      const response = await daFetch(sessionApiUrl(session, "/run-status"), { signal });
      if (!response.ok) throw new Error(`运行状态 HTTP ${response.status}`);
      const status = await response.json();
      // Reconnection is tied to the original Run, never a later channel turn.
      return runId && status.run_id !== runId
        ? { status: "idle", read_only: status.status !== "idle" }
        : status;
    },
    readHistory: async (signal, status) => {
      const response = await daFetch(sessionApiUrl(session, "/history"), { signal });
      if (!response.ok) throw new Error(`历史 HTTP ${response.status}`);
      const history = historyThroughRun(normalizeConversationHistory(await response.json()), runId, { baselineSequence });
      // Checkpoint updates retain their Sequence, so compare payloads rather
      // than treating event_head_sequence as a change/version token.
      const signature = JSON.stringify(history);
      const changed = signature !== lastHistory || status.status !== lastStatus;
      return { history, signature, changed };
    },
    onStatus: (status) => {
      state.historyLoading = false;
      state.observingRun = status.status !== "idle";
      state.streamSessionConfirmed = state.observingRun;
      if (!state.observingRun) state.stopRequested = false;
      setStreaming(state.observingRun);
      if (status.status === "cancel_requested" || state.stopRequested) {
        el.stopBtn.disabled = state.stopRequested;
        el.stopBtn.title = state.stopRequested ? "正在停止…" : "正在停止，可重试";
        el.stopBtn.setAttribute("aria-label", "正在停止");
      }
      if (status.status === "idle") onIdle?.();
    },
    onHistory: async ({ history, signature, changed }, status) => {
      if (changed) {
        applyObservedHistory(history, status);
        refreshTrajectory();
      }
      lastHistory = signature;
      lastStatus = status.status;
      lastError = null;
      if (status.status === "idle") void loadContextStatus(session, { signal: controller.signal, isCurrent: current });
      // Initial failures retry after start() has returned. Only an actual History
      // render can release the opening transition, including an empty session.
      await prepareSessionRender();
      if (current()) globalThis.DeepAgentConsoleWorkspace?.setHistoryLoading?.(false);
    },
    onError: (error) => {
      // An unavailable status/history endpoint does not prove the Run ended.
      // Keep the draft and last visible history, then retry without overlap.
      if (!state.historyReady) globalThis.DeepAgentConsoleWorkspace?.setHistoryLoading?.("加载失败，正在重试…");
      if (error.message !== lastError) showConsoleToast(`会话同步失败，正在重试：${error.message}`, true);
      lastError = error.message;
    },
  });
  if (register) state.historyFollower = follower;
  controller.signal.addEventListener("abort", () => follower.stop(), { once: true });
  await follower.start();
  return follower;
}

function updateObservedSessionMetrics(session, events) {
  const selected = state.sessions.find((item) => item.id === session.id && item.agentId === session.agentId);
  if (!selected) return;
  const { messageCount, turnCount } = savedSessionCounts(events);
  // Saved events aggregate a whole Run; a projected assistant is only one message.
  if (!state.abortController) {
    const saved = savedRunState(events);
    const facts = savedRunFacts(events.filter(event => (event.run_id ?? null) === saved.run_id));
    const previous = state.lastRunMetrics?.run_id === saved.run_id ? state.lastRunMetrics : {};
    state.lastRunUsage = facts.usage;
    state.lastRunMetrics = { ...previous, ...facts, ...saved, detailed: false };
    selected.modelName = [...events].reverse().find(event => event.event_type === "assistant/message" && event.payload?.message?.model_name)?.payload.message.model_name || null;
  }
  if (selected.messageCount === messageCount && selected.turnCount === turnCount) return;
  Object.assign(selected, { messageCount, turnCount });
  // Inspector observes these counters directly; do not dismiss an inline title
  // edit to refresh the sidebar's round count.
  if (!el.sessionList.querySelector(".session-title-input")) renderSessions();
}

function applyObservedHistory(history, status) {
  const latestAssistant = [...history.items].reverse().find((item) => item.kind === "assistant_message");
  if (state.lastRunMetrics?.run_id !== latestAssistant?.run_id) state.lastRunUsage = null;
  state.conversationId = latestAssistant?.conversation_id || null;
  state.lastRunMetrics = latestAssistant ? {
    usage: latestAssistant.usage || null,
    conversation_id: latestAssistant.conversation_id || null,
    model_name: latestAssistant.model_name || null,
    provider_name: latestAssistant.provider_name || null,
    run_id: latestAssistant.run_id,
    status: latestAssistant.status || "completed",
    detailed: false,
  } : null;
  if (history.items.length) hideEmptyState();
  renderHistory(history.items, {
    updating: state.historyRendered.length > 0,
    running: status.status !== "idle" || status.read_only === true,
  });
  if (!history.items.length) showEmptyState();
  state.historyReady = true;
}

async function loadContextStatus(session, { signal, isCurrent = () => true } = {}) {
  const lastCompaction = state.contextStatus?.last_compaction;
  try {
    const response = await daFetch(
      sessionApiUrl(session, "/context-status", { model_id: chatPayloadModelId() }),
      { signal },
    );
    const context = response.ok ? await response.json() : null;
    if (!isCurrent() || signal?.aborted) return;
    state.contextStatus = context;
    if (state.contextStatus && lastCompaction) {
      state.contextStatus.last_compaction = lastCompaction;
    }
  } catch {
    if (!isCurrent() || signal?.aborted) return;
    if (!lastCompaction) state.contextStatus = null;
  }
  updateComposerContextControls();
}

function compactTokenLabel(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}m`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 100_000 ? 0 : 1)}k`;
  return String(Math.round(n));
}

function updateComposerContextControls() {
  const status = state.contextStatus || {};
  const estimated = Number(status.estimated_history_tokens);
  const usable = Number(status.usable_request_tokens);
  const ratio = Number.isFinite(estimated) && Number.isFinite(usable) && usable > 0
    ? estimated / usable
    : null;
  if (el.contextBudgetBadge) {
    const percent = ratio == null ? null : Math.max(0, Math.min(100, Math.round(ratio * 100)));
    el.contextBudgetBadge.classList.toggle("reminder", ratio != null && ratio >= 0.7 && ratio < 0.8);
    el.contextBudgetBadge.classList.toggle("warning", ratio != null && ratio >= 0.8);
    el.contextBudgetBadge.style.setProperty("--context-progress", `${(percent || 0) * 3.6}deg`);
    const detail = ratio == null
      ? "上下文用量暂不可用"
      : `上下文窗口：已用 ${percent}%\n${compactTokenLabel(estimated)} / ${compactTokenLabel(usable)} Token`;
    el.contextBudgetBadge.setAttribute("aria-label", detail.replace("\n", "，"));
    el.contextBudgetBadge.removeAttribute("title");
    if (el.contextBudgetTooltip) el.contextBudgetTooltip.textContent = detail;
  }

  if (el.attachBtn) el.attachBtn.disabled = state.streaming || state.awaitingHitl;
}

function createContextCompactionRow(beforeNode = null) {
  hideEmptyState();
  const row = document.createElement("div");
  row.className = "context-compaction-row";
  row.innerHTML = '<span class="spinner" aria-hidden="true"></span><span class="status-text">正在分析上下文…</span>';
  if (beforeNode?.parentNode === el.messages) {
    el.messages.insertBefore(row, beforeNode);
  } else {
    el.messages.appendChild(row);
  }
  scrollToBottom();
  return row;
}

function setContextCompactionRow(row, message, { failed = false, active = true } = {}) {
  if (!row) return;
  row.classList.toggle("failed", failed);
  row.innerHTML = `${active && !failed ? '<span class="spinner" aria-hidden="true"></span>' : ""}<span class="status-text"></span>`;
  row.querySelector(".status-text").textContent = message;
  scrollToBottom();
}

function clearAutomaticCompactionState({ remove = false } = {}) {
  state.pendingAutomaticCompaction = null;
  if (state.automaticCompactionTimer) {
    window.clearTimeout(state.automaticCompactionTimer);
    state.automaticCompactionTimer = null;
  }
  if (remove) state.automaticCompactionRow?.remove();
  state.automaticCompactionRow = null;
  state.automaticCompactionAnchor = null;
}

function showAutomaticContextCompaction(data, beforeNode) {
  state.pendingAutomaticCompaction = data;
  state.automaticCompactionAnchor = beforeNode || state.automaticCompactionAnchor;
  if (state.automaticCompactionRow) {
    setContextCompactionRow(
      state.automaticCompactionRow,
      contextCompactionPresentation(data).message,
    );
    return;
  }
  if (state.automaticCompactionTimer) return;
  state.automaticCompactionTimer = window.setTimeout(() => {
    state.automaticCompactionTimer = null;
    if (!state.pendingAutomaticCompaction) return;
    state.automaticCompactionRow = createContextCompactionRow(
      state.automaticCompactionAnchor,
    );
    setContextCompactionRow(
      state.automaticCompactionRow,
      contextCompactionPresentation(state.pendingAutomaticCompaction).message,
    );
  }, 150);
}

function finalizeAutomaticContextCompaction(context, runStatus) {
  const compaction = context?.compaction;
  if (!compaction?.triggered && !state.pendingAutomaticCompaction) {
    clearAutomaticCompactionState({ remove: true });
    return;
  }
  if (!state.automaticCompactionRow) {
    state.automaticCompactionRow = createContextCompactionRow(
      state.automaticCompactionAnchor,
    );
  }
  const row = state.automaticCompactionRow;
  if (runStatus === "pending") {
    setContextCompactionRow(
      row,
      "本轮上下文已整理，完成工具交互后再生成压缩事件",
      { active: false },
    );
    state.automaticCompactionRow = null;
    state.pendingAutomaticCompaction = null;
    state.automaticCompactionAnchor = null;
    window.setTimeout(() => row.remove(), 2500);
    return;
  }
  if (
    compaction?.summary_applied
    && !compaction?.summary_failed
    && compaction?.target_met
    && compaction?.strategy !== "sliding_window_fallback"
    && compaction?.compaction_event_saved === true
  ) {
    setContextCompactionRow(
      row,
      `上下文已压缩：${compactTokenLabel(compaction.estimated_tokens_before)} → ${compactTokenLabel(compaction.estimated_tokens_after)} Token`,
      { active: false },
    );
    clearAutomaticCompactionState();
    return;
  }
  if (
    compaction?.summary_applied
    && compaction?.target_met
    && compaction?.compaction_event_saved === false
  ) {
    setContextCompactionRow(
      row,
      "上下文已压缩，但压缩事件保存失败",
      { failed: true, active: false },
    );
    state.automaticCompactionRow = null;
    state.pendingAutomaticCompaction = null;
    state.automaticCompactionAnchor = null;
    window.setTimeout(() => row.remove(), 3500);
    return;
  }
  const temporary = compaction?.strategy === "sliding_window_fallback";
  setContextCompactionRow(
    row,
    temporary
      ? "本轮临时缩减，未生成压缩事件"
      : "本轮上下文已整理，未生成新的压缩事件",
    { active: false },
  );
  state.automaticCompactionRow = null;
  state.pendingAutomaticCompaction = null;
  state.automaticCompactionAnchor = null;
  window.setTimeout(() => row.remove(), 2500);
}

function failAutomaticContextCompaction(message) {
  if (!state.pendingAutomaticCompaction && !state.automaticCompactionRow) return;
  if (!state.automaticCompactionRow) {
    state.automaticCompactionRow = createContextCompactionRow(
      state.automaticCompactionAnchor,
    );
  }
  const row = state.automaticCompactionRow;
  setContextCompactionRow(row, message, { failed: true, active: false });
  clearAutomaticCompactionState();
  window.setTimeout(() => row.remove(), 2500);
}

function showConsoleToast(message, isError = false) {
  if (window.DeepAgentConsole?.toast) window.DeepAgentConsole.toast(message, isError);
}

async function refreshCurrentHistory(options = {}) {
  if (!state.sessionId) return;
  await loadSessionHistory({ id: state.sessionId, agentId: state.agentId }, options);
}

function renderHistory(items, { updating = false, running = false } = {}) {
  state.modelMessageCount = items.filter((item) => ["user_message", "assistant_message"].includes(item.kind)).length;
  let lastDayKey = null;
  state.assistantPresenter = null;
  const presentationNodes = projectConversationItems(items);
  const previous = state.historyRendered;
  const next = [];
  const desired = [];
  const scrollTop = el.messages.scrollTop;
  const following = state.messageAutoFollow;
  // Presenter methods normally auto-scroll. During reconciliation wait until
  // all rows are stable, then keep the user's exact reading position.
  state.messageAutoFollow = false;
  for (const [index, node] of presentationNodes.entries()) {
    const ts = node.createdAt;
    let date = null;
    if (ts) {
      const dayKey = localDayKey(ts);
      if (dayKey !== lastDayKey) {
        date = dayKey;
        lastDayKey = dayKey;
      }
    }
    const active = running && index === presentationNodes.length - 1 && node.type === "assistant";
    const signature = JSON.stringify({ node, date, active, readOnly: running });
    const old = previous[index];
    if (old?.signature === signature) {
      next.push(old);
      desired.push(...old.elements);
      if (old.presenter) state.assistantPresenter = old.presenter;
      continue;
    }
    const elements = [];
    if (date) {
      addDateSeparator(ts);
      elements.push(el.messages.lastElementChild);
    }
    let presenter = null;
    if (node.type === "user") {
      if (
        node.text
        || node.attachments.length
        || node.contextRefs.length
      ) {
        elements.push(addMessage(
          "user",
          node.text,
          ts,
          node.order?.sequence,
          node.attachments,
          node.contextRefs,
        ));
      }
    }

    if (node.type === "assistant") {
      const assistant = createAssistantMessage(ts, node.order?.sequence, null, {
        startedAt: node.startedAt,
        completedAt: node.completedAt,
        allowDeferred: !running,
        autoAttachDeferred: false,
      });
      applyAssistantPresentationActions(assistant, node.actions, {
        prepareDeferredCall: mergeUiToolCall,
      });
      if (active) assistant.resumeProcess();
      if (updating && old?.presenter) restoreHistoryDisclosure(old.presenter.row, assistant.row);
      presenter = assistant;
      elements.push(assistant.row);
      state.assistantPresenter = assistant;
    }
    next.push({ signature, elements, presenter });
    desired.push(...elements);
  }
  const retained = new Set(next);
  for (const old of previous) {
    if (retained.has(old)) continue;
    old.presenter?.dispose?.();
    old.elements.forEach((element) => element.remove());
  }
  desired.forEach((element, index) => {
    const existing = el.messages.children[index];
    if (existing !== element) el.messages.insertBefore(element, existing || null);
  });
  state.historyRendered = next;
  state.messageAutoFollow = following;
  if (updating && !following) {
    el.messages.scrollTop = scrollTop;
  } else {
    scrollToBottom({ force: !updating });
  }
  state.awaitingHitl = !running && latestPendingDeferredTool(items) !== null;
  // Plan 是当前 Run 的实时辅助浮层；历史会话只保留其事实和轨迹，不恢复浮层。
  clearComposerPlan();
  updateHitlInputState();
  updateComposerContextControls();
}

function restoreHistoryDisclosure(oldRow, nextRow) {
  for (const selector of [".assistant-process", ".assistant-thinking", ".tool-block"]) {
    const oldBlocks = [...oldRow.querySelectorAll(selector)];
    for (const [index, block] of [...nextRow.querySelectorAll(selector)].entries()) {
      const previous = selector === ".tool-block"
        ? oldBlocks.find((candidate) => candidate.dataset.toolCallId === block.dataset.toolCallId)
        : oldBlocks[index];
      if (!previous) continue;
      const open = previous.classList.contains("open");
      block.classList.toggle("open", open);
      if (previous.dataset.disclosureTouched) block.dataset.disclosureTouched = "1";
      block.querySelector("[aria-expanded]")?.setAttribute("aria-expanded", String(open));
    }
  }
}

const sessionDeletesInFlight = new Set();
async function deleteSession(session) {
  const normalized = normalizeSession(session);
  const key = sessionLookupKey(normalized);
  if (sessionDeletesInFlight.has(key)) return;
  sessionDeletesInFlight.add(key);
  try {
    const response = await daFetch(sessionApiUrl(normalized), { method: "DELETE" });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(typeof body?.detail === "string" ? body.detail : `HTTP ${response.status}`);
    }
  } catch (error) {
    addSystemNote(`删除失败：${error.message}`);
    return;
  } finally {
    sessionDeletesInFlight.delete(key);
  }
  state.sessions = state.sessions.filter((s) => sessionLookupKey(s) !== key);
  if (sessionPage.agentId === normalized.agentId) sessionPage.offset = Math.max(0, sessionPage.offset - 1);
  if (normalized.id === state.sessionId && (normalized.agentId || "") === (state.agentId || "")) {
    // DELETE has waited for the server run to stop. Detach any buffered SSE
    // before clearing the UI so late frames cannot restore the deleted session.
    const controller = state.abortController;
    state.abortController = null;
    controller?.abort();
    state.stopController?.abort();
    state.stopController = null;
    stopHistoryObservation();
    setStreaming(false);
    newChat();
  }
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
  } finally {
    updateEmptyAgentName();
  }
}

function currentAgent() {
  return state.agents.find((a) => a.id === state.agentId) || null;
}

function updateEmptyAgentName() {
  const heading = el.emptyState.querySelector("#emptyAgentName");
  if (heading) heading.textContent = currentAgent()?.name || "请选择 Agent";
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

function selectedModelUnavailable() {
  const model = selectedLlmModel();
  return Boolean(model && !isModelSelectable(model));
}

function ensureLlmModelSelected() {
  if (!state.llmModels.length) return false;
  const fallback = defaultLlmModelId();
  const valid = state.llmModelId && state.llmModels.some((m) => m.id === state.llmModelId);
  if (!valid) state.llmModelId = fallback || firstSelectableModelId(state.llmModels);
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

function nonTextModalityBadges(modalities = []) {
  const supported = new Set(modalities.map((item) => String(item).toLowerCase()));
  return ["image", "audio", "video"]
    .filter((key) => supported.has(key))
    .map((key) => `<span class="model-modality-badge">${escapeHtml(LLM_MODALITY_LABELS[key] || key)}</span>`)
    .join("");
}

function composerModelLabel(model) {
  return String(model?.name || model?.model || "模型").trim();
}

function composerProviderType(provider) {
  const configured = String(provider?.provider_type || "").trim().toLowerCase();
  if (COMPOSER_PROVIDER_BRANDS[configured]) return configured;
  const hint = `${provider?.name || ""} ${provider?.id || ""}`.toLowerCase();
  if (hint.includes("deepseek")) return "deepseek";
  if (hint.includes("moonshot") || hint.includes("kimi")) return "moonshotai";
  if (hint.includes("qwen") || hint.includes("alibaba")) return "alibaba";
  if (hint.includes("zhipu") || hint.includes("zai")) return "zai";
  if (hint.includes("openrouter")) return "openrouter";
  if (hint.includes("anthropic") || hint.includes("claude")) return "anthropic";
  if (hint.includes("google") || hint.includes("gemini")) return "google";
  if (hint.includes("openai")) return "openai";
  return "openai_compatible";
}

function composerProviderIcon(provider) {
  const providerType = composerProviderType(provider);
  const src = COMPOSER_PROVIDER_BRANDS[providerType];
  return `<img src="${escapeHtml(src)}" alt="" data-provider-type="${escapeHtml(providerType)}" />`;
}

function selectComposerModel(modelId) {
  if (!modelId || modelId === state.llmModelId) {
    setComposerModelMenuOpen(false);
    return;
  }
  state.llmModelId = modelId;
  if (el.modelSelect) el.modelSelect.value = modelId;
  rememberSessionModel();
  setComposerModelMenuOpen(false);
  updateComposerModelInfo();
  updateHitlInputState();
  if (state.sessionId) {
    void loadContextStatus({ id: state.sessionId, agentId: state.agentId });
  }
}

function setComposerModelMenuOpen(open) {
  state.modelMenuOpen = Boolean(open && !state.streaming && el.composerModelMenu);
  el.composerModelMenu?.classList.toggle("hidden", !state.modelMenuOpen);
  el.composerModelTrigger?.setAttribute("aria-expanded", String(state.modelMenuOpen));
  el.composerMeta?.classList.toggle("open", state.modelMenuOpen);
}

function uploadCapabilityText() {
  const kinds = Array.isArray(state.uploadCapabilities?.kinds) ? state.uploadCapabilities.kinds : [];
  const labels = state.uploadCapabilities?.labels || {};
  if (!kinds.length) return "添加附件（上传类型尚未加载）";
  const ordered = UPLOAD_KIND_ORDER.filter((key) => kinds.includes(key)).concat(
    kinds.filter((key) => !UPLOAD_KIND_ORDER.includes(key)),
  );
  return [
    "附件",
    `支持：${ordered.map((key) => labels[key] || key).join("、")}`,
    `支持点击、拖拽或粘贴 · 最多 ${Number(state.uploadCapabilities?.max_count) || 10} 个 · 单个 ${Number(state.uploadCapabilities?.max_size_mb) || 20}MB`,
  ].join("\n");
}

async function loadUploadCapabilities() {
  try {
    const r = await daFetch(serviceUrl("api/v1/uploads/capabilities"));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state.uploadCapabilities = await r.json();
    const allowed = Array.isArray(state.uploadCapabilities?.allowed_mimes)
      ? state.uploadCapabilities.allowed_mimes
      : [];
    if (el.fileInput && allowed.length) {
      const accept = [...allowed];
      if (allowed.includes("text/plain")) accept.push(".log");
      el.fileInput.accept = accept.join(",");
    }
  } catch (e) {
    state.uploadCapabilities = { kinds: [], labels: {}, max_count: 10, max_size_mb: 20 };
    console.warn("[deep-agent] 加载上传能力失败", e);
  }
  updateComposerModelInfo();
}

async function loadLlmModels() {
  try {
    const r = await daFetch(serviceUrl("api/v1/llm-models"));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state.llmModels = preserveCurrentModel(await r.json(), currentAgent()?.model);
    ensureLlmModelSelected();
    updateComposerModelInfo();
    updateHitlInputState();
  } catch (e) {
    state.llmModels = [];
    console.warn("[deep-agent] 加载 LLM 模型失败", e);
    updateComposerModelInfo();
    updateHitlInputState();
  }
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
      el.modelSelect.innerHTML = renderGroupedModelOptions(state.llmModels, { selectedId: state.llmModelId });
      el.modelSelect.value = state.llmModelId;
      el.modelSelect.disabled = state.streaming || !state.llmModels.some(isModelSelectable);
      const selected = selectedLlmModel();
      el.modelSelect.title = selected ? modelOptionLabel(selected) : "选择本会话使用的 LLM 模型";
    }
  }

  const selected = selectedLlmModel();
  if (el.composerModelTrigger) {
    const selectedLabel = selected ? composerModelLabel(selected) : (info?.presetName || "选择模型");
    el.composerModelTrigger.disabled = state.streaming || !state.llmModels.some(isModelSelectable);
    el.composerModelTrigger.querySelector(".composer-model-trigger-label").textContent = selectedLabel;
    el.composerModelTrigger.querySelector(".composer-provider-icon").innerHTML = composerProviderIcon(modelProvider(selected));
    el.composerModelTrigger.title = selected
      ? `${modelProvider(selected)?.name || "Provider"} · ${selectedLabel}`
      : "选择本会话使用的 LLM 模型";
  }
  if (el.composerModelMenu) {
    const visible = state.llmModels.filter((model) => isModelSelectable(model) || model.id === state.llmModelId);
    el.composerModelMenu.innerHTML = visible.length
      ? visible.map((model) => {
          const active = model.id === state.llmModelId;
          const provider = modelProvider(model)?.name || "Provider";
          return `<button type="button" class="composer-model-option${active ? " active" : ""}" role="option" aria-selected="${active}" data-model-id="${escapeHtml(model.id)}">
            <span class="composer-provider-icon" title="${escapeHtml(provider)}">${composerProviderIcon(modelProvider(model))}</span>
            <strong class="composer-model-option-name">${escapeHtml(composerModelLabel(model))}</strong>
            <span class="composer-model-option-badges">${nonTextModalityBadges(model.input_modalities || [])}</span>
            ${active ? `<span class="composer-model-check">${lucideIcon("check")}</span>` : ""}
          </button>`;
        }).join("")
      : '<div class="composer-model-empty">无可用模型</div>';
    el.composerModelMenu.querySelectorAll("[data-model-id]").forEach((button) => {
      button.addEventListener("click", () => selectComposerModel(button.dataset.modelId));
    });
  }
  const tooltip = uploadCapabilityText();
  if (el.attachmentTooltip) el.attachmentTooltip.textContent = tooltip;
  if (el.attachBtn) el.attachBtn.removeAttribute("title");
  refreshLucideIcons();
}

function updateChatHeader() {
  updateEmptyAgentName();
  el.sessionBadge.textContent = formatSessionIdSubtitle(state.sessionId);
  if (el.copySessionIdBtn) {
    el.copySessionIdBtn.hidden = !state.sessionId;
    el.copySessionIdBtn.disabled = false;
    el.copySessionIdBtn.classList.remove("copied");
    el.copySessionIdBtn.title = "复制会话 ID";
    el.copySessionIdBtn.setAttribute("aria-label", "复制会话 ID");
    el.copySessionIdBtn.innerHTML = lucideIcon("copy");
  }
  updateComposerModelInfo();
  refreshLucideIcons();
}

async function copyCurrentSessionId() {
  const sessionId = String(state.sessionId || "");
  if (!sessionId || !el.copySessionIdBtn) return;
  const button = el.copySessionIdBtn;
  button.disabled = true;
  try {
    await copyTextToClipboard(sessionId);
    button.classList.add("copied");
    button.title = "会话 ID 已复制";
    button.setAttribute("aria-label", "会话 ID 已复制");
    button.innerHTML = lucideIcon("check");
    refreshLucideIcons();
    showConsoleToast("会话 ID 已复制");
    window.setTimeout(() => {
      if (!button.isConnected || state.sessionId !== sessionId) return;
      button.classList.remove("copied");
      button.title = "复制会话 ID";
      button.setAttribute("aria-label", "复制会话 ID");
      button.innerHTML = lucideIcon("copy");
      button.disabled = false;
      refreshLucideIcons();
    }, 1500);
  } catch {
    button.disabled = false;
    showConsoleToast("会话 ID 复制失败，请重试", true);
  }
}

function updateAgentSelectState() {
  if (el.agentSelect) el.agentSelect.disabled = state.streaming && !state.observingRun;
  const trigger = document.getElementById("workspaceAgentSelectTrigger");
  if (trigger) trigger.disabled = state.streaming && !state.observingRun;
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
  if ((!text && attachments.length === 0) || state.streaming || state.historyLoading) return;
  if (state.awaitingHitl) {
    addSystemNote("请先完成上方待处理的交互。");
    return;
  }
  if (!ensureAgentSelected()) {
    addSystemNote("请先选择 Agent。");
    return;
  }
  if (selectedModelUnavailable()) {
    showConsoleToast("当前 Agent 的模型已停用或不可用，请先选择可用模型", true);
    return;
  }

  hideEmptyState();
  state.messageAutoFollow = true;
  el.input.value = "";
  clearPendingAttachments();
  autoGrow();

  clearComposerPlan();
  const turnStartedAt = new Date().toISOString();
  addMessage("user", text, turnStartedAt, null, attachments);
  trajectory?.addUser({ content: text, attachments });

  const assistant = createAssistantMessage(null, null, null, { startedAt: turnStartedAt });
  state.assistantPresenter = assistant;
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
    }));
  }
  state.modelMessageCount += 1;
  await runChatStream(payload, assistant);
}

async function resumeDeferred(deferredResults) {
  if (!state.sessionId) return;
  if (state.streaming || state.historyLoading) {
    showConsoleToast("当前会话仍在生成，请先停止后再提交交互结果", true);
    return;
  }
  state.awaitingHitl = false;
  state.messageAutoFollow = true;
  updateHitlInputState();
  clearComposerPlan();

  const assistant = state.assistantPresenter?.row?.isConnected
    ? state.assistantPresenter
    : createAssistantMessage();
  assistant.resumeProcess?.();
  state.assistantPresenter = assistant;
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
  state.historyFollower?.stop();
  state.historyController?.abort();
  state.historyFollower = null;
  state.historyController = null;
  const streamController = new AbortController();
  state.stopController?.abort();
  state.abortController = streamController;
  const current = () => state.abortController === streamController;
  const abortStop = () => { if (current()) state.stopController?.abort(); };
  streamController.signal.addEventListener("abort", abortStop, { once: true });
  state.streamRecoveryNote = null;
  state.stopRequested = false;
  state.streamSessionConfirmed = false;
  // A stream without any terminal event may have persisted facts that the
  // live presenter never received. Only that abnormal path reloads History.
  state.refreshHistoryAfterStream = true;
  try {
    const resp = await daFetch(serviceUrl("api/v1/chat/stream"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(payload),
      signal: streamController.signal,
    });

    if (!resp.ok || !resp.body) {
      // An explicit rejection happens before SSE admission. There may be no
      // persisted session yet; reloading History would erase this error and
      // the unsaved question, then leave the composer waiting on a 404.
      // Timeouts/server errors remain ambiguous and still recover History.
      if (resp.status >= 400 && resp.status < 500 && resp.status !== 408) {
        state.refreshHistoryAfterStream = false;
      }
      let detail = await resp.text().catch(() => "");
      try {
        const body = JSON.parse(detail);
        detail = typeof body?.detail === "string" ? body.detail
          : typeof body?.message === "string" ? body.message : detail;
      } catch { /* Non-JSON upstream errors keep their response text. */ }
      throw new Error(detail || `HTTP ${resp.status}`);
    }
    if (!current()) return;
    setConn("ok", "已连接");

    await readSse(resp.body, (evt) => {
      if (!current() || streamController.signal.aborted) return;
      handleEvent(evt, assistant);
      if (["final", "error", "cancelled"].includes(evt.event || evt.data?.type)) {
        // A real terminal frame needs no further socket bytes to finish.
        streamController.abort();
      }
    });
  } catch (e) {
    if (!current()) return;
    if (e.name === "AbortError") {
      // Transport cleanup (page unload/network teardown), not a user stop result.
      applyAssistantPresentationActions(assistant, assistantActionsFromSse("error"));
    } else {
      setConn("err", "请求失败");
      trajectory?.ingest({ event: "error", data: { message: e.message } });
      failAutomaticContextCompaction("上下文压缩后续请求失败，未生成压缩事件");
      clearComposerPlan();
      addErrorTo(assistant, `请求失败：${e.message}`);
    }
  } finally {
    streamController.signal.removeEventListener("abort", abortStop);
    if (!current()) return;
    state.stopController?.abort();
    state.stopController = null;
    applyAssistantPresentationActions(assistant, assistantActionsFromSse("error"));
    clearComposerPlan();
    if (state.sessionId && state.refreshHistoryAfterStream) {
      try {
        await boundedStreamRequest((signal) => refreshCurrentHistory({ signal, isCurrent: current }));
      } catch {
        if (current()) addSystemNote("恢复历史超时或失败，请重新打开会话查看已保存内容。");
      }
    }
    if (!current()) return;
    if (state.streamRecoveryNote) addSystemNote(state.streamRecoveryNote);
    state.streamRecoveryNote = null;
    state.abortController = null;
    state.stopRequested = false;
    state.streamSessionConfirmed = false;
    setStreaming(false);
    if (state.historyObservationPending) {
      state.historyObservationPending = false;
      const session = normalizeSession({ id: state.sessionId, agentId: state.agentId });
      const controller = new AbortController();
      state.historyController = controller;
      state.historyLoading = true;
      updateHitlInputState();
      void observeSessionHistory(session, controller, () => !controller.signal.aborted
        && state.historyController === controller && !state.abortController
        && state.sessionId === session.id && state.agentId === session.agentId);
    }
    if (state.sessionId) await syncSessionsFromBackend();
  }
}

function updateHitlInputState() {
  const modelUnavailable = selectedModelUnavailable();
  const locked = state.awaitingHitl || modelUnavailable;
  if (el.input) {
    el.input.placeholder = state.awaitingHitl
      ? "请先完成上方交互…"
      : modelUnavailable
        ? "当前 Agent 模型不可用，请先切换模型…"
        : "输入消息…";
    el.input.disabled = locked;
  }
  if (el.sendBtn) el.sendBtn.disabled = locked || state.historyLoading;
  updateComposerContextControls();
}

async function stopStream() {
  if (!state.streaming || state.stopRequested || !state.streamSessionConfirmed) return;
  if (state.observingRun) return stopObservedRun();
  const sessionId = state.sessionId;
  const agentId = state.agentId;
  const streamController = state.abortController;
  if (!sessionId || !streamController) return;
  const current = () => state.abortController === streamController
    && state.sessionId === sessionId && state.agentId === agentId
    && state.streaming && state.refreshHistoryAfterStream;
  const stopController = new AbortController();
  state.stopController?.abort();
  state.stopController = stopController;
  state.stopRequested = true;
  el.stopBtn.disabled = true;
  el.stopBtn.title = "正在停止…";
  el.stopBtn.setAttribute("aria-label", "正在停止");

  const result = await verifyStreamStop({
    signal: stopController.signal,
    isCurrent: current,
    request: (kind, signal) => daFetch(
      sessionApiUrl({ id: sessionId, agentId }, kind === "cancel" ? "/cancel" : "/run-status"),
      { method: kind === "cancel" ? "POST" : "GET", signal },
    ),
    onAccepted: () => showConsoleToast("正在停止当前生成并核验运行状态…"),
  });
  if (state.stopController === stopController) state.stopController = null;
  stopController.abort();
  if (!current() || result.status === "stale") return;
  if (result.status === "idle") {
    // idle proves no Run remains, not that cancellation itself succeeded.
    state.streamRecoveryNote = "连接异常中断：后端已无运行中的任务，已尝试恢复保存的历史；未收到正常结束通知。";
    streamController.abort();
  } else {
    state.stopRequested = false;
    el.stopBtn.disabled = false;
    el.stopBtn.title = "停止失败，可重试";
    el.stopBtn.setAttribute("aria-label", "停止生成");
    showConsoleToast(`尚未确认停止，可重试：${result.message}`, true);
  }
}

async function stopObservedRun() {
  const sessionId = state.sessionId;
  const agentId = state.agentId;
  const historyController = state.historyController;
  const stopController = new AbortController();
  const current = () => state.historyController === historyController
    && !historyController?.signal.aborted && state.observingRun
    && state.sessionId === sessionId && state.agentId === agentId;
  state.stopController?.abort();
  state.stopController = stopController;
  state.stopRequested = true;
  el.stopBtn.disabled = true;
  el.stopBtn.title = "正在停止…";
  const result = await verifyStreamStop({
    signal: stopController.signal, isCurrent: current,
    request: (kind, signal) => daFetch(
      sessionApiUrl({ id: sessionId, agentId }, kind === "cancel" ? "/cancel" : "/run-status"),
      { method: kind === "cancel" ? "POST" : "GET", signal },
    ),
    onAccepted: () => showConsoleToast("正在停止当前生成并同步已保存内容…"),
  });
  if (state.stopController === stopController) state.stopController = null;
  stopController.abort();
  if (!current() || result.status === "stale") return;
  state.stopRequested = false;
  if (result.status === "idle") {
    // Only the follower's successful final History read may unlock controls.
    void state.historyFollower?.refresh();
  } else {
    el.stopBtn.disabled = false;
    el.stopBtn.title = "停止失败，可重试";
    showConsoleToast(`尚未确认停止，可重试：${result.message}`, true);
  }
}

function setStreaming(v) {
  state.streaming = v;
  if (v) setComposerModelMenuOpen(false);
  el.sendBtn.classList.toggle("hidden", v);
  el.stopBtn.classList.toggle("hidden", !v);
  if (v) {
    el.stopBtn.disabled = !state.streamSessionConfirmed;
    el.stopBtn.title = state.streamSessionConfirmed ? "停止生成" : "正在建立会话";
    el.stopBtn.setAttribute(
      "aria-label",
      state.streamSessionConfirmed ? "停止生成" : "正在建立会话",
    );
  } else {
    el.stopBtn.disabled = false;
    el.stopBtn.title = "停止生成";
    el.stopBtn.setAttribute("aria-label", "停止生成");
  }
  updateAgentSelectState();
  updateHitlInputState();
  updateComposerContextControls();
}

function handleEvent({ event, data }, assistant, { allowDeferred = true } = {}) {
  if (state.assistantPresenter?.row?.isConnected) {
    assistant = state.assistantPresenter;
  }
  const type = event || data.type;
  if (type === "session_title") {
    applySessionTitle(
      { id: data.session_id || state.sessionId, agentId: state.agentId },
      data.title,
    );
    return;
  }
  trajectory?.ingest({ event: type, data });
  switch (type) {
    case "session": {
      const id = data.session_id;
      if (id) {
        state.sessionId = id;
        state.conversationId = data.conversation_id || state.conversationId;
        state.lastRunMetrics = {
          conversation_id: state.conversationId,
          run_id: data.run_id || null,
          status: "running",
          detailed: true,
        };
        state.streamSessionConfirmed = true;
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
        if (state.streaming && !state.stopRequested) {
          el.stopBtn.disabled = false;
          el.stopBtn.title = "停止生成";
          el.stopBtn.setAttribute("aria-label", "停止生成");
        }
      }
      break;
    }
    case "context_compaction_progress":
      showAutomaticContextCompaction(data, assistant.row);
      break;
    case "session_event_appended":
    case "session_event_updated":
      break;
    case "thinking":
      applyAssistantPresentationActions(assistant, assistantActionsFromSse(type, data));
      break;
    case "text":
      applyAssistantPresentationActions(assistant, assistantActionsFromSse(type, data));
      break;
    case "tool_call":
      if (isPlanningToolEvent(data)) {
        showComposerPlanPlaceholder(data.tool_call_id);
      } else {
        applyAssistantPresentationActions(assistant, assistantActionsFromSse(type, data));
      }
      break;
    case "tool_call_removed":
      if (isPlanningToolEvent(data) || state.planningCallIds.has(data.tool_call_id)) {
        removePlanningCall(data.tool_call_id);
      } else {
        applyAssistantPresentationActions(assistant, assistantActionsFromSse(type, data));
      }
      break;
    case "tool_result":
      if (isPlanningToolEvent(data) || state.planningCallIds.has(data.tool_call_id)) {
        if (data.tool_call_id) state.planningCallIds.delete(data.tool_call_id);
        if (data.plan) renderComposerPlan(data.plan);
      } else {
        applyAssistantPresentationActions(assistant, assistantActionsFromSse(type, data));
      }
      break;
    case "final":
      applyAssistantPresentationActions(assistant, assistantActionsFromSse(type, data));
      if (data.usage) renderUsage(data.usage);
      else state.lastRunUsage = null;
      state.conversationId = data.conversation_id || state.conversationId;
      state.lastRunMetrics = {
        ...(data.run_metrics || {}),
        conversation_id: state.conversationId,
        run_id: data.run_id || data.run_metrics?.run_id || state.lastRunMetrics?.run_id || null,
        status: data.status || data.run_metrics?.status || "completed",
        detailed: true,
      };
      if (data.context) {
        state.contextStatus = {
          ...(state.contextStatus || {}),
          ...data.context.budget,
          compaction_enabled: data.context.budget.enabled,
          compaction_reason: data.context.budget.reason,
          last_compaction: data.context.compaction,
        };
        if (state.sessionId) void loadContextStatus(state.sessionId);
      }
      finalizeAutomaticContextCompaction(data.context, data.status);
      state.refreshHistoryAfterStream = false;
      clearComposerPlan();
      if (data.status === "pending" && data.deferred && allowDeferred) {
        state.awaitingHitl = true;
        setStreaming(false);
        applyAssistantPresentationActions(
          assistant,
          assistantActionsFromDeferred(data.deferred),
          { prepareDeferredCall: mergeUiToolCall },
        );
      } else {
        state.awaitingHitl = false;
      }
      updateHitlInputState();
      break;
    case "error":
      state.lastRunMetrics = { ...state.lastRunMetrics, status: "error" };
      failAutomaticContextCompaction("上下文压缩后续请求失败，未生成压缩事件");
      applyAssistantPresentationActions(assistant, assistantActionsFromSse(type, data));
      // Error is transient SSE only — refreshing history would wipe the bubble.
      state.refreshHistoryAfterStream = false;
      addErrorTo(assistant, data.message || "未知错误");
      clearComposerPlan();
      break;
    case "cancelled":
      failAutomaticContextCompaction("上下文压缩已取消，未生成压缩事件");
      // Keep the current streamed conversation in place. The official
      // interrupted history remains the source of truth when the session is
      // reopened, but re-rendering it here causes a disruptive visual reload.
      state.refreshHistoryAfterStream = false;
      applyAssistantPresentationActions(assistant, assistantActionsFromSse(type, data));
      clearComposerPlan();
      if (data.usage) renderUsage(data.usage);
      state.conversationId = data.conversation_id || state.conversationId;
      state.lastRunMetrics = {
        ...(data.run_metrics || {}),
        conversation_id: state.conversationId,
        run_id: data.run_id || state.lastRunMetrics?.run_id || null,
        status: "cancelled",
        detailed: true,
      };
      break;
    default:
      break;
  }
  scrollToBottom();
}

function renderUsage(usage) {
  state.lastRunUsage = { ...usage };
}

/* --------------------------- 消息渲染 --------------------------- */
function createMessageActions(...args) { return getConversationPresenter().createMessageActions(...args); }

function addMessage(...args) { return getConversationPresenter().addMessage(...args); }

function renderMessageContextRefs(...args) { return getConversationPresenter().renderMessageContextRefs(...args); }

function renderMessageAttachments(...args) { return getConversationPresenter().renderMessageAttachments(...args); }

async function onFilesSelected(e) {
  const files = Array.from(e.target.files || []);
  e.target.value = "";
  for (const file of files) {
    await uploadAttachment(file);
  }
}

async function onPaste(e) {
  const files = Array.from(e.clipboardData?.files || []);
  if (!files.length) return;
  e.preventDefault();
  for (const file of files) {
    await uploadAttachment(file);
  }
}

function setComposerDragging(active) {
  el.composerDropZone?.classList.toggle("dragging", active);
  el.composerDropOverlay?.setAttribute("aria-hidden", String(!active));
}

function onComposerDragEnter(event) {
  if (!event.dataTransfer?.types?.includes("Files")) return;
  event.preventDefault();
  state.composerDragDepth += 1;
  setComposerDragging(true);
}

function onComposerDragOver(event) {
  if (!event.dataTransfer?.types?.includes("Files")) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "copy";
}

function onComposerDragLeave(event) {
  event.preventDefault();
  state.composerDragDepth = Math.max(0, state.composerDragDepth - 1);
  if (state.composerDragDepth === 0) setComposerDragging(false);
}

async function onComposerDrop(event) {
  event.preventDefault();
  state.composerDragDepth = 0;
  setComposerDragging(false);
  for (const file of Array.from(event.dataTransfer?.files || [])) {
    await uploadAttachment(file);
  }
}

async function uploadAttachment(file) {
  const maxCount = Number(state.uploadCapabilities?.max_count) || 10;
  if (state.pendingAttachments.length >= maxCount) {
    addSystemNote(`单次最多 ${maxCount} 个附件。`);
    return;
  }
  const maxSizeMb = Number(state.uploadCapabilities?.max_size_mb) || 20;
  if (file.size > maxSizeMb * 1024 * 1024) {
    addSystemNote(`单个附件不能超过 ${maxSizeMb}MB。`);
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
      previewUrl: String(data.mime || "").toLowerCase().startsWith("image/")
        ? URL.createObjectURL(file)
        : null,
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
    chip.className = `pending-attachment${att.previewUrl ? " pending-image-attachment" : ""}`;
    const label = escapeHtml(att.name || att.id);
    chip.innerHTML = att.previewUrl
      ? `<button type="button" class="pending-attachment-open" aria-label="预览图片 ${label}"><span>${label}</span><span class="pending-attachment-thumbnail"><img src="${escapeHtml(att.previewUrl)}" alt="" /></span></button><button type="button" class="pending-attachment-remove" aria-label="移除附件">${lucideIcon("x")}</button>`
      : `<span class="pending-attachment-name">${label}</span><button type="button" class="pending-attachment-remove" aria-label="移除附件">${lucideIcon("x")}</button>`;
    chip.querySelector(".pending-attachment-open")?.addEventListener("click", () => {
      openAttachmentImagePreview(att, state.pendingAttachments);
    });
    chip.querySelector(".pending-attachment-remove").addEventListener("click", () => {
      if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
      if (el.attachmentImageDialog?.open && el.attachmentImageDialogImage?.src === att.previewUrl) {
        closeAttachmentImagePreview();
      }
      state.pendingAttachments = state.pendingAttachments.filter((a) => a.id !== att.id);
      renderPendingAttachments();
    });
    el.attachmentPreview.appendChild(chip);
  }
  refreshLucideIcons();
}

function clearPendingAttachments() {
  closeAttachmentImagePreview();
  for (const attachment of state.pendingAttachments) {
    if (attachment.previewUrl) URL.revokeObjectURL(attachment.previewUrl);
  }
  state.pendingAttachments = [];
  renderPendingAttachments();
}

function openAttachmentImagePreview(attachment, attachments) {
  if (!attachment?.previewUrl || !el.attachmentImageDialog) return;
  attachmentImageGallery = createImageGallery(attachment, attachments);
  renderAttachmentImagePreview();
  if (!el.attachmentImageDialog.open) el.attachmentImageDialog.showModal();
  refreshLucideIcons();
}

function renderAttachmentImagePreview() {
  const { items, index } = attachmentImageGallery;
  const attachment = items[index];
  if (!attachment) return;
  el.attachmentImageDialogImage.src = attachment.previewUrl;
  el.attachmentImageDialogImage.alt = attachment.name || "图片附件";
  el.attachmentImageDialogName.textContent = `${attachment.name || "图片附件"}${items.length > 1 ? ` · ${index + 1} / ${items.length}` : ""}`;
  el.attachmentImageDialogPrevious.hidden = items.length < 2;
  el.attachmentImageDialogNext.hidden = items.length < 2;
  el.attachmentImageDialogPrevious.disabled = index === 0;
  el.attachmentImageDialogNext.disabled = index === items.length - 1;
}

function moveAttachmentImagePreview(step) {
  const next = attachmentImageGallery.index + step;
  if (next < 0 || next >= attachmentImageGallery.items.length) return;
  attachmentImageGallery.index = next;
  renderAttachmentImagePreview();
}

function closeAttachmentImagePreview() {
  if (el.attachmentImageDialog?.open) el.attachmentImageDialog.close();
  if (el.attachmentImageDialogImage) el.attachmentImageDialogImage.removeAttribute("src");
}

function addDateSeparator(iso) {
  const sep = document.createElement("div");
  sep.className = "date-separator";
  sep.textContent = formatDateSeparator(iso);
  el.messages.appendChild(sep);
}

function addSystemNote(text) {
  const row = document.createElement("div");
  row.className = "msg-row assistant";
  row.innerHTML = `<div class="avatar assistant system-avatar">${lucideIcon("info")}</div><div class="bubble-wrap"><div class="bubble" style="opacity:.7;font-size:13px"></div></div>`;
  row.querySelector(".bubble").textContent = text;
  el.messages.appendChild(row);
  refreshLucideIcons();
  scrollToBottom();
}

function processElapsedSecondsBetween(...args) { return getConversationPresenter().processElapsedSecondsBetween(...args); }

function createAssistantMessage(...args) { return getConversationPresenter().createAssistantMessage(...args); }

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
  state.refreshHistoryAfterStream = false;
  updateHitlInputState();
}

function renderHitlWidget(block, call, onSubmit) {
  const merged = mergeUiToolCall(call);
  const { tool, args = {}, tool_call_id: toolCallId, metadata = {} } = merged;
  const body = block.querySelector(".tool-body");
  if (!body || body.querySelector(".hitl-widget")) return;

  block.classList.add("hitl-pending");
  if (merged.widget) block.dataset.widget = merged.widget;
  if (metadata && Object.keys(metadata).length) {
    block.dataset.metadata = JSON.stringify(metadata);
  }
  const defaultTitle = block.dataset.defaultTitle || tool || "工具";
  block.dataset.defaultTitle = defaultTitle;
  block.querySelector(".tool-name").textContent = `等待操作 · ${defaultTitle}`;
  block.querySelector(".tool-status").innerHTML = '<span class="tool-status-pending">等待操作</span>';

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
        ? (window.DeepAgentJson?.renderBlock(metadata, { className: "hitl-metadata", ariaLabel: "交互工具 Metadata JSON" })
          || `<pre class="hitl-metadata">${escapeHtml(pretty(metadata))}</pre>`)
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

function setToolBlockExpanded(...args) { return getConversationPresenter().setToolBlockExpanded(...args); }

function renderHitlError(block, message) {
  const body = block.querySelector(".tool-body");
  if (!body || body.querySelector(".hitl-format-error")) return;
  block.classList.remove("hitl-pending");
  const title = block.querySelector(".tool-name");
  if (title && block.dataset.defaultTitle) title.textContent = block.dataset.defaultTitle;
  block.querySelector(".tool-status").innerHTML = '<span class="tool-status-error">参数无效</span>';
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

function addErrorTo(assistant, message) {
  const wrap = assistant.row.querySelector(".bubble-wrap");
  const div = document.createElement("div");
  div.className = "error-bubble";
  div.innerHTML = `${lucideIcon("triangle-alert")}<span>${escapeHtml(message)}</span>`;
  wrap.appendChild(div);
  refreshLucideIcons();
  scrollToBottom();
}

/* --------------------------- 工具函数 --------------------------- */
function localDayKey(iso) {
  const d = parseServerTimestamp(iso);
  if (!d) return "invalid";
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function formatDateSeparator(iso) {
  const d = parseServerTimestamp(iso);
  if (!d) return "日期未知";
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const msgDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.round((today - msgDay) / 86400000);
  if (diffDays === 0) return "今天";
  if (diffDays === 1) return "昨天";
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

function formatMessageTime(...args) { return getConversationPresenter().formatMessageTime(...args); }

function lastUserText() {
  const rows = el.messages.querySelectorAll(".msg-row.user .bubble");
  return rows.length ? rows[rows.length - 1].textContent : "";
}

function scrollToBottom({ force = false } = {}) {
  if (!el.messages || (!force && !state.messageAutoFollow)) return;
  if (force) state.messageAutoFollow = true;
  if (state.messageScrollFrame) cancelAnimationFrame(state.messageScrollFrame);
  state.messageScrollFrame = requestAnimationFrame(() => {
    state.messageScrollFrame = null;
    el.messages.scrollTop = el.messages.scrollHeight;
  });
}

function hasNonEmptyArgs(...args) { return getConversationPresenter().hasNonEmptyArgs(...args); }

function pretty(...args) { return getConversationPresenter().pretty(...args); }

function renderToolResult(...args) { return getConversationPresenter().renderToolResult(...args); }

function findLocalFileResult(...args) { return getConversationPresenter().findLocalFileResult(...args); }

function renderLocalFileDownload(...args) { return getConversationPresenter().renderLocalFileDownload(...args); }

function renderToolResultArtifacts(...args) { return getConversationPresenter().renderToolResultArtifacts(...args); }

function renderGeneratedFileArtifact(...args) { return getConversationPresenter().renderGeneratedFileArtifact(...args); }

function formatArtifactBytes(...args) { return getConversationPresenter().formatArtifactBytes(...args); }

function escapeHtml(...args) { return getConversationPresenter().escapeHtml(...args); }

function initMarkdown() {
  window.DeepAgentMarkdown?.init();
}

function renderMarkdown(...args) { return getConversationPresenter().renderMarkdown(...args); }

function renderMarkdownBody(...args) { return getConversationPresenter().renderMarkdownBody(...args); }

function renderMarkdownDiagrams(...args) { return getConversationPresenter().renderMarkdownDiagrams(...args); }

async function selectAgent(agentId) {
  if (!agentId || !state.agents.some((agent) => agent.id === agentId)) return false;
  if (state.streaming && !state.observingRun) {
    showConsoleToast("请先停止当前生成，再切换 Agent", true);
    return false;
  }

  if (state.agentId === agentId) return true;
  newChat(agentId);
  await syncSessionsFromBackend();
  return true;
}

async function refreshAgentWorkspace() {
  const previousAgentId = state.agentId;
  const previousSessionId = state.sessionId;
  await loadAgents();
  await syncSessionsFromBackend();

  const scoped = state.sessions.filter(
    (session) => (session.agentId || "") === (state.agentId || ""),
  );
  const target = scoped.find((session) => (
    state.agentId === previousAgentId && session.id === previousSessionId
  )) || scoped[0];
  if (target && (!state.streaming || state.observingRun)) await switchSession(target);
  return true;
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
  async openSession(session) {
    await switchSession(session);
  },
  setComposerText(text) {
    el.input.value = String(text || "");
    autoGrow();
    el.input.focus();
  },
  selectAgent,
  refreshAgentWorkspace,
};

// Presentation is shared with diagnostics; execution callbacks stay owned here.
let sharedConversationPresenter;
function getConversationPresenter() {
  return sharedConversationPresenter ||= createConversationPresenter({
    messages: el.messages, scrollToBottom, showConsoleToast, mergeUiToolCall,
    isUiToolCall, maybeAttachHitlWidget, renderHitlWidget, resumeDeferred,
    onContextReference: (ref, point) => window.DeepAgentConsoleWorkspace?.toggleContextReferenceInspector(ref, point),
    isContextReferenceSelected: (ref, point) => window.DeepAgentConsoleWorkspace?.isContextReferenceSelected(ref, point),
  });
}

init();
