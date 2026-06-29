"use strict";

/* ------------------------------------------------------------------ *
 * deep-agent 前端 —— 适配 FastAPI /api/v1/chat/stream (SSE over POST)
 * 协议详见 docs/sse-protocol.md
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

function defaultBaseUrl() {
  if (location.protocol === "file:") return "http://localhost:8000";
  const root = window.__DEEP_AGENT_APP_ROOT__ || window.__DEEP_AGENT_BASE__ || "/";
  return `${location.origin}${root.replace(/\/$/, "")}`;
}

const DEFAULT_BASE_URL = defaultBaseUrl();

const BUILTIN_UI_TOOLS = new Set(["confirm_action", "ask_user_choice"]);
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
    mcp_server_name: call.mcp_server_name || def.mcp_server_name || null,
    parameters_json_schema: call.parameters_json_schema || def.parameters_json_schema || null,
    result_schema: call.result_schema || def.result_schema || null,
  };
}

function isUiToolCall(call) {
  if (!call?.tool) return false;
  if (BUILTIN_UI_TOOLS.has(call.tool)) return true;
  if (call.widget) return true;
  return uiToolsByName.has(call.tool);
}

function resolveWidgetType(call) {
  if (call.tool === "confirm_action" || call.widget === "confirm") return "confirm";
  if (call.tool === "ask_user_choice" || call.widget === "choice") return "choice";
  if (call.widget === "text") return "text";
  if (call.widget === "date_picker") return "date_picker";
  if (call.widget) return "custom";
  return null;
}

async function loadUiTools(agentId) {
  try {
    const params = new URLSearchParams({ enabled: "true" });
    if (agentId) params.set("agent_id", agentId);
    const r = await fetch(`${state.baseUrl}/api/v1/ui-tools?${params}`);
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
  baseUrl: localStorage.getItem("da_base_url") || DEFAULT_BASE_URL,
  agentId: localStorage.getItem("da_agent_id") || "",
  agents: [],
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
  baseUrlInput: $("baseUrlInput"),
  agentSelect: $("agentSelect"),
  connStatus: $("connStatus"),
  sessionBadge: $("sessionBadge"),
  chatTitle: $("chatTitle"),
  usageInfo: $("usageInfo"),
  attachBtn: $("attachBtn"),
  fileInput: $("fileInput"),
  attachmentPreview: $("attachmentPreview"),
};

/* --------------------------- 初始化 --------------------------- */
function updateAdminLink() {
  const link = $("adminLink");
  if (link) link.href = `${state.baseUrl}/admin/`;
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
  return `${state.baseUrl}/api/v1/sessions/${encodeURIComponent(s.id)}${suffix}${qs ? `?${qs}` : ""}`;
}

function sessionLookupKey(session) {
  const s = normalizeSession(session);
  return `${s.agentId || ""}:${s.id}`;
}

function sessionSortTime(session) {
  return Date.parse(session.updatedAt || session.createdAt || 0) || 0;
}

function sortSessions() {
  state.sessions.sort((a, b) => sessionSortTime(b) - sessionSortTime(a));
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
    localStorage.setItem("da_agent_id", state.agentId);
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
  el.baseUrlInput.value = state.baseUrl;
  updateAdminLink();

  const agentFromUrl = new URLSearchParams(location.search).get("agent_id");
  if (agentFromUrl) state.agentId = agentFromUrl;

  el.baseUrlInput.addEventListener("change", () => {
    state.baseUrl = el.baseUrlInput.value.trim().replace(/\/$/, "") || DEFAULT_BASE_URL;
    localStorage.setItem("da_base_url", state.baseUrl);
    updateAdminLink();
    checkHealth();
    loadCapabilities();
    loadAgents();
    syncSessionsFromBackend();
  });
  el.agentSelect?.addEventListener("change", () => {
    state.agentId = el.agentSelect.value;
    localStorage.setItem("da_agent_id", state.agentId);
    updateChatHeader();
    loadUiTools(state.agentId);
  });

  $("newChatBtn").addEventListener("click", newChat);
  $("newChatBtnWide").addEventListener("click", newChat);
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

  initMarkdown();
  localStorage.removeItem("da_sessions");
  renderSessions();
  syncSessionsFromBackend();
  checkHealth();
  loadCapabilities();
  loadAgents().then(() => {
    loadUiTools(state.agentId);
    if (agentFromUrl) newChat(agentFromUrl);
    else updateAgentSelectState();
  });
}

async function syncSessionsFromBackend() {
  try {
    const r = await fetch(`${state.baseUrl}/api/v1/sessions?limit=50`);
    if (!r.ok) return;
    const payload = await r.json();
    const rows = Array.isArray(payload?.items) ? payload.items : [];
    state.sessions = rows.map((row) => {
      const agentId = row.agent_id || "";
      const title = (row.user_prompt || row.session_id.slice(0, 8)).slice(0, 40);
      return normalizeSession({
        id: row.session_id,
        agentId,
        agentName: row.agent_name,
        title,
        createdAt: row.created_at || null,
        updatedAt: row.updated_at || row.created_at || null,
      });
    });
    sortSessions();
    renderSessions();
  } catch (_) {
    state.sessions = [];
    renderSessions();
  }
}

function autoGrow() {
  el.input.style.height = "auto";
  el.input.style.height = Math.min(el.input.scrollHeight, 200) + "px";
}

/* --------------------------- 会话管理 --------------------------- */
function newChat(agentId) {
  if (state.streaming) stopStream();
  if (agentId) {
    state.agentId = agentId;
    localStorage.setItem("da_agent_id", state.agentId);
    if (el.agentSelect) el.agentSelect.value = state.agentId;
  } else {
    ensureAgentSelected();
  }
  state.sessionId = null;
  state.branchFrom = null;
  state.modelMessageCount = 0;
  state.lastUserBranchPoint = null;
  el.messages.innerHTML = "";
  el.messages.appendChild(el.emptyState);
  el.emptyState.classList.remove("hidden");
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
    });
  }
  sortSessions();
  renderSessions();
}

function renderSessions() {
  el.sessionList.innerHTML = "";
  if (state.sessions.length === 0) {
    el.sessionList.innerHTML = '<li class="session-empty">暂无会话</li>';
    return;
  }
  for (const s of state.sessions) {
    const li = document.createElement("li");
    li.className =
      "session-item" +
      (s.id === state.sessionId && (s.agentId || "") === (state.agentId || "") ? " active" : "");
    const agentLabel = s.agentName
      ? `<span class="session-agent">${escapeHtml(s.agentName)}</span>`
      : s.agentId
        ? `<span class="session-agent">${escapeHtml(s.agentId.slice(0, 8))}</span>`
        : "";
    li.innerHTML = `<span class="sid" title="${escapeHtml(s.id)}"><span class="session-title">${escapeHtml(s.title)}</span>${agentLabel}</span><span class="del" title="删除">✕</span>`;
    li.querySelector(".sid").addEventListener("click", () => switchSession(s));
    li.querySelector(".del").addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSession(s);
    });
    el.sessionList.appendChild(li);
  }
}

async function switchSession(s) {
  if (state.streaming) stopStream();
  const session = normalizeSession(s);
  state.sessionId = session.id;
  state.branchFrom = null;
  state.lastUserBranchPoint = null;
  if (session.agentId) {
    state.agentId = session.agentId;
    localStorage.setItem("da_agent_id", state.agentId);
    if (el.agentSelect) el.agentSelect.value = state.agentId;
  }
  el.sessionBadge.textContent = session.id.slice(0, 8);
  el.chatTitle.textContent = session.title || "对话";
  updateChatHeader();
  updateAgentSelectState();
  renderSessions();
  await loadSessionHistory(session);
}

/** 从后端拉取并渲染指定会话的历史（唯一消息来源）。 */
async function loadSessionHistory(session) {
  const normalized = normalizeSession(session);
  el.messages.innerHTML = "";
  el.emptyState.classList.add("hidden");
  try {
    await loadUiTools(state.agentId);
    const r = await fetch(sessionApiUrl(normalized, "/history"));
    if (r.status === 404) {
      addSystemNote("会话不存在或尚无历史记录。");
      return;
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    if (!data.messages || data.messages.length === 0) {
      el.emptyState.classList.remove("hidden");
      el.messages.appendChild(el.emptyState);
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
    await fetch(sessionApiUrl(normalized), { method: "DELETE" });
  } catch (_) { /* 忽略 */ }
  if (normalized.id === state.sessionId && (normalized.agentId || "") === (state.agentId || "")) newChat();
  else renderSessions();
}

/* --------------------------- 健康检查 / 能力 --------------------------- */
async function checkHealth() {
  setConn("pending", "检测中…");
  try {
    const r = await fetch(`${state.baseUrl}/health`, { method: "GET" });
    if (r.ok) setConn("ok", "已连接");
    else setConn("err", `HTTP ${r.status}`);
  } catch (_) {
    setConn("err", "未连接");
  }
}

function setConn(kind, text) {
  el.connStatus.className = "conn-status" + (kind === "ok" ? " ok" : kind === "err" ? " err" : "");
  el.connStatus.querySelector(".conn-text").textContent = text;
}

async function loadAgents() {
  if (!el.agentSelect) return;
  el.agentSelect.innerHTML = '<option value="">加载中…</option>';
  try {
    const r = await fetch(`${state.baseUrl}/api/v1/agents`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state.agents = await r.json();
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
  } catch (e) {
    el.agentSelect.innerHTML = `<option value="">加载失败</option>`;
    console.warn("[deep-agent] 加载 Agent 失败", e);
  }
}

function updateChatHeader() {
  if (state.agentId) {
    el.sessionBadge.textContent = state.sessionId
      ? state.sessionId.slice(0, 8)
      : agentNameById(state.agentId);
  }
}

function updateAgentSelectState() {
  if (el.agentSelect) el.agentSelect.disabled = state.streaming || !!state.sessionId;
}

async function loadCapabilities() {
  el.capList.innerHTML = '<li class="cap-empty">加载中…</li>';
  try {
    const r = await fetch(`${state.baseUrl}/api/v1/tools`);
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

  el.emptyState.classList.add("hidden");
  el.input.value = "";
  clearPendingAttachments();
  autoGrow();

  const branchFrom = state.branchFrom;
  if (branchFrom != null) {
    removeDomFromBranchPoint(branchFrom);
  }

  const userBranchPoint = branchFrom != null ? branchFrom : state.modelMessageCount;
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
  await runChatStream(payload, assistant);
}

async function runChatStream(payload, assistant) {
  state.abortController = new AbortController();
  state.streamCancelled = false;
  state.skipHistoryRefresh = false;
  let stoppedByUser = false;
  try {
    const resp = await fetch(`${state.baseUrl}/api/v1/chat/stream`, {
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

/**
 * 解析 SSE 流（fetch ReadableStream）。
 * 事件块以空行分隔，每块包含 event: / data: 行；以 ":" 开头的是注释(心跳)。
 */
async function readSse(stream, onEvent) {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // sse_starlette 默认以 \r\n 分行，事件块之间是 \r\n\r\n；
    // 同时兼容 \n\n，并取最早出现的边界。
    let m;
    while ((m = buffer.match(/\r\n\r\n|\n\n/))) {
      const idx = m.index;
      const rawBlock = buffer.slice(0, idx);
      buffer = buffer.slice(idx + m[0].length);
      const evt = parseSseBlock(rawBlock);
      if (evt) onEvent(evt);
    }
  }
  // flush 残留
  const evt = parseSseBlock(buffer);
  if (evt) onEvent(evt);
}

function parseSseBlock(block) {
  const lines = block.split(/\r?\n/);
  let event = "message";
  const dataLines = [];
  for (const line of lines) {
    if (!line || line.startsWith(":")) continue; // 空行或心跳注释
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (!dataLines.length) return null;
  let data;
  try {
    data = JSON.parse(dataLines.join("\n"));
  } catch (_) {
    data = { raw: dataLines.join("\n") };
  }
  return { event, data };
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
  switch (type) {
    case "session": {
      const id = data.session_id;
      if (id) {
        state.sessionId = id;
        if (data.agent_id) {
          state.agentId = data.agent_id;
          localStorage.setItem("da_agent_id", state.agentId);
          if (el.agentSelect) el.agentSelect.value = state.agentId;
        }
        updateChatHeader();
        const title = lastUserText() || id.slice(0, 8);
        upsertSession(id, title, state.agentId, agentNameById(state.agentId));
        if (el.chatTitle.textContent === "新对话") el.chatTitle.textContent = title.slice(0, 40);
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
      syncHistoryMeta(data);
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
    const r = await fetch(`${state.baseUrl}/api/v1/uploads`, { method: "POST", body: form });
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
      if (textBubble) {
        textBubble.classList.remove("streaming");
        textBubble.innerHTML = renderMarkdownBody(textBuffer);
      }
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
      // 新文本将进入一个新气泡
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

  if (widgetType === "confirm") {
    const message = args.message || "确认执行？";
    widgetEl.innerHTML = `
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
    const options = Array.isArray(args.options) ? args.options : [];
    const allowMultiple = !!args.allow_multiple;
    widgetEl.innerHTML = `
      <p class="hitl-prompt">${escapeHtml(args.prompt || "请选择：")}</p>
      <div class="hitl-choices"></div>
      <button type="button" class="hitl-btn hitl-submit">提交</button>`;
    const choicesEl = widgetEl.querySelector(".hitl-choices");
    for (const opt of options) {
      const label = document.createElement("label");
      label.className = "hitl-choice";
      const input = document.createElement("input");
      input.type = allowMultiple ? "checkbox" : "radio";
      input.name = `choice-${toolCallId}`;
      input.value = opt;
      label.appendChild(input);
      label.appendChild(document.createTextNode(` ${opt}`));
      choicesEl.appendChild(label);
    }
    widgetEl.querySelector(".hitl-submit").addEventListener("click", () => {
      const selected = [...choicesEl.querySelectorAll("input:checked")].map((i) => i.value);
      const result = allowMultiple ? { selected } : { selected: selected[0] ?? null };
      onSubmit([{ tool_call_id: toolCallId, result }]);
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

init();
